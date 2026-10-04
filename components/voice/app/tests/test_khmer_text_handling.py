"""Khmer narration through the section splitter, the join pauses and the quality gate.

The Khmer test of 2026-10-04 (`_orchestra/audit-20261001/tts-khmer/REPORT.md`, findings 1, 2, 4):
។ was not a sentence end (every join after a Khmer sentence got the 0.15 s mid-sentence pause),
space-less Khmer was hard-cut inside a syllable, and the transcribe-back gate counted 0 words in a
Khmer section and always passed.
"""
from __future__ import annotations

import unicodedata

import pytest

from backend import generation, long_form_policy, tts_quality

# The first paragraph of that test's story (Gemini-written, 4 sentences ending with ។, phrase spaces).
SPACED = (
    "នៅពេលព្រះអាទិត្យរៀបអស្ដង្គត ភូមិស្រុកដ៏ស្ងប់ស្ងាត់មួយបានលិចលង់ក្នុងពន្លឺពណ៌មាស។ "
    "សុភាអង្គុយនៅលើរានហាលនៃផ្ទះឈើខ្ពស់ផុតពីដី ទាំងអារម្មណ៍ស្រស់ស្រាយ និងធូរស្រាល។ "
    "ខ្យល់ល្ងាចបក់មកប៉ះស្បែកត្រជាក់ៗ នាំយកក្លិនក្រអូបប្រហើរនៃដើមស្រូវខៀវស្រងាត់។ "
    "ទឹកទន្លេនៅជិតនោះហូរដោយស្ងៀមស្ងាត់ ឆ្លុះបញ្ចាំងពន្លឺចុងក្រោយនៃផ្ទៃមេឃពណ៌ក្រហមឆ្អិនឆ្អៅ។"
)
# The same paragraph as Khmer is often written: no spaces at all, not even after ។.
UNSPACED = SPACED.replace(" ", "")


def _no_split_syllable(chunks: list[str]) -> None:
    for before, after in zip(chunks, chunks[1:]):
        assert not unicodedata.category(after[0]).startswith("M"), (before[-5:], after[:5])
        assert unicodedata.combining(before[-1]) != 9, (before[-5:], after[:5])


@pytest.mark.parametrize("text", [SPACED, UNSPACED], ids=["spaced", "unspaced"])
def test_khmer_sections_end_at_a_sentence_and_never_inside_a_syllable(text):
    chunks = generation._sentence_safe_text_chunks(text, max_chars=120)
    assert len(chunks) > 1
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")      # nothing lost
    assert all(len(chunk) <= 120 for chunk in chunks)
    _no_split_syllable(chunks)
    # Every sentence here is under 120 characters, so every section ends at ។.
    assert all(chunk.endswith("។") for chunk in chunks), [c[-3:] for c in chunks]


def test_a_join_after_a_khmer_sentence_gets_the_sentence_pause():
    chunks = generation._sentence_safe_text_chunks(SPACED, max_chars=120)
    rule = {boundary: long_form_policy.production_boundary_pause_seconds(boundary)
            for boundary in ("sentence", "paragraph", "soft")}
    pauses = generation._production_join_pauses_s(SPACED, chunks, 1.0, rule)
    assert pauses == [rule["sentence"]] * (len(chunks) - 1)
    assert rule["sentence"] > rule["soft"]


def test_a_forced_cut_in_unbroken_khmer_lands_on_a_syllable_boundary():
    """No ។, no space, no zero-width space: the last resort still keeps each syllable whole."""
    sentence = UNSPACED.split("។")[0]                       # ~75 characters, no break at all
    long_unit = sentence * 4
    for max_chars in range(40, 90):
        pieces = generation._split_long_text_unit(long_unit, max_chars)
        assert "".join(pieces) == long_unit
        assert all(len(piece) <= max_chars for piece in pieces)
        _no_split_syllable(pieces)


def test_a_zero_width_space_is_a_word_break():
    words = ["ភូមិស្រុក", "ដ៏ស្ងប់ស្ងាត់", "មួយ", "បានលិចលង់"] * 8
    text = "​".join(words)
    pieces = generation._split_long_text_unit(text, 60)
    assert "".join(pieces) == text
    assert all(piece.endswith("​") for piece in pieces[:-1])


def test_english_cuts_are_unchanged():
    text = "The value was 3.5 and e.g. it stayed, more or less, the same " * 6
    pieces = generation._split_long_text_unit(text.strip(), 120)
    assert all(not piece.endswith(("3.", "e.")) for piece in pieces)
    assert " ".join(pieces) == " ".join(text.split())


def test_the_quality_gate_counts_khmer_syllables():
    matched, total = tts_quality.heard_words(SPACED, SPACED)
    assert total > 100 and matched == total
    # Whisper writes Khmer without the phrase spaces, or with zero-width spaces.
    assert tts_quality.heard_words(SPACED, UNSPACED) == (total, total)
    assert tts_quality.heard_words(SPACED, SPACED.replace(" ", "​")) == (total, total)


def _metrics(text):
    chars = len(text)
    return {"duration_s": chars / 10.0, "voiced_s": chars / 12.0, "longest_pause_s": 0.4}


def test_a_khmer_section_whisper_heard_passes_and_one_it_did_not_hear_fails():
    evidence = tts_quality.check_section(SPACED, "voxcpm-mlx", _metrics(SPACED), heard=UNSPACED)
    assert evidence["accepted"] and evidence["words_expected"] > 100
    assert evidence["word_overlap"] == 1.0

    first_half = SPACED[: len(SPACED) // 3]
    with pytest.raises(tts_quality.SectionQualityError) as failed:
        tts_quality.check_section(SPACED, "voxcpm-mlx", _metrics(SPACED), heard=first_half)
    assert failed.value.code == "TTS_SECTION_TEXT_MISMATCH"

    # Heard in Latin letters (Whisper decoding Khmer as English) is not Khmer at all.
    with pytest.raises(tts_quality.SectionQualityError):
        tts_quality.check_section(SPACED, "voxcpm-mlx", _metrics(SPACED),
                                  heard="now pel preah atit reap asdangkut")


def test_whisper_reads_a_khmer_section_as_khmer():
    assert tts_quality.script_language(SPACED) == "km"
    assert tts_quality.script_language("An English sentence.") is None
