"""Breeze reads one sentence per section (owner approved the A/B by ear, 2026-10-07).

Breeze pauses at a sentence break inside a section and does not come back; the fleet's rejection rate
climbs with the sentences in a section (1: 6.2%, 4: 25.4%). Every other family keeps packing sentences
up to its character budget.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import soundfile as sf

from backend import catalog, generation, long_form_policy

BREEZE = "mlx-community/Breeze-TTS-2-mlx-4bit"
RULE = long_form_policy.default_boundary_pauses()
S1 = "The weaver ran the tip of her finger along the silver thread, feeling its cool weight."
S2 = "Outside, the clouds drifted slowly past the tower window like sleeping sheep."
S3 = "She smiled and reached for the shuttle again, ready for the next quiet row."
TEXT = f"{S1} {S2}\n\n{S3}"


def _breeze(text: str) -> list[str]:
    return generation._internal_mlx_text_chunks("breeze-tts", BREEZE, text)


def test_only_breeze_reads_one_sentence_per_section():
    for family in catalog.FAMILIES:
        policy = long_form_policy.policy_for(family, f"{family}/repo")
        assert policy["max_sentences_per_section"] == (1 if family == "breeze-tts" else None), family
    assert _breeze(TEXT) == [S1, S2, S3]
    # Every other family is unchanged: sentences pack up to the 250-character budget.
    assert generation._internal_mlx_text_chunks("voxcpm-mlx", "mlx-community/VoxCPM2-4bit", TEXT) \
        == [f"{S1} {S2}", S3]
    assert generation._sentence_safe_text_chunks(TEXT, max_chars=250) == [f"{S1} {S2}", S3]


def test_a_short_sentence_joins_a_neighbour_in_its_own_paragraph():
    # Forward when it opens a paragraph, so the paragraph pause before it stays a paragraph pause.
    text = f"{S1}\n\nLet's get cosy. {S2} {S3}"
    chunks = _breeze(text)
    assert chunks == [S1, f"Let's get cosy. {S2}", S3]
    assert generation._production_join_pauses_s(text, chunks, 1.0, RULE) == [1.5, 1.0]
    # Back when it closes one.
    text = f"{S1} Everything is still.\n\n{S3}"
    assert _breeze(text) == [f"{S1} Everything is still.", S3]
    # 30 spoken characters is a section of its own; 29 is not.
    own = "Everything is in its own place"[:29] + "."
    assert len(own) == 30 and _breeze(f"{S1} {own} {S3}") == [S1, own, S3]
    short = own[:28] + "."
    assert _breeze(f"{S1} {short} {S3}") == [f"{S1} {short}", S3]


def test_a_paragraph_that_is_one_short_sentence_still_joins_a_neighbour():
    assert _breeze(f"{S1}\n\nDrip.\n\n{S3}") == [f"{S1} Drip.", S3]
    assert _breeze("Drip.") == ["Drip."], "a request of one short sentence is still spoken"


def test_a_long_sentence_still_splits_at_the_250_character_cap():
    long = ("The lantern keeper climbed the spiral stair, past the window where the moths gathered, "
            "past the shelf of old logbooks, past the brass bell that nobody rang any more, and on "
            "into the lamp room, where the great lens waited for its evening light to be kindled again, "
            "slowly and carefully, one small flame at a time.")
    assert len(long) > 280
    chunks = _breeze(f"{S1} {long} {S2}")
    assert chunks[0] == S1 and chunks[-1] == S2
    assert len(chunks) == 4 and all(len(c) <= 250 for c in chunks)
    assert " ".join(chunks[1:3]) == long
    pauses = generation._production_join_pauses_s(f"{S1} {long} {S2}", chunks, 1.0, RULE)
    assert pauses == [1.0, RULE["soft"], 1.0]


def test_joins_use_the_sentence_and_paragraph_pauses_the_ab_was_built_with():
    chunks = _breeze(TEXT)
    assert generation._production_join_pauses_s(TEXT, chunks, 1.0, RULE) == [1.0, 1.5]
    # A request's own pauses still win (Story asks 1.0 / 1.5 explicitly).
    asked = {"sentence_pause_sec": 0.8, "paragraph_pause_sec": 2.0, "speed": 1.0}
    assert generation._request_join_pauses_s(TEXT, chunks, asked) == [0.8, 2.0]


def test_the_section_timeline_has_one_row_per_sentence():
    chunks = _breeze(TEXT)
    job = SimpleNamespace(section_quality=None)
    generation._record_section_timeline(job, TEXT, chunks, [(0.0, 7.0), (8.0, 14.0), (15.5, 21.0)])
    rows = job.section_quality["timeline"]
    assert [TEXT[r["text_start"]:r["text_end"]] for r in rows] == [S1, S2, S3]
    assert [(r["start_s"], r["end_s"]) for r in rows] == [(0.0, 7.0), (8.0, 14.0), (15.5, 21.0)]


def test_a_story_request_of_1500_characters_stays_well_inside_the_evidence_caps():
    text = "\n\n".join([f"{S1} {S2} {S3}"] * 6)[:1500]
    chunks = _breeze(text)
    assert " ".join(chunks).split() == text.split()
    assert 15 <= len(chunks) <= 20
    # The Hub keeps 500 section records and 500 timeline rows (broker._record_worker_section_quality).
    assert len(chunks) * 4 < 500


# ── the section validator: Whisper reads later sections in the language it found ────────────────

RATE = 24_000


def _speech(seconds: float) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return (0.14 * np.sin(2 * np.pi * 180 * t) * np.clip(np.sin(2 * np.pi * 4 * t), 0, None)
            ).astype(np.float32)


def test_later_sections_reuse_the_language_whisper_detected_on_an_accepted_one(tmp_path, monkeypatch):
    from backend import transcription
    from backend.native_executor import NativeRunOutcome

    words = ["first section here", "second section here", "third section here"]
    asked: list = []
    # Section 2's first take is rejected.
    heard = iter([words[0], "Thank you for watching.", words[1], words[2]])

    def transcribe(_path, *, model_repo=None, language=None):
        asked.append(language)
        return {"text": next(heard), "language": language or "en"}

    class Renders:
        calls = 0

        def run(self, payload, _cancel, *, on_started):
            Renders.calls += 1
            on_started(f"run-{Renders.calls}", 1)
            sf.write(payload["output_path"], _speech(2.0), RATE, subtype="PCM_16")
            return NativeRunOutcome("done", f"run-{Renders.calls}", 1, value={})

    model = SimpleNamespace(family="breeze-tts", repo="local/voice")
    monkeypatch.setattr(generation.catalog, "get_model", lambda _repo: model)
    monkeypatch.setattr(generation, "_normalized_speech_text", lambda *_a: " ".join(words))
    monkeypatch.setattr(generation, "_internal_mlx_text_chunks", lambda *_a, **_k: list(words))
    monkeypatch.setattr(generation, "_memory_snapshot", lambda: {"available_gb": 64.0})
    monkeypatch.setattr(generation, "_join_long_form_wavs", lambda paths, out, *_a, **_k:
                        [(float(i), float(i) + 1) for i, _ in enumerate(paths)])
    monkeypatch.setattr(transcription.manager, "transcribe_locked", transcribe)
    monkeypatch.setattr(transcription.manager, "release_memory_locked", lambda *_a: {})
    manager = generation.GenerationManager.__new__(generation.GenerationManager)
    manager._native_executor = Renders()
    manager._native_loaded_model_keys = []
    manager._persist = lambda **_k: None
    job = generation.GenerationJob("lang", "txt2speech",
                                   {"repo": "local/voice", "text": " ".join(words), "seed": 5})

    manager._dispatch_txt2speech(job, tmp_path / "out.wav")

    # Detected on section 1, then reused; a re-render detects again, so a wrong guess costs one take.
    assert asked == [None, "en", None, "en"]
    assert [r["accepted"] for r in job.section_quality["sections"]] == [True, False, True, True]
