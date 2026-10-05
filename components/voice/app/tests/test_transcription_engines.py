from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import transcription


WHISPER_REPO = "mlx-community/whisper-large-v3-turbo"
MOONSHINE_REPO = "moonshine-ai/moonshine-base"
NEMOTRON_REPO = "mlx-community/nemotron-3.5-asr-streaming-0.6b-8bit"


class _Sampler:
    def __init__(self, publish):
        self.publish = publish

    def start(self):
        return self

    def finish(self, **kwargs):
        return {"schema": "voicestudio.resource-telemetry", "outcome": kwargs}


class _RecordingModel:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def generate(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


def _manager(monkeypatch, tmp_path: Path, result, *, duration: float = 3.0):
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"audio")
    model = _RecordingModel(result)
    manager = transcription.TranscriptionManager()
    monkeypatch.setattr(transcription.cache, "cache_state", lambda _repo: "cached")
    monkeypatch.setattr(transcription, "_audio_duration_seconds", lambda _path: duration)
    monkeypatch.setattr(manager, "_get_model", lambda _repo: model)
    monkeypatch.setattr(manager, "_evict_loaded_model", lambda _reason: {})
    monkeypatch.setattr(transcription, "_release_device_memory", lambda _device: None)
    monkeypatch.setattr(transcription.resource_telemetry, "JobResourceSampler", _Sampler)
    return manager, model, audio


def test_whisper_alone_receives_the_approved_decode_policy(monkeypatch, tmp_path) -> None:
    result = SimpleNamespace(
        text="The knight slept.",
        language="en",
        segments=[{"start": 0.0, "end": 3.0, "text": "The knight slept."}],
    )
    manager, model, audio = _manager(monkeypatch, tmp_path, result)

    manager.transcribe(str(audio), model_repo=WHISPER_REPO, language="en")

    assert model.calls[0][1] == {
        "language": "en",
        "word_timestamps": False,
        "return_timestamps": True,
        "condition_on_previous_text": False,
    }


def test_moonshine_rejects_word_timestamps_before_inference(monkeypatch, tmp_path) -> None:
    manager, model, audio = _manager(
        monkeypatch,
        tmp_path,
        SimpleNamespace(text="Short transcript."),
    )

    with pytest.raises(ValueError, match="does not support word timestamps"):
        manager.transcribe(
            str(audio), model_repo=MOONSHINE_REPO, word_timestamps=True
        )

    assert model.calls == []


def test_nemotron_receives_language_and_bounded_chunking(monkeypatch, tmp_path) -> None:
    manager, model, audio = _manager(
        monkeypatch,
        tmp_path,
        SimpleNamespace(text="Hello.", sentences=[]),
    )

    manager.transcribe(
        str(audio),
        model_repo=NEMOTRON_REPO,
        language="en",
        word_timestamps=True,
    )

    assert model.calls[0][1] == {"language": "en", "chunk_duration": 30.0}


def test_only_whisper_loading_attaches_a_processor(monkeypatch, tmp_path) -> None:
    from mlx_audio.stt import utils

    manager = transcription.TranscriptionManager()
    attached = []
    monkeypatch.setattr(manager, "_snapshot_path", lambda _repo: tmp_path)
    monkeypatch.setattr(utils, "load_model", lambda _path: SimpleNamespace(_processor=None))
    monkeypatch.setattr(manager, "_attach_processor", lambda _model, repo: attached.append(repo))
    monkeypatch.setattr(transcription, "_release_device_memory", lambda _device: None)

    manager._get_model(MOONSHINE_REPO)
    manager._get_model(NEMOTRON_REPO)
    manager._get_model(WHISPER_REPO)

    assert attached == [WHISPER_REPO]


@pytest.mark.parametrize("repo", [WHISPER_REPO, MOONSHINE_REPO, NEMOTRON_REPO])
def test_loaded_model_key_uses_generic_transcription_family(repo: str) -> None:
    manager = transcription.TranscriptionManager(_model=object(), _model_repo=repo)

    assert manager.loaded_model_key() == (repo, "transcription-stt")


def test_moonshine_text_becomes_one_full_duration_segment(monkeypatch, tmp_path) -> None:
    manager, _model, audio = _manager(
        monkeypatch,
        tmp_path,
        SimpleNamespace(text="  Short transcript.  "),
        duration=4.25,
    )

    result = manager.transcribe(str(audio), model_repo=MOONSHINE_REPO)

    assert result["text"] == "Short transcript."
    assert result["segments"] == [
        {"id": 0, "start": 0.0, "end": 4.25, "text": "Short transcript."}
    ]
    assert "00:00:00,000 --> 00:00:04,250" in result["srt"]


def test_empty_moonshine_text_has_no_subtitle_cues(monkeypatch, tmp_path) -> None:
    manager, _model, audio = _manager(
        monkeypatch,
        tmp_path,
        SimpleNamespace(text="  "),
    )

    result = manager.transcribe(str(audio), model_repo=MOONSHINE_REPO)

    assert result["segments"] == []
    assert result["srt"] == ""
    assert result["vtt"] == "WEBVTT\n"


def test_nemotron_sentences_and_requested_words_are_preserved(monkeypatch, tmp_path) -> None:
    result = SimpleNamespace(
        text="Hello world.",
        sentences=[
            SimpleNamespace(
                text="Hello world.",
                start=0.1,
                end=1.4,
                tokens=[
                    SimpleNamespace(text="Hello", start=0.1, end=0.5),
                    SimpleNamespace(text=" world.", start=0.55, end=1.4),
                ],
            )
        ],
    )
    manager, _model, audio = _manager(monkeypatch, tmp_path, result)

    payload = manager.transcribe(
        str(audio), model_repo=NEMOTRON_REPO, word_timestamps=True
    )

    assert payload["segments"] == [
        {
            "id": 0,
            "start": 0.1,
            "end": 1.4,
            "text": "Hello world.",
            "words": [
                {"word": "Hello", "start": 0.1, "end": 0.5},
                {"word": "world.", "start": 0.55, "end": 1.4},
            ],
        }
    ]


def test_nemotron_omits_words_when_not_requested(monkeypatch, tmp_path) -> None:
    sentence = SimpleNamespace(
        text="Hello.",
        start=0.1,
        end=0.9,
        tokens=[SimpleNamespace(text="Hello.", start=0.1, end=0.9)],
    )
    manager, _model, audio = _manager(
        monkeypatch,
        tmp_path,
        SimpleNamespace(text="Hello.", sentences=[sentence]),
    )

    payload = manager.transcribe(str(audio), model_repo=NEMOTRON_REPO)

    assert "words" not in payload["segments"][0]


# ─── A10: the tokenizer source comes from the model's own published data ───

def test_every_installed_whisper_row_resolves_its_tokenizer_from_its_own_manifest(
    monkeypatch,
) -> None:
    """A new Whisper repo is never handed another model's vocabulary.

    The manifest a Whisper package installs from declares its processor
    companion (`source.layout.companions`); this asserts the worker reads that
    value rather than a per-repo table with a silent default. A repo the
    registry knows but no manifest declares resolves to None, which
    `_attach_processor` turns into a named refusal instead of guessing.
    """
    manifests = Path(__file__).resolve().parents[4] / "model-manifests"
    monkeypatch.setenv("STUDIOHUB_MODEL_MANIFESTS", str(manifests))

    declared = [
        model for model in transcription.TRANSCRIPTION_MODELS
        if model.engine == "whisper" and transcription.cache.manifest_for(model.repo)
    ]
    assert declared, "no Whisper row has a packaged manifest; this test would be vacuous"
    for model in declared:
        expected = [
            companion["repo"]
            for companion in transcription.cache.manifest_for(model.repo)["source"]["layout"]["companions"]
        ]
        resolved = transcription.whisper_processor_repo(model.repo)
        assert resolved in expected, model.repo
        assert resolved.startswith("openai/whisper"), model.repo

    # Nothing declares a companion for this id, so nothing is substituted for it.
    assert transcription.whisper_processor_repo("example/not-a-real-whisper") is None


def test_an_unknown_engine_refuses_by_name(monkeypatch, tmp_path: Path) -> None:
    """A new engine must not fall into the previous engine's call signature."""
    spec = transcription.TranscriptionModel(
        repo="example/new-asr",
        label="New ASR",
        size_gb=0.1,
        note="internal",
        engine="new-engine",
    )
    monkeypatch.setitem(transcription._BY_REPO, spec.repo, spec)
    manager, model, audio = _manager(monkeypatch, tmp_path, SimpleNamespace(text="hi"))

    with pytest.raises(ValueError) as error:
        manager.transcribe(str(audio), model_repo=spec.repo)

    assert "new-engine" in str(error.value)
    assert "components/voice/app/backend/transcription.py" in str(error.value)
    assert model.calls == [], "an unknown engine must refuse before inference"


def test_unknown_engine_refusal_names_the_file_for_both_branches() -> None:
    spec = transcription.TranscriptionModel(
        repo="example/new-asr", label="New ASR", size_gb=0.1, note="", engine="new-engine",
    )
    message = str(transcription._unknown_engine(spec))
    assert "example/new-asr" in message
    assert "transcription.py" in message
    assert "result-shape" in message or "normalisation" in message


# ─── Phonon-2: optional English model (owner decision 2026-10-05) ───

PHONON2_REPO = "selcukkubur/Phonon-2-mlx"


def _phonon2_fixture_result():
    """The shape mlx-audio's ParakeetTDT.decode returns, built with its own alignment helpers.

    Token texts are Parakeet SentencePiece pieces as `parakeet.tokenizer.decode` yields them (a
    leading space starts a word) and times sit on the 80 ms encoder frame grid with TDT durations.
    """
    from mlx_audio.stt.models.nemo.alignment import (
        AlignedToken,
        sentences_to_result,
        tokens_to_sentences,
    )

    pieces = [
        (" The", 0.24, 0.16), (" Dream", 0.48, 0.24), ("lands", 0.72, 0.32), (" sleep", 1.12, 0.24),
        (".", 1.36, 0.08), (" Night", 2.0, 0.24), (" fell", 2.32, 0.32), (".", 2.64, 0.08),
    ]
    tokens = [AlignedToken(index, text=text, start=start, duration=duration)
              for index, (text, start, duration) in enumerate(pieces)]
    return sentences_to_result(tokens_to_sentences(tokens))


def test_phonon2_is_optional_english_and_never_the_default() -> None:
    spec = transcription.model_for_repo(PHONON2_REPO)

    assert spec.engine == "phonon2"
    assert spec.languages == "English"
    assert spec.supports_word_timestamps is True
    assert spec.recommended is False
    assert transcription.recommended_model() == WHISPER_REPO


def test_phonon2_receives_bounded_windows_and_no_language(monkeypatch, tmp_path) -> None:
    manager, model, audio = _manager(monkeypatch, tmp_path, _phonon2_fixture_result())

    payload = manager.transcribe(str(audio), model_repo=PHONON2_REPO, language="en")

    assert model.calls[0][1] == {"chunk_duration": transcription.PHONON2_CHUNK_SECONDS}
    assert payload["language"] == "en"


def test_phonon2_pieces_become_words_with_their_timings(monkeypatch, tmp_path) -> None:
    manager, _model, audio = _manager(monkeypatch, tmp_path, _phonon2_fixture_result())

    payload = manager.transcribe(str(audio), model_repo=PHONON2_REPO, word_timestamps=True)

    assert payload["text"] == "The Dreamlands sleep. Night fell."
    first, second = payload["segments"]
    assert (first["start"], first["text"]) == (0.24, "The Dreamlands sleep.")
    assert [w["word"] for w in first["words"]] == ["The", "Dreamlands", "sleep."]
    dreamlands = first["words"][1]
    assert (dreamlands["start"], round(dreamlands["end"], 2)) == (0.48, 1.04)
    assert [w["word"] for w in second["words"]] == ["Night", "fell."]
    assert "00:00:02,000 --> 00:00:02,720" in payload["srt"]


@pytest.mark.parametrize("language", ["km", "fr", "zh-CN"])
def test_phonon2_refuses_other_languages_before_inference(monkeypatch, tmp_path, language) -> None:
    manager, model, audio = _manager(monkeypatch, tmp_path, _phonon2_fixture_result())

    with pytest.raises(ValueError) as error:
        manager.transcribe(str(audio), model_repo=PHONON2_REPO, language=language)

    assert "English only" in str(error.value)
    assert WHISPER_REPO in str(error.value), "the refusal names the model to fall back to"
    assert model.calls == []


@pytest.mark.parametrize("language", [None, "", "auto", "en", "en-US", "English"])
def test_phonon2_accepts_english_or_no_language(language) -> None:
    spec = transcription.model_for_repo(PHONON2_REPO)
    assert transcription.english_only_refusal(spec, language) is None
    # Whisper is multilingual: the English-only rule never applies to it.
    assert transcription.english_only_refusal(transcription.model_for_repo(WHISPER_REPO), "km") is None


def test_phonon2_loads_through_its_own_loader_without_a_processor(monkeypatch, tmp_path) -> None:
    manager = transcription.TranscriptionManager()
    loaded, attached = [], []
    monkeypatch.setattr(manager, "_snapshot_path", lambda _repo: tmp_path)
    monkeypatch.setattr(transcription, "load_phonon2", lambda path: loaded.append(path) or object())
    monkeypatch.setattr(manager, "_attach_processor", lambda _model, repo: attached.append(repo))

    manager._get_model(PHONON2_REPO)

    assert loaded == [tmp_path]
    assert attached == []


def _pack_five_value(weights, lo, hi):
    """Encode like FermionResearch's container: 5 trits/byte per row, hi bits over the nonzeros."""
    import numpy as np

    rows, cols = weights.shape
    sign = np.sign(weights).astype(np.int8)
    codes = (sign + 1).astype(np.uint8)
    width = (cols + 4) // 5
    padded = np.ones((rows, width * 5), dtype=np.uint8)
    padded[:, :cols] = codes
    trits = (padded.reshape(rows, width, 5).astype(np.uint16) * (3 ** np.arange(5))).sum(-1).astype(np.uint8)
    nonzero = sign != 0
    is_hi = np.abs(weights) == hi[:, None]
    hi_bits = np.packbits(is_hi[nonzero], bitorder="little")
    return trits, hi_bits, nonzero.sum(axis=1).astype(np.uint32)


def test_phonon2_unpacks_five_value_blocks_and_int6_tables_exactly() -> None:
    import json
    import numpy as np

    rng = np.random.default_rng(7)
    lo = np.array([0.25, 0.5, 0.125], dtype=np.float16)
    hi = np.array([1.0, 2.0, 0.75], dtype=np.float16)
    pick = rng.integers(0, 5, size=(3, 12))      # the five values per row: -hi -lo 0 +lo +hi
    levels = np.stack([-hi, -lo, np.zeros(3, np.float16), lo, hi], axis=1)
    dense = np.take_along_axis(levels, pick, axis=1).astype(np.float16)
    trits, hi_bits, nz_row = _pack_five_value(dense, lo, hi)

    q = rng.integers(-32, 32, size=(2, 8))
    scale = np.array([0.5, 0.25], dtype=np.float16)
    u = (q + 32).astype(np.uint32).reshape(-1, 4)
    word = u[:, 0] | (u[:, 1] << 6) | (u[:, 2] << 12) | (u[:, 3] << 18)
    q6 = np.stack([word & 0xFF, (word >> 8) & 0xFF, (word >> 16) & 0xFF], axis=1).astype(np.uint8).reshape(2, -1)

    plain = np.arange(4, dtype=np.float16)
    tensors = {
        "enc.ff.trits": trits, "enc.ff.hi_bits": hi_bits, "enc.ff.nz_row": nz_row,
        "enc.ff.nz_tile": np.zeros((3, 1), np.uint16), "enc.ff.lo": lo, "enc.ff.hi": hi,
        "joint.q6": q6, "joint.scale": scale, "joint.bias": plain,
    }
    metadata = {
        "phonon_packed": json.dumps([{"name": "enc.ff", "tensor": "enc.ff.weight", "shape": [3, 1, 12], "tile": 128}]),
        "phonon_int6": json.dumps([{"name": "joint", "tensor": "joint.weight", "shape": [2, 8], "bits": 6}]),
    }

    out = transcription._phonon2_dense(tensors, metadata)

    assert set(out) == {"enc.ff.weight", "joint.weight", "joint.bias"}
    np.testing.assert_array_equal(out["enc.ff.weight"], dense.reshape(3, 1, 12))
    np.testing.assert_array_equal(out["joint.weight"], (q * scale[:, None].astype(np.float32)).astype(np.float16))
    np.testing.assert_array_equal(out["joint.bias"], plain)


def test_phonon2_unpack_refuses_codes_that_disagree_with_the_nonzero_table() -> None:
    import json
    import numpy as np

    lo, hi = np.array([0.5], np.float16), np.array([1.0], np.float16)
    dense = np.array([[1.0, 0, -0.5, 0, 0]], dtype=np.float16)
    trits, hi_bits, nz_row = _pack_five_value(dense, lo, hi)
    tensors = {"m.trits": trits, "m.hi_bits": hi_bits, "m.nz_row": nz_row + 1, "m.lo": lo, "m.hi": hi}
    metadata = {"phonon_packed": json.dumps([{"name": "m", "tensor": "m.weight", "shape": [1, 5]}]),
                "phonon_int6": "[]"}

    with pytest.raises(ValueError, match="nonzero table"):
        transcription._phonon2_dense(tensors, metadata)
