"""Static catalogue of music-generation models for MusicStudio (ACE-Step 1.5).

This mirrors the shape the Voice catalogue already uses — frozen dataclass
entries, a ``CATALOG`` tuple, ``get_model`` and ``families`` — rather than
inventing a second descriptor format.

Facts in this file are read from the ACE-Step 1.5 checkout that produced the
working 30 s generation (``~/Projects/ace-step-probe/ACE-Step-1.5``), not
guessed:

* the per-tier duration caps come from ``acestep/gpu_config.py:GPU_TIER_CONFIGS``;
* the checkpoint names and repo ids come from
  ``acestep/model_downloader.py:MAIN_MODEL_REPO`` / ``SUBMODEL_REGISTRY``;
* the output format comes from the checkpoint VAE config
  (``checkpoints/vae/config.json``: ``sampling_rate 48000``,
  ``audio_channels 2``);
* the licence name is the first line of the checkout's ``LICENSE``
  ("MIT License"), which the model card repeats as ``License: MIT``.

Per-tier duration policy is not a single number: the engine clamps whatever
duration is requested to the cap for the tier the host memory lands in. On the
owner's 11.8 GB Mac the probe landed in ``tier4`` and the log recorded
"constrained decoding max_duration to 480s based on GPU config (tier: tier4)",
so 480 s is the working single-pass ceiling here and 600 s is the theoretical
one for a machine with enough memory.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Exact source revisions.  Both were read from the local Hugging Face cache
# refs written when the checkpoints were fetched, and confirmed against the
# Hub API; every local checkpoint file was hashed and matches the LFS oid the
# Hub publishes at these revisions.
# ---------------------------------------------------------------------------
MAIN_REPO = "ACE-Step/Ace-Step1.5"
MAIN_REVISION = "19671f406d603126926c1b7e2adc169acbcade22"
LM_06B_REPO = "ACE-Step/acestep-5Hz-lm-0.6B"
LM_06B_REVISION = "148d8ea0225bdab342ee1ae3a354275ccd60ca80"

# The package the local manifest describes. Periods are normalized to hyphens
# to match the model package identifier contract.
PACKAGE_ID = "ace-step-1-5-turbo"
ENGINE = "music-mlx"

# ---------------------------------------------------------------------------
# Tier table — verbatim from acestep/gpu_config.py:GPU_TIER_CONFIGS
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TierLimit:
    """One row of the engine's hardware tier table.

    ``min_unified_memory_gb`` is the lower bound of the tier's range, which is
    how ``get_tier`` selects it.  The duration caps are the engine's own
    ``max_duration_with_lm`` / ``max_duration_without_lm`` values in seconds;
    the engine clamps a longer request to the cap rather than refusing it.
    """

    tier: str
    label: str
    min_unified_memory_gb: Optional[int]
    max_duration_with_lm_s: int
    max_duration_without_lm_s: int
    max_batch_size_with_lm: int
    max_batch_size_without_lm: int
    available_lm_models: tuple[str, ...]
    recommended_lm_model: str
    offload_dit_to_cpu_default: bool
    quantization_default: bool
    note: str


@dataclass(frozen=True)
class LongFormStrategy:
    """How to exceed the single-pass duration cap without lying about it."""

    single_pass_max_seconds: int
    single_pass_cap_source: str
    beyond_cap: str
    repaint_task_type: str
    repaint_mode: str
    repaint_strength: float
    repaint_latent_crossfade_frames: int
    repaint_wav_crossfade_sec: float
    note: str


@dataclass(frozen=True)
class Checkpoint:
    """One downloadable component of the engine.

    ``name`` is the on-disk directory name the engine expects under its
    checkpoint root.  ``size_gb`` is the exact published byte total, not a
    rounded marketing figure.
    """

    name: str
    kind: str
    repo: str
    revision: str
    size_gb: float
    size_bytes: int
    parameters_b: Optional[float]
    repo_path: str
    note: str


# The first package contains only the Turbo DiT used by the historical probe.
TURBO_DIT = Checkpoint(
    name="acestep-v15-turbo", kind="dit", repo=MAIN_REPO, revision=MAIN_REVISION,
    size_gb=4787825604 / 1e9, size_bytes=4787825604, parameters_b=2.0,
    repo_path="acestep-v15-turbo",
    note="2B DiT, CFG-distilled (guidance forced to 1.0), 8 diffusion steps. "
         "Very High quality tier in the upstream model zoo.")
VAE = Checkpoint(
    name="vae", kind="vae", repo=MAIN_REPO, revision=MAIN_REVISION,
    size_gb=337431388 / 1e9, size_bytes=337431388, parameters_b=None,
    repo_path="vae",
    note="AutoencoderOobleck; 48 kHz stereo, decoded with tiled VAE decode on "
         "this class of machine.")
TEXT_ENCODER = Checkpoint(
    name="Qwen3-Embedding-0.6B", kind="text-encoder", repo=MAIN_REPO,
    revision=MAIN_REVISION, size_gb=1191586416 / 1e9, size_bytes=1191586416,
    parameters_b=0.6, repo_path="Qwen3-Embedding-0.6B",
    note="Prompt/lyric text encoder bundled inside the main repo.")
LM_06B = Checkpoint(
    name="acestep-5Hz-lm-0.6B", kind="lm", repo=LM_06B_REPO,
    revision=LM_06B_REVISION, size_gb=1325804024 / 1e9, size_bytes=1325804024,
    parameters_b=0.6, repo_path="acestep-5Hz-lm-0.6B",
    note="5 Hz semantic-token LM (Qwen3-0.6B base). The only LM tier usable at "
         "tier4; responsible for the ~8 s audio-code phase in the probe.")
TIERS: tuple[TierLimit, ...] = (
    TierLimit("tier1", "tier1 (<=4 GB)", 0, 240, 360, 1, 1, (),
              "", True, True,
              "Offload mode; VAE decode falls back to CPU; no LM available."),
    TierLimit("tier2", "tier2 (4-6 GB)", 4, 480, 600, 1, 1, (),
              "", True, True, "Offload mode; no LM available."),
    TierLimit("tier3", "tier3 (6-8 GB)", 6, 480, 600, 2, 2,
              ("acestep-5Hz-lm-0.6B",), "acestep-5Hz-lm-0.6B", True, True,
              "First tier that can hold the 0.6B LM."),
    TierLimit("tier4", "tier4 (8-12 GB)", 8, 480, 600, 2, 4,
              ("acestep-5Hz-lm-0.6B",), "acestep-5Hz-lm-0.6B", True, True,
              "This Mac (11.8 GB unified) — the probe's tier. 480 s with LM."),
    TierLimit("tier5", "tier5 (12-16 GB)", 12, 480, 600, 4, 4,
              ("acestep-5Hz-lm-0.6B",),
              "acestep-5Hz-lm-0.6B", False, True,
              "Keeps DiT resident; this package still uses its verified 0.6B LM."),
    TierLimit("tier6a", "tier6a (16-20 GB)", 16, 480, 600, 4, 8,
              ("acestep-5Hz-lm-0.6B",),
              "acestep-5Hz-lm-0.6B", False, True, "Batch up to 8 without LM."),
    TierLimit("tier6b", "tier6b (20-24 GB)", 20, 480, 480, 8, 8,
              ("acestep-5Hz-lm-0.6B",),
              "acestep-5Hz-lm-0.6B", False, False,
              "No offload and no quantization; only the packaged 0.6B LM is offered."),
    TierLimit("unlimited", "unlimited (>=24 GB)", 24, 600, 600, 8, 8,
              ("acestep-5Hz-lm-0.6B",),
              "acestep-5Hz-lm-0.6B", False, False,
              "600 s is the engine's absolute supported maximum."),
)

# The long-form policy the engine itself implements: one pass at a duration up
# to the tier cap, then ``task_type="repaint"`` with an explicit window and a
# latent-level crossfade to extend past it.  Values are the engine's documented
# defaults (GenerationParams in acestep/inference.py).
LONG_FORM = LongFormStrategy(
    single_pass_max_seconds=480,
    single_pass_cap_source="acestep/gpu_config.py:GPU_TIER_CONFIGS[tier4]"
                           ".max_duration_with_lm (log: 'constrained decoding "
                           "max_duration to 480s based on GPU config (tier: tier4)')",
    beyond_cap="Repaint: generate the first window, then task_type='repaint' over "
               "repainting_start/repainting_end windows with chunk_mask_mode='auto', "
               "blending the boundary in latent space and (optionally) in the waveform.",
    repaint_task_type="repaint",
    repaint_mode="balanced",
    repaint_strength=0.5,
    repaint_latent_crossfade_frames=10,
    repaint_wav_crossfade_sec=0.0,
    note="The worker only implements the single pass today; the catalogue records "
         "the repaint strategy so a long-form request is never promised as one pass.",
)


@dataclass(frozen=True)
class Family:
    id: str
    label: str
    summary: str
    how_to_use: str


FAMILIES: dict[str, Family] = {
    "ace-step-1.5": Family(
        id="ace-step-1.5",
        label="ACE-Step 1.5",
        summary=(
            "Open-source text-to-music foundation model from ACE Studio and "
            "StepFun. A CFG-distilled DiT generates the audio while a 5 Hz "
            "semantic LM plans duration, structure and, when asked, lyrics and "
            "metadata. MIT licensed and trained on licensed/royalty-free/synthetic "
            "audio, so output is intended to be commercially usable."
        ),
        how_to_use=(
            "Describe the music in a caption (genre, instruments, mood, tempo). "
            "Instrumentals set instrumental=true with lyrics='[Instrumental]'. "
            "Request a duration up to the tier cap; anything longer needs the "
            "repaint strategy documented on the entry. First generation on a "
            "cold worker pays a one-time ~3 minute model load."
        ),
    ),
}


@dataclass(frozen=True)
class ModelEntry:
    repo: str
    label: str
    family: str
    checkpoint: str
    size_gb: float
    gated: bool
    min_unified_memory_gb: Optional[int] = 8
    recommended_hardware: str = ""
    capabilities: tuple[str, ...] = ("music", "instrumental", "text-to-music")
    best_for: str = ""
    sample_rate_hz: int = 48000
    channels: int = 2
    license_name: str = "MIT License"
    license_source: str = "ACE-Step-1.5/LICENSE"
    checkpoints: tuple[Checkpoint, ...] = field(default_factory=tuple)
    dit_tiers: tuple[Checkpoint, ...] = field(default_factory=tuple)
    lm_tiers: tuple[Checkpoint, ...] = field(default_factory=tuple)
    tiers: tuple[TierLimit, ...] = TIERS
    long_form: LongFormStrategy = LONG_FORM
    # Default LM the worker loads at the current tier.  The engine's own
    # recommendation for tier4, kept explicit so the HTTP surface can report it.
    default_lm_model: str = "acestep-5Hz-lm-0.6B"
    inference_steps: int = 8
    guidance_scale: float = 1.0
    # huggingface_hub-style glob patterns to skip during a download.
    ignore_patterns: tuple[str, ...] = ()


CATALOG: tuple[ModelEntry, ...] = (
    ModelEntry(
        repo=MAIN_REPO,
        label="ACE-Step 1.5 Turbo (2B DiT)",
        family="ace-step-1.5",
        checkpoint="acestep-v15-turbo",
        # Exact manifest byte total: Turbo DiT, VAE, text encoder and the
        # separately published 0.6B planner.
        size_gb=7709268688 / 1e9,
        gated=False,
        min_unified_memory_gb=8,
        recommended_hardware=(
            "Apple Silicon with 16 GB unified memory; runs at tier4 on this "
            "11.8 GB Mac, which is also where the 30 s probe succeeded."),
        capabilities=("music", "instrumental", "text-to-music", "repaint",
                      "cover", "lyrics", "long-form"),
        best_for=(
            "Text-to-music and instrumental beds with a small footprint: the "
            "2B turbo DiT plus the 0.6B planner fits tier4 and completes a 30 s "
            "clip in ~89 s of generation after a one-time model load. 8 diffusion "
            "steps, guidance forced to 1.0 because turbo is CFG-distilled."),
        sample_rate_hz=48000,
        channels=2,
        license_name="MIT License",
        license_source="LICENSE, line 1: 'MIT License'; model card: 'License: MIT'",
        checkpoints=(TURBO_DIT, VAE, TEXT_ENCODER, LM_06B),
        dit_tiers=(TURBO_DIT,),
        lm_tiers=(LM_06B,),
        default_lm_model="acestep-5Hz-lm-0.6B",
        ignore_patterns=(
            # The engine rewrites these two from the installed wheel's
            # acestep/models/turbo/ on first init; they are not checkpoint
            # identity and are deliberately not part of the package file list.
            "acestep-v15-turbo/configuration_acestep_v15.py",
            "acestep-v15-turbo/modeling_acestep_v15_turbo.py",
        ),
    ),
)


def get_model(repo: str) -> Optional[ModelEntry]:
    for model in CATALOG:
        if model.repo == repo:
            return model
    return None


def get_checkpoint(name: str) -> Optional[Checkpoint]:
    for model in CATALOG:
        for checkpoint in model.checkpoints:
            if checkpoint.name == name:
                return checkpoint
    return None


def families() -> tuple[Family, ...]:
    return tuple(FAMILIES.values())


def serialize_tier(tier: TierLimit) -> dict:
    return {
        "tier": tier.tier,
        "label": tier.label,
        "min_unified_memory_gb": tier.min_unified_memory_gb,
        "max_duration_with_lm_s": tier.max_duration_with_lm_s,
        "max_duration_without_lm_s": tier.max_duration_without_lm_s,
        "max_batch_size_with_lm": tier.max_batch_size_with_lm,
        "max_batch_size_without_lm": tier.max_batch_size_without_lm,
        "available_lm_models": list(tier.available_lm_models),
        "recommended_lm_model": tier.recommended_lm_model,
        "offload_dit_to_cpu_default": tier.offload_dit_to_cpu_default,
        "quantization_default": tier.quantization_default,
        "note": tier.note,
    }


def serialize_checkpoint(checkpoint: Checkpoint) -> dict:
    return {
        "name": checkpoint.name,
        "kind": checkpoint.kind,
        "repo": checkpoint.repo,
        "revision": checkpoint.revision or None,
        "size_gb": round(checkpoint.size_gb, 4),
        "size_bytes": checkpoint.size_bytes,
        "parameters_b": checkpoint.parameters_b,
        "repo_path": checkpoint.repo_path,
        "note": checkpoint.note,
    }


def serialize_long_form(strategy: LongFormStrategy) -> dict:
    return {
        "single_pass_max_seconds": strategy.single_pass_max_seconds,
        "single_pass_cap_source": strategy.single_pass_cap_source,
        "beyond_cap": strategy.beyond_cap,
        "repaint_task_type": strategy.repaint_task_type,
        "repaint_mode": strategy.repaint_mode,
        "repaint_strength": strategy.repaint_strength,
        "repaint_latent_crossfade_frames": strategy.repaint_latent_crossfade_frames,
        "repaint_wav_crossfade_sec": strategy.repaint_wav_crossfade_sec,
        "note": strategy.note,
    }


def serialize_model(model: ModelEntry) -> dict:
    return {
        "repo": model.repo,
        "label": model.label,
        "family": model.family,
        "checkpoint": model.checkpoint,
        "size_gb": round(model.size_gb, 4),
        "gated": model.gated,
        "min_unified_memory_gb": model.min_unified_memory_gb,
        "recommended_hardware": model.recommended_hardware,
        "capabilities": list(model.capabilities),
        "best_for": model.best_for,
        "sample_rate_hz": model.sample_rate_hz,
        "channels": model.channels,
        "license": {"name": model.license_name, "source": model.license_source},
        "checkpoints": [serialize_checkpoint(item) for item in model.checkpoints],
        "dit_tiers": [serialize_checkpoint(item) for item in model.dit_tiers],
        "lm_tiers": [serialize_checkpoint(item) for item in model.lm_tiers],
        "tiers": [serialize_tier(item) for item in model.tiers],
        "long_form": serialize_long_form(model.long_form),
        "default_lm_model": model.default_lm_model,
        "inference_steps": model.inference_steps,
        "guidance_scale": model.guidance_scale,
        "ignore_patterns": list(model.ignore_patterns),
    }


def serialize_family(family: Family) -> dict:
    return {
        "id": family.id,
        "label": family.label,
        "summary": family.summary,
        "how_to_use": family.how_to_use,
    }
