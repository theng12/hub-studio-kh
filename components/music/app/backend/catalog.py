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
ENGINE_REPO = "ace-step/ACE-Step-1.5"
ENGINE_REVISION = "ca1e85fe9430179831e6bc6be790c332190a3866"
ENGINE_SOURCE_SHA256 = "a55b4bf166464a690e967b4a31862e39efc6bd954ef0214ac91030d99e97cb33"
ENGINE_MODEL_CODE_SHA256 = {
    "acestep/models/turbo/configuration_acestep_v15.py":
        "5f15aa79fe793bba3c00b6992dd193a269954950603d5031ecd7703b0fa69da1",
    "acestep/models/turbo/modeling_acestep_v15_turbo.py":
        "71b22e01b490bd9e149e4c0c5da2e1f512126f3ab3316e0ae97779af84d19512",
}

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
# ── Stable Audio 3 (MLX) ────────────────────────────────────────────────────
# A second music engine: Stability's latent-diffusion model, run through the
# vendored pure-MLX runtime under ``components/music/engines/stable_audio_3``.
# Instrumental only (no vocals); 44.1 kHz stereo; 380 s absolute maximum.
# ── The third engine: YuE2, in its own pinned runtime ─────────────────────
#
# `mlx-Yue` pins `mlx==0.32.2` while this worker's own runtime (the Voice runtime plus the Music
# overlay) carries `mlx==0.31.2`, so the engine runs in a separate interpreter and this worker
# spawns it — the same shape the Video lane uses for its two engines. The tier below is MEASURED on
# this Mac, not read from a card: the model card publishes no memory table at all.
YUE2_REPO = "vanch007/mlx-Yue2-3B"
YUE2_REVISION = "fa66d203dd56d7e033e05ee8b32269768984c4cc"
YUE2_VAE_REPO = "m-a-p/YuE2-Vae"
YUE2_VAE_REVISION = "9a94e1d0ea9f8087e98f77fa88df4a4068104d2a"
# The port's own source, pinned at an exact commit — it is the runner, so its revision is part of
# this model's identity the way a package revision is for an MLX engine.
YUE2_PORT_REPO = "vanch007/mlx-Yue"
YUE2_PORT_COMMIT = "9253ed133343406947bde7b67d43c7a63fb39d99"
# What the port's AR stage emits per second of 48 kHz audio, measured: 3000 tokens produced 120.0 s
# and 600 produced 24.0 s, so ~25 tokens per second. Published nowhere by the port; this is the
# constant the worker converts a caller's seconds with, and the receipt is
# `qualification/yue2-music-results-20260927/`.
YUE2_TOKENS_PER_SECOND = 25

STABLE_AUDIO_REPO = "stabilityai/stable-audio-3-optimized"
STABLE_AUDIO_REVISION = "da6edc54ddba10bfd79a077102ded687f80e882b"
SA3_MAX_DURATION_SECONDS = 380

# One Music worker serves more than one engine, so the engine is a property of the
# model itself (``ModelEntry.runtime``), not of a per-repo table.  generation.py maps
# these values to a request validator and a runner, and refuses an unknown value by
# naming the model and this file; add the engine there when you add the model.
RUNTIME_ACE_STEP = "ace-step"
RUNTIME_STABLE_AUDIO = "stable-audio"
# The engine's manifest token, so the Hub resolves its worker and modality the way it does for every
# other package, and the worker picks the interpreter from it.
RUNTIME_YUE2 = "music-yue2"

SA3_DIT_MEDIUM = Checkpoint(
    name="dit_medium", kind="dit", repo=STABLE_AUDIO_REPO,
    revision=STABLE_AUDIO_REVISION, size_gb=2907300946 / 1e9,
    size_bytes=2907300946, parameters_b=1.4, repo_path="MLX/dit_medium_f16.npz",
    note="1.4B medium DiT (sa3-medium-ARC), fp16 MLX. Higher quality than the "
         "small DiTs; paired with the SAME-L codec.")
SA3_SAME_L_ENCODER = Checkpoint(
    name="same_l_encoder", kind="codec", repo=STABLE_AUDIO_REPO,
    revision=STABLE_AUDIO_REVISION, size_gb=1704313504 / 1e9,
    size_bytes=1704313504, parameters_b=None,
    repo_path="MLX/same_l_encoder_f32.npz",
    note="SAME-Large encoder (fp32): 44.1 kHz stereo into 256-dim latents.")
SA3_SAME_L_DECODER = Checkpoint(
    name="same_l_decoder", kind="codec", repo=STABLE_AUDIO_REPO,
    revision=STABLE_AUDIO_REVISION, size_gb=1704311976 / 1e9,
    size_bytes=1704311976, parameters_b=None,
    repo_path="MLX/same_l_decoder_f32.npz",
    note="SAME-Large decoder (fp32); always FP32 because its differential "
         "attention cancels in fp16.")
SA3_T5GEMMA = Checkpoint(
    name="t5gemma", kind="text-encoder", repo=STABLE_AUDIO_REPO,
    revision=STABLE_AUDIO_REVISION, size_gb=567443068 / 1e9,
    size_bytes=567443068, parameters_b=0.3, repo_path="MLX/t5gemma_f16.npz",
    note="T5Gemma text encoder + tokenizer (fp16), redistributed under the "
         "Gemma Terms of Use.")
SA3_TIERS: tuple[TierLimit, ...] = (
    TierLimit("sa3", "any Apple Silicon (>=8 GB)", 8, SA3_MAX_DURATION_SECONDS,
              SA3_MAX_DURATION_SECONDS, 1, 1, (), "", False, False,
              "380 s is the model's absolute maximum."),
)
SA3_LONG_FORM = LongFormStrategy(
    single_pass_max_seconds=SA3_MAX_DURATION_SECONDS,
    single_pass_cap_source="stable-audio-3 model table: medium max length 380 s",
    beyond_cap="Not supported in one request: render in sections and join them "
               "outside the app.",
    repaint_task_type="", repaint_mode="", repaint_strength=0.0,
    repaint_latent_crossfade_frames=0, repaint_wav_crossfade_sec=0.0,
    note="Stable Audio 3 does not extend past 380 s in one request.",
)

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
            "metadata. MIT licensed, trained on licensed, royalty-free and "
            "synthetic audio."
        ),
        how_to_use=(
            "Describe the music in a caption (genre, instruments, mood, tempo). "
            "Instrumentals set instrumental=true with lyrics='[Instrumental]'. "
            "Request a duration up to the tier cap; anything longer needs the "
            "repaint strategy documented on the entry. First generation on a "
            "cold worker pays a one-time ~3 minute model load."
        ),
    ),
    "stable-audio-3": Family(
        id="stable-audio-3",
        label="Stable Audio 3",
        summary=(
            "Stability AI's latent-diffusion text-to-audio family, run here "
            "through its pure-MLX Apple-Silicon implementation. Instrumental "
            "music and sound at 44.1 kHz stereo, up to 380 s (6 min 20 s). "
            "Weights are under the Stability AI Community License."
        ),
        how_to_use=(
            "Describe the music in a caption (genre, instruments, mood, tempo). "
            "This engine is instrumental only - there is nothing to sing. Pick a "
            "duration up to 380 s; longer pieces need to be rendered in sections. "
            "It is much faster than ACE-Step: 30 s renders in about 9 s and 380 s "
            "in about 62 s on an M2 Max."
        ),
    ),
}


@dataclass(frozen=True)
class ModelEntry:
    """One music model: what it is, which package it runs from, and its contract.

    Every per-model fact is a required field.  The worker publishes these from the
    entry (``main.catalog_route``), verifies and loads the package named by
    ``package_id``/``package_root`` (``cache.MusicCache`` -> ``package_id_for`` /
    ``package_root_for``) and enforces the entry's own ``limits``.  A third model
    therefore cannot be added without its own package identity, revision and
    contract, and cannot inherit ACE-Step's.

    ``manifest_name`` is derived: the installer-owned manifest for a package is
    ``<package_id>.json`` (``cache.resolve_manifest_path``), and ``MusicCache``
    refuses a manifest whose ``package_id`` is not this model's.
    """

    repo: str
    label: str
    family: str
    checkpoint: str
    size_gb: float
    gated: bool
    # ── Package identity: the installed folder this model is verified from ──
    package_id: str
    package_root: str
    revision: str
    # ── The executor that runs it, one of the RUNTIME_* values above ──
    runtime: str
    # ── What the worker publishes and request validation enforces for it ──
    defaults: dict
    limits: dict
    qualification: dict
    min_unified_memory_gb: Optional[int]
    sample_rate_hz: int
    channels: int
    license_name: str
    license_source: str
    capabilities: tuple[str, ...]
    tiers: tuple[TierLimit, ...]
    long_form: LongFormStrategy
    # Default LM the worker loads at the current tier, from this model's engine.
    default_lm_model: str
    inference_steps: int
    guidance_scale: float
    # ── Optional prose and checkpoint detail; an honest empty means "none" ──
    recommended_hardware: str = ""
    best_for: str = ""
    checkpoints: tuple[Checkpoint, ...] = field(default_factory=tuple)
    dit_tiers: tuple[Checkpoint, ...] = field(default_factory=tuple)
    lm_tiers: tuple[Checkpoint, ...] = field(default_factory=tuple)
    # huggingface_hub-style glob patterns to skip during a download.
    ignore_patterns: tuple[str, ...] = ()
    # What one item of this model actually costs, where it has been measured. Published as the
    # lane contract's `runtime_seconds`; `None` means unmeasured, and an unmeasured model must
    # publish nothing rather than an estimate — a guessed runtime is how a scheduler sets a
    # deadline that fails on use.
    measured_runtime: Optional[dict] = None

    @property
    def manifest_name(self) -> str:
        """The installed manifest for this model's package: ``<package_id>.json``."""
        return f"{self.package_id}.json"


CATALOG: tuple[ModelEntry, ...] = (
    ModelEntry(
        repo=MAIN_REPO,
        label="ACE-Step 1.5 Turbo (2B DiT)",
        family="ace-step-1.5",
        checkpoint="acestep-v15-turbo",
        # Exact manifest byte total: Turbo DiT, VAE, text encoder and the
        # separately published 0.6B planner.
        size_gb=7709377612 / 1e9,
        gated=False,
        # The package the local manifest describes. Periods are normalized to
        # hyphens to match the model package identifier contract.
        package_id="ace-step-1-5-turbo",
        package_root="model",
        revision=MAIN_REVISION,
        runtime=RUNTIME_ACE_STEP,
        defaults={
            "duration_seconds": 30.0,
            "steps": 8,
            "guidance_scale": 1.0,
            "shift": 3.0,
            "seed": -1,
            "instrumental": True,
            "lyrics": "[Instrumental]",
            "vocal_language": "unknown",
            "dit_model": "acestep-v15-turbo",
            "lm_model": "acestep-5Hz-lm-0.6B",
        },
        limits={
            "min_duration_seconds": 1.0,
            # The engine's absolute supported maximum
            # (acestep/gpu_config.py:GPU_TIER_CONFIGS["unlimited"]); the per-tier cap
            # this host gets is 480 s at tier4 (see TIERS).
            "max_duration_seconds": 600.0,
            "single_pass_max_seconds": LONG_FORM.single_pass_max_seconds,
            "min_steps": 1,
            "max_steps": 50,
            "min_guidance": 0.0,
            "max_guidance": 30.0,
            "min_seed": -1,
            "max_seed": 2 ** 32 - 1,
        },
        qualification={
            "historical_source_workload":
                "30 s instrumental, 8 steps, turbo DiT + 0.6B LM, MLX backend, tier4",
            "packaged_inference": "unqualified; source contract only",
        },
        # Measured, not estimated: `qualification/evidence/r07-2026-09-21/` holds one JSON per
        # run with its own wall time. Published as the lane contract's `runtime_seconds` so a
        # scheduler sets its deadline from a number this hardware actually produced — the
        # per-run detail stays in the evidence, the contract carries the shape of it.
        measured_runtime={
            "measured": [
                {"duration_seconds": 30.0, "seconds": 97.35},
                {"duration_seconds": 60.0, "seconds": 104.71},
                {"duration_seconds": 360.0, "seconds": 256.41},
                {"duration_seconds": 20.0, "seconds": 106.65, "note": "with vocals"},
            ],
            "measured_on": "M2 Max 32 GB, ACE-Step 1.5 Turbo, 8 steps",
            "evidence": "measured",
        },
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
        tiers=TIERS,
        long_form=LONG_FORM,
        default_lm_model="acestep-5Hz-lm-0.6B",
        inference_steps=8,
        guidance_scale=1.0,
        # The two executable model files that upstream normally synchronizes
        # are package members instead. Studio Hub's adapter verifies equality
        # and refuses a mismatch; it never writes into the installed package.
        ignore_patterns=(),
    ),
    ModelEntry(
        repo=STABLE_AUDIO_REPO,
        label="Stable Audio 3 Medium (1.4B)",
        family="stable-audio-3",
        checkpoint="dit_medium",
        size_gb=6883369494 / 1e9,
        gated=False,
        package_id="stable-audio-3-medium",
        # Where the engine's own files sit inside the imported package folder.
        package_root="model/mlx",
        revision=STABLE_AUDIO_REVISION,
        runtime=RUNTIME_STABLE_AUDIO,
        defaults={
            "duration_seconds": 30,
            "steps": 8,
            "guidance_scale": 1.0,
            "seed": -1,
            "dit_model": "dit_medium",
        },
        limits={
            "min_duration_seconds": 1,
            "max_duration_seconds": SA3_MAX_DURATION_SECONDS,
            "single_pass_max_seconds": SA3_LONG_FORM.single_pass_max_seconds,
            "min_steps": 1,
            "max_steps": 50,
            "min_guidance": 0.0,
            "max_guidance": 30.0,
            "min_seed": -1,
            "max_seed": 2 ** 32 - 1,
        },
        qualification={
            "historical_source_workload":
                "1.4B medium DiT + SAME-L codec, pure MLX backend",
            "packaged_inference": "measured locally, not yet qualified in-app",
        },
        min_unified_memory_gb=8,
        recommended_hardware=(
            "Any Apple Silicon Mac. Measured 3.89 GB peak for a 30 s clip and "
            "5.05 GB for 380 s on a 32 GB M2 Max."),
        capabilities=("music", "instrumental", "text-to-music"),
        best_for=(
            "Fast instrumental music at 44.1 kHz stereo, up to 380 s (6 min 20 s). "
            "Measured 30 s in 9.3 s and 380 s in 61.6 s on an M2 Max - several "
            "times faster than ACE-Step - but it cannot sing. Pick ACE-Step when "
            "you need vocals or more than 380 s."),
        sample_rate_hz=44100,
        channels=2,
        license_name="Stability AI Community License",
        license_source=(
            "stabilityai/stable-audio-3-optimized model card; license text at "
            "https://stability.ai/license"),
        # Measured on the same host as the ACE-Step runs, recorded in
        # `qualification/evidence/sa3-2026-09-22/proof.json` with each artifact's own SHA-256.
        measured_runtime={
            "measured": [
                {"duration_seconds": 30.0, "seconds": 9.26, "realtime_x": 3.24},
                {"duration_seconds": 380.0, "seconds": 61.58, "realtime_x": 6.17},
            ],
            "measured_on": "M2 Max 32 GB, Stable Audio 3 Medium MLX, 8 steps, seed 42",
            "evidence": "measured",
        },
        checkpoints=(SA3_DIT_MEDIUM, SA3_SAME_L_ENCODER, SA3_SAME_L_DECODER,
                     SA3_T5GEMMA),
        dit_tiers=(SA3_DIT_MEDIUM,),
        lm_tiers=(),
        tiers=SA3_TIERS,
        long_form=SA3_LONG_FORM,
        default_lm_model="",
        inference_steps=8,
        guidance_scale=1.0,
    ),
    ModelEntry(
        repo=YUE2_REPO,
        label="YuE2 3B Music (8-bit AR + bf16 NAR)",
        family="yue2",
        checkpoint="yue2-3b",
        size_gb=9920168755 / 1e9,
        gated=False,
        package_id="yue2-3b-mlx",
        package_root="model",
        revision=YUE2_REVISION,
        runtime=RUNTIME_YUE2,
        # Every value here is one the three measured runs used: mode off, the port's standard
        # 32 ODE steps, its default guidance, and the 8-bit AR. 36 s is that run's length.
        defaults={"mode": "off", "duration_seconds": 36, "steps": 32, "guidance_scale": 1.0,
                  "precision": "8bit", "seed": -1},
        limits={
            "min_duration_seconds": 8,
            "max_duration_seconds": 360,
            "single_pass_max_seconds": 360,
            # The port's two documented configurations: 8 steps is its "ultra-fast mode" and 32 is
            # its standard (`GenerationConfig.ode_steps`, and the README's own benchmark table).
            # Both are real controls — the worker passes the chosen count into the pipeline's
            # generation config — so the lane offers them rather than dropping them.
            "min_steps": 8,
            "max_steps": 32,
            # `SongRequest.cfg_scale` is validated by the port itself to [0, 20]; this is that
            # bound, not an invented one.
            "min_guidance": 0.0,
            "max_guidance": 20.0,
            "min_seed": -1,
            "max_seed": 2 ** 63 - 1,
        },
        qualification={
            "measured_runs": [
                {"precision": "8bit", "mode": "off", "max_tokens": 600, "audio_seconds": 24.0,
                 "wall_seconds": 67.7, "peak_footprint_bytes": 10793966216},
                {"precision": "8bit", "mode": "off", "max_tokens": 900, "audio_seconds": 36.0,
                 "wall_seconds": 103.2, "peak_footprint_bytes": 11811160064,
                 "via": "this app's runner bridge"},
                {"precision": "8bit", "mode": "off", "max_tokens": 3000, "audio_seconds": 120.0,
                 "wall_seconds": 429.8, "peak_footprint_bytes": 11930000000},
            ],
            "memory_evidence": (
                "MEASURED here [M], three runs, by sampled `footprint phys_footprint_peak` in the "
                "process doing the work: 24.0 s of audio at 10.79 GB peak, 36.0 s through this "
                "app's runner bridge at 11.81 GB, and 120.0 s at 11.93 GB. The peak barely moves "
                "with length because the weights dominate (2.66 GB 8-bit AR + 2.93 GB bf16 NAR + "
                "0.51 GB VAE), which is also why an 8 GB Mac cannot run it in any configuration "
                "this port offers: the AR is the only quantizable part and 4-bit would save about "
                "1 GB against an 11.9 GB peak."),
            "measured_on": "32 GiB M2 Max (Mac14,13), macOS 27.0, mlx 0.32.2, the port at "
                           "9253ed133343406947bde7b67d43c7a63fb39d99",
            "source": "qualification/yue2-music-results-20260927/",
            "status": "qualified",
        },
        min_unified_memory_gb=16,
        recommended_hardware=(
            "16 GB or more. Measured peak 10.79-11.93 GB on a 32 GB M2 Max under no memory "
            "pressure, so a 16 GB Mac has roughly 4 GB left for macOS; 24 GB is comfortable."),
        capabilities=("music", "text-to-music", "lyrics", "long-form"),
        best_for=(
            "Sung songs with lyrics, up to about six minutes, at 48 kHz stereo. This is the only "
            "engine here that writes a vocal; ACE-Step 1.5 also sings, and Stable Audio 3 cannot. "
            "Measured: 36 s of song in 103 s on an M2 Max."),
        sample_rate_hz=48000,
        channels=2,
        license_name="Apache-2.0 (generator); CC BY-NC 4.0 (decoder)",
        license_source="vanch007/mlx-Yue2-3B and m-a-p/YuE2-Vae model cards",
        measured_runtime={
            "measured": [
                {"duration_seconds": 24.0, "seconds": 58.6, "max_tokens": 600},
                {"duration_seconds": 36.0, "seconds": 103.2, "max_tokens": 900},
                {"duration_seconds": 120.0, "seconds": 429.8, "max_tokens": 3000},
            ],
            "measured_on": "M2 Max 32 GB, YuE2 3B 8-bit AR + bf16 NAR, mode off, seed 2026",
            "evidence": "measured",
        },
        checkpoints=(),
        dit_tiers=(),
        lm_tiers=(),
        tiers=(),
        long_form=LongFormStrategy(
            single_pass_max_seconds=360,
            single_pass_cap_source="the port's own semantic budget: 9000 tokens at the measured "
                                   "~25 tokens per second of 48 kHz audio",
            beyond_cap="Not in one request. The port's context is 24576 tokens (~16 minutes at the "
                       "measured rate), and this lane publishes 360 s; a longer song is rendered in "
                       "sections.",
            repaint_task_type="", repaint_mode="", repaint_strength=0.0,
            repaint_latent_crossfade_frames=0, repaint_wav_crossfade_sec=0.0,
            note="The caller's seconds are converted to the port's token budget; the port stops "
                 "when the model emits its own end token, so a request may come back shorter than "
                 "asked and never longer.",
        ),
        default_lm_model="",
        # The port's standard step count, which is what the three measured runs used.
        inference_steps=32,
        guidance_scale=1.0,
    ),
)


def _validate_catalog() -> None:
    """Refuse a catalog that could publish one model's facts for another.

    ``ModelEntry`` already makes every per-model fact mandatory, so this only has
    to catch the ways two entries can still be confused: a repeated repo or
    package id, an empty identity, a package root that escapes the installed
    package, and generation limits that contradict the entry's own long-form
    strategy.  A violation is raised at import, so it fails the worker and the
    tests rather than reaching ``/api/catalog``.
    """
    repos: set[str] = set()
    packages: dict[str, str] = {}
    required_limits = ("min_duration_seconds", "max_duration_seconds",
                       "single_pass_max_seconds", "min_steps", "max_steps",
                       "min_guidance", "max_guidance", "min_seed", "max_seed")
    for model in CATALOG:
        for label, value in (("package_id", model.package_id),
                             ("package_root", model.package_root),
                             ("revision", model.revision),
                             ("runtime", model.runtime)):
            if not value:
                raise ValueError(f"Music catalog entry {model.repo!r} has no {label}")
        if model.repo in repos:
            raise ValueError(f"Music catalog repeats the repo {model.repo!r}")
        repos.add(model.repo)
        if model.package_id in packages:
            raise ValueError(
                f"Music catalog entry {model.repo!r} reuses package id "
                f"{model.package_id!r} from {packages[model.package_id]!r}")
        packages[model.package_id] = model.repo
        if model.package_root.startswith("/") or ".." in model.package_root.split("/"):
            raise ValueError(
                f"Music catalog entry {model.repo!r} has a package root outside its "
                f"package: {model.package_root!r}")
        missing = [key for key in required_limits if key not in model.limits]
        if missing:
            raise ValueError(
                f"Music catalog entry {model.repo!r} publishes no generation limit "
                f"for {', '.join(missing)}")
        if model.limits["single_pass_max_seconds"] != model.long_form.single_pass_max_seconds:
            raise ValueError(
                f"Music catalog entry {model.repo!r} publishes single_pass_max_seconds="
                f"{model.limits['single_pass_max_seconds']!r} but its long_form strategy "
                f"says {model.long_form.single_pass_max_seconds!r}")
        if model.limits["min_duration_seconds"] > model.limits["max_duration_seconds"]:
            raise ValueError(
                f"Music catalog entry {model.repo!r} has min_duration_seconds above "
                "max_duration_seconds")


_validate_catalog()


def get_model(identifier: str) -> Optional[ModelEntry]:
    """One registered model by repo id or by package id, or None when unknown."""
    for model in CATALOG:
        if identifier in (model.repo, model.package_id):
            return model
    return None


def require_model(identifier: str) -> ModelEntry:
    """One model by repo id or package id, refused by name when unregistered.

    Every per-model fact the worker publishes, verifies or loads is read from the
    returned entry.  There is deliberately no fallback to the first model: that
    fallback is how a newly added model used to be published with ACE-Step's
    manifest, package id and package root.
    """
    model = get_model(identifier)
    if model is None:
        known = ", ".join(item.repo for item in CATALOG)
        raise ValueError(
            f"Music model {identifier!r} is not in catalog.CATALOG ({known}); add a "
            "ModelEntry in components/music/app/backend/catalog.py")
    return model


def manifest_name_for(repo: str) -> str:
    return require_model(repo).manifest_name


def package_id_for(repo: str) -> str:
    return require_model(repo).package_id


def package_root_for(repo: str) -> str:
    return require_model(repo).package_root


def revision_for(repo: str) -> str:
    return require_model(repo).revision


def runtime_for(repo: str) -> str:
    return require_model(repo).runtime


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
