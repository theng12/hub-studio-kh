"""No tiny or unspeakable section reaches a voice model (2026-10-04).

The Dreamlands chapter in the TTS calibration ended with a "***" scene-break line. The splitter cut it
into its own 3-character section; Qwen3-TTS 0.6B wrote no audio for it on every Mac (Gateway jobs
0ce0b966 and 0cf938c6), the error blamed memory (on a 17 GB Mac with 7.5 GB free too), and the Hub
paused the model on mac-1305 and -1408 as if the model were broken.
"""
from __future__ import annotations

import pytest

from backend import generation, long_form_policy

RULE = {boundary: long_form_policy.production_boundary_pause_seconds(boundary)
        for boundary in ("sentence", "paragraph", "soft")}
# One sentence of 118 characters: with max_chars=120 nothing else fits beside it.
LONG = ("The key remained safely held in her pocket now, its heavy weight resting "
        "against her hip as she walked the garden path.")
assert len(LONG) <= 120


def _assert_no_tiny(chunks: list[str]) -> None:
    for chunk in chunks:
        assert generation._speakable(chunk), chunk
        assert not generation._tiny_section(chunk), chunk


def _chapter(tail: str) -> str:
    return "\n\n".join([f"{LONG} {LONG}", LONG, tail])


def test_a_chapter_ending_in_a_scene_break_never_sends_the_break_as_a_section():
    text = _chapter("***")
    chunks = generation._sentence_safe_text_chunks(text, max_chars=120)
    assert "***" not in " ".join(chunks)
    assert chunks == [LONG, LONG, LONG]
    _assert_no_tiny(chunks)


def test_a_scene_break_between_paragraphs_still_gets_the_paragraph_pause():
    text = f"{LONG}\n\n***\n\n{LONG}"
    chunks = generation._sentence_safe_text_chunks(text, max_chars=120)
    assert chunks == [LONG, LONG]
    assert generation._production_join_pauses_s(text, chunks, 1.0, RULE) == [RULE["paragraph"]]


@pytest.mark.parametrize("tail", ["No.", "Yes.", "...", "“Oh.”"])
def test_a_tiny_last_paragraph_joins_the_section_before_it(tail):
    text = _chapter(tail)
    chunks = generation._sentence_safe_text_chunks(text, max_chars=120)
    _assert_no_tiny(chunks)
    if generation._speakable(tail):
        assert chunks[-1] == f"{LONG} {tail}"
    else:
        assert chunks[-1] == LONG
    assert len(chunks[-1]) < 120 + generation._SECTION_MIN_SPOKEN_CHARACTERS


def test_merged_sections_keep_the_pause_rule():
    # A tiny paragraph merged backward: the boundary after it is still a paragraph.
    text = f"{LONG}\n\nYes.\n\n{LONG}"
    chunks = generation._sentence_safe_text_chunks(text, max_chars=120)
    assert chunks == [f"{LONG} Yes.", LONG]
    assert generation._production_join_pauses_s(text, chunks, 1.0, RULE) == [RULE["paragraph"]]
    # A tiny head merged forward: the merged section still ends a sentence, so a sentence pause.
    text = f"Oh. {LONG} {LONG}"
    chunks = generation._sentence_safe_text_chunks(text, max_chars=120)
    assert chunks == [f"Oh. {LONG}", LONG]
    assert generation._production_join_pauses_s(text, chunks, 1.0, RULE) == [RULE["sentence"]]


def test_a_tiny_tail_of_an_overlong_sentence_joins_its_neighbour():
    sentence = ("word " * 50).strip() + " end."       # 254 chars, no sentence end until the last
    chunks = generation._sentence_safe_text_chunks(sentence + " Go.", max_chars=120)
    _assert_no_tiny(chunks)
    assert " ".join(chunks).split() == (sentence + " Go.").split()


def test_khmer_end_mark_and_tiny_khmer_tail():
    sentence = "នៅពេលព្រះអាទិត្យរៀបអស្ដង្គត ភូមិស្រុកដ៏ស្ងប់ស្ងាត់មួយបានលិចលង់ក្នុងពន្លឺពណ៌មាស។"
    # ៕ alone closes a Khmer text: nothing to speak.
    chunks = generation._sentence_safe_text_chunks(f"{sentence}\n\n៕", max_chars=120)
    assert chunks == [sentence]
    # បាទ។ ("yes") is 4 code points: it joins the sentence before it.
    text = f"{sentence} {sentence}\n\nបាទ។"
    chunks = generation._sentence_safe_text_chunks(text, max_chars=120)
    _assert_no_tiny(chunks)
    assert chunks[-1].endswith("បាទ។") and chunks[-1] != "បាទ។"


def test_cjk_counts_a_character_as_three():
    sentence = "夜色笼罩着安静的山谷，古老的庄园沉浸在柔和的黑暗与深深的宁静之中。" * 3
    chunks = generation._sentence_safe_text_chunks(f"{sentence}\n\n好。", max_chars=60)
    _assert_no_tiny(chunks)
    assert chunks[-1].endswith("好。") and chunks[-1] != "好。"
    # Five CJK characters are a real phrase (13 spoken characters): its own section is fine.
    chunks = generation._sentence_safe_text_chunks(f"{sentence}\n\n我们走吧。", max_chars=60)
    assert chunks[-1] == "我们走吧。"


def test_a_request_with_nothing_to_speak_is_an_input_error():
    with pytest.raises(ValueError, match="nothing to speak"):
        generation._sentence_safe_text_chunks("***\n\n* * *", max_chars=120)
    assert generation._sentence_safe_text_chunks("   ", max_chars=120) == []


def _memory(monkeypatch, total, free):
    monkeypatch.setattr(generation, "_memory_snapshot",
                        lambda: {"total_gb": total, "available_gb": free})


def test_no_audio_for_a_tiny_section_is_the_text_not_the_model_or_memory(monkeypatch, tmp_path):
    _memory(monkeypatch, 8.6, 2.3)
    err = generation._no_wav_produced_error(tmp_path, "qwen3-tts", {"text": "***"})
    # A ValueError reaches the Hub as "RuntimeError: ValueError: ...", which it does not count
    # against the model (broker._classify_worker_failure).
    assert isinstance(err, ValueError)
    assert "3-character section" in str(err) and "'***'" in str(err)
    assert "memory —" not in str(err)


def test_memory_is_blamed_only_when_free_memory_was_short(monkeypatch, tmp_path):
    kwargs = {"text": LONG}
    _memory(monkeypatch, 17.2, 7.5)
    roomy = generation._no_wav_produced_error(tmp_path, "qwen3-tts", kwargs, need_gb=3.1)
    assert isinstance(roomy, RuntimeError)
    assert "too large for the available memory" not in str(roomy)
    assert "not a memory limit" in str(roomy)
    _memory(monkeypatch, 8.6, 1.9)
    short = generation._no_wav_produced_error(tmp_path, "qwen3-tts", kwargs, need_gb=3.1)
    assert "too large for the available memory" in str(short)
