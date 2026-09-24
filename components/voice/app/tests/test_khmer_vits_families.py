"""Khmer VITS families (ST-12): catalog, dispatch wiring and worker contracts.

The two engines are non-MLX, so these tests pin the wiring that the shared MLX
suites cannot cover: family identity, the sectioned-family set, dispatch, the
reference-voice rejection, and the ONNX grapheme/blank tokenisation.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from backend import catalog, generation

MMS_REPO = "khmerttsopensource/khmer-tts"
ONNX_REPO = "sengtha/khmer-tts-female-v2"
KHMER = "សួស្តីពិភពលោក"


def _mms_entry():
    entry = catalog.get_model(MMS_REPO)
    assert entry is not None
    return entry


def _onnx_entry():
    entry = catalog.get_model(ONNX_REPO)
    assert entry is not None
    return entry


def test_both_families_are_catalogued_with_their_own_engine():
    mms, onnx = _mms_entry(), _onnx_entry()
    assert (mms.family, onnx.family) == ("khmer-tts-mms", "khmer-tts-onnx")
    assert mms.family != onnx.family
    assert mms.sample_rate_hz == 16000
    assert onnx.sample_rate_hz == 22050
    assert mms.languages == ("km",) and onnx.languages == ("km",)
    # Single-speaker: neither may advertise cloning.
    assert "voice-cloning" not in mms.capabilities
    assert "voice-cloning" not in onnx.capabilities
    assert "khmer-tts-mms" in catalog.FAMILIES
    assert "khmer-tts-onnx" in catalog.FAMILIES


def test_families_are_sectioned_but_not_mlx():
    """Every non-MLX family is sectioned, wired and published as non-Apple-optimized.

    Stated as an invariant over the registry rather than a list of two names:
    a third non-MLX family is covered by this test the day it is added, and the
    old exact-membership version could not have caught one.
    """
    non_mlx = {
        family_id for family_id, family in catalog.FAMILIES.items()
        if family.engine != catalog.ENGINE_MLX_AUDIO
    }
    assert non_mlx, "no non-MLX family is registered any more; this test would be vacuous"
    for family in non_mlx:
        assert family in generation._WIRED_FAMILIES
        assert family in generation._ENGINE_REQUIREMENTS
        assert family in generation._SECTIONED_FAMILIES
        assert family not in generation.MLX_AUDIO_FAMILIES
        published = catalog.serialize_model(catalog.get_model(
            next(entry.repo for entry in catalog.CATALOG if entry.family == family)
        ))
        assert published["apple_optimized"] is False, family
    # The declared requirements are the family's own published ones.
    for family_id, family in catalog.FAMILIES.items():
        assert generation._ENGINE_REQUIREMENTS[family_id] == list(family.requirements)
    assert generation._ENGINE_REQUIREMENTS["khmer-tts-onnx"] == [
        "onnxruntime", "soundfile", "numpy"]


def test_long_khmer_text_is_sectioned():
    text = KHMER + " " + KHMER * 40
    for family, repo in (("khmer-tts-mms", MMS_REPO), ("khmer-tts-onnx", ONNX_REPO)):
        chunks = generation._internal_mlx_text_chunks(family, repo, text)
        assert len(chunks) > 1
        assert "".join(chunks).replace(" ", "") == text.replace(" ", "")


def test_runtime_readiness_tracks_the_engine_dependency():
    assert generation.GenerationManager.runtime_ready_for_family("khmer-tts-mms") == \
        generation.TTS_AVAILABLE
    assert generation.GenerationManager.runtime_ready_for_family("khmer-tts-onnx") == \
        generation._have_onnxruntime()


def test_onnx_worker_interleaves_the_blank_token_and_writes_audio(tmp_path):
    manager = generation.GenerationManager()
    calls: list[dict] = []

    class _Session:
        def run(self, _outputs, feed):
            calls.append(feed)
            return [np.zeros(2205, dtype=np.float32)]

    meta = {"vocab": ["@", "ក", "ខ", "គ"], "blank": "@",
            "add_blank": True, "sample_rate": 22050}
    manager._khmer_onnx_get_model = lambda _repo: (_Session(), meta)

    job = generation.GenerationJob(job_id="t", mode="txt2speech",
                                   params={"repo": ONNX_REPO, "text": "កខ"})
    out = tmp_path / "out.wav"
    manager._generate_khmer_onnx(job, _onnx_entry(), out)

    assert calls[0]["x"][0].tolist() == [0, 1, 0, 2, 0]
    assert calls[0]["x_lengths"].tolist() == [5]
    import soundfile as sf
    info = sf.info(str(out))
    assert (info.samplerate, info.channels) == (22050, 1)
    assert info.frames == 2205


def test_onnx_worker_rejects_text_with_no_khmer_vocabulary(tmp_path):
    manager = generation.GenerationManager()
    manager._khmer_onnx_get_model = lambda _repo: (
        SimpleNamespace(run=lambda *_a, **_k: [np.zeros(10, dtype=np.float32)]),
        {"vocab": ["@"], "blank": "@", "add_blank": True, "sample_rate": 22050},
    )
    job = generation.GenerationJob(job_id="t", mode="txt2speech",
                                   params={"repo": ONNX_REPO, "text": "hello"})
    with pytest.raises(ValueError):
        manager._generate_khmer_onnx(job, _onnx_entry(), tmp_path / "o.wav")


@pytest.mark.parametrize("repo", [MMS_REPO, ONNX_REPO])
def test_single_speaker_families_refuse_a_reference_voice(tmp_path, repo):
    import torch

    manager = generation.GenerationManager()
    if repo == MMS_REPO:
        manager._khmer_vits_get_model = lambda _repo: (
            SimpleNamespace(config=SimpleNamespace(sampling_rate=16000),
                            __call__=lambda self, **kw: SimpleNamespace(
                                waveform=torch.zeros(1600))),
            SimpleNamespace(__call__=lambda self, text, **kw: {"input_ids": torch.ones(1, 2, dtype=torch.long)}),
        )
        worker, entry = manager._generate_khmer_vits, _mms_entry()
    else:
        worker, entry = manager._generate_khmer_onnx, _onnx_entry()
    job = generation.GenerationJob(
        job_id="t", mode="txt2speech",
        params={"repo": repo, "text": KHMER, "voice_library_id": "some-voice"})
    with pytest.raises(ValueError):
        worker(job, entry, tmp_path / "o.wav")
