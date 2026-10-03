from __future__ import annotations

from pathlib import Path
import inspect
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from backend import catalog, generation, long_form_policy, model_audits


class _VoiceLibrary:
    def __init__(self, reference: Path, transcript: str = "Saved reference transcript") -> None:
        self.reference = reference
        self._transcript = transcript

    def get(self, voice_id: str):
        return {"id": voice_id} if voice_id == "voice-1" else None

    def reference_path(self, voice_id: str) -> Path:
        return self.reference

    def transcript(self, voice_id: str) -> str:
        return self._transcript


def _repos(family: str) -> list[str]:
    return [entry.repo for entry in catalog.CATALOG if entry.family == family]


def test_priority_catalog_is_focused_and_clone_capable() -> None:
    qwen = _repos("qwen3-tts")
    assert "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit" in qwen
    assert "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit" in qwen
    # Pruned 2026-09-24 (ST-13.B) to the two tiers the owner kept after the
    # listening review: the 1.7B 8-bit, the 0.6B 4-bit and both the CustomVoice
    # and VoiceDesign tiers were cut. The owner re-added the 0.6B 4-bit on 2026-10-02 as the
    # lighter twin of the 8-bit default (same speech tokenizer, same clone path).
    assert "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit" in qwen
    assert len(qwen) == 3

    chatterbox = _repos("chatterbox-mlx")
    assert len(chatterbox) == 2
    assert "mlx-community/chatterbox-fp16" not in chatterbox
    assert "mlx-community/chatterbox-turbo-8bit" not in chatterbox

    omni = [entry for entry in catalog.CATALOG if entry.family == "omnivoice"]
    assert [entry.repo for entry in omni] == ["mlx-community/OmniVoice-bfloat16"]
    assert all(entry.repo.startswith("mlx-community/") for entry in omni)
    assert all("voice-cloning" in entry.capabilities for entry in omni)
    assert "k2-fsa/OmniVoice" not in {entry.repo for entry in omni}

    fish = _repos("fish-audio-mlx")
    assert fish == [
        "mlx-community/fish-audio-s2-pro-8bit",
        "majentik/fishaudio-s2-pro-MLX-4bit",
        "mlx-community/fish-audio-s2-pro-bf16",
    ]
    assert all(
        "voice-cloning" in entry.capabilities
        for entry in catalog.CATALOG
        if entry.family == "fish-audio-mlx"
    )
    fish_model = catalog.get_model(fish[0])
    assert fish_model is not None
    assert fish_model.sample_rate_hz == 44100
    # Fish was unqualified when this test was written. It has since been
    # measured on the fleet -- 13.234 GB peak on a 17.2 GB machine at 3.75x
    # realtime, the slowest model measured -- and the owner set the floor at
    # 24 GB, which one machine in the fleet meets. Assert the qualified state
    # rather than the absence of one.
    assert fish_model.min_unified_memory_gb == 24
    assert "not yet qualified" not in fish_model.recommended_hardware.lower()
    serialized = catalog.serialize_model(fish_model)
    assert serialized["min_unified_memory_gb"] == 24
    assert serialized["fit"]["state"] != "unqualified"
def test_unqualified_fish_memory_never_renders_as_a_numeric_fit_claim() -> None:
    frontend = Path(__file__).resolve().parents[1] / "frontend"
    markup = (frontend / "index.html").read_text(encoding="utf-8")
    script = (frontend / "app.js").read_text(encoding="utf-8")
    styles = (frontend / "style.css").read_text(encoding="utf-8")

    assert 'x-text="memoryFloorLabel(m)"' in markup
    assert 'state: "unqualified"' in script
    assert 'unqualified: "⏳ test pending"' in script
    assert ".fit-dot.fit-unqualified" in styles


def test_qwen_06b_base_is_the_8gb_production_voice_generator() -> None:
    model = catalog.get_model(
        "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"
    )
    assert model is not None
    assert model.label == "Qwen3-TTS 0.6B Base"
    assert model.min_unified_memory_gb == 8
    assert model.languages == (
        "en", "zh", "ja", "ko", "de", "fr", "ru", "pt", "es", "it",
    )
    assert "8 GB" in model.recommended_hardware
    assert any(
        kind == "good" and "8 GB" in text
        for kind, text in model.use_cases
    )


def test_qwen_17b_base_uses_the_official_language_roster_and_its_floor() -> None:
    # The 1.7B Base 8-bit was cut on 2026-09-24 (ST-13.B); the kept 4-bit tier
    # carries the same official ten-language roster at a 12 GB floor.
    model = catalog.get_model("mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit")

    assert model is not None
    assert model.min_unified_memory_gb == 12
    assert model.languages == (
        "en", "zh", "ja", "ko", "de", "fr", "ru", "pt", "es", "it",
    )
    assert all("11 languages" not in text for _kind, text in model.use_cases)


def test_diagnostics_cover_every_wired_engine_and_show_package_versions() -> None:
    result = generation.diagnostics()

    assert {engine["family"] for engine in result["engines"]} == generation._WIRED_FAMILIES
    # Derived, not hardcoded. This used to assert a fixed count and then name the engines that
    # happened to be wired at the time, so it had to be edited whenever a family was added - and
    # worse, it kept asserting that CUT families were still wired after they left the catalog.
    # Both facts now come from the registries the app actually dispatches on, so this test can
    # only fail for a real reason.
    assert result["total_engines"] == len(generation._WIRED_FAMILIES)
    catalog_families = {model.family for model in catalog.CATALOG}
    assert catalog_families <= generation._WIRED_FAMILIES, (
        "a catalog family has no worker: "
        + repr(sorted(catalog_families - generation._WIRED_FAMILIES)))
    assert catalog_families <= set(generation._ENGINE_REQUIREMENTS), (
        "a catalog family declares no dependencies: "
        + repr(sorted(catalog_families - set(generation._ENGINE_REQUIREMENTS))))
    # MLX_AUDIO_FAMILIES is the dispatch table and _WIRED_FAMILIES is what the app advertises, so
    # a row in one without the other is the "new model needs a second edit" defect in miniature.
    assert set(generation.MLX_AUDIO_FAMILIES) <= generation._WIRED_FAMILIES
    packages = {package["package"]: package for package in result["packages"]}
    assert packages["mlx_audio"]["version"]
    # The two non-MLX engines each need a package no MLX family needs.
    assert "onnxruntime" in generation._ENGINE_REQUIREMENTS["khmer-tts-onnx"]
    assert "transformers" in generation._ENGINE_REQUIREMENTS["khmer-tts-mms"]
    # Fish S2 Pro is the one shipped family that needs the MLX language-model runtime.
    assert "mlx_lm" in generation._ENGINE_REQUIREMENTS["fish-audio-mlx"]
    assert "omnivoice" not in generation._ENGINE_REQUIREMENTS["omnivoice"]


def test_every_mlx_family_is_published_as_apple_optimized() -> None:
    """The catalog's MLX marker and the dispatch table must agree, family for family.

    `serialize_model` cannot import `generation` (circular import), so it carries its own tuple of
    MLX family ids. When the two drift, the model is published as "PyTorch / MPS" and is HIDDEN by
    the frontend's "MLX only" filter - which is exactly how `kittentts`, `vibevoice` and
    `omnivoice` shipped invisible once. This asserts the invariant against serialized output rather
    than pinning either list, so a family added to the dispatch table alone fails here by name.
    """
    published = {entry.family: catalog.serialize_model(entry)["apple_optimized"]
                 for entry in catalog.CATALOG}
    missing = sorted(family for family in generation.MLX_AUDIO_FAMILIES
                     if not published.get(family))
    assert not missing, (
        "MLX family published as non-Apple-optimized (hidden by the MLX-only filter): "
        + repr(missing))


def test_mlx_cache_release_prefers_current_api() -> None:
    source = inspect.getsource(generation._release_device_memory)
    assert source.index('hasattr(mx, "clear_cache")') < source.index('hasattr(mx, "metal")')


@pytest.mark.parametrize(
    "error",
    [
        MemoryError("unable to allocate Metal buffer"),
        RuntimeError("MPS backend out of memory"),
        RuntimeError("Abort trap: 6"),
    ],
)
def test_memory_failure_classifier_catches_engine_allocation_failures(error) -> None:
    assert generation._is_memory_failure(error)


def test_memory_failure_classifier_does_not_catch_normal_model_errors() -> None:
    assert not generation._is_memory_failure(RuntimeError("invalid speaker name"))


def test_memory_preflight_refuses_when_live_headroom_is_too_low(monkeypatch) -> None:
    manager = object.__new__(generation.GenerationManager)
    manager._mlx_audio_model = None
    manager._mlx_audio_model_repo = None
    monkeypatch.setattr(
        generation,
        "_memory_snapshot",
        lambda: {"total_gb": 8.0, "available_gb": 1.0, "used_gb": 7.0, "percent": 87.5},
    )
    with pytest.raises(generation.MemoryGuardError, match="Memory guard paused"):
        manager._memory_preflight(SimpleNamespace(repo="repo", family="voxcpm-mlx", size_gb=2.3))


def test_memory_preflight_honours_the_operator_floor_the_hub_forwards(monkeypatch) -> None:
    """The Hub forwards its per-model free-memory override as ``memory_floor_gb`` (only while one is
    set). The worker's own estimate (size + headroom) must give way to it, in both directions, or the
    two guards disagree about one job."""
    manager = object.__new__(generation.GenerationManager)
    manager._mlx_audio_model = None
    manager._mlx_audio_model_repo = None
    model = SimpleNamespace(repo="repo", family="qwen3-tts", size_gb=1.9)   # estimate: 3.15 GB
    monkeypatch.setattr(
        generation, "_memory_snapshot",
        lambda: {"total_gb": 8.0, "available_gb": 2.0, "used_gb": 6.0, "percent": 75.0},
    )
    with pytest.raises(generation.MemoryGuardError):
        manager._memory_preflight(model, SimpleNamespace(params={}))
    manager._memory_preflight(model, SimpleNamespace(params={"memory_floor_gb": 1.0}))
    with pytest.raises(generation.MemoryGuardError, match="2.5 GB free is needed"):
        manager._memory_preflight(model, SimpleNamespace(params={"memory_floor_gb": 2.5}))


def test_memory_preflight_frees_a_different_idle_model_before_refusing(monkeypatch) -> None:
    """An idle worker holding model A, asked for model B, is not short of memory: the load evicts A
    anyway. The guard used to refuse B on the reading taken with A still resident (an idle 8 GB Mac
    that last ran Qwen refusing OmniVoice); it now frees A first and decides on a fresh reading."""
    manager = object.__new__(generation.GenerationManager)
    manager._mlx_audio_model = object()
    manager._mlx_audio_model_repo = "held/qwen"
    readings = iter([2.2, 4.3])
    monkeypatch.setattr(
        generation, "_memory_snapshot",
        lambda: {"total_gb": 8.0, "available_gb": next(readings), "used_gb": 4.0, "percent": 50.0},
    )
    evicted = []
    monkeypatch.setattr(manager, "_evict_loaded_models",
                        lambda reason="": (evicted.append(reason),
                                           setattr(manager, "_mlx_audio_model", None)))
    omnivoice = SimpleNamespace(repo="wanted/omnivoice", family="omnivoice", size_gb=2.0)  # 3.25 GB
    manager._memory_preflight(omnivoice, SimpleNamespace(params={}))
    assert evicted and manager._mlx_audio_model is None

    # Still short after freeing it: refused, with the fresh numbers it decided on.
    manager._mlx_audio_model = object()
    readings = iter([2.2, 2.9])
    with pytest.raises(generation.MemoryGuardError, match="3.2 GB free is needed, but only 2.9 GB"):
        manager._memory_preflight(omnivoice, SimpleNamespace(params={}))

    # The model it holds is the one asked for: nothing to free, the loaded floor applies.
    manager._mlx_audio_model, manager._mlx_audio_model_repo = object(), "wanted/omnivoice"
    evicted.clear()
    readings = iter([2.0])
    with pytest.raises(generation.MemoryGuardError):
        manager._memory_preflight(omnivoice, SimpleNamespace(params={}))
    assert not evicted


def test_a_later_section_of_an_admitted_job_is_not_measured_against_itself(monkeypatch) -> None:
    """2026-10-04: OmniVoice parts on idle 8 GB Macs failed "2.8 GB free is needed, but only 2.0-2.5
    GB is available" - the guard re-ran for every section, with the job's own model resident."""
    manager = object.__new__(generation.GenerationManager)
    manager._mlx_audio_model = object()
    manager._mlx_audio_model_repo = "wanted/omnivoice"
    monkeypatch.setattr(
        generation, "_memory_snapshot",
        lambda: {"total_gb": 8.0, "available_gb": 2.2, "used_gb": 6.0, "percent": 75.0},
    )
    omnivoice = SimpleNamespace(repo="wanted/omnivoice", family="omnivoice", size_gb=2.0)
    with pytest.raises(generation.MemoryGuardError, match="2.8 GB free is needed"):
        manager._memory_preflight(omnivoice, SimpleNamespace(params={}))
    manager._memory_preflight(omnivoice, SimpleNamespace(params={"_memory_admitted": True}))


def test_worker_memory_snapshot_uses_the_shared_available_figure(monkeypatch) -> None:
    from components import host_memory
    monkeypatch.setattr(host_memory, "available_bytes", lambda vm: 3_650_000_000)
    assert generation._memory_snapshot()["available_gb"] == 3.65


def test_a_model_above_the_machines_tier_still_runs_when_memory_is_free(
    monkeypatch,
) -> None:
    """A published RAM floor is a tier, not a wall.

    The owner runs a model on machines below its qualified tier deliberately, with
    the evidence to back it. The floor is reported and the job proceeds; what still
    stops it is the live free-memory check, which is a fact about this moment.
    """
    manager = object.__new__(generation.GenerationManager)
    manager._mlx_audio_model = None
    manager._mlx_audio_model_repo = None
    # The 1.7B CustomVoice tier was cut on 2026-09-24 (ST-13.B); VoxCPM2 4-bit is
    # a kept 16 GB-floor model, which is what this guard is about.
    model = catalog.get_model("mlx-community/VoxCPM2-4bit")
    assert model is not None and model.min_unified_memory_gb == 16
    monkeypatch.setattr(
        generation,
        "_memory_snapshot",
        lambda: {"total_gb": 8.0, "available_gb": 6.0, "used_gb": 2.0, "percent": 25.0},
    )

    # Below the tier, with room to work: allowed.
    manager._memory_preflight(model)

    # Below the tier and with no room to work: the live guard still stops it.
    monkeypatch.setattr(
        generation,
        "_memory_snapshot",
        lambda: {"total_gb": 8.0, "available_gb": 0.4, "used_gb": 7.6, "percent": 95.0},
    )
    with pytest.raises(generation.MemoryGuardError, match="Memory guard paused"):
        manager._memory_preflight(model)
def test_mlx_worker_joins_all_generated_segments() -> None:
    source = inspect.getsource(generation.GenerationManager._generate_mlx_audio)
    assert "join_audio=True" in source
    assert 'temp_dir / "audio.wav"' in source


def test_voxcpm_catalog_exposes_the_three_mlx_tiers() -> None:
    # The owner asked for the 8-bit middle tier to be selectable (2026-09-22),
    # so all three quantizations are offered in ascending precision order.
    assert _repos("voxcpm-mlx") == [
        "mlx-community/VoxCPM2-4bit",
        "mlx-community/VoxCPM2-8bit",
        "mlx-community/VoxCPM2-bf16",
    ]
    assert "voxcpm" not in catalog.FAMILIES
    requirements = (Path(__file__).resolve().parents[1] / "requirements-generation.txt").read_text()
    assert not any(line.startswith("voxcpm") for line in requirements.splitlines())


def test_voxcpm_saved_transcript_enables_ultimate_clone(tmp_path: Path) -> None:
    reference = tmp_path / "voice.wav"
    reference.touch()
    voices = SimpleNamespace(library=_VoiceLibrary(reference))
    manager = object.__new__(generation.GenerationManager)
    kwargs: dict = {}

    label = manager._mlx_kwargs_voxcpm_flex(
        {"voice_library_id": "voice-1", "voice_design_prompt": "calm and warm"},
        kwargs,
        voices,
    )

    assert label == "ultimate clone + style"
    # `ref_text` is kept as well as the prompt pair: without it mlx-audio loads its
    # own undeclared STT model to transcribe the reference, which fails offline.
    assert kwargs == {
        "instruct": "calm and warm",
        "ref_audio": str(reference),
        "ref_text": "Saved reference transcript",
        "prompt_audio": str(reference),
        "prompt_text": "Saved reference transcript",
    }


def test_voxcpm_clone_without_transcript_stays_reference_only(tmp_path: Path) -> None:
    reference = tmp_path / "voice.wav"
    reference.touch()
    voices = SimpleNamespace(library=_VoiceLibrary(reference, transcript=""))
    manager = object.__new__(generation.GenerationManager)
    kwargs: dict = {}

    label = manager._mlx_kwargs_voxcpm_flex(
        {"voice_library_id": "voice-1"}, kwargs, voices
    )

    assert label == "reference clone"
    # An empty `ref_text` is kept on purpose: dropping it makes mlx-audio load its
    # own undeclared STT model, which fails offline. Empty keeps the reference
    # timbre with no STT load.
    assert kwargs == {"ref_audio": str(reference), "ref_text": ""}


def test_mlx_worker_applies_seed_and_voxcpm_controls() -> None:
    source = inspect.getsource(generation.GenerationManager._generate_mlx_audio)
    assert "mx.random.seed" in source
    assert 'gen_kwargs["warmup_patches"]' in source
    assert 'gen_kwargs["max_tokens"]' in source


def test_voxcpm_api_preserves_advanced_controls() -> None:
    from backend.main import Txt2SpeechBody

    body = Txt2SpeechBody(
        repo="mlx-community/VoxCPM2-4bit",
        text="Hello",
        voxcpm_warmup_patches=2,
        voxcpm_max_tokens=3072,
    )
    params = body.model_dump()
    assert params["voxcpm_warmup_patches"] == 2
    assert params["voxcpm_max_tokens"] == 3072


def test_voxcpm_cached_model_materializes_thread_sensitive_boundary() -> None:
    source = inspect.getsource(generation.GenerationManager._mlx_audio_get_model)
    assert 'entry.family == "voxcpm-mlx"' in source
    assert "mx.eval(sr_boundaries)" in source


def test_qwen_17b_base_uses_clone_mode() -> None:
    assert generation._qwen3_mode_from_repo(
        "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"
    ) == "clone"


def test_qwen_06b_custom_voice_uses_preset_speaker_mode() -> None:
    assert generation._qwen3_mode_from_repo(
        "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"
    ) == "custom"


def test_qwen_base_requires_an_exact_reference_transcript(tmp_path: Path) -> None:
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"private")
    voices = SimpleNamespace(library=_VoiceLibrary(reference, transcript=""))
    manager = object.__new__(generation.GenerationManager)

    with pytest.raises(ValueError, match="exact transcript"):
        manager._mlx_kwargs_qwen3(
            SimpleNamespace(
                repo="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"
            ),
            {"voice_library_id": "voice-1"},
            {},
            voices,
        )

    kwargs: dict = {}
    label = manager._mlx_kwargs_qwen3(
        SimpleNamespace(
            repo="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"
        ),
        {
            "_reference_audio_path": str(reference),
            "ref_transcript": "These are the exact spoken words.",
        },
        kwargs,
        voices_module=None,
    )
    assert label == "clone (voice=private-reference)"
    assert kwargs == {
        "ref_audio": str(reference),
        "ref_text": "These are the exact spoken words.",
    }


def test_qwen_clone_long_form_chunks_at_sentences_without_losing_text() -> None:
    text = " ".join([
        "First sentence keeps a natural pace.",
        "Second sentence remains with the same cloned voice.",
        "Third sentence is long enough to make this a genuine narration section.",
        "Fourth sentence begins the next section without accelerating the ending.",
        "Fifth sentence confirms every word survives the split.",
    ])
    chunks = generation._qwen_clone_text_chunks(text, max_chars=120)

    assert len(chunks) >= 2
    assert all(len(chunk) <= 120 for chunk in chunks)
    assert " ".join(chunks) == " ".join(text.split())


def test_qwen_clone_long_sentence_falls_back_to_word_boundaries() -> None:
    text = "word " * 100
    chunks = generation._qwen_clone_text_chunks(text, max_chars=80)

    assert len(chunks) > 1
    assert all(len(chunk) <= 80 for chunk in chunks)
    assert " ".join(chunks) == " ".join(text.split())


@pytest.mark.parametrize(
    ("family", "repo", "expected_limit"),
    [
        ("qwen3-tts", "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit", 288),
        ("qwen3-tts", "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit", 360),
        ("chatterbox-mlx", "mlx-community/chatterbox-8bit", 500),
        # The shipped 1.7B Base tier resolves to the measured production audit,
        # so its Auto section size (280) governs, not the family clone default.
        ("qwen3-tts", "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit", 280),
        ("voxcpm-mlx", "mlx-community/VoxCPM2-4bit", 400),
        ("omnivoice", "mlx-community/OmniVoice-bfloat16", 288),
        ("fish-audio-mlx", "mlx-community/fish-audio-s2-pro-8bit", 300),
        ("pocket-tts", "mlx-community/pocket-tts-4bit", 280),
    ],
)
def test_long_form_local_engines_split_safely_without_losing_text(
    family: str, repo: str, expected_limit: int
) -> None:
    text = " ".join(
        f"Sentence {index} keeps every ordinary word at a natural boundary."
        for index in range(1, 121)
    )

    chunks = generation._internal_mlx_text_chunks(family, repo, text)

    assert len(chunks) > 1
    assert all(len(chunk) <= expected_limit for chunk in chunks)
    assert " ".join(chunks) == " ".join(text.split())


def test_long_form_catalogs_do_not_ask_callers_to_manually_chunk() -> None:
    # Derived from the catalog, so a newly adopted family is covered without editing this list -
    # which is how the previous hardcoded tuple kept passing after its families were cut.
    for family in sorted({model.family for model in catalog.CATALOG}):
        guidance = catalog.FAMILIES[family].text_guidance
        assert guidance.soft_max_chars is None
        assert guidance.chunking == "auto-split"


@pytest.mark.parametrize(
    ("family", "repo"),
    [
        ("omnivoice", "mlx-community/OmniVoice-bfloat16"),
        ("fish-audio-mlx", "mlx-community/fish-audio-s2-pro-8bit"),
        ("pocket-tts", "mlx-community/pocket-tts-4bit"),
    ],
)
def test_customer_sized_40k_text_is_preserved_across_private_sections(
    family: str, repo: str
) -> None:
    text = " ".join(
        f"Sentence {index} stays complete, naturally paced, and privately sectioned."
        for index in range(1, 701)
    )
    assert len(text) > 40_000

    chunks = generation._internal_mlx_text_chunks(family, repo, text)

    assert len(chunks) > 10
    assert all(len(chunk) <= 3000 for chunk in chunks)
    assert " ".join(chunks) == " ".join(text.split())


def test_qwen_clone_join_preserves_segment_audio_and_pause(tmp_path: Path) -> None:
    import numpy as np
    import soundfile as sf

    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    output = tmp_path / "joined.wav"
    sf.write(first, np.full(10, 0.25, dtype=np.float32), 1000, subtype="PCM_16")
    sf.write(second, np.full(10, -0.25, dtype=np.float32), 1000, subtype="PCM_16")

    generation._join_qwen_clone_wavs([first, second], output, pause_s=0.01)
    audio, sample_rate = sf.read(output, dtype="float32")

    assert sample_rate == 1000
    assert len(audio) == 30
    assert np.allclose(audio[:10], 0.25, atol=1e-3)
    assert np.allclose(audio[10:20], 0.0, atol=1e-6)
    assert np.allclose(audio[20:], -0.25, atol=1e-3)


def test_omnivoice_long_form_join_uses_boundary_aware_speed_compensated_gaps(
    tmp_path: Path,
) -> None:
    import numpy as np
    import soundfile as sf

    chunks = [
        "First complete sentence.",
        "Second complete sentence.",
        "Third soft phrase,",
        "fourth soft phrase continues",
    ]
    full_text = f"{chunks[0]} {chunks[1]}\n\n{chunks[2]} {chunks[3]}"
    output = tmp_path / "omnivoice.wav"
    sections = tmp_path / "sections"
    sections.mkdir()
    calls = 0

    def fake_generate_audio(*, model, output_path, join_audio, **kwargs) -> None:
        nonlocal calls
        calls += 1
        sf.write(
            Path(output_path) / "audio.wav",
            np.full(200, calls / 10, dtype=np.float32),
            1000,
            subtype="PCM_16",
        )

    manager = object.__new__(generation.GenerationManager)
    job = generation.GenerationJob(
        job_id="omnivoice-boundaries",
        mode="txt2speech",
        params={"speed": 1.25},
    )
    manager._generate_mlx_long_form_sections(
        job,
        "omnivoice",
        "omni-model",
        {"text": full_text},
        chunks,
        sections,
        output,
        fake_generate_audio,
        "mlx-community/OmniVoice-bfloat16",
    )

    audio, sample_rate = sf.read(output, dtype="float32")

    assert sample_rate == 1000
    # Target output gaps (owner's uniform rule, 2026-10-03) are 600 ms for a sentence, 1000 ms
    # for a paragraph, and 150 ms for a soft punctuation split. Pre-scaling by 1.25 keeps those
    # durations stable after the finished WAV receives its 1.25x tempo pass.
    assert len(audio) == 200 + 750 + 200 + 1250 + 200 + 188 + 200
    assert np.allclose(audio[200:950], 0.0, atol=1e-6)
    assert np.allclose(audio[1150:2400], 0.0, atol=1e-6)
    assert np.allclose(audio[2600:2788], 0.0, atol=1e-6)
    # Each independently rendered section receives its own micro-fade. There
    # is no audio overlap or speech crossfade at any join.
    for start, end in ((0, 200), (950, 1150), (2400, 2600), (2788, 2988)):
        assert abs(audio[start]) < 1e-3
        assert abs(audio[end - 1]) < 1e-3
        assert np.max(np.abs(audio[start + 20:end - 20])) > 0.09


def test_qwen_clone_long_form_join_uses_production_boundary_pacing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import numpy as np
    import soundfile as sf

    chunks = [
        "First complete sentence.",
        "Second complete sentence.",
        "Third soft phrase,",
        "fourth soft phrase continues",
    ]
    full_text = f"{chunks[0]} {chunks[1]}\n\n{chunks[2]} {chunks[3]}"
    output = tmp_path / "qwen.wav"
    sections = tmp_path / "sections"
    sections.mkdir()
    calls = 0

    def fake_generate_audio(*, model, output_path, join_audio, **kwargs) -> None:
        nonlocal calls
        calls += 1
        sf.write(
            Path(output_path) / "audio.wav",
            np.full(200, calls / 10, dtype=np.float32),
            1000,
            subtype="PCM_16",
        )

    manager = object.__new__(generation.GenerationManager)
    monkeypatch.setattr(
        generation,
        "_trim_model_terminal_silence",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("Qwen 1.7B assembly must retain every generated frame")
        ),
    )
    # Loudness matching is switched off for this test, and that is not a concession — it is this
    # test staying about what it is about. The fixture identifies each section by giving it its own
    # amplitude (0.1, 0.2, 0.3, 0.4) and then asserts those amplitudes survive, which is how it
    # proves the sections are neither mixed up nor crossfaded at a join. Levelling exists to make
    # every section the *same* loudness, so it removes exactly that signal. The subject here is
    # production boundary pacing, and the pass is measured to be length-preserving (ratio 1.000 at
    # 50 ms, 200 ms and 1 s), so turning it off does not weaken a single pacing or silence
    # assertion below. `tests/test_long_form_loudness.py` is where levelling itself is tested.
    monkeypatch.setattr(generation, "_levelled_sections",
                        lambda paths, *args, **kwargs: (list(paths), False))
    job = generation.GenerationJob(
        job_id="qwen-boundaries",
        mode="txt2speech",
        params={"speed": 1.25},
    )
    manager._generate_mlx_long_form_sections(
        job,
        "qwen3-tts",
        "qwen-model",
        {"text": full_text},
        chunks,
        sections,
        output,
        fake_generate_audio,
        "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
    )

    audio, sample_rate = sf.read(output, dtype="float32")

    assert sample_rate == 1000
    assert len(audio) == 200 + 750 + 200 + 1250 + 200 + 188 + 200
    assert np.allclose(audio[200:950], 0.0, atol=1e-6)
    assert np.allclose(audio[1150:2400], 0.0, atol=1e-6)
    assert np.allclose(audio[2600:2788], 0.0, atol=1e-6)
    assert np.allclose(audio[:200], 0.1, atol=1e-3)
    assert np.allclose(audio[950:1150], 0.2, atol=1e-3)
    assert np.allclose(audio[2400:2600], 0.3, atol=1e-3)
    assert np.allclose(audio[2788:2988], 0.4, atol=1e-3)


def test_every_model_publishes_the_uniform_sentence_join() -> None:
    # Owner decision 2026-10-03: one join rule for every model; the published flat pause is the
    # sentence pause (Qwen clone was 0.18 s, CustomVoice 0.12 s, Qwen 1.7B and OmniVoice 0.30 s).
    for family, repo in (
        ("qwen3-tts", "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"),
        ("qwen3-tts", "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"),
        ("qwen3-tts", "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"),
        ("omnivoice", "mlx-community/OmniVoice-bfloat16"),
    ):
        assert generation._long_form_join_pause_s(family, repo) == pytest.approx(0.60)


def test_qwen_speed_is_pitch_preserving_and_changes_duration(tmp_path: Path) -> None:
    import numpy as np
    import soundfile as sf

    if generation._find_ffmpeg_executable() is None:
        pytest.skip("FFmpeg is unavailable in this test environment")
    sample_rate = 24000
    seconds = 2.0
    time_axis = np.arange(round(sample_rate * seconds), dtype=np.float32) / sample_rate
    original = 0.25 * np.sin(2 * np.pi * 440.0 * time_axis)
    output = tmp_path / "qwen.wav"
    sf.write(output, original, sample_rate, subtype="PCM_16")

    assert generation._apply_qwen_output_speed(output, 0.90) is True
    adjusted, adjusted_rate = sf.read(output, dtype="float32")

    assert adjusted_rate == sample_rate
    assert len(adjusted) == pytest.approx(len(original) / 0.90, rel=0.03)
    spectrum = np.abs(np.fft.rfft(adjusted))
    frequencies = np.fft.rfftfreq(len(adjusted), 1 / adjusted_rate)
    dominant_hz = frequencies[int(np.argmax(spectrum))]
    assert dominant_hz == pytest.approx(440.0, abs=3.0)


def test_qwen_speed_one_is_a_lossless_noop(tmp_path: Path) -> None:
    output = tmp_path / "qwen.wav"
    payload = b"unchanged"
    output.write_bytes(payload)

    assert generation._apply_qwen_output_speed(output, 1.0) is False
    assert output.read_bytes() == payload


def test_voxcpm_speed_uses_shared_pitch_preserving_postprocess(tmp_path: Path) -> None:
    import soundfile as sf

    if generation._find_ffmpeg_executable() is None:
        pytest.skip("FFmpeg is unavailable in this test environment")
    sample_rate = 48000
    original = np.zeros(sample_rate * 2, dtype=np.float32)
    output = tmp_path / "voxcpm.wav"
    sf.write(output, original, sample_rate, subtype="PCM_16")

    assert generation._apply_mlx_output_speed(output, 0.95, "voxcpm-mlx") is True
    adjusted = sf.info(output)

    assert adjusted.samplerate == sample_rate
    assert adjusted.frames == pytest.approx(len(original) / 0.95, rel=0.03)


def test_voxcpm_long_form_speed_runs_once_after_join(
    tmp_path: Path, monkeypatch
) -> None:
    import importlib
    import soundfile as sf

    generate_module = importlib.import_module("mlx_audio.tts.generate")
    generated_sections: list[str] = []
    tempo_calls: list[tuple[Path, float, str]] = []

    def fake_generate_audio(*, model, output_path, join_audio, **kwargs) -> None:
        assert model == "voxcpm-model"
        assert join_audio is True
        generated_sections.append(kwargs["text"])
        sf.write(
            Path(output_path) / "audio.wav",
            np.ones(480, dtype=np.float32),
            48000,
            subtype="PCM_16",
        )

    def fake_apply_speed(path: Path, speed: float, family: str) -> bool:
        assert path.exists()
        tempo_calls.append((path, speed, family))
        return True

    monkeypatch.setattr(generate_module, "generate_audio", fake_generate_audio)
    monkeypatch.setattr(generation, "_apply_mlx_output_speed", fake_apply_speed)
    monkeypatch.setattr(generation, "_release_device_memory", lambda device: None)

    manager = object.__new__(generation.GenerationManager)
    manager._mlx_audio_get_model = lambda repo: "voxcpm-model"
    manager._resolve_mlx_kwargs = (
        lambda mode, family, model_entry, params, gen_kwargs: "zero-shot"
    )
    text = " ".join(
        f"Sentence {index} stays safely inside the VoxCPM long-form renderer."
        for index in range(1, 25)
    )
    job = generation.GenerationJob(
        job_id="voxcpm-long-speed",
        mode="txt2speech",
        params={"text": text, "speed": 0.93, "seed": 7},
    )
    output = tmp_path / "voxcpm-long.wav"

    manager._generate_mlx_audio(
        job,
        SimpleNamespace(
            repo="mlx-community/VoxCPM2-4bit",
            family="voxcpm-mlx",
            sample_rate_hz=48000,
        ),
        output,
    )

    assert len(generated_sections) > 1
    assert " ".join(generated_sections) == " ".join(text.split())
    assert tempo_calls == [(output, 0.93, "voxcpm-mlx")]
    assert sf.info(output).frames > 480


def test_qwen_regular_long_form_speed_runs_once_after_join(
    tmp_path: Path, monkeypatch
) -> None:
    import importlib
    import soundfile as sf

    generate_module = importlib.import_module("mlx_audio.tts.generate")
    generated_sections: list[str] = []
    tempo_calls: list[tuple[Path, float, str]] = []

    def fake_generate_audio(*, model, output_path, join_audio, **kwargs) -> None:
        assert model == "qwen-regular-model"
        assert join_audio is True
        generated_sections.append(kwargs["text"])
        sf.write(
            Path(output_path) / "audio.wav",
            np.ones(240, dtype=np.float32),
            24000,
            subtype="PCM_16",
        )

    def fake_apply_speed(path: Path, speed: float, family: str) -> bool:
        assert path.exists()
        tempo_calls.append((path, speed, family))
        return True

    monkeypatch.setattr(generate_module, "generate_audio", fake_generate_audio)
    monkeypatch.setattr(generation, "_apply_mlx_output_speed", fake_apply_speed)
    monkeypatch.setattr(generation, "_release_device_memory", lambda device: None)

    manager = object.__new__(generation.GenerationManager)
    manager._mlx_audio_get_model = lambda repo: "qwen-regular-model"
    manager._resolve_mlx_kwargs = (
        lambda mode, family, model_entry, params, gen_kwargs: "custom (speaker=Ryan)"
    )
    text = " ".join(
        f"Sentence {index} stays safely inside the Qwen preset long-form renderer."
        for index in range(1, 25)
    )
    job = generation.GenerationJob(
        job_id="qwen-regular-long-speed",
        mode="txt2speech",
        params={"text": text, "speed": 0.95, "seed": 11, "preset_speaker": "Ryan"},
    )
    output = tmp_path / "qwen-regular-long.wav"

    manager._generate_mlx_audio(
        job,
        SimpleNamespace(
            repo="mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit",
            family="qwen3-tts",
            sample_rate_hz=24000,
        ),
        output,
    )

    assert len(generated_sections) > 1
    assert " ".join(generated_sections) == " ".join(text.split())
    assert tempo_calls == [(output, 0.95, "qwen3-tts")]
    assert sf.info(output).frames > 240


def test_fish_audio_long_form_passes_no_speed_upstream_and_postprocesses_once(
    tmp_path: Path, monkeypatch
) -> None:
    """Fish is a tempo family: it must not be handed `speed` at all.

    It used to receive ``speed=1.0``. That looked harmless, but making the engine
    argument part of the contract let the engine and the public control disagree.
    The dispatcher now passes the argument only to native families and applies
    exactly one pitch-preserving pass to the finished artifact.
    """
    import importlib
    import soundfile as sf

    generate_module = importlib.import_module("mlx_audio.tts.generate")
    generated_sections: list[str] = []
    tempo_calls: list[tuple[Path, float, str]] = []

    def fake_generate_audio(*, model, output_path, join_audio, **kwargs) -> None:
        assert model == "fish-model"
        assert join_audio is True
        assert "speed" not in kwargs
        assert kwargs["chunk_length"] == 100000
        generated_sections.append(kwargs["text"])
        sf.write(
            Path(output_path) / "audio.wav",
            np.ones(441, dtype=np.float32),
            44100,
            subtype="PCM_16",
        )

    def fake_apply_speed(path: Path, speed: float, family: str) -> bool:
        assert path.exists()
        tempo_calls.append((path, speed, family))
        return True

    monkeypatch.setattr(generate_module, "generate_audio", fake_generate_audio)
    monkeypatch.setattr(generation, "_apply_mlx_output_speed", fake_apply_speed)
    monkeypatch.setattr(generation, "_release_device_memory", lambda device: None)

    manager = object.__new__(generation.GenerationManager)
    manager._mlx_audio_get_model = lambda repo: "fish-model"
    manager._resolve_mlx_kwargs = (
        lambda mode, family, model_entry, params, gen_kwargs: "clone + style"
    )
    text = " ".join(
        f"Sentence {index} stays safely inside the Fish long-form renderer."
        for index in range(1, 80)
    )
    job = generation.GenerationJob(
        job_id="fish-long-speed",
        mode="txt2speech",
        params={"text": text, "speed": 0.97, "seed": 23},
    )
    output = tmp_path / "fish-long.wav"

    assert long_form_policy.speed_policy_for("fish-audio-mlx").delivery == "tempo"

    manager._generate_mlx_audio(
        job,
        SimpleNamespace(
            repo="mlx-community/fish-audio-s2-pro-8bit",
            family="fish-audio-mlx",
            sample_rate_hz=44100,
        ),
        output,
    )

    assert len(generated_sections) > 1
    assert " ".join(generated_sections) == " ".join(text.split())
    assert tempo_calls == [(output, 0.97, "fish-audio-mlx")]
    assert sf.info(output).frames > 441


def test_qwen_duration_ceiling_accounts_for_final_speed() -> None:
    assert generation._qwen_max_tokens_for_duration(30.0, 1.0) == 375
    assert generation._qwen_max_tokens_for_duration(30.0, 0.5) == 187
    assert generation._qwen_max_tokens_for_duration(30.0, 2.0) == 750


def test_requested_duration_ceiling_rejects_long_wav(tmp_path: Path) -> None:
    import numpy as np
    import soundfile as sf

    output = tmp_path / "too-long.wav"
    sf.write(output, np.zeros(30_010, dtype=np.float32), 1000, subtype="PCM_16")

    with pytest.raises(RuntimeError, match="exceeding the requested"):
        generation._enforce_output_duration_limit(
            output,
            {"max_output_duration_s": 30.0},
        )


def test_requested_duration_ceiling_accepts_exact_limit(tmp_path: Path) -> None:
    import numpy as np
    import soundfile as sf

    output = tmp_path / "exact.wav"
    sf.write(output, np.zeros(30_000, dtype=np.float32), 1000, subtype="PCM_16")

    generation._enforce_output_duration_limit(
        output,
        {"max_output_duration_s": 30.0},
    )


def test_tts_request_has_no_default_generated_audio_duration_cap() -> None:
    from backend import main

    body = main.Txt2SpeechBody(
        repo="mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit",
        text="Long-form narration. " * 1_000,
        client_request_id="studiohub:stable-long-form-attempt",
        speed=0.9,
    )

    assert body.max_output_duration_s is None
    assert body.client_request_id == "studiohub:stable-long-form-attempt"
    assert body.speed == 0.9
    assert generation._requested_output_duration_limit(body.model_dump()) is None


def test_qwen_and_voxcpm_speed_control_is_visible_and_truthful() -> None:
    markup = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()

    assert "qwen3Mode(gen.repo) !== 'clone'" not in markup
    assert "!isVoxCPMMlx(gen.repo)" not in markup
    assert (
        "Qwen, VoxCPM2, VibeVoice, Fish S2 Pro, and OmniVoice preserve pitch"
        in markup
    )
    assert "after all sections are joined" in markup
    # OmniVoice used to be hidden from the speed control while the backend still
    # handed `speed` to a generate() that silently discards it, so the public
    # control was a no-op for API callers. It must stay visible.
    assert "!isOmniVoice(gen.repo)" not in markup


def test_omnivoice_speed_is_applied_to_the_finished_wav_not_passed_upstream() -> None:
    """mlx-audio's OmniVoice.generate() has no `speed` parameter -- it declares
    **kwargs and never reads it. Speed must therefore be applied as a
    pitch-preserving tempo pass on the finished WAV, and must never be handed to
    the engine where it vanishes.

    The authority is now `long_form_policy.speed_policy_for(family)`, published
    per model in the catalogue, rather than a second family table in generation.py
    that could drift from it.
    """
    assert long_form_policy.speed_policy_for("omnivoice").delivery == "tempo"

    src = inspect.getsource(generation.GenerationManager._generate_mlx_audio)
    assert 'if _speed_delivery(family) == "native":' in src
    assert 'gen_kwargs["speed"] = speed' in src
    # The upstream assignment is guarded by the policy, so no tempo family can
    # reach it by being absent from a hand-maintained set.
    assert src.split('gen_kwargs["speed"] = speed', 1)[0].rstrip().endswith(
        'if _speed_delivery(family) == "native":'
    )
    # The two native-speed engines (kokoro-mlx, kittentts) were cut on
    # 2026-09-24 (ST-13.B), so no catalog family applies speed natively now.
    native = {
        family
        for family in catalog.FAMILIES
        if long_form_policy.speed_policy_for(family).delivery == "native"
    }
    assert native == set()


def test_qwen_clone_long_form_renders_each_section_and_reports_progress(tmp_path: Path) -> None:
    import numpy as np
    import soundfile as sf

    calls: list[str] = []

    def fake_generate_audio(*, model, output_path, join_audio, **kwargs) -> None:
        assert model == "qwen-model"
        assert join_audio is True
        calls.append(kwargs["text"])
        sf.write(Path(output_path) / "audio.wav", np.ones(10, dtype=np.float32), 1000)

    manager = object.__new__(generation.GenerationManager)
    job = generation.GenerationJob(job_id="qwen-long", mode="txt2speech", params={})
    chunks = ["First short section.", "Second short section."]
    output = tmp_path / "output.wav"
    manager._generate_qwen_clone_long_form(
        job, "qwen-model", {"text": "ignored", "ref_audio": "voice.wav"},
        chunks, tmp_path, output, fake_generate_audio,
    )

    assert calls == chunks
    assert (job.chunk_index, job.chunk_total) == (2, 2)
    assert 0.92 < job.progress < 0.94
    assert output.exists()


def test_shared_long_form_renderer_fails_when_any_section_is_missing(tmp_path: Path) -> None:
    import soundfile as sf

    calls = 0

    def incomplete_generate_audio(*, model, output_path, join_audio, **kwargs) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            sf.write(Path(output_path) / "audio.wav", np.ones(10, dtype=np.float32), 1000)

    manager = object.__new__(generation.GenerationManager)
    job = generation.GenerationJob(job_id="missing-section", mode="txt2speech", params={})

    # A missing section must still fail loudly rather than joining silence. The
    # wording changed in v1.32.3 to name the cause instead of a temp path.
    with pytest.raises(RuntimeError, match="produced no audio"):
        manager._generate_mlx_long_form_sections(
            job,
            "voxcpm-mlx",
            "voxcpm-model",
            {"text": "ignored", "ref_audio": "voice.wav"},
            ["First section.", "Second section."],
            tmp_path,
            tmp_path / "output.wav",
            incomplete_generate_audio,
        )

    assert not (tmp_path / "output.wav").exists()


def test_chatterbox_controls_match_standard_and_turbo_engines(tmp_path: Path) -> None:
    reference = tmp_path / "voice.wav"
    reference.touch()
    voices = SimpleNamespace(library=_VoiceLibrary(reference))
    manager = object.__new__(generation.GenerationManager)
    params = {
        "voice_library_id": "voice-1",
        "language": "en",
        "temperature": 1.1,
        "cfg_value": 0.7,
        "chatterbox_cfg_weight": 0.6,
        "chatterbox_repetition_penalty": 1.3,
        "chatterbox_min_p": 0.1,
        "chatterbox_top_p": 0.9,
    }

    standard: dict = {}
    manager._mlx_kwargs_clone_with_intensity(
        SimpleNamespace(repo="mlx-community/chatterbox-8bit"),
        params,
        standard,
        voices,
    )
    assert standard == {
        "ref_audio": str(reference),
        "ref_text": "Saved reference transcript",
        "lang_code": "en",
        "exaggeration": 0.7,
        "temperature": 1.1,
        "repetition_penalty": 1.3,
        "top_p": 0.9,
        "cfg_weight": 0.6,
        "min_p": 0.1,
    }

    turbo: dict = {}
    manager._mlx_kwargs_clone_with_intensity(
        SimpleNamespace(repo="mlx-community/chatterbox-turbo-4bit"),
        params,
        turbo,
        voices,
    )
    assert "exaggeration" not in turbo
    assert "cfg_weight" not in turbo
    assert "min_p" not in turbo
    assert turbo["temperature"] == 1.1


def test_omnivoice_mlx_supports_clone_plus_traits_and_clamps(tmp_path: Path) -> None:
    reference = tmp_path / "voice.wav"
    reference.touch()
    voices = SimpleNamespace(library=_VoiceLibrary(reference))
    manager = object.__new__(generation.GenerationManager)
    kwargs: dict = {}

    label = manager._mlx_kwargs_omnivoice(
        {
            "voice_library_id": "voice-1",
            "voice_design_prompt": "female, warm, khmer accent",
            "ref_transcript": "Override transcript",
            "omnivoice_num_steps": 100,
            "omnivoice_guidance_scale": 99,
            "omnivoice_duration_s": 240,
        },
        kwargs,
        voices,
    )

    assert label.startswith("combined")
    assert kwargs["ref_audio"] == str(reference)
    assert kwargs["ref_text"] == "Override transcript"
    assert kwargs["instruct"] == "female, warm, khmer accent"
    assert kwargs["num_steps"] == 64
    assert kwargs["guidance_scale"] == 8.0
    assert kwargs["duration_s"] == 120.0


def test_fish_audio_mlx_supports_optional_clone_style_and_clamps(tmp_path: Path) -> None:
    reference = tmp_path / "voice.wav"
    reference.touch()
    voices = SimpleNamespace(library=_VoiceLibrary(reference, transcript=""))
    manager = object.__new__(generation.GenerationManager)
    kwargs: dict = {}

    label = manager._mlx_kwargs_fish_audio(
        {
            "voice_library_id": "voice-1",
            "voice_design_prompt": "warm, intimate audiobook narration",
            "fish_temperature": 9,
            "fish_top_p": 0,
            "fish_top_k": 999,
            "fish_max_tokens": 9999,
        },
        kwargs,
        voices,
    )

    assert label.startswith("clone + style")
    assert kwargs["ref_audio"] == str(reference)
    assert kwargs["ref_text"] == ""
    assert kwargs["instruct"] == "warm, intimate audiobook narration"
    assert kwargs["temperature"] == 2.0
    assert kwargs["top_p"] == 0.05
    assert kwargs["top_k"] == 100
    assert kwargs["max_tokens"] == 4096


def test_no_wav_error_names_the_real_cause_not_just_a_temp_dir(monkeypatch, tmp_path) -> None:
    """The bare "mlx-audio didn't produce a wav file. Temp dir: ..." is true and
    useless. On a small machine an oversized OmniVoice request dies mid-inference
    with no exception of its own, and the owner was shown a temp path. The error
    must name what was attempted, what the host had, and what actually fits."""
    monkeypatch.setattr(
        generation, "_memory_snapshot",
        lambda: {"total_gb": 8.6, "available_gb": 4.2, "used_gb": 4.4, "percent": 51.0},
    )
    err = generation._no_wav_produced_error(
        tmp_path, "omnivoice", {"duration_s": 120.0, "text": "x" * 40}
    )
    msg = str(err)
    assert "3000 latent frames" in msg          # what was attempted
    assert "8.6 GB unified memory" in msg       # what the host had
    assert "2250 frames" in msg                 # what actually fits, measured
    assert "shorten the section" in msg         # what to do about it

    # A machine with headroom must not be told it is too small.
    monkeypatch.setattr(
        generation, "_memory_snapshot",
        lambda: {"total_gb": 25.8, "available_gb": 18.0, "used_gb": 7.8, "percent": 30.0},
    )
    big = str(generation._no_wav_produced_error(
        tmp_path, "omnivoice", {"duration_s": 120.0}))
    assert "2250 frames" not in big
    assert "25.8 GB unified memory" in big


def test_fish_4bit_is_a_16gb_fish_row_with_its_licence_and_revision_evidence() -> None:
    """Fish S2 Pro 4-bit rides the 8-bit row's engine and section policy, on the 16 GB tier (owner, 2026-10-02).

    Its publisher spells the repo `fishaudio-s2-pro`, so the local-revision evidence branch (keyed on
    the repo name) must still recognise it: without that a finished clone job has no voice evidence.
    """
    import json

    model = catalog.get_model("majentik/fishaudio-s2-pro-MLX-4bit")
    assert model is not None and model.family == "fish-audio-mlx"
    assert model.min_unified_memory_gb == 16
    assert model.sample_rate_hz == 44100
    assert model.license_name == "Fish Audio Research License"
    assert "Built with Fish Audio" in model.license_source and "commercial" in model.license_source
    serialized = catalog.serialize_model(model)
    assert serialized["license"]["name"] == "Fish Audio Research License"

    manifest = json.loads((Path(__file__).resolve().parents[4] / "model-manifests"
                           / "fish-audio-s2-pro-4bit.json").read_text())
    assert manifest["model_id"] == model.repo
    assert manifest["qualification"]["minimum_unified_memory_gb"] == model.min_unified_memory_gb
    assert round(sum(f["size"] for f in manifest["files"]) / 1e9, 2) == model.size_gb

    job = generation.GenerationJob(job_id="fish4-evidence", mode="txt2speech", params={
        "repo": model.repo, "_reference_audio_sha256": "a" * 64})
    original = generation.cache.snapshot_revision
    generation.cache.snapshot_revision = lambda repo: manifest["revision"]
    try:
        generation.GenerationManager._record_local_revision_evidence(job)
    finally:
        generation.cache.snapshot_revision = original
    assert job.model_revision == manifest["revision"]
    assert job.voice_revision == "a" * 64
