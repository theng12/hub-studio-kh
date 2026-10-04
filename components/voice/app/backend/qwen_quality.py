"""Model-owned safety checks for Qwen3-TTS Base voice cloning.

Qwen Base can occasionally fail to emit EOS and fill its entire acoustic-token
budget with repeated or unrelated speech.  These helpers keep that failure
inside Voice Studio: references are aligned before inference, every section
gets a bounded acoustic-token budget, and generated speech is accepted only
after Whisper Large agrees that it matches the requested text.

No customer transcript or filesystem path is stored in quality evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from . import paths


VALIDATOR_REVISION = "voicestudio.qwen-clone-quality.v1"
TERMINAL_GATE_REVISION = "voicestudio.qwen-clone-quality.v2"
TERMINAL_GATE_WINDOW = 24
RETRY_SECTION_MAX_CHARACTERS = 230
RETRYABLE_OUTPUT_CODES = frozenset({
    "QWEN_OUTPUT_DURATION_EXCEEDED",
    "QWEN_OUTPUT_TEXT_MISMATCH",
    "QWEN_OUTPUT_REPETITION_DETECTED",
})
_CACHE_DIR = paths.data_path("reference_cache", "qwen_quality")


def validator_repo(model_id: str) -> str:
    """The companion ASR model the clone-quality gate runs, from the model's manifest.

    The relationship is the producer's: a Qwen Base manifest declares the
    validator package under ``requires_packages`` (``whisper-turbo`` today), and
    that package's own manifest names the repository. Nothing is restated here,
    so re-pointing the gate at a different validator is a manifest edit.

    When the relationship is not declared this refuses by name instead of
    validating against a model the manifest never required: a gate that quietly
    runs a different ASR is a quality claim nobody can reproduce.
    """
    # Local import: `catalog` imports this module at load time, and `cache` is a
    # small reader that this function only needs while a job runs.
    from . import cache

    manifest = cache.manifest_for(model_id) or {}
    by_package = cache.manifests_by_package_id()
    for package_id in manifest.get("requires_packages") or []:
        repo = str((by_package.get(str(package_id)) or {}).get("model_id") or "")
        if repo:
            return repo
    raise QwenQualityError(
        "QWEN_VALIDATOR_MODEL_UNDECLARED",
        f"{model_id} declares no validator package under `requires_packages` in its manifest, "
        "so the clone-quality gate has no ASR model to validate with; add the companion "
        "package id in model-manifests/<package_id>.json (see whisper-turbo.json).",
        {"validator_revision": VALIDATOR_REVISION, "model_id": model_id},
    )


def validator_models(model_id: str) -> list[str]:
    """``validator_repo`` as a published list, or ``[]`` when nothing is declared.

    Used where the answer is advisory (the catalogue's ``required_local_models``
    hint); the gate itself calls ``validator_repo`` and refuses by name.
    """
    try:
        return [validator_repo(model_id)]
    except QwenQualityError:
        return []


@dataclass
class QwenQualityError(RuntimeError):
    code: str
    detail: str
    evidence: dict

    def __str__(self) -> str:
        return self.detail


def is_qwen_base_clone(repo: str, params: dict | None = None) -> bool:
    normalized = (repo or "").strip().lower()
    if "qwen3-tts" not in normalized or "base" not in normalized:
        return False
    if params is None:
        return True
    return bool(
        params.get("voice_library_id")
        or params.get("_reference_audio_path")
    )


def reference_cache_key(audio_path: str | Path, transcript: str) -> str:
    digest = hashlib.sha256()
    with Path(audio_path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    digest.update(b"\0")
    digest.update(transcript.encode("utf-8"))
    digest.update(b"\0")
    digest.update(VALIDATOR_REVISION.encode("ascii"))
    return digest.hexdigest()


def cached_reference_evidence(cache_key: str) -> dict | None:
    path = _CACHE_DIR / f"{cache_key}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        value.get("validator_revision") != VALIDATOR_REVISION
        or value.get("accepted") is not True
    ):
        return None
    return value


def save_reference_evidence(cache_key: str, evidence: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = _CACHE_DIR / f"{cache_key}.json"
    temporary = _CACHE_DIR / f".{cache_key}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, target)


def _normalized_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    # Khmer vowel signs and the coeng are marks, not `\w`; they stay, or every syllable falls apart.
    value = re.sub(r"[^\w\s\u17b4-\u17d3\u17dd]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


# One Khmer orthographic syllable: a consonant or independent vowel with its subscripts (coeng +
# consonant) and signs. Khmer writes no spaces between words, so it is compared syllable by
# syllable, as CJK is compared character by character.
_KHMER_SYLLABLE = re.compile(r"[\u1780-\u17b3](?:\u17d2[\u1780-\u17b3]|[\u17b4-\u17d1\u17d3\u17dd])*")


def _tokens(value: str) -> list[str]:
    normalized = _normalized_text(value)
    words = re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", normalized)
    cjk = re.findall(r"[\u3400-\u9fff\uf900-\ufaff]", normalized)
    return words + cjk + _KHMER_SYLLABLE.findall(normalized)


# ── numeral canonicalization ────────────────────────────────────────────────
# Whisper writes a spoken number as digits ("one two three" -> "123", "forty
# five" -> "45", "seven forty five" -> "7.45"), while the submitted script
# spells it out.  Scoring the two orthographies word by word charged a correct
# reading with three "errors" on a seven-word line, so a flawless short clone
# could cross the mismatch gate purely because of how the number was typed.
# Both sides are folded into one comparable form before any error rate is
# computed.  This changes only what is compared: the 0.40/0.25 gates and the
# retry policy are untouched.
_UNIT_NUMBERS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
}
_TEEN_NUMBERS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS_NUMBERS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALE_NUMBERS = {
    "hundred": 100,
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
}
_NUMBER_WORDS = (
    frozenset(_UNIT_NUMBERS)
    | frozenset(_TEEN_NUMBERS)
    | frozenset(_TENS_NUMBERS)
    | frozenset(_SCALE_NUMBERS)
)


def _canonical_digit_token(token: str) -> str:
    """Fold a numeric token: drop leading zeros, leave codes and words alone.

    ``a1b2c3`` is a code, not a number, and must survive unchanged.
    """
    if token.isdigit():
        stripped = token.lstrip("0")
        return stripped or "0"
    return token


def _parse_number_run(words: list[str]) -> str:
    """Render one run of English number words as a digit string.

    Values are concatenated group by group, so a year or a clock reading keeps
    its spoken shape: "seven forty five" -> "745", "nineteen eighty four" ->
    "1984", while a composed number stays whole: "three hundred twenty four"
    -> "324".
    """
    groups: list[int] = []
    total = 0
    current = 0
    has_units = False
    has_tens = False

    def flush() -> None:
        nonlocal total, current, has_units, has_tens
        groups.append(total + current)
        total = 0
        current = 0
        has_units = False
        has_tens = False

    for word in words:
        scale = _SCALE_NUMBERS.get(word)
        if scale is not None:
            if scale >= 1_000:
                total = max(total + current, 1) * scale
                current = 0
            else:
                current = max(current, 1) * scale
            has_units = False
            has_tens = False
            continue
        value = _UNIT_NUMBERS.get(word)
        if value is None:
            value = _TEEN_NUMBERS.get(word)
        if value is not None:
            # A second units/teens value starts a new group ("seven forty" -> 7,
            # then 40), but a units word after a tens word completes it
            # ("forty five" -> 45).
            if has_units:
                flush()
            current += value
            has_units = True
            continue
        value = _TENS_NUMBERS.get(word)
        if value is not None:
            if has_units or has_tens:
                flush()
            current += value
            has_tens = True
    flush()
    return "".join(str(group) for group in groups)


def _render_number_run(words: list[str]) -> str:
    """A run of single-digit words is read digit by digit ("one two three")."""
    if len(words) >= 2 and all(word in _UNIT_NUMBERS for word in words):
        rendered = "".join(str(_UNIT_NUMBERS[word]) for word in words)
    else:
        rendered = _parse_number_run(words)
    # "oh one five" and an observed "015" must land on the same token.
    return _canonical_digit_token(rendered)


def _number_token_runs(tokens: list[str]) -> dict[int, int]:
    """Map each number-run start index to its inclusive end index.

    A connective "and" joins a run only when a scale word precedes it and a
    number word follows it, so "one hundred and five" is one run while
    "one and two" is not silently merged.
    """
    runs: dict[int, int] = {}
    index = 0
    total = len(tokens)
    while index < total:
        if tokens[index] not in _NUMBER_WORDS:
            index += 1
            continue
        end = index
        while end + 1 < total:
            following = tokens[end + 1]
            if following in _NUMBER_WORDS:
                end += 1
                continue
            if (
                following == "and"
                and any(word in _SCALE_NUMBERS for word in tokens[index:end + 1])
                and end + 2 < total
                and tokens[end + 2] in _NUMBER_WORDS
            ):
                end += 1
                continue
            break
        runs[index] = end
        index = end + 1
    return runs


def _canonical_tokens(tokens: list[str]) -> list[str]:
    """Fold spelled numerals and digit strings into one comparable token form."""
    if not tokens:
        return []
    runs = _number_token_runs(tokens)
    canonical: list[str] = []
    index = 0
    while index < len(tokens):
        end = runs.get(index)
        if end is None:
            canonical.append(_canonical_digit_token(tokens[index]))
            index += 1
            continue
        canonical.append(
            _render_number_run(
                [token for token in tokens[index:end + 1] if token != "and"]
            )
        )
        index = end + 1
    return canonical


def _canonical_characters(value: str) -> list[str]:
    """Character stream of the canonical tokens, in the original word order."""
    return list("".join(_canonical_tokens(_normalized_text(value).split())))


def _distance(left: list[str], right: list[str]) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, item in enumerate(left, start=1):
        current = [row]
        for column, other in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (item != other),
            ))
        previous = current
    return previous[-1]


def _error_rate(expected: list[str], observed: list[str], *, ngram_size: int) -> tuple[float, str]:
    """Exact edit distance for short clips; bounded linear evidence for long form."""
    if max(len(expected), len(observed)) <= 2_048:
        return _distance(expected, observed) / max(1, len(expected)), "edit-distance"
    width = min(ngram_size, max(1, len(expected)))
    expected_ngrams = Counter(
        tuple(expected[index:index + width])
        for index in range(max(1, len(expected) - width + 1))
    )
    observed_ngrams = Counter(
        tuple(observed[index:index + width])
        for index in range(max(1, len(observed) - width + 1))
    )
    overlap = sum((expected_ngrams & observed_ngrams).values())
    missing_rate = 1.0 - overlap / max(1, sum(expected_ngrams.values()))
    length_penalty = max(0, len(observed) - len(expected)) / max(1, len(expected))
    return min(2.0, missing_rate + length_penalty), f"{width}-gram-multiset"


def _similarity(expected: str, observed: str) -> dict:
    expected_tokens = _canonical_tokens(_tokens(expected))
    observed_tokens = _canonical_tokens(_tokens(observed))
    expected_chars = _canonical_characters(expected)
    observed_chars = _canonical_characters(observed)
    token_error, token_method = _error_rate(expected_tokens, observed_tokens, ngram_size=2)
    character_error, character_method = _error_rate(expected_chars, observed_chars, ngram_size=4)
    return {
        "token_error_rate": round(token_error, 4),
        "character_error_rate": round(character_error, 4),
        "comparison_method": {
            "tokens": token_method,
            "characters": character_method,
        },
        "expected_tokens": len(expected_tokens),
        "observed_tokens": len(observed_tokens),
    }


def _word_timestamp_count(result: dict) -> int:
    return sum(
        len(segment.get("words") or [])
        for segment in (result.get("segments") or [])
    )


def _terminal_gate(expected_tokens: list[str], result: dict) -> dict:
    """Return bounded, redacted ordered evidence for a long-form suffix."""
    evidence = {
        "revision": TERMINAL_GATE_REVISION,
        "window_size": TERMINAL_GATE_WINDOW,
        "aligned_word_count": 0,
        "spelling_variant_count": 0,
        "hard_mismatch_count": 0,
        "accepted": False,
    }
    try:
        duration = float(result["duration"])
    except (KeyError, TypeError, ValueError):
        evidence["hard_mismatch_count"] = TERMINAL_GATE_WINDOW
        return evidence
    if not math.isfinite(duration) or duration <= 0:
        evidence["hard_mismatch_count"] = TERMINAL_GATE_WINDOW
        return evidence

    observed_tokens: list[str] = []
    previous_start: float | None = None
    for segment in result.get("segments") or []:
        for word in segment.get("words") or []:
            try:
                start = float(word["start"])
                end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                evidence["hard_mismatch_count"] = TERMINAL_GATE_WINDOW
                return evidence
            if (
                not math.isfinite(start)
                or not math.isfinite(end)
                or start < 0
                or start >= duration
                or end < start
                or end > duration
                or (previous_start is not None and start < previous_start)
            ):
                evidence["hard_mismatch_count"] = TERMINAL_GATE_WINDOW
                return evidence
            previous_start = start
            observed_tokens.extend(_tokens(str(word.get("word") or "")))

    # The aligned word count stays the raw Whisper word count: it is the
    # alignment evidence, not the comparison. Canonicalization then folds the
    # ordered stream exactly as the expected side was folded, so a run the
    # expected side collapsed to digits ("twenty four" -> "24") meets the same
    # digits on the observed side instead of shifting the whole tail.
    evidence["aligned_word_count"] = len(observed_tokens)
    aligned_observed_tokens = _canonical_tokens(observed_tokens)[-TERMINAL_GATE_WINDOW:]

    if len(aligned_observed_tokens) < TERMINAL_GATE_WINDOW:
        evidence["hard_mismatch_count"] = TERMINAL_GATE_WINDOW
        return evidence

    for expected_word, observed_word in zip(
        expected_tokens[-TERMINAL_GATE_WINDOW:], aligned_observed_tokens
    ):
        if expected_word == observed_word:
            continue
        if SequenceMatcher(None, expected_word, observed_word, autojunk=False).ratio() >= 0.80:
            evidence["spelling_variant_count"] += 1
        else:
            evidence["hard_mismatch_count"] += 1
    evidence["accepted"] = evidence["hard_mismatch_count"] <= 1
    return evidence


def _evidence(kind: str, expected: str, observed: str, result: dict) -> dict:
    metrics = _similarity(expected, observed)
    # The evidence names the ASR model that produced it, or nothing at all: a
    # result that does not name its validator cannot be attributed, and writing a
    # repo name this file happens to remember would make the evidence look
    # reproducible when it is not. In the app the transcription manager always
    # sets `model`, and the gate resolved it from the model's own manifest
    # (`validator_repo`).
    whisper_model = str(result.get("model") or "") or None
    return {
        "validator_revision": VALIDATOR_REVISION,
        "kind": kind,
        "whisper_model": whisper_model,
        "expected_sha256": hashlib.sha256(expected.encode("utf-8")).hexdigest(),
        "observed_sha256": hashlib.sha256(observed.encode("utf-8")).hexdigest(),
        "duration_seconds": round(float(result.get("duration") or 0.0), 3),
        "word_timestamp_count": _word_timestamp_count(result),
        **metrics,
    }


def validate_reference(expected: str, result: dict) -> dict:
    observed = str(result.get("text") or "").strip()
    evidence = _evidence("reference", expected, observed, result)
    if not expected.strip():
        raise QwenQualityError(
            "QWEN_REFERENCE_TRANSCRIPT_REQUIRED",
            "Qwen3-TTS Base needs the exact transcript for its reference audio.",
            evidence,
        )
    if evidence["word_timestamp_count"] <= 0:
        raise QwenQualityError(
            "QWEN_REFERENCE_ALIGNMENT_UNAVAILABLE",
            "Whisper Large could not produce word-aligned reference evidence.",
            evidence,
        )
    if not observed or (
        evidence["token_error_rate"] > 0.35
        and evidence["character_error_rate"] > 0.22
    ):
        raise QwenQualityError(
            "QWEN_REFERENCE_TRANSCRIPT_MISMATCH",
            "The supplied reference transcript does not match the selected speech closely enough.",
            evidence,
        )
    evidence["accepted"] = True
    return evidence


def automatic_duration_limit(text: str, speed: float = 1.0) -> float:
    """Return a generous final-audio ceiling while preventing 96 s EOS failures.

    The estimate intentionally favors false acceptance: normal narration can be
    much slower than average and still pass.  A 250-300 character English
    section receives roughly 35-50 seconds, safely above healthy Qwen output
    while remaining far below its 96-second 1,200-token exhaustion point.
    """
    normalized = _normalized_text(text)
    latin_words = len(re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", normalized))
    cjk_chars = len(re.findall(r"[\u3400-\u9fff\uf900-\ufaff]", normalized))
    nonspace_chars = len(normalized.replace(" ", ""))
    natural_seconds = max(
        latin_words / 1.65,
        cjk_chars / 3.2,
        nonspace_chars / 10.0,
        1.0,
    )
    safe_speed = max(0.5, min(float(speed or 1.0), 2.0))
    return round(min(70.0, max(8.0, natural_seconds * 1.65 + 4.0)) / safe_speed, 3)


def max_tokens_for_text(text: str, speed: float = 1.0) -> int:
    native_seconds = automatic_duration_limit(text, speed) * max(0.5, min(speed, 2.0))
    return max(1, min(1199, math.floor(native_seconds * 12.5)))


def validate_output(expected: str, result: dict, *, max_duration_s: float) -> dict:
    observed = str(result.get("text") or "").strip()
    evidence = _evidence("output", expected, observed, result)
    evidence["max_duration_seconds"] = round(float(max_duration_s), 3)
    duration = float(result.get("duration") or 0.0)
    if duration > max_duration_s + 0.05:
        raise QwenQualityError(
            "QWEN_OUTPUT_DURATION_EXCEEDED",
            "Qwen3-TTS produced an implausibly long result and it was rejected.",
            evidence,
        )
    if evidence["comparison_method"]["tokens"] == "2-gram-multiset":
        evidence["terminal_gate"] = _terminal_gate(
            _canonical_tokens(_tokens(expected)), result
        )
        if not evidence["terminal_gate"]["accepted"]:
            raise QwenQualityError(
                "QWEN_OUTPUT_TEXT_MISMATCH",
                "Whisper validation could not establish an ordered long-form ending.",
                evidence,
            )
    if not observed or (
        evidence["token_error_rate"] > 0.40
        and evidence["character_error_rate"] > 0.25
    ):
        raise QwenQualityError(
            "QWEN_OUTPUT_TEXT_MISMATCH",
            "Whisper validation found missing, repeated, or unrelated generated speech.",
            evidence,
        )
    if evidence["observed_tokens"] > max(12, evidence["expected_tokens"] * 1.65):
        raise QwenQualityError(
            "QWEN_OUTPUT_REPETITION_DETECTED",
            "Whisper validation found excessive repeated generated speech.",
            evidence,
        )
    evidence["accepted"] = True
    return evidence
