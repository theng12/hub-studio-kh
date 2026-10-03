"""Per-section acceptance for local TTS: a section must contain its sentence.

An autoregressive TTS section can fail without raising: Breeze TTS 2 4-bit was measured
(Dreamlands, 2026-10-03) returning silence, a slow DC drift, tens of seconds of humming or babble,
or speech that skips whole sentences, for a section of at most 500 characters - and the job still
finished "done". Every section is therefore checked before it is joined:

* cheap checks on the audio itself (no model): DC and drift are removed, then the voiced time and
  the duration are compared with the text length at the family's measured narration rate;
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

from . import qwen_quality

REVISION = "voicestudio.tts-section-quality.v1"
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
WIDE_CHARACTER_WEIGHT = 3      # one CJK/kana/hangul character takes about as long as three Latin
_WIDE = re.compile(r"[\u1100-\u11ff\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uf900-\ufaff]")
SPELLING_VARIANT_RATIO = 0.8   # "Elara" heard as "Alara" still counts as heard
# Families whose engine emits 12.5 acoustic tokens per second (the Qwen3-TTS 12 Hz codec), so a
# section's duration ceiling is also its token budget. Unbounded, mlx-audio's `generate_audio`
# default of 1,200 tokens let one failed Breeze clause run on for 96 s.
TOKENS_PER_SECOND = {"breeze-tts": 12.5}


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
    voiced = float((db > VOICED_DBFS).sum() * frame / rate)
    return {
        "duration_s": round(duration, 3),
        "dc_offset": round(float(pcm.mean()), 4),
        "voiced_s": round(voiced, 3),
        "voiced_ratio": round(voiced / duration, 3) if duration else 0.0,
    }


def _band(family: str, speed: float) -> tuple[float, float]:
    low, high = NARRATION_CHARS_PER_SECOND.get(family, DEFAULT_CHARS_PER_SECOND)
    return low * speed, high * speed


def _spoken_characters(text: str) -> int:
    """Latin-equivalent characters: the measured rates are Latin-script narration rates."""
    text = " ".join(text.split())
    return len(text) + (WIDE_CHARACTER_WEIGHT - 1) * len(_WIDE.findall(text))


def transcribe_back_applies(text: str) -> bool:
    """Whisper writes Chinese in either script, so a CJK section is checked by its audio only."""
    return not _WIDE.search(text)


def max_duration_s(text: str, family: str, speed: float = 1.0) -> float:
    """Longest a section of ``text`` may run: the slowest measured rate, slowed further, plus edges."""
    low, _ = _band(family, max(0.25, float(speed or 1.0)))
    return round(_spoken_characters(text) / (low * SLOW_FACTOR) + EDGE_ALLOWANCE_S, 2)


def max_tokens_for_section(text: str, family: str, engine_cap: int = 1200) -> int | None:
    """The section's token budget from its duration ceiling, or None for a family with no known rate."""
    rate = TOKENS_PER_SECOND.get(family)
    if rate is None:
        return None
    return max(1, min(engine_cap, math.ceil(max_duration_s(text, family) * rate)))


def heard_words(expected: str, heard: str) -> tuple[int, int]:
    """(words heard, words expected); numbers fold as in the Qwen gate, and a close spelling (a
    name Whisper spells differently) counts as heard."""
    want = Counter(qwen_quality._canonical_tokens(qwen_quality._tokens(expected)))
    got = Counter(qwen_quality._canonical_tokens(qwen_quality._tokens(heard)))
    total = sum(want.values())
    matched = sum((want & got).values())
    spare = list((got - want).elements())
    for word in (want - got).elements():
        for index, other in enumerate(spare):
            if SequenceMatcher(None, word, other, autojunk=False).ratio() >= SPELLING_VARIANT_RATIO:
                matched += 1
                del spare[index]
                break
    return matched, total


def check_section(text: str, family: str, metrics: dict, *, speed: float = 1.0,
                  heard: str | None = None) -> dict:
    """Evidence for one section; raises ``SectionQualityError`` when the audio is not the text."""
    chars = _spoken_characters(text)
    _, high = _band(family, max(0.25, float(speed or 1.0)))
    evidence = {
        **metrics,
        "characters": chars,
        "max_duration_s": max_duration_s(text, family, speed),
        "min_voiced_s": round(chars / (high * FAST_FACTOR), 2),
    }
    missing_allowed = None
    if heard is not None:
        matched, total = heard_words(text, heard)
        evidence["words_expected"] = total
        evidence["words_heard"] = matched
        evidence["word_overlap"] = round(matched / total, 3) if total else 1.0
        missing_allowed = max(MAX_MISSING_WORDS, math.floor(total * MAX_MISSING_SHARE))
    if metrics["voiced_s"] < evidence["min_voiced_s"]:
        code, why = "TTS_SECTION_SILENT", "came out (nearly) silent or as a DC drift"
    elif metrics["duration_s"] > evidence["max_duration_s"]:
        code, why = "TTS_SECTION_TOO_LONG", "ran far longer than its text (babble or humming)"
    elif missing_allowed is not None and total - matched > missing_allowed:
        code, why = "TTS_SECTION_TEXT_MISMATCH", "does not say its text (Whisper heard other words)"
    else:
        return {**evidence, "accepted": True}
    evidence.update(accepted=False, rejection_code=code)
    raise SectionQualityError(code, why, evidence)
