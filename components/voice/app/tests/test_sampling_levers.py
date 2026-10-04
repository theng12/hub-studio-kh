"""Every TTS model's sampling levers are reachable, bounded, published, and default to what ran before.

Owner, 2026-10-04: "find a balanced default for each of the models". Breeze TTS 2 sampled at
mlx-audio's loosest settings because the Hub passed it none; the levers are now one table
(`catalog.SAMPLING_LEVERS`). This file pins the table's defaults to the values each engine actually
received in 0.6.141, so exposing them changed no output; a calibrated default is a deliberate edit
of the pin below.
"""
from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from backend import catalog, generation, lane_contract, main


# What each engine received in 0.6.141 (mlx-audio 94c7716). `temperature` 0.7 is mlx-audio's
# `generate_audio` default, which it passes to every model; Qwen's repetition penalty is floored at
# 1.5 by the engine in ICL cloning; the rest are the engines' own signature defaults, or (VoxCPM2,
# OmniVoice steps/guidance, Fish) the Hub's earlier literals. Khmer MMS: the model's config.json.
EFFECTIVE_IN_0_6_141 = {
    "qwen3-tts": {"temperature": 0.7, "top_p": 1.0, "top_k": 50, "repetition_penalty": 1.5},
    "breeze-tts": {"temperature": 0.7, "top_p": 1.0, "top_k": 50, "repetition_penalty": 1.0,
                   "cfg_scale": 1.0},
    "pocket-tts": {"temperature": 0.7, "lsd_decode_steps": 1, "eos_threshold": -4.0},
    "omnivoice": {"num_steps": 32, "guidance_scale": 2.0, "class_temperature": 0.0,
                  "position_temperature": 5.0, "layer_penalty_factor": 5.0, "t_shift": 0.1},
    "voxcpm-mlx": {"cfg_value": 2.0, "inference_timesteps": 7, "warmup_patches": 0,
                   "max_tokens": 2000},
    "fish-audio-mlx": {"temperature": 0.7, "top_p": 0.7, "top_k": 30, "max_tokens": 1024},
    "khmer-tts-mms": {"noise_scale": 0.667, "noise_scale_duration": 0.8},
}

ALL_LEVERS = [
    (model, name, lever)
    for model in catalog.CATALOG
    for name, lever in catalog.sampling_levers(model.family, model.repo).items()
]


def test_every_model_defaults_to_what_its_engine_received_before():
    for model in catalog.CATALOG:
        expected = EFFECTIVE_IN_0_6_141.get(model.family, {})
        assert catalog.sampling_kwargs(model.family, model.repo, {}) == expected, model.repo


def test_every_family_that_samples_has_its_levers():
    """Chatterbox keeps its own resolver (its levers were already request fields); the Khmer ONNX
    graph takes only text, so it has none to offer."""
    families = {model.family for model in catalog.CATALOG}
    assert families - set(catalog.SAMPLING_LEVERS) == {"chatterbox-mlx", "khmer-tts-onnx"}


@pytest.mark.parametrize("model,name,lever", ALL_LEVERS,
                         ids=[f"{m.repo}:{n}" for m, n, _ in ALL_LEVERS])
def test_each_lever_reaches_its_engine_argument_within_its_bound(model, name, lever):
    def sent(value):
        return catalog.sampling_kwargs(model.family, model.repo, {name: value})[lever["kwarg"]]

    assert sent(None) == lever["default"]
    assert sent(lever["maximum"] + 100) == lever["maximum"]
    assert sent(lever["minimum"] - 100) == lever["minimum"]
    inside = lever["minimum"] + (lever["maximum"] - lever["minimum"]) / 2
    if lever["type"] == "integer":
        inside = int(inside)
    assert sent(inside) == inside


@pytest.mark.parametrize("model,name,lever", ALL_LEVERS,
                         ids=[f"{m.repo}:{n}" for m, n, _ in ALL_LEVERS])
def test_each_lever_is_published_with_its_bound_and_accepted_by_the_api(model, name, lever):
    published = {p["name"]: p for p in lane_contract.parameters_for(model)}[name]
    for key in ("type", "minimum", "maximum", "default"):
        assert published[key] == lever[key], (name, key)
    assert name in main.Txt2SpeechBody.model_fields


def test_a_per_model_default_overrides_its_family(monkeypatch):
    repo = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit"
    monkeypatch.setitem(catalog.SAMPLING_DEFAULTS_BY_REPO, repo, {"qwen_temperature": 0.55})
    assert catalog.sampling_kwargs("qwen3-tts", repo, {})["temperature"] == 0.55
    published = {p["name"]: p for p in lane_contract.parameters_for(catalog.get_model(repo))}
    assert published["qwen_temperature"]["default"] == 0.55
    sibling = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"
    assert catalog.sampling_kwargs("qwen3-tts", sibling, {})["temperature"] == 0.7


ENGINES = {
    "qwen3-tts": "qwen3_tts", "breeze-tts": "breeze_tts", "pocket-tts": "pocket_tts",
    "omnivoice": "omnivoice", "voxcpm-mlx": "voxcpm2", "fish-audio-mlx": "fish_qwen3_omni",
}


@pytest.mark.parametrize("family", sorted(ENGINES))
def test_every_engine_argument_is_one_the_installed_engine_reads(family):
    module = pytest.importorskip(f"mlx_audio.tts.models.{ENGINES[family]}")
    generate = module.Model.generate
    signature = inspect.signature(generate).parameters
    source = inspect.getsource(generate)
    for lever in catalog.SAMPLING_LEVERS[family].values():
        kwarg = lever["kwarg"]
        assert kwarg in signature or f'"{kwarg}"' in source, (family, kwarg)


def test_vits_noise_scales_are_attributes_the_model_reads():
    vits = pytest.importorskip("transformers.models.vits.modeling_vits")
    source = inspect.getsource(vits.VitsModel)
    for lever in catalog.SAMPLING_LEVERS["khmer-tts-mms"].values():
        assert f"self.{lever['kwarg']}" in source


def _render_breeze(tmp_path, monkeypatch, params):
    import soundfile as sf

    sent: list[dict] = []
    seeds: list[int] = []

    def fake_generate_audio(*, model, output_path, join_audio, **kwargs):
        sent.append(kwargs)
        sf.write(Path(output_path) / "audio.wav", np.ones(2400, dtype=np.float32) * 0.1, 24000,
                 subtype="PCM_16")

    monkeypatch.setattr(importlib.import_module("mlx_audio.tts.generate"), "generate_audio",
                        fake_generate_audio)
    mx = importlib.import_module("mlx.core")
    monkeypatch.setattr(mx.random, "seed", lambda value: seeds.append(value))
    monkeypatch.setattr(generation, "_release_device_memory", lambda device: None)
    monkeypatch.setattr(generation, "_trim_model_terminal_silence", lambda *a, **k: 0.0)
    manager = object.__new__(generation.GenerationManager)
    manager._mlx_audio_get_model = lambda repo: "breeze-model"
    job = generation.GenerationJob(job_id="breeze", mode="txt2speech", params={
        "text": "One quiet sentence.", "voice_design_prompt": "a calm narrator",
        "_native_single_section": True, **params})
    manager._generate_mlx_audio(job, catalog.get_model("mlx-community/Breeze-TTS-2-mlx-4bit"),
                                tmp_path / "out.wav", postprocess_speed=False)
    return sent, seeds, job


def test_breeze_levers_reach_generate_audio_and_omitted_ones_keep_the_old_values(
        tmp_path, monkeypatch):
    pytest.importorskip("mlx.core")
    sent, _, _ = _render_breeze(tmp_path, monkeypatch, {
        "breeze_temperature": 0.5, "breeze_top_p": 0.9, "breeze_cfg_scale": 9.0})
    assert sent[0]["temperature"] == 0.5
    assert sent[0]["top_p"] == 0.9
    assert sent[0]["cfg_scale"] == 4.0                      # clamped to the published bound
    assert sent[0]["top_k"] == 50 and sent[0]["repetition_penalty"] == 1.0

    sent, _, _ = _render_breeze(tmp_path, monkeypatch, {})
    assert {k: sent[0][k] for k in EFFECTIVE_IN_0_6_141["breeze-tts"]} == (
        EFFECTIVE_IN_0_6_141["breeze-tts"])


def test_a_fixed_seed_seeds_the_request_once_and_never_reaches_the_engine(tmp_path, monkeypatch):
    """Breeze's own `seed` argument would reseed every section to one state; the worker seeds MLX
    once per request and a later section continues the stream."""
    pytest.importorskip("mlx.core")
    sent, seeds, job = _render_breeze(tmp_path, monkeypatch, {"seed": 1234})
    assert "seed" not in sent[0]
    assert seeds == [1234] and job.resolved_seed == 1234

    sent, seeds, _ = _render_breeze(tmp_path, monkeypatch, {
        "seed": 1234, "_qwen_attempt_seed": 1234, "_native_continue_rng": True})
    assert seeds == []                                      # section 2+: same stream, no reseed


def test_omitted_levers_are_not_recorded_and_a_supplied_one_is(monkeypatch):
    model = catalog.get_model("mlx-community/Breeze-TTS-2-mlx-4bit")
    monkeypatch.setattr(main, "_defaults_in_force", lambda m: {})
    omitted = main._job_parameters(main.Txt2SpeechBody(repo=model.repo, text="Hi."), model)
    assert not set(omitted) & set(catalog.SAMPLING_LEVERS["breeze-tts"])
    supplied = main._job_parameters(
        main.Txt2SpeechBody(repo=model.repo, text="Hi.", breeze_temperature=0.6), model)
    assert supplied["breeze_temperature"] == 0.6


def _khmer_render(monkeypatch, params):
    import torch

    seeded: list[int] = []
    monkeypatch.setattr(torch, "manual_seed", lambda value: seeded.append(value))
    class FakeVits:
        config = SimpleNamespace(sampling_rate=16000)

        def __call__(self, **kw):
            return SimpleNamespace(waveform=torch.zeros(1600))

    vits = FakeVits()
    manager = generation.GenerationManager()
    manager._khmer_vits_get_model = lambda _repo: (
        vits, lambda text, **kw: {"input_ids": torch.ones(1, 2, dtype=torch.long)})
    job = generation.GenerationJob(job_id="k", mode="txt2speech", params={
        "repo": "khmerttsopensource/khmer-tts", "text": "សួស្តី", **params})
    return manager, job, vits, seeded


def test_khmer_sections_continue_one_seed_and_take_their_noise_scales(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    entry = catalog.get_model("khmerttsopensource/khmer-tts")
    manager, job, vits, seeded = _khmer_render(monkeypatch, {
        "seed": 7, "_qwen_attempt_seed": 7, "_native_continue_rng": False,
        "vits_noise_scale": 0.3})
    manager._generate_khmer_vits(job, entry, tmp_path / "a.wav")
    assert seeded == [7] and job.resolved_seed == 7
    assert (vits.noise_scale, vits.noise_scale_duration) == (0.3, 0.8)

    manager, job, vits, seeded = _khmer_render(monkeypatch, {
        "seed": 7, "_qwen_attempt_seed": 7, "_native_continue_rng": True})
    manager._generate_khmer_vits(job, entry, tmp_path / "b.wav")
    assert seeded == []                     # section 2 continues the stream, it is not reseeded to 7
    assert (vits.noise_scale, vits.noise_scale_duration) == (0.667, 0.8)

    manager, job, _, seeded = _khmer_render(monkeypatch, {})
    manager._generate_khmer_vits(job, entry, tmp_path / "c.wav")
    assert seeded == [job.resolved_seed]    # the recorded seed is the one the audio used
