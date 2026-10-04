"""Per-section acceptance for local TTS: a section must contain its sentence.

An autoregressive TTS section can fail without raising: Breeze TTS 2 4-bit was measured
(Dreamlands, 2026-10-03) returning silence, a slow DC drift, tens of seconds of humming or babble,
or speech that skips whole sentences, for a section of at most 500 characters - and the job still
finished "done". Every section is therefore checked before it is joined:

* cheap checks on the audio itself (no model): DC and drift are removed, then the voiced time and
  the duration are compared with the text length at the family's measured narration rate, and the
  longest silence between two voiced frames with the longest pause the text calls for;
* a transcribe-back check with the Whisper model the family's manifest already requires, when it
  is installed on this Mac: the section's words must be heard.

A failed section is rendered again with a new seed; ``SECTION_RETRIES`` failures in a row fail the
job with a reason that names the section. No transcript, text or path is kept in the evidence.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from . import long_form_policy, qwen_quality

REVISION = "voicestudio.tts-section-quality.v2"
SECTION_RETRIES = 3
FRAME_S = 0.02
DRIFT_WINDOW_S = 0.05          # a moving average this long is "DC" and is removed before levels
VOICED_DBFS = -40.0            # a 20 ms frame above this (after DC/drift removal) is voiced
# Narration rate in characters per second of audio, lowest and highest, measured per family.
# Breeze: 48 Dreamlands chapters, 8.6-11.7 chars/s. Families with no measurement get a band wide
# enough for any human-paced reading; it still catches 50 s of babble for one clause and a section
# that is mostly silence.
NARRATION_CHARS_PER_SECOND = {"breeze-tts": (8.6, 11.7)}
DEFAULT_CHARS_PER_SECOND = (6.0, 20.0)
SLOW_FACTOR = 0.55             # a section may run this much slower than the slowest measured rate
FAST_FACTOR = 2.5              # and its voiced time may be this much faster than the fastest
EDGE_ALLOWANCE_S = 2.5         # engines return up to ~1.2 s of edge silence per side
# Whisper must hear all but max(2 words, 10%) of the section's words. Measured on 131 Dreamlands
# Breeze sections: every good one heard >= 0.938 of its words, every failed one <= 0.849.
MAX_MISSING_SHARE = 0.10
MAX_MISSING_WORDS = 2
# ... and never a clause: a run of more than this many consecutive words not heard. Good sections'
# longest run was 2 (a sentence-initial "the" lost after a pause); every bad one's was 8 or more, and
# EP0003 Ch09 part 5 lost the clause "Silas drew the soft crest of the ridge" to a 35 s silence
# while its 7 missing words of 76 were still inside the 10% share.
MAX_MISSING_RUN = 3
# A silence inside a section may be the longest pause its text calls for (a paragraph, 0.6 s, else a
# sentence, 0.3 s; never less than the longest pause the caller asked for, e.g. Sleep's 1.0 s
# paragraph pause) plus this. Breeze's own sleep-narration pauses run to 3.2-3.4 s at -40 dBFS
# (131 Dreamlands sections, 10 fresh renders, EP0003 Ch09); the failures it must catch were 4.3-35 s
# of silence (EP0003 Ch09 part 5 had 35 s at -66 dB and passed every other check).
# ponytail: one margin for every family, measured on Breeze; give a family its own (as with
# NARRATION_CHARS_PER_SECOND) once one is measured pausing longer on purpose.
PAUSE_MARGIN_S = 3.5
WIDE_CHARACTER_WEIGHT = 3      # one CJK/kana/hangul character takes about as long as three Latin
_WIDE = re.compile(r"[\u1100-\u11ff\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uf900-\ufaff]")
SPELLING_VARIANT_RATIO = 0.8   # "Elara" heard as "Alara" still counts as heard
# Khmer is scored syllable by syllable (`qwen_quality._KHMER_SYLLABLE`) but never rejected on it: the
# score is recorded (`text_check` "advisory") and only the audio checks enforce. Whisper large-v3 turbo
# cannot read Khmer - measured through the fleet (`_orchestra/audit-20261001/tts-khmer/GATE.md`):
# 48 known-good sections (VoxCPM2 4/8-bit, OmniVoice) scored syllable overlap median 0.00, max 0.11, and
# the native Khmer MMS voice 0.007-0.095, while the same audio against the wrong text, cut in half or
# missing a clause scored up to 0.42, and pure silence 0.05: Whisper writes repetition loops
# ("ចញញញញ...", 41 of 48 good sections) or mixed-script fragments, whatever the audio says,
# so no threshold separates a good section from a broken one. Revisit with a Khmer-capable recogniser.
_KHMER = re.compile(r"[\u1780-\u17ff]")
# Families whose engine emits 12.5 acoustic tokens per second (the Qwen3-TTS 12 Hz codec), so a
# section's duration ceiling is also its token budget. Unbounded, mlx-audio's `generate_audio`
# default of 1,200 tokens let one failed Breeze clause run on for 96 s.
TOKENS_PER_SECOND = {"breeze-tts": 12.5}


# The measured value and its limit behind each rejection, as a failed job's message states them
# (short: the Hub keeps 500 characters of a worker's error).
_REJECTION_NUMBERS = {
    "TTS_SECTION_SILENT": "voiced {voiced_s} s < {min_voiced_s} s",
    "TTS_SECTION_TOO_LONG": "{duration_s} s > {max_duration_s} s",
    "TTS_SECTION_INTERNAL_SILENCE": "pause {longest_pause_s} s > {max_pause_s} s",
    "TTS_SECTION_TEXT_MISMATCH": "heard {words_heard}/{words_expected}, overlap {word_overlap}, "
                                 "missing run {longest_missing_run}",
}


def rejection_numbers(records: list[dict]) -> str:
    """'attempt 1: heard 12/40, overlap 0.3, missing run 18; attempt 2: ...' for rejected records."""
    return "; ".join(f"attempt {r['attempt']}: " + _REJECTION_NUMBERS[r["rejection_code"]].format(**r)
                     for r in records if r.get("rejection_code") in _REJECTION_NUMBERS)


@dataclass
class SectionQualityError(RuntimeError):
    code: str
    detail: str
    evidence: dict

    def __str__(self) -> str:
        return self.detail


def audio_metrics(path: str | Path) -> dict:
    """Duration, DC offset and voiced time of one section WAV, with DC and slow drift removed."""
    import numpy as np
    import soundfile as sf

    pcm, rate = sf.read(str(path), dtype="float32", always_2d=True)
    pcm = pcm.mean(axis=1)
    duration = len(pcm) / rate if rate else 0.0
    frame = max(1, int(rate * FRAME_S))
    frames = len(pcm) // frame
    if frames == 0:
        return {"duration_s": round(duration, 3), "dc_offset": 0.0, "voiced_s": 0.0,
                "voiced_ratio": 0.0}
    window = max(1, int(rate * DRIFT_WINDOW_S))
    padded = np.concatenate(([0.0], np.cumsum(pcm, dtype=np.float64)))
    left = np.clip(np.arange(len(pcm)) - window // 2, 0, len(pcm))
    right = np.clip(left + window, 0, len(pcm))
    drift = (padded[right] - padded[left]) / np.maximum(1, right - left)
    level = (pcm - drift)[: frames * frame].reshape(frames, frame)
    db = 20 * np.log10(np.sqrt((level.astype(np.float64) ** 2).mean(axis=1)) + 1e-10)
    voiced_frames = np.flatnonzero(db > VOICED_DBFS)
    voiced = float(len(voiced_frames) * frame / rate)
    # Edge silence is the engine's and is trimmed at the join; only a gap between speech counts.
    gap = int(np.diff(voiced_frames).max() - 1) if len(voiced_frames) > 1 else 0
    return {
        "duration_s": round(duration, 3),
        "dc_offset": round(float(pcm.mean()), 4),
        "voiced_s": round(voiced, 3),
        "voiced_ratio": round(voiced / duration, 3) if duration else 0.0,
        "longest_pause_s": round(gap * frame / rate, 3),
    }


def _band(family: str, speed: float) -> tuple[float, float]:
    low, high = NARRATION_CHARS_PER_SECOND.get(family, DEFAULT_CHARS_PER_SECOND)
    return low * speed, high * speed


def _spoken_characters(text: str) -> int:
    """Latin-equivalent characters: the measured rates are Latin-script narration rates."""
    text = " ".join(text.split())
    return len(text) + (WIDE_CHARACTER_WEIGHT - 1) * len(_WIDE.findall(text))


def transcribe_back_applies(text: str) -> bool:
    """Whisper writes Chinese in either script, and cannot read Khmer at all (GATE.md, above), so a
    CJK or Khmer section is checked by its audio only and Whisper is not loaded for it."""
    return not (_WIDE.search(text) or _KHMER.search(text))


def script_language(text: str) -> str | None:
    """The Whisper language a section's own script settles, whatever the request's `language` says."""
    return "km" if _KHMER.search(text) else None


def max_duration_s(text: str, family: str, speed: float = 1.0) -> float:
    """Longest a section of ``text`` may run: the slowest measured rate, slowed further, plus edges."""
    low, _ = _band(family, max(0.25, float(speed or 1.0)))
    return round(_spoken_characters(text) / (low * SLOW_FACTOR) + EDGE_ALLOWANCE_S, 2)


def max_pause_s(text: str, speed: float = 1.0, requested_pause_s: float = 0.0) -> float:
    """Longest silence a section of ``text`` may hold between two words.

    ``requested_pause_s`` is the longest pause the request asked for (its paragraph pause), so a
    caller's own longer pause never reads as an unnatural one.
    """
    boundary = "paragraph" if re.search(r"\n\s*\n", text) else "sentence"
    pause = max(long_form_policy.production_boundary_pause_seconds(boundary),
                float(requested_pause_s or 0.0)) + PAUSE_MARGIN_S
    return round(pause / max(0.25, float(speed or 1.0)), 2)


def max_tokens_for_section(text: str, family: str, engine_cap: int = 1200) -> int | None:
    """The section's token budget from its duration ceiling, or None for a family with no known rate."""
    rate = TOKENS_PER_SECOND.get(family)
    if rate is None:
        return None
    return max(1, min(engine_cap, math.ceil(max_duration_s(text, family) * rate)))


def heard_words(expected: str, heard: str) -> tuple[int, int]:
    """(words heard, words expected); numbers fold as in the Qwen gate, and a close spelling (a
    name Whisper spells differently) counts as heard."""
    want = Counter(_words(expected))
    got = Counter(_words(heard))
    total = sum(want.values())
    matched = sum((want & got).values())
    spare = list((got - want).elements())
    for word in (want - got).elements():
        for index, other in enumerate(spare):
            if _close(word, other):
                matched += 1
                del spare[index]
                break
    return matched, total


def longest_missing_run(expected: str, heard: str) -> int:
    """Most consecutive words of ``expected`` not heard, in order: a dropped clause."""
    want, got = _words(expected), _words(heard)
    longest = 0
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, want, got, autojunk=False).get_opcodes():
        run = 0
        for word in want[i1:i2] if tag in ("delete", "replace") else ():
            run = 0 if any(_close(word, other) for other in got[j1:j2]) else run + 1
            longest = max(longest, run)
    return longest


def _words(text: str) -> list[str]:
    return qwen_quality._canonical_tokens(qwen_quality._tokens(text))


def _close(word: str, other: str) -> bool:
    return SequenceMatcher(None, word, other, autojunk=False).ratio() >= SPELLING_VARIANT_RATIO


def check_section(text: str, family: str, metrics: dict, *, speed: float = 1.0,
                  heard: str | None = None, requested_pause_s: float = 0.0) -> dict:
    """Evidence for one section; raises ``SectionQualityError`` when the audio is not the text."""
    chars = _spoken_characters(text)
    _, high = _band(family, max(0.25, float(speed or 1.0)))
    evidence = {
        **metrics,
        "characters": chars,
        "max_duration_s": max_duration_s(text, family, speed),
        "min_voiced_s": round(chars / (high * FAST_FACTOR), 2),
        "max_pause_s": max_pause_s(text, speed, requested_pause_s),
    }
    missing_allowed = None
    if heard is not None:
        matched, total = heard_words(text, heard)
        evidence["words_expected"] = total
        evidence["words_heard"] = matched
        evidence["word_overlap"] = round(matched / total, 3) if total else 1.0
        evidence["longest_missing_run"] = longest_missing_run(text, heard)
        if _KHMER.search(text):
            evidence["text_check"] = "advisory"
        else:
            missing_allowed = max(MAX_MISSING_WORDS, math.floor(total * MAX_MISSING_SHARE))
    if metrics["voiced_s"] < evidence["min_voiced_s"]:
        code, why = "TTS_SECTION_SILENT", "came out (nearly) silent or as a DC drift"
    elif metrics["duration_s"] > evidence["max_duration_s"]:
        code, why = "TTS_SECTION_TOO_LONG", "ran far longer than its text (babble or humming)"
    elif metrics.get("longest_pause_s", 0.0) > evidence["max_pause_s"]:
        code, why = "TTS_SECTION_INTERNAL_SILENCE", "stopped mid-text for an unnatural pause"
    elif missing_allowed is not None and (
            total - matched > missing_allowed
            or evidence["longest_missing_run"] > MAX_MISSING_RUN):
        code, why = "TTS_SECTION_TEXT_MISMATCH", "does not say its text (Whisper heard other words)"
    else:
        return {**evidence, "accepted": True}
    evidence.update(accepted=False, rejection_code=code)
    raise SectionQualityError(code, why, evidence)
