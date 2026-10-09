"""A TTS section is accepted only when its audio says its text (backend/tts_quality.py).

Fixtures are a few seconds of synthetic audio: a syllable-modulated tone stands in for speech, and
the Breeze failures measured on Dreamlands (2026-10-03) are reproduced in miniature - silence, a DC
drift with nothing above it, voiced babble far longer than the text, and speech that is not the text.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from backend import generation, tts_quality
from backend.native_executor import NativeRunOutcome

RATE = 24_000
TEXT = "The weaver felt small but significant, a single thread in the vast loom of the night."


def _speech(seconds: float) -> np.ndarray:
    """A 180 Hz voice at about -20 dBFS, in 4 Hz syllables with short gaps between them."""
    t = np.arange(int(RATE * seconds)) / RATE
    syllables = np.clip(np.sin(2 * np.pi * 4 * t), 0, None)
    return (0.14 * np.sin(2 * np.pi * 180 * t) * syllables).astype(np.float32)


def _write(path, pcm) -> str:
    sf.write(str(path), pcm, RATE, subtype="PCM_16")
    return str(path)


def _verdict(path, *, heard=None, text=TEXT, family="breeze-tts"):
    try:
        return tts_quality.check_section(text, family, tts_quality.audio_metrics(path), heard=heard)
    except tts_quality.SectionQualityError as exc:
        return exc.evidence


def test_a_section_that_reads_its_text_at_a_normal_pace_is_accepted(tmp_path):
    evidence = _verdict(_write(tmp_path / "ok.wav", _speech(8.0)), heard=TEXT)
    assert evidence["accepted"] is True
    assert evidence["word_overlap"] == 1.0


@pytest.mark.parametrize("name, pcm", [
    ("silence", np.zeros(RATE * 8, dtype=np.float32)),
    # The Ch03 failure: a slow drift from +0.54 to +0.81 with nothing audible above it.
    ("dc-drift", (np.linspace(0.54, 0.81, RATE * 8)
                  + np.random.default_rng(1).normal(0, 1e-4, RATE * 8)).astype(np.float32)),
    ("two-seconds-of-speech-then-silence",
     np.concatenate([_speech(1.0), np.zeros(RATE * 8, dtype=np.float32)])),
])
def test_silence_and_dc_drift_are_rejected_without_any_model(tmp_path, name, pcm):
    evidence = _verdict(_write(tmp_path / f"{name}.wav", pcm))
    assert evidence["rejection_code"] == "TTS_SECTION_SILENT", evidence


def test_voiced_babble_far_longer_than_its_text_is_rejected(tmp_path):
    # The Ch11 / EP0002 Ch12 failure: ~50 s of humming for one clause.
    evidence = _verdict(_write(tmp_path / "babble.wav", _speech(45.0)))
    assert evidence["rejection_code"] == "TTS_SECTION_TOO_LONG"
    assert evidence["duration_s"] > evidence["max_duration_s"]


def test_speech_that_is_not_the_text_is_rejected_when_whisper_is_available(tmp_path):
    path = _write(tmp_path / "other.wav", _speech(8.0))
    assert _verdict(path, heard="Piano playing. Thank you.")["rejection_code"] == (
        "TTS_SECTION_TEXT_MISMATCH")
    # A skipped clause is a mismatch too (Ch07: one and a half sentences of four were spoken).
    assert _verdict(path, heard="The weaver felt small but significant.")["rejection_code"] == (
        "TTS_SECTION_TEXT_MISMATCH")


def test_a_long_silence_between_words_is_rejected_without_any_model(tmp_path):
    # EP0003 Ch09 part 5: 35 s at -66 dB inside a 67 s section; long text and the speech around it
    # kept the voiced time and the duration inside their bounds. Here in miniature, over a DC offset.
    hush = (0.002 + np.random.default_rng(2).normal(0, 3e-4, RATE * 6)).astype(np.float32)
    evidence = _verdict(_write(tmp_path / "gap.wav",
                               np.concatenate([_speech(4.0), hush, _speech(4.0)])), heard=TEXT)
    assert evidence["rejection_code"] == "TTS_SECTION_INTERNAL_SILENCE", evidence
    assert evidence["longest_pause_s"] > evidence["max_pause_s"] == 4.5
    # Breeze's own sentence pause (up to ~3.4 s measured) and the engine's edge silence are speech.
    natural = np.concatenate([np.zeros(RATE), _speech(4.0), np.zeros(RATE * 3), _speech(4.0),
                              np.zeros(RATE * 2)]).astype(np.float32)
    assert _verdict(_write(tmp_path / "pause.wav", natural), heard=TEXT)["accepted"] is True


def test_the_pause_allowed_follows_the_text_and_the_speed():
    # The uniform join rule (owner 2026-10-04): 1.0 s after a sentence, 1.5 s at a paragraph.
    assert tts_quality.max_pause_s("One sentence. Another one.") == 4.5
    assert tts_quality.max_pause_s("A paragraph ends.\n\nThe next begins.") == 5.0
    assert tts_quality.max_pause_s("One sentence. Another one.", speed=0.5) == 9.0


def test_a_dropped_clause_is_a_mismatch_even_inside_the_missing_share():
    # EP0003 Ch09 part 5: Whisper heard 69 of 76 words and 7 may be missing, so it passed; the
    # missing words were one clause, "Silas drew the soft crest of the ridge", lost to the silence.
    text = ("The cartographer took out his field book and opened it to a clean spread of pages. "
            "He records the gentle contour of the ridge in quiet charcoal strokes. The charcoal "
            "moved across the paper in long, sweeping curves that matched the natural rise and "
            "fall of the land. Silas drew the soft crest of the ridge, the precise placement of the "
            "apiary shelter, and the winding course of the footpath that climbed from the orchard "
            "below.")
    heard = text.replace("drew the soft crest of the ridge, ", "")
    metrics = {"duration_s": 40.0, "dc_offset": 0.0, "voiced_s": 22.5, "voiced_ratio": 0.56}
    with pytest.raises(tts_quality.SectionQualityError) as failed:
        tts_quality.check_section(text, "breeze-tts", metrics, heard=heard)
    assert failed.value.code == "TTS_SECTION_TEXT_MISMATCH"
    assert failed.value.evidence["words_heard"] == 69 and failed.value.evidence["word_overlap"] > 0.9
    assert failed.value.evidence["longest_missing_run"] == 7
    # The same number of words lost one here and one there is Whisper, not a dropped clause.
    scattered = text.replace("The cartographer", "cartographer").replace("in quiet", "quiet")
    assert tts_quality.longest_missing_run(text, scattered) <= 1


def test_a_misspelt_name_and_a_word_or_two_lost_are_not_a_mismatch():
    text = "Elara felt a wave of gratitude for her reliable companion, the dark oak frame."
    matched, total = tts_quality.heard_words(text, "Alara felt a wave of gratitude for her "
                                                   "reliable companion, the oak frame.")
    assert total - matched == 1          # "Alara" counts, "dark" was lost
    # Two words may be lost on any section; beyond that, at most 15% of its words.
    metrics = {"duration_s": 7.0, "dc_offset": 0.0, "voiced_s": 4.5, "voiced_ratio": 0.64}
    tts_quality.check_section(text, "breeze-tts", metrics, heard="Alara felt a wave of "
                              "gratitude for her companion, the oak frame.")
    with pytest.raises(tts_quality.SectionQualityError):
        tts_quality.check_section(text, "breeze-tts", metrics,
                                  heard="Alara felt a wave for her companion.")


def test_numbers_are_compared_the_way_the_qwen_gate_compares_them():
    assert tts_quality.heard_words("She counted twenty four threads.",
                                   "She counted 24 threads.") == (4, 4)


def test_decades_and_split_compounds_are_the_words_the_text_says():
    """EP0028 (night-atlas, 2026-10-09): Section 2 of 5 failed 12 renders on 3 Macs, each "heard 14/17,
    missing run 2" - 0.6.161 reproduces it from this transcript. Whisper wrote the decade as digits
    and the compound as two words; the voice read the sentence right."""
    text = ("This tall storage vessel stood beside washhouses and kitchen doors across northern "
            "England during the eighteen-eighties.")
    heard = ("This tall storage vessel stood beside wash houses and kitchen doors across northern "
             "England during the 1880s.")
    metrics = {"duration_s": 8.72, "dc_offset": 0.0, "voiced_s": 7.26, "voiced_ratio": 0.833}
    evidence = tts_quality.check_section(text, "breeze-tts", metrics, heard=heard)
    assert evidence["accepted"] and evidence["words_heard"] == evidence["words_expected"] == 16
    # The other way round, and the other decade/year shapes.
    assert tts_quality.heard_words("beside the wash houses", "beside the washhouses") == (4, 4)
    assert tts_quality.heard_words("It was the eighties.", "It was the 80s.") == (4, 4)
    assert tts_quality.heard_words("In nineteen fifty-two it rained.", "In 1952 it rained.") == (4, 4)
    # Spaces are removed only to pair a missing word with spare heard ones, never to excuse others.
    assert tts_quality.heard_words("the wash house", "the washhouse door") == (3, 3)
    assert tts_quality.heard_words("the washhouse", "the wash") == (1, 2)
    # A genuinely wrong reading of the same sentence still fails.
    wrong = ("This tall storage vessel stood beside the river and garden walls across southern "
             "Spain during the 1920s.")
    with pytest.raises(tts_quality.SectionQualityError) as failed:
        tts_quality.check_section(text, "breeze-tts", metrics, heard=wrong)
    assert failed.value.code == "TTS_SECTION_TEXT_MISMATCH"


def test_breeze_section_token_budget_follows_the_text():
    short = tts_quality.max_tokens_for_section("One quiet clause of about forty chars.", "breeze-tts")
    long = tts_quality.max_tokens_for_section("word " * 100, "breeze-tts")
    assert short < 250 < long <= 1200          # 1,200 was the 96 s ceiling every section had
    # The budget is the duration ceiling the section is then checked against.
    assert short == int(np.ceil(tts_quality.max_duration_s(
        "One quiet clause of about forty chars.", "breeze-tts") * 12.5))
    assert tts_quality.max_tokens_for_section("text", "voxcpm-mlx") is None


def test_wide_scripts_get_time_for_their_characters_and_skip_the_transcript_check():
    chinese = "织布的女人在夜里安静地工作，月光照在白色的丝线上。"
    assert tts_quality.max_duration_s(chinese, "breeze-tts") > len(chinese) / 4.0
    assert tts_quality.transcribe_back_applies(chinese) is False
    assert tts_quality.transcribe_back_applies(TEXT) is True


# ── the parent loop: retry a failed section with a new seed, then fail by name ──────────────────

def _manager_with(executor, monkeypatch, family="breeze-tts", chunks=("first section text",)):
    model = SimpleNamespace(family=family, repo="local/voice")
    monkeypatch.setattr(generation.catalog, "get_model", lambda _repo: model)
    monkeypatch.setattr(generation, "_normalized_speech_text", lambda *_args: " ".join(chunks))
    monkeypatch.setattr(generation, "_internal_mlx_text_chunks",
                        lambda *_args, **_kwargs: list(chunks))
    manager = generation.GenerationManager.__new__(generation.GenerationManager)
    manager._native_executor = executor
    manager._native_loaded_model_keys = []
    manager._persist = lambda **_kwargs: None
    return manager


class _Renders:
    """A native executor whose sections come out as the given sequence of fixtures."""

    def __init__(self, *fixtures):
        self.fixtures = list(fixtures)
        self.calls: list[dict] = []

    def run(self, payload, _cancel_event, *, on_started):
        self.calls.append(payload)
        on_started(f"run-{len(self.calls)}", 1)
        sf.write(payload["output_path"], self.fixtures.pop(0), RATE, subtype="PCM_16")
        return NativeRunOutcome("done", f"run-{len(self.calls)}", 1, value={})


def _no_whisper(monkeypatch):
    from backend import transcription

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("Transcription model is not downloaded.")

    monkeypatch.setattr(generation, "_memory_snapshot", lambda: {"available_gb": 64.0})
    monkeypatch.setattr(transcription.manager, "transcribe_locked", unavailable)


def test_a_failed_section_is_rendered_again_with_a_new_seed(tmp_path, monkeypatch):
    _no_whisper(monkeypatch)
    renders = _Renders(np.zeros(RATE * 3, dtype=np.float32), _speech(2.0))
    manager = _manager_with(renders, monkeypatch)
    job = generation.GenerationJob("retry", "txt2speech",
                                   {"repo": "local/voice", "text": "first section text", "seed": 5})

    manager._dispatch_txt2speech(job, tmp_path / "out.wav")

    assert (tmp_path / "out.wav").exists()
    seeds = [call["params"]["_qwen_attempt_seed"] for call in renders.calls]
    assert seeds[0] == 5 and seeds[1] != 5
    assert renders.calls[1]["params"]["_native_continue_rng"] is False
    assert renders.calls[1]["params"]["_memory_admitted"] is True, "a re-render is the same admitted job"
    records = job.section_quality["sections"]
    assert [(r["section"], r["attempt"], r["accepted"]) for r in records] == [
        (1, 1, False), (1, 2, True)]
    assert records[0]["rejection_code"] == "TTS_SECTION_SILENT"
    # No Whisper on this Mac: skipped by name, and the audio checks still ran.
    assert {r["transcribe_back"] for r in records} == {"unavailable"}


def test_a_section_that_never_passes_fails_the_job_naming_the_section(tmp_path, monkeypatch):
    _no_whisper(monkeypatch)
    silent = np.zeros(RATE * 3, dtype=np.float32)
    renders = _Renders(_speech(2.0), *[silent] * (tts_quality.SECTION_RETRIES + 1))
    manager = _manager_with(renders, monkeypatch, chunks=("first section text", "second one"))
    job = generation.GenerationJob("fails", "txt2speech",
                                   {"repo": "local/voice", "text": "x", "seed": 5})

    with pytest.raises(tts_quality.SectionQualityError) as raised:
        manager._dispatch_txt2speech(job, tmp_path / "out.wav")

    assert raised.value.code == "TTS_SECTION_SILENT"
    assert str(raised.value).startswith("Section 2 of 2 came out (nearly) silent")
    # The measured values of every attempt ride in the message, the field a failed job carries.
    assert "(attempt 1: voiced 0.0 s < " in str(raised.value)
    assert "; attempt 4: voiced 0.0 s < " in str(raised.value)
    assert len(renders.calls) == 1 + tts_quality.SECTION_RETRIES + 1
    assert not (tmp_path / "out.wav").exists()


def test_whisper_hearing_other_words_rejects_the_section(tmp_path, monkeypatch):
    from backend import transcription

    heard = iter(["Thank you for watching.", "first section text"])
    monkeypatch.setattr(generation, "_memory_snapshot", lambda: {"available_gb": 64.0})
    monkeypatch.setattr(transcription.manager, "transcribe_locked",
                        lambda *_args, **_kwargs: {"text": next(heard)})
    monkeypatch.setattr(transcription.manager, "release_memory_locked", lambda *_args: {})
    renders = _Renders(_speech(2.0), _speech(2.0))
    manager = _manager_with(renders, monkeypatch)
    job = generation.GenerationJob("asr", "txt2speech",
                                   {"repo": "local/voice", "text": "first section text", "seed": 5})

    manager._dispatch_txt2speech(job, tmp_path / "out.wav")

    records = job.section_quality["sections"]
    assert [r.get("rejection_code") for r in records] == ["TTS_SECTION_TEXT_MISMATCH", None]
    assert {r["transcribe_back"] for r in records} == {"whisper"}


def test_a_text_mismatch_failure_states_each_attempts_overlap_and_missing_run(tmp_path, monkeypatch):
    from backend import transcription

    monkeypatch.setattr(generation, "_memory_snapshot", lambda: {"available_gb": 64.0})
    monkeypatch.setattr(transcription.manager, "transcribe_locked",
                        lambda *_args, **_kwargs: {"text": "Thank you for watching."})
    monkeypatch.setattr(transcription.manager, "release_memory_locked", lambda *_args: {})
    renders = _Renders(*[_speech(2.0)] * (tts_quality.SECTION_RETRIES + 1))
    manager = _manager_with(renders, monkeypatch)
    job = generation.GenerationJob("asr-fails", "txt2speech",
                                   {"repo": "local/voice", "text": "first section text", "seed": 5})

    with pytest.raises(tts_quality.SectionQualityError) as raised:
        manager._dispatch_txt2speech(job, tmp_path / "out.wav")

    message = f"SectionQualityError: {raised.value}"
    assert ("so the audio was not delivered (attempt 1: heard 0/3, overlap 0.0, missing run 3; "
            "attempt 2: heard 0/3, overlap 0.0, missing run 3; attempt 3: ") in message
    assert message.endswith("missing run 3). Try again, or reword that part of the text.")
    assert len(message) <= 500, "the Hub keeps 500 characters of a worker's error"


def test_whisper_is_not_loaded_beside_the_model_when_memory_is_short(tmp_path, monkeypatch):
    from backend import transcription

    monkeypatch.setattr(generation, "_memory_snapshot", lambda: {"available_gb": 1.0})
    monkeypatch.setattr(transcription.manager, "transcribe_locked",
                        lambda *_a, **_k: pytest.fail("Whisper must not load on a full Mac"))
    renders = _Renders(_speech(2.0))
    manager = _manager_with(renders, monkeypatch)
    job = generation.GenerationJob("8gb", "txt2speech",
                                   {"repo": "local/voice", "text": "first section text", "seed": 5})

    manager._dispatch_txt2speech(job, tmp_path / "out.wav")

    assert job.section_quality["sections"][0]["transcribe_back"] == "memory"


def test_a_short_section_whisper_heard_most_of_passes_but_a_dropped_clause_still_fails():
    # night-atlas 2026-10-05: a 27-word section, same words on 3 Macs x 4 seeds, heard 23-24/27
    # with the misses scattered (run <= 2). The 10% share allowed 2 and failed the part.
    text = ("The old lighthouse keeper climbed the winding stairs each evening, carrying a small "
            "brass lamp and a worn notebook full of the names of all passing ships.")
    metrics = {"duration_s": 14.0, "dc_offset": 0.0, "voiced_s": 9.0, "voiced_ratio": 0.64}
    heard = ("old lighthouse keeper climbed winding stairs each evening, carrying a small "
             "brass lamp and a worn notebook full of the names of passing ships.")
    for lost, words in ((heard, 24), (heard.replace(" a worn", " worn"), 23)):
        evidence = tts_quality.check_section(text, "breeze-tts", metrics, heard=lost)
        assert (evidence["words_heard"], evidence["words_expected"]) == (words, 27), evidence
        assert evidence["accepted"] is True and evidence["longest_missing_run"] <= 2
    # Four words lost as one clause is still a dropped clause.
    clause = text.replace("climbed the winding stairs ", "")
    with pytest.raises(tts_quality.SectionQualityError) as failed:
        tts_quality.check_section(text, "breeze-tts", metrics, heard=clause)
    assert failed.value.evidence["longest_missing_run"] == 4
