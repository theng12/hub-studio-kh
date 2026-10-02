"""
TTS generation manager.

Worker dispatch is keyed off `model.family`. See `_WIRED_FAMILIES` below for
the authoritative list — the audit_truth.py script cross-checks that list
against the actual dispatch branches on every release.

Currently wired (workers exist):
- voxcpm-mlx      → mlx-audio worker (zero-shot / voice design / cloning)
- qwen3-tts       → mlx-audio worker (custom / design / clone modes by repo)
- chatterbox-mlx  → mlx-audio worker
- omnivoice       → mlx-audio worker (voice design + cloning)
- fish-audio-mlx  → mlx-audio worker (Fish S2 Pro style + cloning)
- pocket-tts      → mlx-audio worker (preset voice or cloning)
- khmer-tts-mms   → transformers VitsModel (separate worker)
- khmer-tts-onnx  → ONNX Runtime (separate worker)

Pruned 2026-09-24 (ST-13.B): the per-family wiring for bark, kokoro-mlx, spark-tts-mlx, orpheus,
kittentts, vibevoice, voxtral-tts, marvis, longcat-audiodit, arktts, moss-tts-nano, echo-tts and
f5-tts was carried here after those families left the catalog. It was keyed by family and never
reached, but "never reached" is not a property anyone can check later — this list and the tables
below now name exactly the families that ship.

Removed in v1.3.1:
- chatterbox (PyTorch) — chatterbox-mlx covers it on Apple Silicon
- spark-tts (PyTorch) — spark-tts-mlx covers it on Apple Silicon
- xtts (Coqui)         — TTS pip package pinned an old torch, so it could not ship here

═════════════════════════════════════════════════════════════════════════
WORKER MODEL-LOADING STANDARD (established v1.3.5)
═════════════════════════════════════════════════════════════════════════

THE RULE: in every worker added to this file, resolve the local HF Hub
snapshot path and pass it EXPLICITLY to the loader. Never pass a HF repo
ID string when the loader will accept an absolute filesystem path.

THE PATTERN:

    def _engine_get_model(self, repo: str, device: str):
        # ... eviction-on-repo-switch boilerplate ...

        snapshot_path = self._mlx_audio_snapshot_path(repo)   # generic walker
        # Optional: validate critical files exist, raise clean RuntimeError
        ckpt = snapshot_path / "subfolder" / "model.safetensors"
        if not ckpt.exists():
            raise RuntimeError(
                f"<Engine> file missing at {ckpt}. "
                "Re-download <repo> from the Models tab."
            )

        model = SomeEngine.from_pretrained(
            str(snapshot_path),
            local_files_only=True,                            # belt + suspenders
            # ... engine-specific args
        )
        return model

WHY THIS EXISTS: different upstream libraries use different cache backends.
The standard `huggingface_hub` cache lives at
    ${HF_HOME}/hub/models--<org>--<repo>/...
Some libraries keep their OWN cache layout that does not share with HF Hub (`cached_path`, for
example). When a worker passes a repo string and the library internally uses that other cache, it
looks in the wrong directory and silently re-downloads even when the file is already cached.
Passing an absolute path bypasses the library's cache lookup entirely.

COMPLIANCE TABLE (keep current when adding workers):

| Worker                | Pattern                                                       | Status |
|-----------------------|---------------------------------------------------------------|--------|
| _generate_mlx_audio   | load_model(snapshot_path)  Path not str (v1.2.8)              | OK     |
| _generate_khmer_vits  | VitsModel.from_pretrained(local snapshot)                     | OK     |
| _generate_khmer_onnx  | onnxruntime InferenceSession(local .onnx)                     | OK     |

═════════════════════════════════════════════════════════════════════════

Outputs land in the external Voice state `output/<job_id>.wav` and are
persisted to `output/.history.json` (same shape as MusicStudio's gen history)
so they survive server restarts without mutating the app bundle.
"""
from __future__ import annotations

import hashlib
import importlib.util
from importlib import metadata
import json
import math
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from packaging.version import InvalidVersion, Version

from . import (
    catalog,
    cache,
    long_form_policy,
    media_tools,
    mlx_memory,
    model_audits,
    qwen_quality,
    reference_audio,
    resource_telemetry,
    text_normalization,
    paths,
)
from .native_executor import ExclusiveNativeExecutor
from .voicestudio_genstudio_integration import final_tts_result


def ensure_espeak_available() -> bool:
    """Point the phonemizer at espeak's bundled library instead of a command that is not installed.

    Anything phonemizing through espeak needs an espeak *command* by default, and this app ships
    none: what it ships is `espeakng_loader`, carrying `libespeak-ng.dylib` and 400 files of
    `espeak-ng-data`. Without this the pipeline starts and exits with `SystemExit: 1`, which is how
    a G2P-backed family fails every job while looking like a broken model.

    No shipped family needs it today (the twelve kept families plus Pocket TTS are all
    grapheme/tokenizer driven), so it stays only as the import-time guard for the misaki-style
    stack that is still installed in the pinned runtime. The loader's own
    `make_library_available()` is the supported way to do this, and the two environment variables
    are set as well because `phonemizer` reads them. Existing values win, so an operator who has
    their own espeak is not overridden. Returns whether a library was found.
    """
    try:
        import espeakng_loader
        library = str(espeakng_loader.get_library_path())
        data = str(espeakng_loader.get_data_path())
    except Exception:
        return False
    os.environ.setdefault("PHONEMIZER_ESPEAK_LIBRARY", library)
    os.environ.setdefault("ESPEAK_DATA_PATH", data)
    try:
        espeakng_loader.make_library_available()
    except Exception:
        pass
    # The phonemizer runs the `espeak-ng` *command*, and it finds it on PATH. This app ships that
    # command in its own tools directory (vendored: the binary, its two libraries rewritten to
    # `@loader_path`, and an ad-hoc signature so macOS will run it on a Mac with no Homebrew), so the
    # directory goes on PATH here rather than relying on the operator having espeak installed.
    candidates = []
    try:
        from . import paths
        candidates.append(str(paths.tools_dir()))
    except Exception:
        pass
    # The interpreter that runs this IS the app's runtime, so its own directory is where the vendored
    # command lives in both the developer checkout and the packaged app - `paths.tools_dir()` points
    # at the bundle's tools folder, which does not exist until the app is staged.
    candidates.append(os.path.dirname(os.path.abspath(sys.executable)))
    for candidate in candidates:
        command = os.path.join(candidate, "espeak-ng")
        if os.path.isfile(command) and os.access(command, os.X_OK):
            if candidate not in os.environ.get("PATH", "").split(os.pathsep):
                os.environ["PATH"] = candidate + os.pathsep + os.environ.get("PATH", "")
            # Name the executable outright as well. Generation runs in a *spawned* child process, and
            # a PATH entry alone left it failing there with `SystemExit: 1` while the very same call
            # succeeded in-process - the phonemizer's own path setting is not inferred from PATH.
            os.environ.setdefault("PHONEMIZER_ESPEAK_PATH", command)
            return True
    return bool(library)


ENSURE_ESPEAK_AT_IMPORT = ensure_espeak_available()


# ───────────── module-level state ─────────────

# TTS on MPS can OOM if multiple models are loaded simultaneously — even a
# compact 82M checkpoint next to a second model fights for unified memory.
# Serialize all generations.
_GEN_LOCK = threading.Lock()

OUTPUT_DIR = paths.data_path("output")
HISTORY_FILE = OUTPUT_DIR / ".history.json"
# Finished job records are kept 90 days (the same rule as every other worker's jobs.json), then dropped.
HISTORY_RETENTION_DAYS = 90
_HISTORY_DONE = {"done", "error", "cancelled", "uncertain"}


def _history_expired(job, now: float | None = None) -> bool:
    stamp = getattr(job, "finished_at", None) or getattr(job, "created_at", None)
    if getattr(job, "state", None) not in _HISTORY_DONE or not isinstance(stamp, (int, float)):
        return False
    return float(stamp) < (time.time() if now is None else now) - HISTORY_RETENTION_DAYS * 86400
HISTORY_MAX = 200
_AUDIO_OUTPUT_SUFFIXES = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}

# Direct Voice Studio jobs need the same live-memory protection that Studio Hub
# already applies before dispatching local work. These are intentionally
# conservative: the model's on-disk size is only an approximation, so we keep
# extra room for token buffers, audio activations, and the rest of macOS.
MEMORY_HEADROOM_GB = 1.25
MEMORY_RETRY_LIMIT = 1
MEMORY_RESTART_FAILURES = 2


class MemoryGuardError(RuntimeError):
    """A job was refused before inference because the host had too little RAM."""


class NativeExecutionUncertain(RuntimeError):
    """The parent could not prove what happened to a native execution."""


class PersistenceError(RuntimeError):
    """A required job-history write did not complete."""


def _memory_snapshot() -> Optional[dict]:
    """Return live host memory, or None when the optional probe is unavailable."""
    try:
        import psutil
        from components import host_memory
        vm = psutil.virtual_memory()
        return {
            "total_gb": round(vm.total / 1e9, 2),
            "available_gb": round(host_memory.available_bytes(vm) / 1e9, 2),
            "used_gb": round(vm.used / 1e9, 2),
            "percent": float(vm.percent),
        }
    except Exception:
        return None


# OmniVoice commits its whole latent block up front at 25 frames/sec, so an
# oversized request dies mid-inference with no exception of its own -- mlx-audio
# simply returns having written nothing. Measured on the fleet 2026-08-07,
# 3 reps per tier: 8 GB passed 2250 frames and failed 3000 every time; 16 and
# 24 GB cleared 3000 (the API's own clamp, not their limit).
_OMNIVOICE_FRAMES_PER_SECOND = 25
_OMNIVOICE_MEASURED_SAFE_FRAMES = ((12, 2250),)   # (unified_memory_gb_below, frames)


# Families whose engine does NO number normalisation of its own, so raw digits
# reach the tokenizer and are mispronounced. Measured on OmniVoice 2026-08-07:
# "1,200" -> "one two hundred", "12,500" -> "one million two hundred thousand
# five hundred", "$1,450.75" -> nonsense. 5 of 8 digit forms were wrong, while
# every fully spelled-out form was correct. Other mlx-audio engines expand
# digits themselves; OmniVoice simply never used them.
#
# Deliberately a per-family flag, not a global default: families that already
# normalise upstream must not be normalised twice. It lives on the family entry
# (`catalog.FAMILIES[...].numbers_need_expansion`) beside the rest of that
# family's engine facts, so a new family states it in the registry instead of
# being silently omitted from a set in this file.
def _needs_number_expansion(family: str) -> bool:
    entry = catalog.FAMILIES.get(family)
    if entry is None:
        raise ValueError(
            f"family {family!r} is not in catalog.FAMILIES; add the Family entry (with its "
            "engine and requirements) in components/voice/app/backend/catalog.py"
        )
    return entry.numbers_need_expansion


def _normalized_speech_text(family: str, text: str, params: dict) -> str:
    """Expand digits to words for engines that cannot pronounce them.

    ``normalize_text`` was an accepted-but-ignored API field. It now means what
    it says, and can force normalisation for any family; the family's own
    ``numbers_need_expansion`` flag applies it automatically where the engine is
    known to need it.
    """
    if not (_needs_number_expansion(family) or params.get("normalize_text")):
        return text
    try:
        spoken = text_normalization.normalize_for_speech(text)
    except Exception as exc:   # never fail a job over cosmetics
        print(f"[gen] number normalisation skipped ({type(exc).__name__}: {exc})",
              flush=True)
        return text
    if spoken != text:
        print(f"[gen] {family} normalised numbers to words for speech", flush=True)
    return spoken


def _no_wav_produced_error(output_dir, family: str, gen_kwargs: dict) -> RuntimeError:
    """Explain why mlx-audio returned without writing audio.

    The bare "didn't produce a wav file" is technically true and practically
    useless: on a small machine it is nearly always the request being too large
    to fit, and the temp-dir path tells the owner nothing they can act on. Name
    what was attempted, what the host had, and what actually fits.
    """
    mem = _memory_snapshot() or {}
    total = float(mem.get("total_gb") or 0)
    avail = float(mem.get("available_gb") or 0)

    detail = [f"{family or 'mlx-audio'} produced no audio"]
    frames = None
    duration = gen_kwargs.get("duration_s")
    if family == "omnivoice" and duration:
        frames = int(float(duration) * _OMNIVOICE_FRAMES_PER_SECOND)
        detail.append(
            f"requested {float(duration):.0f}s of audio "
            f"({frames} latent frames committed in one pass)"
        )
    elif gen_kwargs.get("text"):
        detail.append(f"{len(str(gen_kwargs['text']))}-character section")

    if total:
        detail.append(f"host has {total:.1f} GB unified memory, {avail:.1f} GB free")

    safe = next((f for below, f in _OMNIVOICE_MEASURED_SAFE_FRAMES
                 if total and total < below), None)
    if family == "omnivoice" and safe and (frames is None or frames > safe):
        detail.append(
            f"this machine is measured safe to about {safe} frames "
            f"(~{safe // _OMNIVOICE_FRAMES_PER_SECOND}s per pass); "
            "shorten the section or run it on a larger machine"
        )
    else:
        detail.append(
            "this is usually the request being too large for the available "
            "memory — shorten the section, or free memory and retry"
        )
    return RuntimeError(". ".join(detail) + f". Temp dir: {output_dir}")


def _required_free_memory_gb(model_entry, *, loaded: bool) -> float:
    """Use the exact same RAM estimate for inventory and admission."""
    model_gb = max(0.5, float(model_entry.size_gb or 0.5))
    required = (
        max(1.5, model_gb * 0.45) + MEMORY_HEADROOM_GB
        if loaded
        else model_gb + MEMORY_HEADROOM_GB
    )
    return round(required, 2)


def _is_memory_failure(exc: BaseException) -> bool:
    """Recognize allocation failures without treating ordinary model errors as OOM."""
    if isinstance(exc, MemoryError):
        return True
    message = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in message for marker in (
        "out of memory", "out-of-memory", "mps backend out of memory",
        "metal allocation", "metal alloc", "allocation failed",
        "alloc failed", "resource limit", "abort trap: 6",
    ))


# ───────────── lightweight dependency discovery ─────────────

# Importing these libraries just to discover whether they are installed adds
# 10-20 seconds to every server restart. Model workers still perform the real
# imports when generation begins, while diagnostics performs an explicit deep
# import when the user asks for it.
def _package_installed(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


_TTS_MISSING = [name for name in ("torch", "transformers") if not _package_installed(name)]
TTS_AVAILABLE = not _TTS_MISSING
TTS_IMPORT_ERROR: Optional[str] = (
    f"Missing packages: {', '.join(_TTS_MISSING)}" if _TTS_MISSING else None
)


def availability() -> dict:
    """Per-engine availability + the static config the frontend needs.

    `wired_families` is every shipped family whose engine runtime is present on
    this machine. It is derived from `catalog.FAMILIES` and the engine's own
    probe, so a new family (or a missing package) changes this list without an
    edit here. The bark / voxtral / marvis / orpheus / vibevoice / kokoro
    rosters went with their families on 2026-09-24 (ST-13.B); the frontend reads
    each key with `|| []`, so a key that is no longer sent cannot break the
    picker. The preset rosters come from the same place (see
    `_published_preset_rosters`), so the picker and the lane contract a scheduler
    reads cannot disagree about which voices exist.
    """
    mlx_audio_ok = _have_mlx_audio()
    wired = [family for family in _WIRED_FAMILIES if runtime_ready_for_family(family)]
    return {
        "available": TTS_AVAILABLE,
        "qwen3_available": mlx_audio_ok,
        "voxcpm_available": mlx_audio_ok,
        "omnivoice_available": mlx_audio_ok,
        "diffusers_available": _have_diffusers(),
        "error": TTS_IMPORT_ERROR,
        "device": _detect_device() if TTS_AVAILABLE else None,
        **_published_preset_rosters(),
        "qwen3_voice_design_examples": QWEN3_VOICE_DESIGN_EXAMPLES,
        "voxcpm_emotion_examples": VOXCPM_EMOTION_EXAMPLES,
        "phase": 2,
        "wired_families": wired,
        "media": media_tools.availability(),
    }


def _published_preset_rosters() -> dict:
    """Every roster a catalog family publishes, under the key that family names it.

    Built from `catalog.FAMILIES` rather than a key literal here: the app joins a model to its
    family's roster by the key's shape (`<family>_preset_speakers`), so a second family that
    publishes a roster reaches the picker with no edit in this file. The roster object itself is
    the one the lane contract publishes as `preset_speaker`'s enum, so the two producers cannot
    disagree about which voices exist.
    """
    return {
        f"{family.preset_roster.key}_preset_speakers": family.preset_roster.speakers
        for family in catalog.FAMILIES.values()
        if family.preset_roster is not None
    }


def _have_mlx_audio() -> bool:
    return _package_installed("mlx_audio")


def _have_onnxruntime() -> bool:
    """Presence check for the ONNX Runtime used by the Khmer VITS female family."""
    return _package_installed("onnxruntime")


VOXCPM_EMOTION_EXAMPLES = [
    "calm and measured",
    "excited and happy",
    "sad and slow",
    "angry and forceful",
    "whispering softly",
    "elderly male, gravelly",
    "young female, cheerful",
]


def _detect_device() -> str:
    """Report the native Voice Studio target without importing PyTorch."""
    if sys.platform == "darwin" and platform.machine() == "arm64":
        return "mps"
    return "cpu"


def _have_diffusers() -> bool:
    return _package_installed("diffusers")


# One presence probe per engine. The diagnostics panel and the picker both ask
# "is this family usable on this Mac?", and the answer is a property of the
# engine, not of a per-family branch.
_RUNTIME_PROBE_BY_ENGINE = {
    catalog.ENGINE_MLX_AUDIO: _have_mlx_audio,
    catalog.ENGINE_TRANSFORMERS_VITS: lambda: TTS_AVAILABLE,
    catalog.ENGINE_ONNX_VITS: _have_onnxruntime,
}


# ───────────── diagnostics ─────────────

# The packages the SHIPPED engines need, checked one-by-one by the user-visible health check so
# the UI can say which engines are ready. This is deliberately narrower than
# requirements-generation.txt, which still installs the full pinned runtime: packages that only a
# retired family used are no longer reported, and a package no shipped family imports cannot make
# a shipped engine unavailable.
_PACKAGE_CHECKLIST = [
    ("torch",         "Core ML framework + MPS device support"),
    ("torchaudio",    "Audio operators for the PyTorch speech stack"),
    ("transformers",  "Tokenizers used by the Khmer VITS and Whisper models"),
    ("diffusers",     "Diffusion utilities used by optional audio pipelines"),
    ("accelerate",    "Multi-device model loading"),
    ("soundfile",     "WAV file writing (libsndfile)"),
    ("numpy",         "Tensor numerics"),
    # MLX-side packages. Apple Silicon native, not PyTorch.
    ("mlx",           "Apple Silicon ML framework (MLX TTS families)"),
    ("mlx_lm",        "MLX language-model runtime used by Fish S2 Pro"),
    ("mlx_audio",     "MLX inference wrapper for audio models (all MLX TTS families)"),
    # Khmer VITS families (khmer-tts-mms uses torch/transformers above; the
    # ONNX female voice uses ONNX Runtime).
    ("onnxruntime",   "ONNX Runtime for the Khmer TTS Female (VITS/ONNX) family"),
]

# ───────────── family wiring: derived from the producer, checked at import ─────────────
#
# This module used to carry three family lists -- `_ENGINE_REQUIREMENTS`, `_WIRED_FAMILIES`
# and `MLX_AUDIO_FAMILIES` -- beside the catalog's own MLX tuple, and a family could be in one
# and not the others. That is how one screen shows a family as wired and another as unsupported,
# and it is how `kittentts`, `vibevoice` and `omnivoice` shipped published as "PyTorch / MPS"
# and hidden behind the MLX-only filter (CHANGELOG, 2026-09-24).
#
# The producer is `catalog.FAMILIES`: each family states its engine and the packages that engine
# imports. Everything here is derived from that registry. `MLX_AUDIO_FAMILIES` is the one table
# that must stay a literal in this file -- `components/voice/audit_contract_runtime.py` parses it
# out of the source with `ast` to resolve a model audit's engine mode -- so its membership is
# *checked* against the catalog at import rather than trusted.
_WORKER_BY_ENGINE: dict[str, str] = {
    catalog.ENGINE_MLX_AUDIO: "_generate_mlx_audio",
    catalog.ENGINE_TRANSFORMERS_VITS: "_generate_khmer_vits",
    catalog.ENGINE_ONNX_VITS: "_generate_khmer_onnx",
}

# What to call an engine's runtime in a user-facing refusal. A label, not a
# membership test.
_ENGINE_RUNTIME_LABEL: dict[str, str] = {
    catalog.ENGINE_MLX_AUDIO: "mlx-audio",
    catalog.ENGINE_TRANSFORMERS_VITS: "torch/transformers",
    catalog.ENGINE_ONNX_VITS: "onnxruntime",
}

# Packages each family's engine needs, published by `diagnostics()` and checked one-by-one by
# the user-visible health panel. Read from the family entry, so a family cannot advertise a
# dependency set nobody maintains.
_ENGINE_REQUIREMENTS: dict[str, list[str]] = {
    family_id: list(family.requirements)
    for family_id, family in catalog.FAMILIES.items()
}

# Which engines have an actual worker implemented in this app -- i.e. picking one of these
# models won't trip a NotImplementedError. Membership is the catalog's own family registry,
# and `_validate_family_wiring` below refuses to import a family whose engine has no worker.
_WIRED_FAMILIES = frozenset(catalog.FAMILIES)

# Families whose long script Voice Studio renders in its own sentence-safe sections before
# dispatch. Every shipped family states a long-form policy (`long_form_policy.policy_for` never
# returns None), so a new family is sectioned from the day it lands instead of being sent to its
# engine as one request.
_SECTIONED_FAMILIES = frozenset(catalog.FAMILIES)


# ───────────── mlx-audio family config table ─────────────
#
# All families in this table share `_generate_mlx_audio` as their backend
# worker. Per-family differences are expressed declaratively here instead of
# duplicating the load/save scaffolding in N separate worker functions.
#
# Fields:
# - default_sample_rate: Hz emitted by the engine (used for log lines).
# - uses_cfg: whether to pass `cfg_value` + `inference_timesteps` knobs to
#   mlx-audio.generate_audio. Only VoxCPM2 uses these today.
# - mode: which kwarg-resolver to dispatch to:
#     "qwen3"             — repo-name picks one of custom/design/clone
#     "voxcpm_flex"       — any combo of ref_audio + ref_text + instruct
#     "clone_with_intensity" — needs ref_audio, optional exaggeration/intensity
#     "voice_or_clone"    — voice param OR ref_audio (zero-shot or clone)
#     "omnivoice"         — design prompt and/or clone
#     "fish_audio"        — optional ref_audio + optional instruct + full sampling
#
# To add a new mlx-audio family: add a catalog FAMILIES entry with
# `engine=catalog.ENGINE_MLX_AUDIO` and its requirements, add ModelEntry rows,
# and drop a config line here. Both files refuse to import if the two
# disagree, naming the family and the file to edit. No new worker code is
# needed unless mlx-audio exposes a never-before-seen kwarg.
MLX_AUDIO_FAMILIES: dict[str, dict] = {
    "qwen3-tts": {
        "default_sample_rate": 24000,
        "uses_cfg": False,
        "mode": "qwen3",
        "label": "Qwen3-TTS",
    },
    "voxcpm-mlx": {
        "default_sample_rate": 48000,
        "uses_cfg": True,
        "mode": "voxcpm_flex",
        "label": "VoxCPM2 (MLX)",
    },
    "chatterbox-mlx": {
        "default_sample_rate": 24000,
        "uses_cfg": False,
        "mode": "clone_with_intensity",
        "label": "Chatterbox (MLX)",
    },
    "omnivoice": {
        "default_sample_rate": 24000,
        "uses_cfg": False,
        "mode": "omnivoice",
        "label": "OmniVoice (MLX)",
    },
    "fish-audio-mlx": {
        "default_sample_rate": 44100,
        "uses_cfg": False,
        "mode": "fish_audio",
        "label": "Fish Audio S2 Pro (MLX)",
    },
    "pocket-tts": {
        # Kyutai Pocket TTS 4-bit. `generate(text, voice=None, ref_audio=None, …)`
        # takes a preset voice name OR a reference clip, which is exactly the
        # `voice_or_clone` contract: the reference wins, a preset name is used when
        # there is no reference, and with neither the engine falls back to its own
        # default preset ("alba"). The catalogue declares `voice-cloning` for this
        # family, so the app files it under the clone form.
        "default_sample_rate": 24000,
        "uses_cfg": False,
        "mode": "voice_or_clone",
        "label": "Pocket TTS (MLX)",
    },
    "breeze-tts": {
        # Breeze TTS 2 4-bit. One `generate(text, voice, instruct, ref_audio, ref_text, …)` covers
        # all three of its forms: a reference clip clones, an `instruct` describes or directs the
        # reading, and `voice` names a preset. Measured 2026-09-29 at 100% word match through a
        # Whisper round trip for design, clone and direction.
        #
        # `split_pattern` is deliberately NOT overridden. Its default is "\n", and that is exactly
        # the behaviour that was measured: handed a 14-paragraph script, Breeze produced 14
        # sections — one per paragraph — each a verbatim continuation in order. Forcing one engine
        # chunk to stop it splitting would throw away the segmentation it already gets right.
        "default_sample_rate": 24000,
        "uses_cfg": False,
        "mode": "design_clone_or_direct",
        "label": "Breeze TTS 2 (MLX 4-bit)",
    },
}


def engine_for_family(family: str) -> str:
    """The engine a family runs on, or a failure that names both files to edit."""
    entry = catalog.FAMILIES.get(family)
    if entry is None:
        raise ValueError(
            f"family {family!r} is not in catalog.FAMILIES; add the Family entry in "
            "components/voice/app/backend/catalog.py and a worker for its engine in "
            "components/voice/app/backend/generation.py"
        )
    return entry.engine


def runtime_ready_for_family(family: str) -> bool:
    """Whether this family's engine runtime is importable right now.

    One probe per engine, read off the family's own `engine` fact, so the three
    former per-family branches cannot fall out of step with the engines the
    registry declares.
    """
    engine = engine_for_family(family)
    probe = _RUNTIME_PROBE_BY_ENGINE.get(engine)
    if probe is None:
        raise ValueError(
            f"family {family!r} names engine {engine!r}, which has no runtime probe; add "
            "one to _RUNTIME_PROBE_BY_ENGINE in "
            "components/voice/app/backend/generation.py"
        )
    return probe()


def _validate_family_wiring() -> None:
    """Refuse to import a wiring table that disagrees with the family registry.

    Three checks, each of which used to be a silent UI contradiction:
      * every mlx-audio family in the catalog has a config row here;
      * no config row here names a family the catalog does not run on mlx-audio
        (a stale row is how a retired family keeps a "wired" chip);
      * every engine a family names has a worker.
    """
    mlx_families = {
        family_id for family_id, family in catalog.FAMILIES.items()
        if family.engine == catalog.ENGINE_MLX_AUDIO
    }
    configured = set(MLX_AUDIO_FAMILIES)
    if configured != mlx_families:
        raise ValueError(
            "MLX_AUDIO_FAMILIES and catalog.FAMILIES disagree about which families run on "
            f"mlx-audio: missing from MLX_AUDIO_FAMILIES {sorted(mlx_families - configured)}, "
            f"stale in MLX_AUDIO_FAMILIES {sorted(configured - mlx_families)}; fix "
            "components/voice/app/backend/catalog.py (Family.engine) and "
            "components/voice/app/backend/generation.py (MLX_AUDIO_FAMILIES)"
        )
    for family_id, family in catalog.FAMILIES.items():
        if family.engine not in _WORKER_BY_ENGINE:
            raise ValueError(
                f"family {family_id!r} names engine {family.engine!r}, which has no worker; "
                "add one to _WORKER_BY_ENGINE in "
                "components/voice/app/backend/generation.py and set the engine in "
                "components/voice/app/backend/catalog.py"
            )
        if family.engine not in _RUNTIME_PROBE_BY_ENGINE:
            raise ValueError(
                f"family {family_id!r} names engine {family.engine!r}, which has no runtime "
                "probe; add one to _RUNTIME_PROBE_BY_ENGINE in "
                "components/voice/app/backend/generation.py"
            )


_validate_family_wiring()


# ───────────── Qwen3-TTS preset metadata ─────────────

# The roster itself lives with the family that publishes it (`catalog.QWEN3_PRESET_ROSTER`),
# where `availability()` reads it for the app's picker and `lane_contract` reads its ids as
# `preset_speaker`'s enum for a scheduler - one list, so the two cannot disagree. The name is
# re-exported here because this module's Qwen3-TTS resolver validates a requested speaker
# against exactly the roster the app offers.
QWEN3_PRESET_SPEAKERS = catalog.QWEN3_PRESET_SPEAKERS

QWEN3_VOICE_DESIGN_EXAMPLES = [
    "Calm narrator, slow and contemplative",
    "Excited and happy, speaking very fast",
    "Sad and crying, speaking slowly",
    "Angry and shouting",
    "Whispering quietly",
    "Deep gravelly male, like a 60-year-old narrator",
    "Light cheerful female, friendly customer-service tone",
]


def _qwen3_mode_from_repo(repo: str) -> str:
    """Detect the Qwen3-TTS subtype from the repo name.
    Base → 'clone' (uses voice library), CustomVoice → 'custom' (preset speakers),
    VoiceDesign → 'design' (natural language voice prompt)."""
    name = repo.rsplit("/", 1)[-1].lower()
    if "voicedesign" in name:
        return "design"
    if "customvoice" in name:
        return "custom"
    if "base" in name:
        return "clone"
    return "custom"   # safest fallback


# Qwen's Base / ICL clone path does not use the model's built-in newline
# splitting. Owner listening found the 360-character setting could change
# tonation at a boundary while a controlled 288-character run remained stable.
# Keep Base/ICL cloning at that verified conservative ceiling. CustomVoice is a
# different preset-speaker path and retains its existing budget.
_QWEN_CLONE_CHUNK_CHARS = (
    long_form_policy.QWEN_CLONE_SECTION_MAX_CHARACTERS
)
_LONG_FORM_JOIN_PAUSE_S = long_form_policy.DEFAULT_JOIN_PAUSE_SECONDS
_QWEN_SENTENCE_ENDINGS = frozenset(".!?。！？")
_QWEN_TRAILING_CLOSERS = frozenset("\"'”’»）】〕〉")


def _qwen_clone_text_chunks(text: str, max_chars: int = _QWEN_CLONE_CHUNK_CHARS) -> list[str]:
    """Split long Qwen Base clone text at natural narration boundaries.

    Qwen's ICL cloning routine treats its whole input as one sequence, even
    though the non-cloning code path understands newline segments. This keeps
    each independent clone safely short, prefers sentence endings, retains
    paragraph pauses, and falls back to word (then character) boundaries for
    unusually long sentences.
    """
    return _sentence_safe_text_chunks(text, max_chars=max_chars)


def _sentence_safe_text_chunks(text: str, max_chars: int) -> list[str]:
    """Split narration without dropping text or cutting ordinary words.

    This is shared by local engines whose public request can be long while
    their stable synthesis context is much shorter. Paragraph and sentence
    boundaries win; an unusually long sentence falls back to punctuation,
    whitespace, and only then an unavoidable character boundary.
    """
    if max_chars < 40:
        raise ValueError("Sentence-safe chunk size must be at least 40 characters")
    paragraphs = [
        " ".join(part.split())
        for part in text.strip().split("\n\n")
        if part.strip()
    ]
    chunks: list[str] = []
    for paragraph in paragraphs:
        sentences: list[str] = []
        start = 0
        index = 0
        while index < len(paragraph):
            if paragraph[index] in _QWEN_SENTENCE_ENDINGS:
                end = index + 1
                while end < len(paragraph) and paragraph[end] in _QWEN_TRAILING_CLOSERS:
                    end += 1
                if end == len(paragraph) or paragraph[end].isspace():
                    sentence = paragraph[start:end].strip()
                    if sentence:
                        sentences.append(sentence)
                    start = end
                    index = end
                    continue
            index += 1
        remainder = paragraph[start:].strip()
        if remainder:
            sentences.append(remainder)

        current = ""
        for sentence in sentences:
            units = _split_long_text_unit(sentence, max_chars)
            for unit in units:
                if current and len(current) + 1 + len(unit) > max_chars:
                    chunks.append(current)
                    current = ""
                current = f"{current} {unit}".strip()
        if current:
            chunks.append(current)
    return chunks


def _split_long_text_unit(text: str, max_chars: int) -> list[str]:
    """Break one overlong sentence without dropping or rewriting text."""
    pieces: list[str] = []
    remaining = text.strip()
    floor = max(1, int(max_chars * 0.55))
    while len(remaining) > max_chars:
        whitespace = remaining.rfind(" ", floor, max_chars + 1)
        punctuation = max(
            (remaining.rfind(mark, floor, max_chars + 1) + 1 for mark in ",;:、，；："),
            default=0,
        )
        cut = max(whitespace, punctuation)
        if cut <= 0:
            cut = max_chars
        piece = remaining[:cut].strip()
        if not piece:
            cut = max_chars
            piece = remaining[:cut]
        pieces.append(piece)
        remaining = remaining[cut:].strip()
    if remaining:
        pieces.append(remaining)
    return pieces


def _internal_mlx_text_chunks(
    family: str,
    repo: str,
    text: str,
    *,
    max_chars_override: Optional[int] = None,
) -> list[str]:
    """Return safe independent synthesis sections for long-form local TTS.

    A passed model audit may tighten or expand the private section budget for
    one exact checkpoint. Until that evidence exists, the established family
    fallback remains active. Callers still submit one complete script.
    """
    pinned = model_audits.qwen_17b_production_v2_limits(repo)
    if pinned:
        policy = long_form_policy.policy_for(
            family,
            repo,
            audited_section_max_characters=pinned.get("private_section_max_characters"),
            audited_default_section_max_characters=pinned.get(
                "default_private_section_max_characters"
            ),
        )
    else:
        policy = long_form_policy.policy_for(
            family,
            repo,
            audited_section_max_characters=model_audits.input_limits(repo).get(
                "private_section_max_characters"
            ),
        )
    if policy is None:
        return []
    max_chars = int(max_chars_override or policy["section_max_characters"])
    return _sentence_safe_text_chunks(text, max_chars=max_chars)


def _long_form_join_pause_s(family: str, repo: str) -> float:
    """Return a private, model-path-specific pause between rendered sections."""
    policy = long_form_policy.policy_for(family, repo)
    if policy is None:
        return _LONG_FORM_JOIN_PAUSE_S
    return float(policy["join_pause_seconds"])


def _production_join_pauses_s(text: str, chunks: list[str], speed: float) -> list[float]:
    """Keep production narration boundaries stable after final tempo adjustment."""
    pauses: list[float] = []
    cursor = 0
    speed = max(long_form_policy.SPEED_MINIMUM,
                min(float(speed), long_form_policy.SPEED_MAXIMUM))
    for index, chunk in enumerate(chunks[:-1]):
        start = text.find(chunk, cursor)
        end = start + len(chunk) if start >= 0 else cursor
        next_start = text.find(chunks[index + 1], end)
        separator = text[end:next_start] if next_start >= 0 else ""
        visible_end = chunk.rstrip()
        while visible_end and visible_end[-1] in _QWEN_TRAILING_CLOSERS:
            visible_end = visible_end[:-1].rstrip()
        if separator.count("\n") >= 2:
            pause = long_form_policy.production_boundary_pause_seconds("paragraph")
        elif visible_end and visible_end[-1] in _QWEN_SENTENCE_ENDINGS:
            pause = long_form_policy.production_boundary_pause_seconds("sentence")
        else:
            pause = long_form_policy.production_boundary_pause_seconds("soft")
        pauses.append(pause * speed)
        cursor = max(end, next_start if next_start >= 0 else end)
    return pauses


# The levelling pass is FFmpeg's EBU R128 `loudnorm`, per section. `long_form_policy` carries the
# measurement that chose it over a per-section linear gain: the sections' crest factors decide how
# much gain each one can take, so the obvious linear answer narrowed the span only 4.9 → 4.3 LU,
# while this brings the same material to 1.5 LU. The filter string is built from the family's policy
# so a family can state a different target, ceiling or range without editing this function.
def _loudnorm_filter(target_lufs: float, ceiling_dbtp: float, range_lu: float) -> str:
    return (f"loudnorm=I={target_lufs:g}:TP={ceiling_dbtp:g}:LRA={range_lu:g}")


# The sample format each of `soundfile`'s subtypes maps to, so a levelled section comes back in the
# same container the engine produced rather than being silently converted to 16-bit.
_PCM_CODEC_FOR_SUBTYPE = {
    "PCM_16": "pcm_s16le", "PCM_24": "pcm_s24le", "PCM_32": "pcm_s32le",
    "FLOAT": "pcm_f32le", "DOUBLE": "pcm_f64le",
}
# A levelled section is the same audio decoded and re-encoded, so it cannot legitimately take longer
# than the render that produced it. This is a wedge guard, not a performance budget.
_LOUDNORM_TIMEOUT_S = 600


def _level_one_section(path: Path, destination: Path, *, sample_rate: int, channels: int,
                       subtype: str, target_lufs: float, ceiling_dbtp: float,
                       range_lu: float) -> bool:
    """Write `path` to `destination` at the target loudness. False when it could not be done.

    False is not an error: every failure here — no FFmpeg, a crash, a timeout — degrades to joining
    the section at the level the engine returned, which is exactly what this joiner did before
    levelling existed. A levelling pass that cannot run must never fail a job that has already spent
    twenty minutes generating.
    """
    ffmpeg = _find_ffmpeg_executable()
    if ffmpeg is None:
        return False
    try:
        completed = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-nostats", "-y", "-i", str(path),
             "-af", _loudnorm_filter(target_lufs, ceiling_dbtp, range_lu),
             "-ar", str(sample_rate), "-ac", str(channels),
             "-c:a", _PCM_CODEC_FOR_SUBTYPE.get(subtype, "pcm_s16le"),
             str(destination)],
            capture_output=True, text=True, timeout=_LOUDNORM_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and destination.is_file()


def _levelled_sections(segment_paths: list[Path], output_path: Path, family: str,
                       repo: str, sample_rate: int, channels: int,
                       subtype: str,
                       cancel_event: threading.Event | None = None) -> tuple[list[Path], bool]:
    """The sections to join, and whether they are levelled copies that the caller must clean up.

    Returns the originals and False when the family has opted out (`target` is None), when there is
    only one section (nothing to match it against, and re-levelling it would change the delivery for
    no measured reason), or when FFmpeg is unavailable — each with its reason stated, because a
    silent fallback is how a levelling pass ships for five releases without levelling anything.
    """
    policy = long_form_policy.policy_for(family, repo) or {}
    target = policy.get("section_loudness_target_lufs",
                        long_form_policy.SECTION_LOUDNESS_TARGET_LUFS)
    if target is None:
        return list(segment_paths), False
    if len(segment_paths) < 2:
        return list(segment_paths), False
    if _find_ffmpeg_executable() is None:
        print(
            f"[gen] {family} long-form sections are joined without loudness matching: FFmpeg is "
            "not available in this runtime, and it is what measures the level. Sections of a long "
            "script can differ audibly in level when this happens.",
            flush=True,
        )
        return list(segment_paths), False
    ceiling = float(policy.get("section_true_peak_ceiling_dbtp",
                               long_form_policy.SECTION_TRUE_PEAK_CEILING_DBTP))
    range_lu = float(policy.get("section_loudness_range_lu",
                                long_form_policy.SECTION_LOUDNESS_RANGE_LU))
    token = uuid.uuid4().hex
    levelled: list[Path] = []

    def abandon(reason: str) -> tuple[list[Path], bool]:
        """Drop every copy written so far and join the originals: the pre-levelling behaviour."""
        for written in levelled:
            written.unlink(missing_ok=True)
        print(reason, flush=True)
        return list(segment_paths), False

    for index, path in enumerate(segment_paths):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("long-form join cancelled")
        destination = output_path.parent / f".{output_path.name}.{token}.{index:04d}.levelled.wav"
        if not _level_one_section(path, destination, sample_rate=sample_rate, channels=channels,
                                  subtype=subtype, target_lufs=float(target),
                                  ceiling_dbtp=ceiling, range_lu=range_lu):
            return abandon(
                f"[gen] {family} section {index + 1} of {len(segment_paths)} could not be levelled, "
                "so this long-form result joins its sections at the levels the engine returned.")
        # A levelled section must come back the same shape **and the same length**. Both halves are
        # load-bearing, and the second one was missing:
        #
        #   * **The empty case is a real defect this caught.** Given a 10-sample input, `loudnorm`
        #     writes a **0-frame** file rather than failing. The first version accepted it on rate
        #     and channel count alone, so a section's audio was silently replaced by nothing:
        #     `test_qwen_clone_join_preserves_segment_audio_and_pause` joined two sections and got
        #     back only the pause — ten samples of silence where thirty were due.
        #   * **The length check is not paranoia.** `loudnorm` is exactly length-preserving —
        #     measured ratio 1.000 at 50 ms, 200 ms and 1 s — so a section whose length moved is a
        #     section the pass failed on.
        #
        # Either failure abandons levelling for the whole join rather than mixing levelled and
        # unlevelled sections, which would put a new level step where the fixed one was.
        info = _safe_audio_info(destination)
        expected = _safe_audio_info(path)
        empty = info is None or info.frames <= 0
        if (empty or expected is None
                or info.samplerate != sample_rate or info.channels != channels
                or abs(info.frames - expected.frames) > max(2, round(expected.frames * 0.01))):
            destination.unlink(missing_ok=True)
            return abandon(
                f"[gen] {family} section {index + 1} came back from loudness matching as "
                f"{'an empty file' if empty else 'a differently sized one'}, so the sections are "
                "joined at the levels the engine returned.")
        levelled.append(destination)
    print(
        f"[gen] {family} long-form sections levelled to {float(target):g} LUFS "
        f"(true peak {ceiling:g} dBTP) before the join: {len(levelled)} sections",
        flush=True,
    )
    return levelled, True


def _safe_audio_info(path: Path):
    """`soundfile.info` for a file that may not be readable, or None instead of raising."""
    import soundfile as sf
    try:
        return sf.info(str(path))
    except Exception:
        return None


# --- Section edges and level at the join (0.6.126) ---------------------------------------------
# Every engine renders a section with up to ~1.2 s of near-silence at its start and/or end, and the
# joiner appended the designed pause to that, so a designed 0.3 s pause was heard as ~1.0 s and the
# gaps were uneven (audit 2026-09-30, 300 joins: median 1.0 s on OmniVoice). So each section is cut
# back to its speech plus a short pad of its own near-silence, and the designed pause is made up of
# that pad plus inserted zeros: the silence a listener hears between two sections is the designed
# pause. Speech is never cut: the cut points are the first/last run of >= 30 ms above a threshold
# relative to the section's own level, with a pad kept beyond them for consonants and breaths.
_JOIN_WINDOW_S = 0.01
_JOIN_MIN_RUN_WINDOWS = 3
_JOIN_HEAD_PAD_S = 0.03
_JOIN_TAIL_PAD_S = 0.06
_JOIN_FADE_S = 0.01
_JOIN_THRESHOLD_BELOW_P95_DB = 33.0
_JOIN_THRESHOLD_RANGE_DB = (-60.0, -40.0)
# A one-line section is too short for `loudnorm` to measure well (the audit saw it 3-8 dB off its
# neighbours), so a section with less than this much voiced audio is matched, gently, to the voiced
# level of the sections around it. Bounded, and never into clipping: peaks that the gain would push
# past the levelling ceiling are rounded off by a soft knee (the sections come out of `loudnorm`
# with their peaks AT the ceiling, so a quiet short section with one tall transient has no headroom).
_JOIN_SHORT_VOICED_S = 4.0
_JOIN_MATCH_MAX_GAIN_DB = 6.0
_JOIN_MATCH_MIN_GAIN_DB = 0.5
_JOIN_MATCH_NEIGHBOURS = 2
_JOIN_LIMITER_KNEE = 0.7  # of the ceiling


def _analyse_join_section(x, sample_rate: int):
    """(speech_start, speech_end, voiced_db, voiced_seconds, peak) in frames, or None when the
    section is too short or has no speech-like run (then it is joined untouched)."""
    import numpy as np
    window = max(1, round(sample_rate * _JOIN_WINDOW_S))
    count = len(x) // window
    if count < _JOIN_MIN_RUN_WINDOWS * 2:
        return None
    power = np.square(x[:count * window], dtype=np.float64).mean(axis=1).reshape(count, window).mean(axis=1)
    db = 10.0 * np.log10(power + 1e-12)
    low, high = _JOIN_THRESHOLD_RANGE_DB
    threshold = float(np.clip(np.percentile(db, 95) - _JOIN_THRESHOLD_BELOW_P95_DB, low, high))
    on = db >= threshold
    run = _JOIN_MIN_RUN_WINDOWS
    starts = np.flatnonzero(np.convolve(on, np.ones(run), "valid") == run)
    if not len(starts):
        return None
    voiced_db = 10.0 * np.log10(float(np.mean(10.0 ** (db[on] / 10.0))))
    return (int(starts[0] * window), int((starts[-1] + run) * window), voiced_db,
            float(on.sum() * _JOIN_WINDOW_S), float(np.max(np.abs(x))))


def _join_match_gains_db(analyses: list) -> list[float]:
    """Gain (dB) per section: 0 except for short sections, which move toward the median voiced
    level of their neighbours by at most `_JOIN_MATCH_MAX_GAIN_DB`."""
    import numpy as np
    gains = [0.0] * len(analyses)
    long_idx = [i for i, a in enumerate(analyses) if a and a[3] >= _JOIN_SHORT_VOICED_S]
    for i, a in enumerate(analyses):
        if not a or a[3] >= _JOIN_SHORT_VOICED_S:
            continue
        refs = [j for j in long_idx if 0 < abs(j - i) <= _JOIN_MATCH_NEIGHBOURS] or long_idx
        if not refs:
            continue
        wanted = float(np.clip(np.median([analyses[j][2] for j in refs]) - a[2],
                               -_JOIN_MATCH_MAX_GAIN_DB, _JOIN_MATCH_MAX_GAIN_DB))
        if abs(wanted) >= _JOIN_MATCH_MIN_GAIN_DB:
            gains[i] = wanted
    return gains


def _join_cut(x, analysis, gain_db: float, sample_rate: int, ceiling: float = 1.0):
    """The section cut to speech + pad, faded at each real cut, gained and kept under `ceiling`
    (linear): (audio, kept_head, kept_tail)."""
    import numpy as np
    if analysis is None:
        return x, 0, 0
    first, last = analysis[0], analysis[1]
    start = max(0, first - round(_JOIN_HEAD_PAD_S * sample_rate))
    end = min(len(x), last + round(_JOIN_TAIL_PAD_S * sample_rate))
    out = x[start:end].copy()
    head, tail = first - start, end - last
    fade = round(_JOIN_FADE_S * sample_rate)
    if start > 0 and min(fade, head) > 0:
        n = min(fade, head)
        out[:n] *= np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None]
    if end < len(x) and min(fade, tail) > 0:
        n = min(fade, tail)
        out[-n:] *= np.linspace(1.0, 0.0, n, dtype=np.float32)[:, None]
    if gain_db:
        out *= np.float32(10.0 ** (gain_db / 20.0))
        knee = _JOIN_LIMITER_KNEE * ceiling
        over = np.abs(out) > knee
        if over.any():
            out[over] = np.sign(out[over]) * (knee + (ceiling - knee) * np.tanh((np.abs(out[over]) - knee) / (ceiling - knee)))
    return out, head, tail


def _join_long_form_wavs(segment_paths: list[Path], output_path: Path,
                         family: str, pause_s: float = _LONG_FORM_JOIN_PAUSE_S,
                         pause_sequence_s: Optional[list[float]] = None,
                         cancel_event: threading.Event | None = None,
                         repo: str = "") -> None:
    """Validate, level, and atomically stream independently synthesized WAV sections.

    A long script is rendered as independent sections, and nothing told the engine what level the
    section before it came out at. Measured on Breeze TTS 2 4-bit, the 14 sections of one
    1,227-word script span **4.9 LU** of integrated loudness (−30.0 to −25.0 LUFS): the narrator
    audibly steps back from the microphone at each paragraph and forward again at the next. The join
    was already correct — right pause, right order, no drift — so it was never a joining fault. It
    was the absence of a levelling pass, and this joiner had none for any family.

    Each section is now brought to its family's `section_loudness_target_lufs` before it is written
    into the join. `_levelled_sections` holds the decision and its fallbacks; the level itself is
    measured by FFmpeg rather than assumed, and the pass chosen is the one the owner's approved
    samples were made with.

    Each section is then cut back to its speech and the pause between sections made exactly the
    designed one, and a very short section is level-matched to its neighbours (see the
    `_JOIN_*` constants above): the engines return up to ~1.2 s of near-silence at a section's edges.
    """
    if not segment_paths:
        raise ValueError(f"No {family} audio segments were generated")
    import numpy as np
    import soundfile as sf

    first_info = sf.info(str(segment_paths[0]))
    sample_rate = first_info.samplerate
    channels = first_info.channels
    if sample_rate <= 0 or channels <= 0 or first_info.frames <= 0:
        raise RuntimeError(f"{family} returned an empty or invalid audio segment")

    # Validate every section before creating any candidate final file. A
    # missing, empty, or incompatible section must never look like a successful
    # partial long-form result.
    for path in segment_paths:
        info = sf.info(str(path))
        if info.samplerate != sample_rate or info.channels != channels:
            raise RuntimeError(f"{family} segments returned incompatible audio formats")
        if info.frames <= 0:
            raise RuntimeError(f"{family} returned an empty audio segment: {path.name}")

    if pause_sequence_s is not None and len(pause_sequence_s) != len(segment_paths) - 1:
        raise ValueError("Long-form pause sequence must match the section boundaries")
    pauses = pause_sequence_s or [pause_s] * (len(segment_paths) - 1)
    levelled, are_copies = _levelled_sections(segment_paths, output_path, family, repo,
                                              sample_rate, channels, first_info.subtype,
                                              cancel_event=cancel_event)
    joining_path = output_path.parent / (
        f".{output_path.name}.{uuid.uuid4().hex}.joining"
    )
    try:
        # Pass 1: where each section's speech starts and ends, and how loud its voiced part is.
        analyses = []
        for path in levelled:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("long-form join cancelled")
            audio, _ = sf.read(str(path), dtype="float32", always_2d=True)
            analyses.append(_analyse_join_section(audio, sample_rate))
        ceiling = 10.0 ** (float((long_form_policy.policy_for(family, repo) or {}).get(
            "section_true_peak_ceiling_dbtp", long_form_policy.SECTION_TRUE_PEAK_CEILING_DBTP)) / 20.0)
        gains = _join_match_gains_db(analyses)
        if any(gains):
            print(f"[gen] {family} short sections level-matched to their neighbours: "
                  + ", ".join(f"#{i + 1} {g:+.1f} dB" for i, g in enumerate(gains) if g), flush=True)
        # Pass 2: write each section cut to its speech, and make the pause between two sections the
        # designed one: the pad kept either side of the cut counts toward it.
        with sf.SoundFile(
            str(joining_path),
            mode="w",
            samplerate=sample_rate,
            channels=channels,
            format="WAV",
            subtype=first_info.subtype,
        ) as destination:
            carried_tail = 0
            for index, path in enumerate(levelled):
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("long-form join cancelled")
                audio, _ = sf.read(str(path), dtype="float32", always_2d=True)
                audio, head, tail = _join_cut(audio, analyses[index], gains[index], sample_rate, ceiling)
                if index:
                    pause_frames = round(max(0.0, pauses[index - 1]) * sample_rate) - carried_tail - head
                    if pause_frames > 0:
                        destination.write(np.zeros((pause_frames, channels), dtype=np.float32))
                destination.write(audio)
                carried_tail = tail
        os.replace(joining_path, output_path)
    finally:
        joining_path.unlink(missing_ok=True)
        # The levelled copies are working files for one join and are never a result: leaving them
        # would put a second full copy of every long-form render in the output directory.
        if are_copies:
            for written in levelled:
                written.unlink(missing_ok=True)




def _join_qwen_clone_wavs(segment_paths: list[Path], output_path: Path,
                          pause_s: float = _LONG_FORM_JOIN_PAUSE_S) -> None:
    """Compatibility wrapper for the established Qwen regression surface."""
    _join_long_form_wavs(segment_paths, output_path, "qwen3-tts", pause_s)


def _find_ffmpeg_executable() -> Optional[Path]:
    """Resolve FFmpeg from the packaged tools root."""
    return media_tools.find_executable("ffmpeg")


def _speed_delivery(family: str) -> str:
    """``native`` when the engine applies the multiplier itself, ``tempo`` when Voice Studio does.

    One decision, made once, from the same table the catalogue publishes - so "the speed control
    works for this model" is a fact about the shipped policy rather than a property of whichever
    branch of the dispatcher happens to run.
    """
    return long_form_policy.speed_policy_for(family).delivery


# Engine labels for the error text of a tempo pass, where the family name alone is not what the
# operator sees in the model list. The label comes from the family's own catalog entry, so a new
# family is named correctly without an edit here. This is a LABEL lookup, not the list of families
# that receive a tempo pass - that is `long_form_policy.speed_policy_for(family).delivery`, so a
# family can never be missing from the mechanism: a family that accepts no speed argument and is
# absent from this map would leave the public speed control doing nothing at all.
def _family_engine_label(family: str) -> str:
    entry = catalog.FAMILIES.get(family)
    if entry is not None:
        return entry.label
    return (MLX_AUDIO_FAMILIES.get(family) or {}).get("label") or family


# Qwen CustomVoice and Chatterbox can occasionally continue emitting silent
# acoustic tokens after audible speech has finished.  The defect is distinct
# from a natural pause: it is a multi-second *terminal* tail, sometimes with
# one tiny non-speech blip far after the real narration. Keep a short tail pad
# for natural word decay, and never apply this repair to another family, Qwen
# clone mode, references, or transcription.
_TERMINAL_SILENCE_MIN_SECONDS = 2.0
_TERMINAL_SILENCE_PAD_SECONDS = 0.25
_TERMINAL_SILENCE_FRAME_SECONDS = 0.02
_TERMINAL_SILENCE_RELATIVE_THRESHOLD = 0.015
_TERMINAL_SILENCE_FLOOR = 0.0005
_TERMINAL_BLIP_MAX_SECONDS = 0.10
_TERMINAL_BLIP_BRIDGE_SECONDS = 0.12

# Independently rendered OmniVoice sections receive a short micro-fade before
# joining. Do not trim from signal energy: quiet sentence-opening words and
# unwanted phoneme fragments can have the same acoustic shape, so only a
# transcript-aligned process could safely decide which samples are disposable.
# The finished WAV receives its speed pass later, so the fade is pre-scaled to
# remain about 10 ms in the delivered audio.
_OMNIVOICE_EDGE_FADE_SECONDS = 0.010


def _terminal_silence_correction_label(repo: str, family: str) -> Optional[str]:
    """Return the explicitly audited local model modes that need this repair."""
    if family == "qwen3-tts" and "qwen3-tts" in repo.lower() and _qwen3_mode_from_repo(repo) == "custom":
        return "Qwen CustomVoice"
    if family == "chatterbox-mlx":
        return "Chatterbox"
    return None


def _trim_model_terminal_silence(output_path: Path, repo: str, family: str) -> float:
    """Remove a pathological terminal silent tail from an audited local TTS WAV.

    Returns seconds removed. Audio is scanned and copied in bounded blocks so a
    long-form artifact is not loaded alongside the active MLX model. A small,
    isolated terminal blip after a pathological silent gap is ignored rather
    than preserving the entire gap. Normal interior pauses and short natural
    endings are left byte-for-byte unchanged.
    """
    label = _terminal_silence_correction_label(repo, family)
    if label is None:
        return 0.0

    import numpy as np
    import soundfile as sf

    info = sf.info(str(output_path))
    if info.frames <= 0 or info.samplerate <= 0 or info.channels <= 0:
        raise RuntimeError(f"{label} produced an invalid WAV before silence validation")

    analysis_frame = max(1, round(info.samplerate * _TERMINAL_SILENCE_FRAME_SECONDS))
    block_frames = max(analysis_frame, analysis_frame * 500)
    peak = 0.0
    with sf.SoundFile(str(output_path), mode="r") as reader:
        while True:
            samples = reader.read(block_frames, dtype="float32", always_2d=True)
            if len(samples) == 0:
                break
            peak = max(peak, float(np.max(np.abs(samples))))

    # A fully silent output remains a model failure rather than a misleading
    # zero-length success.  Existing final-artifact validation will reject it
    # only if it is empty, so make this explicit for this faulty model path.
    if peak < _TERMINAL_SILENCE_FLOOR:
        raise RuntimeError(f"{label} produced no audible speech")
    threshold = max(
        _TERMINAL_SILENCE_FLOOR,
        peak * _TERMINAL_SILENCE_RELATIVE_THRESHOLD,
    )

    voiced_frames: list[bool] = []
    with sf.SoundFile(str(output_path), mode="r") as reader:
        while True:
            samples = reader.read(block_frames, dtype="float32", always_2d=True)
            if len(samples) == 0:
                break
            full_frames = len(samples) // analysis_frame
            if full_frames:
                framed = samples[:full_frames * analysis_frame].reshape(
                    full_frames, analysis_frame, info.channels
                )
                rms = np.sqrt(np.mean(np.square(framed), axis=(1, 2)))
                voiced_frames.extend(bool(value) for value in rms >= threshold)
            if full_frames * analysis_frame < len(samples):
                remainder = samples[full_frames * analysis_frame:]
                voiced_frames.append(float(np.sqrt(np.mean(np.square(remainder)))) >= threshold)

    bridge_frames = max(1, round(_TERMINAL_BLIP_BRIDGE_SECONDS / _TERMINAL_SILENCE_FRAME_SECONDS))
    min_meaningful_frames = max(1, round(_TERMINAL_BLIP_MAX_SECONDS / _TERMINAL_SILENCE_FRAME_SECONDS))
    tail_gap_frames = max(1, round(_TERMINAL_SILENCE_MIN_SECONDS / _TERMINAL_SILENCE_FRAME_SECONDS))
    last_audible_end = 0
    index = len(voiced_frames) - 1
    while index >= 0:
        while index >= 0 and not voiced_frames[index]:
            index -= 1
        if index < 0:
            break
        end = index + 1
        start = index
        voiced_count = 1
        previous = index - 1
        while previous >= 0:
            while previous >= 0 and not voiced_frames[previous]:
                previous -= 1
            if previous < 0 or start - previous - 1 > bridge_frames:
                break
            voiced_count += 1
            start = previous
            previous -= 1

        # A one- or two-frame blip after multiple seconds of silence is not
        # narration. Ignore it and continue looking for the final meaningful
        # speech region, while retaining a similarly short sound after a
        # normal pause to avoid clipping natural endings.
        if voiced_count < min_meaningful_frames and previous >= 0 and start - previous - 1 >= tail_gap_frames:
            index = previous
            continue
        last_audible_end = min(info.frames, end * analysis_frame)
        break

    if not last_audible_end:
        raise RuntimeError(f"{label} produced no audible speech")
    keep_frames = min(
        info.frames,
        last_audible_end + round(info.samplerate * _TERMINAL_SILENCE_PAD_SECONDS),
    )
    removed_frames = info.frames - keep_frames
    if removed_frames / info.samplerate < _TERMINAL_SILENCE_MIN_SECONDS:
        return 0.0

    temporary = output_path.with_name(
        f".{output_path.stem}.qwen-terminal-trim-{uuid.uuid4().hex}.wav"
    )
    try:
        with sf.SoundFile(str(output_path), mode="r") as source, sf.SoundFile(
            str(temporary),
            mode="w",
            samplerate=info.samplerate,
            channels=info.channels,
            format="WAV",
            subtype=info.subtype,
        ) as destination:
            remaining = keep_frames
            while remaining > 0:
                samples = source.read(min(block_frames, remaining), dtype="float32", always_2d=True)
                if len(samples) == 0:
                    raise RuntimeError(f"{label} WAV ended during silence correction")
                destination.write(samples)
                remaining -= len(samples)
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)
    return removed_frames / info.samplerate


def _trim_qwen_custom_terminal_silence(output_path: Path, repo: str) -> float:
    """Compatibility wrapper for the established Qwen artifact regression tests."""
    return _trim_model_terminal_silence(output_path, repo, "qwen3-tts")


def _clean_omnivoice_section_edges(
    output_path: Path, speed: float
) -> tuple[float, float]:
    """Apply word-safe micro-fades without deleting OmniVoice audio frames."""
    import numpy as np
    import soundfile as sf

    info = sf.info(str(output_path))
    if info.frames <= 0 or info.samplerate <= 0 or info.channels <= 0:
        raise RuntimeError("OmniVoice produced an invalid WAV before edge cleanup")

    cleaned_frames = info.frames
    speed = float(speed)
    if not math.isfinite(speed):
        raise ValueError("OmniVoice speed must be finite")
    speed = max(long_form_policy.SPEED_MINIMUM,
                min(speed, long_form_policy.SPEED_MAXIMUM))
    fade_frames = min(
        round(info.samplerate * _OMNIVOICE_EDGE_FADE_SECONDS * speed),
        cleaned_frames // 2,
    )
    fade_in = (
        0.5 - 0.5 * np.cos(np.linspace(0.0, math.pi, fade_frames, dtype=np.float32))
        if fade_frames
        else np.empty(0, dtype=np.float32)
    )
    fade_out = fade_in[::-1]

    temporary = output_path.with_name(
        f".{output_path.stem}.omnivoice-edge-{uuid.uuid4().hex}.wav"
    )
    try:
        with sf.SoundFile(str(output_path), mode="r") as source, sf.SoundFile(
            str(temporary),
            mode="w",
            samplerate=info.samplerate,
            channels=info.channels,
            format=info.format,
            subtype=info.subtype,
        ) as destination:
            position = 0
            while position < cleaned_frames:
                samples = source.read(
                    min(262_144, cleaned_frames - position),
                    dtype="float32",
                    always_2d=True,
                )
                if len(samples) == 0:
                    raise RuntimeError("OmniVoice WAV ended during edge cleanup")
                if not bool(np.all(np.isfinite(samples))):
                    raise RuntimeError("OmniVoice produced non-finite audio samples")
                block_end = position + len(samples)
                if fade_frames and position < fade_frames:
                    fade_end = min(block_end, fade_frames)
                    samples[:fade_end - position] *= fade_in[position:fade_end, None]
                fade_out_start = cleaned_frames - fade_frames
                if fade_frames and block_end > fade_out_start:
                    overlap_start = max(position, fade_out_start)
                    samples[overlap_start - position:] *= fade_out[
                        overlap_start - fade_out_start:block_end - fade_out_start,
                        None,
                    ]
                destination.write(samples)
                position = block_end
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)

    return 0.0, 0.0


def _apply_mlx_output_speed(output_path: Path, speed: float, family: str,
                            cancel_event: threading.Event | None = None) -> bool:
    """Apply pitch-preserving tempo to a finished MLX-family WAV atomically.

    Qwen's current MLX implementation accepts ``speed`` without applying it,
    while VoxCPM2 has no numeric speed parameter at all. FFmpeg's ``atempo``
    changes duration while preserving pitch, so speaker identity survives and
    both short and already-joined long-form output obey one exact public speed
    control. Returns False for the 1.0 no-op.
    """
    engine = _family_engine_label(family)
    speed = max(long_form_policy.SPEED_MINIMUM,
                min(float(speed), long_form_policy.SPEED_MAXIMUM))
    if abs(speed - 1.0) < 1e-6:
        return False
    ffmpeg = _find_ffmpeg_executable()
    if ffmpeg is None:
        raise RuntimeError(
            f"{engine} speed {speed:.2f}x needs FFmpeg for pitch-preserving tempo, "
            "but Voice Studio could not find FFmpeg. Run Update or reinstall Voice Studio."
        )

    import soundfile as sf

    source = sf.info(str(output_path))
    if source.frames <= 0 or source.samplerate <= 0 or source.channels <= 0:
        raise RuntimeError(f"{engine} produced an invalid WAV before speed adjustment")
    codec = {
        "PCM_16": "pcm_s16le",
        "PCM_24": "pcm_s24le",
        "PCM_32": "pcm_s32le",
        "FLOAT": "pcm_f32le",
        "DOUBLE": "pcm_f64le",
    }.get(source.subtype, "pcm_s16le")
    temporary = output_path.with_name(f".{output_path.stem}.tempo-{uuid.uuid4().hex}.wav")
    try:
        process = subprocess.Popen(
            [
                str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-i", str(output_path), "-filter:a", f"atempo={speed:.6f}",
                "-c:a", codec, str(temporary),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=os.name == "posix",
        )
        deadline = time.monotonic() + 300
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        process.terminate()
                else:
                    process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    if os.name == "posix":
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            process.kill()
                    else:
                        process.kill()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired as exc:
                        raise NativeExecutionUncertain(
                            "FFmpeg exit could not be verified after cancellation"
                        ) from exc
                return False
            if time.monotonic() >= deadline:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        process.kill()
                else:
                    process.kill()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired as exc:
                    raise NativeExecutionUncertain(
                        "FFmpeg exit could not be verified after timeout"
                    ) from exc
                raise TimeoutError(f"{engine} speed adjustment timed out")
            time.sleep(0.02)
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            detail = (stderr or stdout or "unknown FFmpeg error").strip()
            raise RuntimeError(f"{engine} speed adjustment failed: {detail[-800:]}")
        adjusted = sf.info(str(temporary))
        expected_frames = source.frames / speed
        tolerance = max(source.samplerate * 0.20, expected_frames * 0.08)
        if (
            adjusted.frames <= 0
            or adjusted.samplerate != source.samplerate
            or adjusted.channels != source.channels
            or abs(adjusted.frames - expected_frames) > tolerance
        ):
            raise RuntimeError(
                f"{engine} speed adjustment produced an invalid audio duration or format"
            )
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _apply_qwen_output_speed(output_path: Path, speed: float) -> bool:
    """Compatibility wrapper for the established Qwen regression surface."""
    return _apply_mlx_output_speed(output_path, speed, "qwen3-tts")


def _requested_output_duration_limit(params: dict) -> Optional[float]:
    raw = params.get("max_output_duration_s")
    if raw is None:
        return None
    limit = float(raw)
    if not math.isfinite(limit) or limit <= 0 or limit > 120:
        raise ValueError("max_output_duration_s must be between 0 and 120 seconds")
    return limit


def _qwen_max_tokens_for_duration(limit_s: float, speed: float) -> int:
    """Translate a final WAV ceiling into Qwen's 12.5-Hz audio-token limit."""
    native_seconds = limit_s * speed
    return max(1, min(1200, math.floor(native_seconds * 12.5)))


def _enforce_output_duration_limit(output_path: Path, params: dict) -> None:
    limit = _requested_output_duration_limit(params)
    if limit is None:
        return
    import soundfile as sf

    info = sf.info(str(output_path))
    if info.samplerate <= 0 or info.frames <= 0:
        raise RuntimeError("Generated audio has invalid duration metadata")
    duration = info.frames / info.samplerate
    if duration > limit + 0.001:
        raise RuntimeError(
            f"Generated audio is {duration:.3f}s, exceeding the requested "
            f"{limit:.3f}s maximum"
        )


_PACKAGE_DISTRIBUTIONS = {
    "mlx_lm": "mlx-lm",
    "mlx_audio": "mlx-audio",
}
# No shipped engine pins a minimum version of its own any more: the MLX and
# Khmer families run against the versions frozen into the packaged runtime.
_PACKAGE_MIN_VERSIONS: dict[str, str] = {}


def _probe_package(name: str) -> dict:
    try:
        import importlib
        mod = importlib.import_module(name)
        version = getattr(mod, "__version__", None)
        if not version:
            try:
                version = metadata.version(_PACKAGE_DISTRIBUTIONS.get(name, name))
            except metadata.PackageNotFoundError:
                version = None
        minimum = _PACKAGE_MIN_VERSIONS.get(name)
        compatible = True
        error = None
        if version and minimum:
            try:
                compatible = Version(version) >= Version(minimum)
            except InvalidVersion:
                compatible = False
            if not compatible:
                error = f"Version {version} is too old; {minimum} or newer is required"
        return {
            "installed": True,
            "compatible": compatible,
            "version": version,
            "minimum_version": minimum,
            "error": error,
        }
    except Exception as e:
        return {
            "installed": False,
            "compatible": False,
            "version": None,
            "minimum_version": _PACKAGE_MIN_VERSIONS.get(name),
            "error": f"{type(e).__name__}: {e}",
        }


def diagnostics() -> dict:
    """Per-package + per-engine health check. The frontend renders this as a
    checklist in the Generate tab so users see what's missing BEFORE they
    submit and hit a cryptic error."""
    pkg_results = []
    pkg_status: dict[str, bool] = {}
    for pkg, role in _PACKAGE_CHECKLIST:
        probe = _probe_package(pkg)
        pkg_results.append({"package": pkg, "role": role, **probe})
        pkg_status[pkg] = probe["installed"] and probe["compatible"]

    engine_results = []
    for family, requires in _ENGINE_REQUIREMENTS.items():
        missing = [p for p in requires if not pkg_status.get(p)]
        deps_ok = not missing
        wired = family in _WIRED_FAMILIES
        engine_results.append({
            "family": family,
            "requires": requires,
            "missing": missing,
            "deps_ok": deps_ok,           # all packages importable?
            "wired": wired,               # backend has a worker for this family?
            "ready": deps_ok and wired,   # both — only "ready" engines can generate
        })

    return {
        "device": _detect_device() if TTS_AVAILABLE else None,
        "packages": pkg_results,
        "engines": engine_results,
        "any_missing": any(not p["compatible"] for p in pkg_results),
        "ready_count": sum(1 for e in engine_results if e["ready"]),
        "total_engines": len(engine_results),
    }


def _release_device_memory(device: str) -> None:
    """Free GPU/MPS/MLX memory between generations so the next call doesn't OOM.

    Important on 16 GB M-series Macs: MLX maintains its own Metal allocation
    cache separate from PyTorch's MPS cache. Without clearing both, sequential
    voxcpm-mlx jobs accumulate activation tensors across calls and eventually
    blow past Metal's per-buffer cap (~9.5 GB on M4 16 GB) — see v1.2.7 fix."""
    try:
        import gc
        gc.collect()
    except Exception:
        pass
    try:
        import torch
        if device == "mps":
            torch.mps.empty_cache()
        elif device == "cuda":
            torch.cuda.empty_cache()
    except Exception:
        pass
    # MLX-specific cache release. Prefer the current API and keep the older
    # mlx.metal fallback for existing installations.
    # Without this, MLX retains buffers from the previous generation and the
    # next mlx-audio call's activations stack on top → Metal alloc OOM.
    try:
        import mlx.core as mx
        if hasattr(mx, "clear_cache"):
            mx.clear_cache()
        elif hasattr(mx, "metal") and hasattr(mx.metal, "clear_cache"):
            mx.metal.clear_cache()
    except Exception:
        pass


# ───────────── job model ─────────────

def _origin(value: object) -> str:
    return value if value in {"hub", "local_ui", "api", "unknown"} else "unknown"


def _origin_device(value: object) -> str | None:
    text = str(value or "").strip()
    return text[:160] or None


@dataclass
class GenerationJob:
    job_id: str
    mode: str                            # "txt2speech"
    params: dict
    state: str = "queued"
    progress: float = 0.0
    output_path: Optional[str] = None
    partial_output_path: Optional[str] = None
    resolved_seed: Optional[int] = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    cancel_requested_at: Optional[float] = None
    finished_at: Optional[float] = None
    model_revision: Optional[str] = None
    voice_revision: Optional[str] = None
    media_type: Optional[str] = None
    format: Optional[str] = None
    bytes: Optional[int] = None
    sha256: Optional[str] = None
    audio_duration_s: Optional[float] = None
    audio_duration_ms: Optional[int] = None
    sample_rate_hz: Optional[int] = None
    channels: Optional[int] = None
    # Set only for a job that asked for `output_format` (compression at source): what was asked,
    # what was delivered, the lossless master's own bytes/sha256, and why a failed encode fell
    # back to the WAV. `delivery_fallback_reason` repeats that reason at the top level.
    delivery: Optional[dict] = None
    delivery_fallback_reason: Optional[str] = None
    resource_usage: Optional[dict] = None  # worker-owned per-job resource evidence
    quality_validation: Optional[dict] = None  # redacted model-owned acceptance evidence
    quality_retry_count: int = 0
    quality_retry_history: list[dict] = field(default_factory=list)
    error_code: Optional[str] = None
    client_request_params: Optional[dict] = None  # immutable idempotency comparison
    origin: str = "unknown"
    origin_device: Optional[str] = None
    chunk_index: Optional[int] = None       # current long-form local segment (1-based)
    chunk_total: Optional[int] = None       # number of long-form local segments
    execution_run_id: Optional[str] = None  # opaque active child execution identity
    executor_generation: Optional[int] = None
    receipt_acked_at: Optional[float] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None
    created_at: float = field(default_factory=time.time)

    def serialize(self) -> dict:
        duration = None
        if self.started_at is not None:
            end = self.finished_at if self.finished_at is not None else time.time()
            duration = max(0.0, end - self.started_at)
        public_params = {
            key: value for key, value in self.params.items()
            if not str(key).startswith("_")
        }
        audited_strategy = model_audits.input_limits(
            str(self.params.get("repo") or "")
        ).get("long_form_strategy")
        result = {
            "id": self.job_id,
            "mode": self.mode,
            "state": self.state,
            "progress": self.progress,
            "chunk_index": self.chunk_index,
            "chunk_total": self.chunk_total,
            "params": public_params,
            "output_path": self.output_path,
            "output_url": (
                f"/api/generate/jobs/{self.job_id}/audio"
                if self.state == "done" and self.output_path else None
            ),
            "resolved_seed": self.resolved_seed,
            "error": self.error,
            "started_at": self.started_at,
            "cancel_requested_at": self.cancel_requested_at,
            "finished_at": self.finished_at,
            "generated_at": self.finished_at,
            "receipt_acked_at": self.receipt_acked_at,
            "duration_seconds": duration,
            "resource_usage": self.resource_usage,
            "quality_validation": self.quality_validation,
            "quality_retry_count": self.quality_retry_count,
            "quality_retry_history": self.quality_retry_history,
            "error_code": self.error_code,
            # Provenance of the reference contract the job's clone path used.
            # `reference_profile_audited: false` means the execution copy was
            # derived from the model's published facts, not from an audit record,
            # and `reference_profile_sources` names the field behind every value.
            # Published so an un-audited cloning path is visible in the job
            # record instead of the request being refused.
            "reference_profile_audited": self.params.get("_reference_profile_audited"),
            "reference_profile_sources": self.params.get("_reference_profile_sources"),
        }
        if self.delivery is not None:
            result["delivery"] = self.delivery
            if self.delivery_fallback_reason:
                result["delivery_fallback_reason"] = self.delivery_fallback_reason
        return {
            **result,
            **final_tts_result(
                internal_model_id=self.params.get("repo"),
                model_revision=self.model_revision,
                voice_library_id=self.params.get("voice_library_id"),
                voice_revision=self.voice_revision,
                reference_audio_sha256=self.params.get("_reference_audio_sha256"),
                reference_source_sha256=self.params.get("_reference_source_sha256"),
                reference_preparation_revision=self.params.get(
                    "_reference_preparation_revision"
                ),
                reference_duration_s=self.params.get("_reference_duration_s"),
                long_form_strategy=audited_strategy or "unverified",
                chunk_total=self.chunk_total,
                runtime_s=duration,
                media_type=self.media_type,
                format=self.format,
                byte_size=self.bytes,
                sha256=self.sha256,
                audio_duration_s=self.audio_duration_s,
                audio_duration_ms=self.audio_duration_ms,
                sample_rate_hz=self.sample_rate_hz,
                channels=self.channels,
            ),
        }


# ───────────── manager ─────────────

class GenerationManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, GenerationJob] = {}
        # Cache one mlx-audio model at a time (loading is slow — 5-10s for
        # MLX 8-bit dequantization, longer for larger models). When the user
        # switches repos OR switches mlx-audio families (qwen3-tts →
        # chatterbox-mlx etc.), we evict the old one to free Apple Silicon
        # unified memory. One cache slot is enough since unified memory is
        # shared — loading two large mlx-audio models would OOM anyway.
        self._mlx_audio_model = None
        self._mlx_audio_model_repo: Optional[str] = None
        # Khmer VITS engines — one cache slot each. They are small, but a repo
        # switch still evicts so `loaded_model_keys` can name exactly what is
        # resident and memory policy can release it.
        self._khmer_vits_model = None
        self._khmer_vits_model_repo: Optional[str] = None
        self._khmer_onnx_model = None
        self._khmer_onnx_model_repo: Optional[str] = None
        # Native models belong to a clean spawned process, never this server
        # process. The mirror is only public inventory for memory policy/UI.
        self._native_executor = ExclusiveNativeExecutor(_native_dispatch_payload)
        self._native_loaded_model_keys: list[tuple[str, str]] = []
        self._consecutive_memory_failures = 0
        self._last_memory_event: Optional[dict] = None
        self._restart_scheduled = False
        self._last_model_activity_at: Optional[float] = None
        self._load_history()

    def is_available(self) -> bool:
        return TTS_AVAILABLE

    def list_jobs(self) -> list[GenerationJob]:
        return list(self._jobs.values())

    @staticmethod
    def _activity_time(value: object) -> Optional[float]:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    @classmethod
    def _activity_created_at(cls, job: GenerationJob) -> float:
        value = getattr(job, "created_at", None)
        if value is None:
            value = job.started_at if job.started_at is not None else job.finished_at
        parsed = cls._activity_time(value)
        return parsed if parsed is not None else 0.0

    @staticmethod
    def _activity_progress(job: GenerationJob) -> float:
        try:
            value = float(job.progress)
        except (TypeError, ValueError):
            value = 0.0
        if not math.isfinite(value):
            value = 0.0
        return max(0.0, min(1.0, value))

    @staticmethod
    def _activity_chunk(value: object) -> Optional[int]:
        if isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return max(0, parsed)

    @staticmethod
    def _activity_error_code(value: object) -> Optional[str]:
        code = str(value or "").strip()
        if re.fullmatch(r"[A-Z][A-Z0-9_.-]{0,99}", code):
            return code
        return None

    @classmethod
    def _activity_projection(cls, job: GenerationJob, *, observed_at: float) -> dict:
        params = job.params if isinstance(job.params, dict) else {}
        model = str(params.get("repo") or "").strip()[:200] or None
        state = str(job.state)
        started_at = cls._activity_time(job.started_at)
        finished_at = cls._activity_time(job.finished_at)
        origin_device = _origin_device(job.origin_device)
        projection = {
            "id": str(job.job_id)[:200],
            "state": state,
            "model": model,
            "operation": "speech",
            "progress": cls._activity_progress(job),
            "created_at": cls._activity_created_at(job),
            "started_at": started_at,
            "source": "direct",
            "origin": _origin(job.origin),
            **({"origin_device": origin_device} if origin_device else {}),
            "chunk_index": cls._activity_chunk(job.chunk_index),
            "chunk_total": cls._activity_chunk(job.chunk_total),
        }
        if state in {"queued", "running", "cancel_requested"}:
            projection["updated_at"] = observed_at
        else:
            projection["finished_at"] = finished_at
            runtime = None
            if started_at is not None and finished_at is not None:
                runtime = max(0.0, finished_at - started_at)
            projection["runtime_s"] = runtime
            # Worker errors may contain prompts, transcripts, or filesystem paths.
            # Keep the activity contract safe with a generic outcome and a
            # machine-generated uppercase code only.
            projection["error"] = "Generation failed" if state == "error" else None
            projection["error_code"] = cls._activity_error_code(job.error_code)
        return projection

    def activity_snapshot(self, observed_at: float | None = None) -> dict:
        """Return a bounded, private-data-free snapshot for the fleet controller."""
        observed = self._activity_time(observed_at)
        if observed is None:
            observed = time.time()
        with self._lock:
            jobs = [
                job for job in self._jobs.values()
                if str(job.state) in {
                    "queued", "running", "cancel_requested", "done", "error", "cancelled", "uncertain"
                }
            ]
        running_jobs = [job for job in jobs if str(job.state) in {"running", "cancel_requested"}]
        queued_jobs = [job for job in jobs if str(job.state) == "queued"]
        terminal_jobs = [
            job for job in jobs if str(job.state) in {"done", "error", "cancelled", "uncertain"}
        ]
        active_job = max(running_jobs, key=self._activity_created_at, default=None)
        if active_job is None:
            active_job = max(queued_jobs, key=self._activity_created_at, default=None)
        latest_job = max(
            terminal_jobs,
            key=lambda job: (
                self._activity_time(job.finished_at) or self._activity_created_at(job),
                self._activity_created_at(job),
            ),
            default=None,
        )
        return {
            "schema": "kh-studio.activity.v1",
            "observed_at": observed,
            "studio": "voice",
            "active": self._activity_projection(active_job, observed_at=observed) if active_job else None,
            "latest": self._activity_projection(latest_job, observed_at=observed) if latest_job else None,
        }

    def get(self, job_id: str) -> Optional[GenerationJob]:
        return self._jobs.get(job_id)

    def has_active_jobs(self) -> bool:
        return any(job.state in ("queued", "running", "cancel_requested") for job in self._jobs.values())

    def loaded_model_keys(self) -> list[tuple[str, str]]:
        loaded: list[tuple[str, str]] = []
        if self._mlx_audio_model is not None and self._mlx_audio_model_repo:
            loaded.append((self._mlx_audio_model_repo, "tts-mlx"))
        if getattr(self, "_khmer_vits_model", None) is not None and getattr(
            self, "_khmer_vits_model_repo", None
        ):
            loaded.append((self._khmer_vits_model_repo, "khmer-tts-mms"))
        if getattr(self, "_khmer_onnx_model", None) is not None and getattr(
            self, "_khmer_onnx_model_repo", None
        ):
            loaded.append((self._khmer_onnx_model_repo, "khmer-tts-onnx"))
        for item in getattr(self, "_native_loaded_model_keys", []):
            if item not in loaded:
                loaded.append(item)
        return loaded

    def has_loaded_model(self) -> bool:
        """Is a TTS model resident right now?

        The single definition of "loaded" for this manager. `loaded_model_keys()`
        already knows where each engine parks its model — the MLX engine on
        `_mlx_audio_model`, the Khmer engines on their own slots — and memory_policy's
        release path evicts exactly those. Per-job telemetry used to ask a
        second, invented question — it probed a private `_loaded_model`
        attribute that no engine has ever set — so `outcome.model_retained`
        reported False for every job on every machine, including ones whose own
        release log proves the MLX model was resident. Anything that needs to
        know whether a model is held must come through here.
        """
        return bool(self.loaded_model_keys())

    @staticmethod
    def runtime_ready_for_family(family: str) -> bool:
        """Kept as a manager method for callers that already hold the manager."""
        return runtime_ready_for_family(family)

    def model_runtime_status(self, model_entry) -> dict:
        """Truthful per-model load, dependency, and live-memory inventory."""
        loaded_repos = {repo for repo, _runtime in self.loaded_model_keys()}
        loaded = model_entry.repo in loaded_repos
        cold_required = _required_free_memory_gb(model_entry, loaded=False)
        loaded_required = _required_free_memory_gb(model_entry, loaded=True)
        required = loaded_required if loaded else cold_required
        snapshot = _memory_snapshot()
        memory_eligible = None
        # A null catalog floor means the model is deliberately not yet memory
        # qualified. Keep the runtime free-memory guard below, but do not
        # manufacture a total-memory eligibility claim from missing evidence.
        if snapshot is not None and model_entry.min_unified_memory_gb is not None:
            memory_eligible = (
                float(snapshot["total_gb"]) >= float(model_entry.min_unified_memory_gb)
                and float(snapshot["available_gb"]) >= required
            )
        return {
            "loaded": loaded,
            "runtime_ready": self.runtime_ready_for_family(model_entry.family),
            "cold_load_required_free_memory_gb": cold_required,
            "loaded_required_free_memory_gb": loaded_required,
            "required_free_memory_gb": required,
            "memory_eligible": memory_eligible,
        }

    def last_activity_at(self) -> Optional[float]:
        return self._last_model_activity_at

    def release_memory_locked(self, reason: str = "manual") -> dict:
        if self.has_active_jobs():
            raise RuntimeError("voice generation is queued or running")
        return self._evict_loaded_models(reason=reason)

    def cancel(self, job_id: str) -> bool:
        """
        Queued jobs are blocked on `_GEN_LOCK` and can't check the cancel_event
        until they acquire the lock. To make the UI react instantly, flip
        queued → cancelled immediately. The worker still sees cancel_event when
        it eventually wakes up and exits cleanly. Running jobs only get the
        signal — TTS engines (the mlx-audio families, the Khmer VITS engines) don't honor
        mid-generation cancellation, so the worker discards the result after
        generation completes.
        """
        try:
            with self._lock:
                job = self._jobs.get(job_id)
                if job is None or job.state in ("done", "error", "cancelled", "uncertain"):
                    return False
                previous_state = job.state
                previous_cancel_requested_at = job.cancel_requested_at
                previous_finished_at = job.finished_at
                job.cancel_requested_at = time.time()
                if job.state == "queued":
                    job.state = "cancelled"
                    job.finished_at = time.time()
                else:
                    job.state = "cancel_requested"
                try:
                    self._persist(strict=True)
                except PersistenceError:
                    job.state = previous_state
                    job.cancel_requested_at = previous_cancel_requested_at
                    job.finished_at = previous_finished_at
                    return False
                job.cancel_event.set()
        except PersistenceError:
            return False
        return True

    def clear_history(self) -> int:
        with self._lock:
            terminal = [jid for jid, j in self._jobs.items()
                        if j.state in ("done", "error", "cancelled", "uncertain")]
            for jid in terminal:
                self._jobs.pop(jid, None)
        self._persist()
        return len(terminal)

    def delete_job(self, job_id: str) -> bool:
        """Remove one finished job from history AND delete its audio file from disk.
        (The DELETE .../jobs/{id} route only cancels active jobs; this is for a
        finished job the user wants gone.)"""
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is None:
            return False
        if job.output_path:
            try:
                Path(job.output_path).unlink()
            except FileNotFoundError:
                pass
            except Exception as e:
                print(f"[gen] delete_job unlink failed: {e}", file=sys.stderr, flush=True)
        self._persist()
        return True

    def acknowledge_output(self, job_id: str) -> bool:
        """Persist an idempotent private Hub receipt for one final audio file."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state != "done" or not job.output_path:
                return False
            if job.receipt_acked_at is not None:
                return True
            job.receipt_acked_at = time.time()
            try:
                self._persist(strict=True)
            except PersistenceError:
                job.receipt_acked_at = None
                return False
        return True

    def output_stats(self) -> dict:
        """Total size + count of generated audio in the outputs folder — so the UI
        can show how much disk the outputs are using (history index and the files
        on disk can diverge)."""
        total = 0
        count = 0
        if OUTPUT_DIR.exists():
            for p in OUTPUT_DIR.iterdir():
                if (
                    p.name.startswith(".")
                    or not p.is_file()
                    or p.suffix.lower() not in _AUDIO_OUTPUT_SUFFIXES
                ):
                    continue
                try:
                    total += p.stat().st_size
                    count += 1
                except OSError:
                    pass
        return {"bytes": total, "count": count, "dir": str(OUTPUT_DIR.resolve())}

    def memory_status(self) -> dict:
        """Live memory and recovery state for the UI/Hub diagnostics panels."""
        return {
            "snapshot": _memory_snapshot(),
            "consecutive_failures": self._consecutive_memory_failures,
            "restart_scheduled": self._restart_scheduled,
            "last_event": self._last_memory_event,
            "service_supervised": self._service_installed(),
        }

    def prune_outputs(self, keep_last: int = 0, older_than_days: float = 0.0) -> dict:
        """Delete generated audio files to reclaim disk. Exactly one mode:
          - keep_last > 0: keep the newest N, delete the rest.
          - older_than_days > 0: delete files older than that many days.
        History entries for deleted files are trimmed too."""
        if not OUTPUT_DIR.exists():
            return {"deleted": 0, "freed_bytes": 0}
        audio_files = sorted(
            (
                p for p in OUTPUT_DIR.iterdir()
                if p.is_file() and p.suffix.lower() in _AUDIO_OUTPUT_SUFFIXES
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if keep_last > 0:
            to_delete = audio_files[keep_last:]
        elif older_than_days > 0:
            cutoff = time.time() - older_than_days * 86400
            to_delete = [p for p in audio_files if p.stat().st_mtime < cutoff]
        else:
            return {"deleted": 0, "freed_bytes": 0}
        freed = 0
        deleted = 0
        stems = set()
        for p in to_delete:
            try:
                sz = p.stat().st_size
                p.unlink()
                freed += sz
                deleted += 1
                stems.add(p.stem)
            except OSError:
                pass
        if stems:
            with self._lock:
                for jid in [j for j in self._jobs if j in stems]:
                    self._jobs.pop(jid, None)
            self._persist()
        return {"deleted": deleted, "freed_bytes": freed}

    def start_txt2speech(
        self, params: dict, *, origin: str = "unknown", origin_device: str | None = None,
    ) -> GenerationJob:
        params = dict(params)
        repo = str(params.get("repo") or "")
        model = catalog.get_model(repo)
        if model is None:
            raise ValueError(f"Unknown repo: {repo}")
        requested_budget = params.get("section_max_characters")
        resolved_budget = params.get("_resolved_section_max_characters")
        qwen_06b_base = (
            model.family == "qwen3-tts"
            and "0.6b-base" in repo.rsplit("/", 1)[-1].lower()
        )
        if (
            model.section_size_control is not None
            or requested_budget is not None
            or resolved_budget is not None
            or qwen_06b_base
        ):
            auto_budget = catalog.resolve_section_budget(
                model.family, repo, None
            )
            if requested_budget is None and resolved_budget == int(
                auto_budget["section_max_characters"]
            ):
                resolved_budget = auto_budget
            else:
                if requested_budget is None:
                    requested_budget = resolved_budget
                resolved_budget = catalog.resolve_section_budget(
                    model.family, repo, requested_budget
                )
            params.pop("section_max_characters", None)
            params["_resolved_section_max_characters"] = int(
                resolved_budget["section_max_characters"]
            )
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        request_id = str(params.get("client_request_id") or "").strip()
        with self._lock:
            if request_id:
                existing = next((
                    item for item in self._jobs.values()
                    if str(item.params.get("client_request_id") or "").strip() == request_id
                ), None)
                if existing is not None:
                    original = existing.client_request_params or existing.params
                    if original != params:
                        raise ValueError(
                            "client_request_id was already used for a different request"
                        )
                    return existing
            job = GenerationJob(
                job_id=uuid.uuid4().hex[:12],
                mode="txt2speech",
                params=params,
                client_request_params=dict(params) if request_id else None,
                origin=_origin(origin),
                origin_device=_origin_device(origin_device),
            )
            self._jobs[job.job_id] = job
            try:
                self._persist(strict=True)
            except PersistenceError:
                self._jobs.pop(job.job_id, None)
                raise
        job.thread = threading.Thread(
            target=self._run_txt2speech,
            args=(job,),
            name=f"gen-{job.job_id}",
            daemon=True,
        )
        job.thread.start()
        return job

    @staticmethod
    def _service_installed() -> bool:
        # The packaged worker is supervised by its host process.  It must not
        # discover or act on a copied Pinokio launchd marker inside the bundle.
        return False

    def _evict_loaded_models(self, reason: str = "memory-recovery") -> dict:
        """Drop both cached engines before a memory retry or supervised restart."""
        loaded = [list(item) for item in self.loaded_model_keys()]
        actions: list[str] = []
        executor = getattr(self, "_native_executor", None)
        if executor is not None and getattr(self, "_native_loaded_model_keys", []):
            outcome = executor.release({"action": "release"})
            if outcome.state == "done":
                self._native_loaded_model_keys = []
                actions.append("cleared native executor model cache")
            elif outcome.state == "uncertain":
                self._native_loaded_model_keys = []
                actions.append("native executor cache ownership became uncertain")
        for attr in ("_mlx_audio_model", "_khmer_vits_model", "_khmer_onnx_model"):
            model = getattr(self, attr, None)
            if model is not None:
                setattr(self, attr, None)
                actions.append(f"cleared {attr.removeprefix('_')}")
            model = None
        self._mlx_audio_model_repo = None
        self._khmer_vits_model_repo = None
        self._khmer_onnx_model_repo = None
        _release_device_memory("mps")
        actions.append("cleared Python, PyTorch, MLX and Metal allocator caches")
        return {"released": bool(loaded), "models": loaded, "actions": actions, "reason": reason}

    def _memory_preflight(self, model_entry, job=None) -> None:
        """Refuse a job that cannot safely fit in currently available memory."""
        snapshot = _memory_snapshot()
        if snapshot is None:
            print("[gen] memory guard unavailable; continuing without live RAM probe", flush=True)
            return

        # The model's published floor is the tier it is qualified at, not a wall. Running
        # below it is the operator's call - they may have 30,000 generations that say
        # otherwise - so it is reported and the job proceeds. What still stops a job is the
        # live check below: not enough memory free *right now* is a fact about this moment,
        # and starting anyway is what turns a slow generation into a killed worker.
        minimum_total = float(getattr(model_entry, "min_unified_memory_gb", 0) or 0)
        total = float(snapshot["total_gb"])
        if minimum_total and total < minimum_total:
            print(
                f"[gen] below the qualified tier: {model_entry.repo} is qualified at "
                f"{minimum_total:g} GB unified memory and this Mac reports {total:.1f} GB; "
                "continuing because the operator asked for it",
                flush=True,
            )

        cached = (
            (model_entry.family in MLX_AUDIO_FAMILIES
             and self._mlx_audio_model_repo == model_entry.repo
             and self._mlx_audio_model is not None)
            or (model_entry.family == "khmer-tts-mms"
                and getattr(self, "_khmer_vits_model_repo", None) == model_entry.repo
                and getattr(self, "_khmer_vits_model", None) is not None)
            or (model_entry.family == "khmer-tts-onnx"
                and getattr(self, "_khmer_onnx_model_repo", None) == model_entry.repo
                and getattr(self, "_khmer_onnx_model", None) is not None)
        )
        required = _required_free_memory_gb(model_entry, loaded=cached)
        # The operator's per-model free-memory override, forwarded by the Hub only while one is set
        # (`PUT /api/hub/memory/admission`). It replaces this estimate, as it replaces the Hub's
        # floor, so the two guards can never disagree about the same job.
        floor = ((getattr(job, "params", None) or {}).get("memory_floor_gb"))
        if floor is not None:
            required = float(floor)
        available = float(snapshot["available_gb"])
        if available < required:
            raise MemoryGuardError(
                f"Memory guard paused this job: {required:.1f} GB free is needed, "
                f"but only {available:.1f} GB is available. Close memory-heavy apps "
                "or choose a smaller model; the job was not sent to the engine."
            )
        print(
            f"[gen] memory guard: {available:.1f} GB free, "
            f"{required:.1f} GB reserved ({'cached model' if cached else 'model load'})",
            flush=True,
        )

    def _record_memory_failure(self, job: GenerationJob, exc: BaseException) -> None:
        self._consecutive_memory_failures += 1
        snapshot = _memory_snapshot()
        self._last_memory_event = {
            "time": time.time(),
            "job_id": job.job_id,
            "error": f"{type(exc).__name__}: {exc}",
            "snapshot": snapshot,
        }
        self._evict_loaded_models()
        print(
            f"[gen] memory failure {self._consecutive_memory_failures}/"
            f"{MEMORY_RESTART_FAILURES}; caches evicted",
            file=sys.stderr,
            flush=True,
        )
        if self._consecutive_memory_failures < MEMORY_RESTART_FAILURES:
            return
        if not self._service_installed():
            print(
                "[gen] repeated memory failures detected, but no supervised "
                "startup service is installed; keeping this Pinokio process alive",
                file=sys.stderr,
                flush=True,
            )
            return
        if self._restart_scheduled:
            return
        self._restart_scheduled = True
        self._persist()

        def _exit_for_launchd() -> None:
            print(
                "[gen] restarting Voice Studio after repeated memory failures; "
                "launchd KeepAlive will bring it back",
                file=sys.stderr,
                flush=True,
            )
            os._exit(75)

        threading.Timer(0.75, _exit_for_launchd).start()

    # ----- worker -----

    @staticmethod
    def _is_qwen_clone_job(job: GenerationJob) -> bool:
        return qwen_quality.is_qwen_base_clone(
            str(job.params.get("repo") or ""),
            job.params,
        )

    def _record_qwen_quality_attempt(
        self,
        job: GenerationJob,
        *,
        evidence: dict,
        accepted: bool,
        rejection_code: Optional[str] = None,
    ) -> None:
        """Keep redacted evidence for every Qwen quality attempt.

        The final ``quality_validation`` field intentionally remains the
        latest result for compatibility.  The history makes a one-retry
        failure auditable without retaining the transcript, audio, or a
        filesystem path.
        """
        if not self._is_qwen_clone_job(job):
            return
        record = {
            **dict(evidence or {}),
            "attempt": len(job.quality_retry_history) + 1,
            "accepted": bool(accepted),
        }
        if rejection_code:
            record["rejection_code"] = rejection_code
        job.quality_retry_history.append(record)

    @staticmethod
    def _qwen_job_duration_limit(job: GenerationJob) -> float:
        text = str(job.params.get("text") or "")
        speed = float(job.params.get("speed") or 1.0)
        # The override is optional: only the 1.7B 8-bit checkpoint and the 0.6B
        # builds publish a section-size control, so a 1.7B Base 4-bit clone job
        # arrives without this key. The ceiling must describe the sectioning the
        # engine actually used, so it reads the override exactly like
        # _dispatch_txt2speech does and falls back to the family policy.
        budget = job.params.get("_resolved_section_max_characters")
        chunks = _internal_mlx_text_chunks(
            "qwen3-tts",
            str(job.params.get("repo") or ""),
            text,
            max_chars_override=budget,
        ) or [text]
        pauses = max(0, len(chunks) - 1) * _long_form_join_pause_s(
            "qwen3-tts", str(job.params.get("repo") or "")
        )
        return round(
            sum(qwen_quality.automatic_duration_limit(chunk, speed) for chunk in chunks)
            + pauses,
            3,
        )

    def _validate_qwen_output_locked(
        self,
        job: GenerationJob,
        output_path: Path,
    ) -> None:
        """Accept a Qwen clone only when Whisper agrees with the request."""
        if not self._is_qwen_clone_job(job):
            return
        if not job.params.get("_qwen_reference_validation"):
            raise qwen_quality.QwenQualityError(
                "QWEN_REFERENCE_VALIDATION_REQUIRED",
                "Qwen3-TTS reference validation evidence is missing.",
                {"validator_revision": qwen_quality.VALIDATOR_REVISION},
            )

        # Qwen and Whisper must never co-reside on unified memory. The rejected
        # local artifact is certain and unpaid, so it is safe to validate and,
        # if necessary, regenerate it once.
        self._evict_loaded_models("qwen-output-validation")
        from .transcription import manager as stt_manager

        try:
            result = stt_manager.transcribe_locked(
                str(output_path),
                model_repo=qwen_quality.validator_repo(str(job.params.get("repo") or "")),
                language=job.params.get("language"),
                word_timestamps=True,
            )
            job.quality_validation = qwen_quality.validate_output(
                str(job.params.get("text") or ""),
                result,
                max_duration_s=self._qwen_job_duration_limit(job),
            )
        except qwen_quality.QwenQualityError:
            raise
        except Exception as exc:
            print(
                f"[gen] Qwen output validator unavailable: {type(exc).__name__}",
                file=sys.stderr,
                flush=True,
            )
            raise qwen_quality.QwenQualityError(
                "QWEN_OUTPUT_VALIDATION_UNAVAILABLE",
                "Whisper Large could not validate the Qwen output.",
                {
                    "validator_revision": qwen_quality.VALIDATOR_REVISION,
                    "error_type": type(exc).__name__,
                },
            ) from exc
        finally:
            stt_manager.release_memory_locked("qwen-output-validation-complete")

    @staticmethod
    def _require_readable_qwen_reference(
        reference_path: Optional[Path], *, source: str,
    ) -> None:
        """Fail with one machine-readable, actionable code per reference defect.

        A missing voice, a dangling path, a directory, an unreadable file and a
        zero-byte clip are different user problems; reporting them all as
        "unavailable" left the caller with nothing to act on.  No path or
        transcript is retained in the evidence.
        """
        evidence = {
            "validator_revision": qwen_quality.VALIDATOR_REVISION,
            "reference_source": source,
        }
        if reference_path is None:
            raise qwen_quality.QwenQualityError(
                "QWEN_REFERENCE_AUDIO_MISSING",
                "No Qwen reference voice is selected. Choose a voice from the Voices "
                "library or attach a reference clip, then generate again.",
                {**evidence, "reason": "not_selected"},
            )
        try:
            is_file = reference_path.is_file()
        except OSError as exc:
            raise qwen_quality.QwenQualityError(
                "QWEN_REFERENCE_AUDIO_UNAVAILABLE",
                "The selected Qwen reference voice could not be inspected. "
                "Re-select or re-upload the reference clip.",
                {**evidence, "reason": "unreadable", "error_type": type(exc).__name__},
            ) from exc
        if not is_file:
            if reference_path.exists():
                raise qwen_quality.QwenQualityError(
                    "QWEN_REFERENCE_AUDIO_UNAVAILABLE",
                    "The selected Qwen reference is not a regular audio file. "
                    "Re-upload the reference clip.",
                    {**evidence, "reason": "not_a_file"},
                )
            raise qwen_quality.QwenQualityError(
                "QWEN_REFERENCE_AUDIO_MISSING",
                "The selected Qwen reference audio no longer exists. "
                "Re-upload the reference clip or choose another voice.",
                {**evidence, "reason": "absent"},
            )
        try:
            size = reference_path.stat().st_size
        except OSError as exc:
            raise qwen_quality.QwenQualityError(
                "QWEN_REFERENCE_AUDIO_UNAVAILABLE",
                "The selected Qwen reference audio could not be read. "
                "Re-upload the reference clip.",
                {**evidence, "reason": "unreadable", "error_type": type(exc).__name__},
            ) from exc
        if size <= 0:
            raise qwen_quality.QwenQualityError(
                "QWEN_REFERENCE_AUDIO_EMPTY",
                "The selected Qwen reference audio is empty. Upload a 3-15 second clip "
                "of continuous speech.",
                {**evidence, "reason": "empty", "bytes": size},
            )
        try:
            with reference_path.open("rb") as handle:
                handle.read(1)
        except OSError as exc:
            raise qwen_quality.QwenQualityError(
                "QWEN_REFERENCE_AUDIO_UNREADABLE",
                "The selected Qwen reference audio could not be opened for reading. "
                "Check the file's permissions, then re-upload it.",
                {**evidence, "reason": "not_readable", "error_type": type(exc).__name__},
            ) from exc

    def _validate_qwen_reference_locked(self, job: GenerationJob) -> None:
        """Validate the exact reference inside the already durable local job."""
        if not self._is_qwen_clone_job(job):
            return
        if job.params.get("_qwen_reference_validation"):
            return
        private_path = str(job.params.get("_reference_audio_path") or "").strip()
        transcript = str(job.params.get("ref_transcript") or "").strip()
        source = "private_reference"
        if private_path:
            reference_path = Path(private_path)
        else:
            from . import voices as voices_module

            source = "voice_library"
            voice_id = str(job.params.get("voice_library_id") or "").strip()
            reference_path = voices_module.library.reference_path(voice_id)
            if not transcript:
                transcript = voices_module.library.transcript(voice_id) or ""
        self._require_readable_qwen_reference(reference_path, source=source)
        if not transcript.strip():
            raise qwen_quality.QwenQualityError(
                "QWEN_REFERENCE_TRANSCRIPT_REQUIRED",
                "Qwen3-TTS Base needs the exact transcript for its reference audio. "
                "Add the reference transcript to the selected voice before generating.",
                {
                    "validator_revision": qwen_quality.VALIDATOR_REVISION,
                    "reference_source": source,
                },
            )

        cache_key = qwen_quality.reference_cache_key(reference_path, transcript)
        evidence = qwen_quality.cached_reference_evidence(cache_key)
        if evidence is None:
            self._evict_loaded_models("qwen-reference-validation")
            from .transcription import manager as stt_manager

            try:
                result = stt_manager.transcribe_locked(
                    str(reference_path),
                    model_repo=qwen_quality.validator_repo(str(job.params.get("repo") or "")),
                    language=job.params.get("language"),
                    word_timestamps=True,
                )
                evidence = qwen_quality.validate_reference(transcript, result)
                qwen_quality.save_reference_evidence(cache_key, evidence)
            except qwen_quality.QwenQualityError:
                raise
            except Exception as exc:
                print(
                    f"[gen] Qwen reference validator unavailable: {type(exc).__name__}",
                    file=sys.stderr,
                    flush=True,
                )
                raise qwen_quality.QwenQualityError(
                    "QWEN_REFERENCE_VALIDATION_UNAVAILABLE",
                    "Whisper Large could not validate the Qwen reference.",
                    {
                        "validator_revision": qwen_quality.VALIDATOR_REVISION,
                        "reference_source": source,
                        "error_type": type(exc).__name__,
                    },
                ) from exc
            finally:
                stt_manager.release_memory_locked("qwen-reference-validation-complete")
        job.params["_qwen_reference_validation"] = evidence

    def _run_txt2speech(self, job: GenerationJob) -> None:
        # NOTE: don't re-set `job.state = "queued"` here. The dataclass default
        # already initialised it to "queued", and `cancel()` may legitimately
        # have flipped it to "cancelled" between submit_job() and this thread
        # being scheduled. Re-asserting "queued" outside the lock clobbers that
        # cancel decision — the cancel_event flag still survives, but the UI
        # would see the job pop back to "queued" until the worker eventually
        # acquired the lock (potentially minutes later).
        with _GEN_LOCK:
            if job.cancel_event.is_set():
                job.state = "cancelled"
                job.finished_at = time.time()
                self._persist()
                return

            job.state = "running"
            job.started_at = time.time()
            self._last_model_activity_at = job.started_at
            job.progress = 0.05          # move the bar off zero the moment work starts
            memory_failure_seen = False
            print(
                f"[gen] starting {job.job_id}: repo={job.params.get('repo')} "
                f"text_chars={len(str(job.params.get('text') or ''))} "
                f"private_reference={bool(job.params.get('_reference_audio_path'))}",
                flush=True,
            )

            if not TTS_AVAILABLE:
                job.state = "error"
                job.error = f"TTS engine not installed: {TTS_IMPORT_ERROR}"
                job.finished_at = time.time()
                self._persist()
                return

            output_path: Optional[Path] = None
            # A job that asked for `m4a` leaves the loop holding its verified WAV master here and
            # is delivered after this lock is released, so the next generation starts at once.
            held_master: Optional[Path] = None
            memory_retries = 0
            quality_retries = 0
            while True:
                try:
                    final_output_path = OUTPUT_DIR / f"{job.job_id}.wav"
                    output_path = OUTPUT_DIR / f".{job.job_id}.{uuid.uuid4().hex}.partial.wav"
                    job.partial_output_path = str(output_path)
                    self._persist()
                    self._validate_qwen_reference_locked(job)
                    self._dispatch_txt2speech(job, output_path)
                    self._apply_parent_output_speed(job, output_path)
                    _enforce_output_duration_limit(output_path, job.params)
                    self._validate_qwen_output_locked(job, output_path)
                    self._record_qwen_quality_attempt(
                        job,
                        evidence=job.quality_validation or {},
                        accepted=True,
                    )
                    self._record_local_revision_evidence(job)
                    self._record_final_audio_evidence(job, output_path)
                    if job.params.get("output_format") == "m4a":
                        output_path = self._hold_master_or_cancel(job, output_path)
                        held_master = output_path
                    else:
                        output_path = self._promote_or_cancel_output(
                            job, output_path, final_output_path,
                        )
                    if output_path is not None and held_master is None:
                        print(f"[gen] done {job.job_id} → {output_path}", flush=True)
                    break
                except Exception as e:
                    if output_path is not None:
                        try:
                            output_path.unlink(missing_ok=True)
                        except OSError:
                            pass
                    job.partial_output_path = None
                    if isinstance(e, NativeExecutionUncertain):
                        job.state = "uncertain"
                        job.error = "Generation status is uncertain because native execution could not be verified."
                        job.error_code = "EXECUTION_IDENTITY_LOST"
                        break
                    if (
                        isinstance(e, qwen_quality.QwenQualityError)
                        and e.code in qwen_quality.RETRYABLE_OUTPUT_CODES
                        and self._is_qwen_clone_job(job)
                        and quality_retries < 1
                    ):
                        quality_retries += 1
                        job.quality_retry_count = quality_retries
                        job.quality_validation = {
                            **e.evidence,
                            "accepted": False,
                            "rejection_code": e.code,
                        }
                        self._record_qwen_quality_attempt(
                            job,
                            evidence=e.evidence,
                            accepted=False,
                            rejection_code=e.code,
                        )
                        original_seed = (
                            job.resolved_seed
                            if job.resolved_seed is not None
                            else int(job.params.get("seed") or 0)
                        )
                        job.params["_qwen_attempt_seed"] = (
                            int(original_seed) + 1
                        ) % (2**32)
                        job.params["_resolved_section_max_characters"] = min(
                            int(job.params["_resolved_section_max_characters"]),
                            qwen_quality.RETRY_SECTION_MAX_CHARACTERS,
                        )
                        self._evict_loaded_models("qwen-quality-retry")
                        print(
                            f"[gen] rejected Qwen clone {job.job_id} ({e.code}); "
                            "retrying once with a new seed and shorter sections",
                            file=sys.stderr,
                            flush=True,
                        )
                        continue
                    if (
                        _is_memory_failure(e)
                        and memory_retries < MEMORY_RETRY_LIMIT
                    ):
                        memory_retries += 1
                        memory_failure_seen = True
                        print(
                            f"[gen] memory failure on {job.job_id}; evicting models "
                            f"and retrying once ({memory_retries}/{MEMORY_RETRY_LIMIT})",
                            file=sys.stderr,
                            flush=True,
                        )
                        self._record_memory_failure(job, e)
                        if self._restart_scheduled:
                            job.state = "error"
                            job.error = (
                                "Generation hit repeated memory failures. Voice Studio "
                                "is restarting automatically to recover."
                            )
                            break
                        continue
                    if job.cancel_event.is_set():
                        job.state = "cancelled"
                    else:
                        job.state = "error"
                        if isinstance(e, qwen_quality.QwenQualityError):
                            job.error_code = e.code
                            job.quality_validation = {
                                **e.evidence,
                                "accepted": False,
                                "rejection_code": e.code,
                            }
                            self._record_qwen_quality_attempt(
                                job,
                                evidence=e.evidence,
                                accepted=False,
                                rejection_code=e.code,
                            )
                        job.error = f"{type(e).__name__}: {e}"
                        if not isinstance(e, MemoryGuardError) and _is_memory_failure(e):
                            memory_failure_seen = True
                            self._record_memory_failure(job, e)
                        print(f"[gen] error {job.job_id}: {job.error}", file=sys.stderr, flush=True)
                        traceback.print_exc()
                    break
            job.finished_at = time.time()
            self._last_model_activity_at = job.finished_at
            # A held master is a finished generation whose file is still being compressed: for
            # the model and the resource record it is "done".
            outcome_state = "done" if held_master is not None else job.state
            if outcome_state != "done":
                self._evict_loaded_models("failed-or-cancelled-generation")
            self._finish_native_resource_usage(
                job,
                state=outcome_state,
                memory_failure=memory_failure_seen,
                restart_scheduled=self._restart_scheduled,
                model_retained=(
                    outcome_state == "done" and self.has_loaded_model()
                ),
            )
            self._persist()
        if held_master is not None:
            self._deliver_m4a(job, held_master)

    def _promote_or_cancel_output(self, job: GenerationJob, partial_output: Path,
                                  final_output: Path) -> Path | None:
        """Serialize cancel acceptance with the irreversible final promotion."""
        with self._lock:
            if job.cancel_event.is_set():
                partial_output.unlink(missing_ok=True)
                job.partial_output_path = None
                job.state = "cancelled"
                return None
            os.replace(partial_output, final_output)
            job.partial_output_path = None
            job.output_path = str(final_output.resolve())
            job.progress = 1.0
            job.state = "done"
            self._consecutive_memory_failures = 0
            return final_output

    def _hold_master_or_cancel(self, job: GenerationJob, partial_output: Path) -> Path | None:
        """The ``m4a`` twin of :meth:`_promote_or_cancel_output`: keep the master, publish nothing.

        Cancel acceptance is still serialized with the one irreversible step. The verified WAV is
        renamed to a hidden master name and the job stays ``running`` until
        :meth:`_deliver_m4a` publishes the file the caller will actually fetch; recording the
        master as ``partial_output_path`` means a worker restart removes it with the other partials.
        """
        with self._lock:
            if job.cancel_event.is_set():
                partial_output.unlink(missing_ok=True)
                job.partial_output_path = None
                job.state = "cancelled"
                return None
            master = OUTPUT_DIR / f".{job.job_id}.master.wav"
            os.replace(partial_output, master)
            job.partial_output_path = str(master)
            job.progress = 0.97
            return master

    def _deliver_m4a(self, job: GenerationJob, master: Path) -> None:
        """Compress the held WAV master to AAC and publish it; run **outside** ``_GEN_LOCK``.

        The facts of the audio (duration, rate, channels) stay the master's. Any failure to encode
        publishes the WAV instead and records ``delivery_fallback_reason``: a finished generation
        is never failed by compression. The master is this worker's own temporary file, removed
        only after the encoded file is verified and in place.
        """
        from components import audio_encode

        wav_final = OUTPUT_DIR / f"{job.job_id}.wav"
        m4a_final = OUTPUT_DIR / f"{job.job_id}.m4a"
        encoded_path = OUTPUT_DIR / f".{job.job_id}.{uuid.uuid4().hex}.encoded.m4a"
        record = {"requested": "m4a", "master_media_type": job.media_type,
                  "master_bytes": job.bytes, "master_sha256": job.sha256}
        encoded = None
        try:
            encoded = audio_encode.encode_m4a(
                master, encoded_path, profile="narration",
                expected_seconds=job.audio_duration_s)
        except Exception as exc:  # noqa: BLE001 - deliver the WAV, never fail the finished job
            record.update(applied="wav", delivery_fallback_reason=str(exc)[:400])
            print(f"[gen] m4a encode failed for {job.job_id}; delivering WAV: {exc}",
                  file=sys.stderr, flush=True)
        try:
            with self._lock:
                if job.cancel_event.is_set():
                    for path in (master, encoded_path):
                        path.unlink(missing_ok=True)
                    job.partial_output_path = None
                    job.state = "cancelled"
                    job.finished_at = time.time()
                    self._persist()
                    return
                if encoded is not None:
                    os.replace(encoded_path, m4a_final)
                    master.unlink(missing_ok=True)
                    job.output_path = str(m4a_final.resolve())
                    job.media_type = encoded["media_type"]
                    job.format = encoded["format"]
                    job.bytes = encoded["bytes"]
                    job.sha256 = encoded["sha256"]
                    record.update(applied="m4a", codec=encoded["codec"],
                                  bitrate_kbps=encoded["bitrate_kbps"], channels=1)
                else:
                    os.replace(master, wav_final)
                    job.output_path = str(wav_final.resolve())
                    job.delivery_fallback_reason = record["delivery_fallback_reason"]
                job.delivery = record
                job.partial_output_path = None
                job.progress = 1.0
                job.state = "done"
                job.finished_at = time.time()
                self._last_model_activity_at = job.finished_at
                self._consecutive_memory_failures = 0
                print(f"[gen] done {job.job_id} → {job.output_path}", flush=True)
                self._persist()
        except Exception as exc:  # noqa: BLE001 - the job must reach a terminal state
            for path in (master, encoded_path):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            with self._lock:
                job.partial_output_path = None
                job.output_path = None
                job.state = "error"
                job.error = f"{type(exc).__name__}: {exc}"
                job.finished_at = time.time()
                self._persist()

    @staticmethod
    def _record_local_revision_evidence(job: GenerationJob) -> None:
        """Fence supported local output to exact cached weights and voice bytes.

        GenStudio validates this evidence before publishing the final asset. A
        preset voice is versioned by the immutable model snapshot plus its
        engine-owned preset key; a cloned voice is versioned by the digest of
        the exact reference audio Voice Studio used.
        """
        repo = str(job.params.get("repo") or "").strip()
        normalized_repo = repo.lower()
        is_qwen = "qwen3-tts" in normalized_repo
        is_voxcpm = "voxcpm2" in normalized_repo
        is_chatterbox = "chatterbox" in normalized_repo
        is_fish = "fish-audio-s2-pro" in normalized_repo
        is_omnivoice = "omnivoice" in normalized_repo
        is_pocket = "pocket-tts" in normalized_repo
        if not (
            is_qwen or is_voxcpm or is_chatterbox
            or is_fish or is_omnivoice or is_pocket
        ):
            return
        revision = cache.snapshot_revision(repo)
        if not revision:
            raise RuntimeError("Local TTS model revision evidence is unavailable")
        job.model_revision = revision
        mode = _qwen3_mode_from_repo(repo) if is_qwen else "voxcpm"
        if is_qwen and mode == "custom":
            speaker = str(job.params.get("preset_speaker") or "").strip().lower()
            if not speaker:
                raise RuntimeError("Qwen3-TTS preset voice evidence is unavailable")
            job.voice_revision = f"{revision}:preset:{speaker}"
            return
        voice_id = str(job.params.get("voice_library_id") or "").strip()
        private_revision = str(
            job.params.get("_reference_audio_sha256") or ""
        ).strip().lower()
        if (
            (is_qwen and mode == "clone")
            or (is_voxcpm and (voice_id or private_revision))
            or is_chatterbox
            or (is_fish and (voice_id or private_revision))
            or (is_omnivoice and (voice_id or private_revision))
            or (is_pocket and (voice_id or private_revision))
        ):
            if private_revision:
                job.voice_revision = private_revision
                return
            from . import voices as voices_module

            if not voice_id:
                raise RuntimeError("Cloned voice revision evidence is unavailable")
            voice = voices_module.library.get(voice_id)
            if voice is None or not voice.audio_sha256:
                raise RuntimeError("Cloned voice revision evidence is unavailable")
            job.voice_revision = voice.audio_sha256.lower()

    @staticmethod
    def _record_qwen_revision_evidence(job: GenerationJob) -> None:
        """Compatibility wrapper retained for existing Qwen callers/tests."""
        GenerationManager._record_local_revision_evidence(job)

    @staticmethod
    def _record_final_audio_evidence(job: GenerationJob, output_path: Path) -> None:
        """Derive immutable media facts from the one final local WAV."""
        import soundfile as sf

        info = sf.info(str(output_path))
        if (
            info.format != "WAV"
            or info.frames <= 0
            or info.samplerate <= 0
            or info.channels not in (1, 2)
        ):
            raise RuntimeError("Generated audio is not a valid mono/stereo WAV")
        payload = output_path.read_bytes()
        if not payload.startswith(b"RIFF") or payload[8:12] != b"WAVE":
            raise RuntimeError("Generated audio has an invalid WAV container")
        duration_s = info.frames / info.samplerate
        job.media_type = "audio/wav"
        job.format = "wav"
        job.bytes = len(payload)
        job.sha256 = hashlib.sha256(payload).hexdigest()
        job.audio_duration_s = round(duration_s, 6)
        job.audio_duration_ms = round(duration_s * 1000)
        job.sample_rate_hz = info.samplerate
        job.channels = info.channels

    @staticmethod
    def _finish_native_resource_usage(job: GenerationJob, *, state: str,
                                      memory_failure: bool, restart_scheduled: bool,
                                      model_retained: bool) -> None:
        """Annotate child-measured telemetry with the parent-owned job outcome."""
        if isinstance(job.resource_usage, dict):
            job.resource_usage["outcome"] = {
                "state": state,
                "memory_failure": bool(memory_failure),
                "restart_scheduled": bool(restart_scheduled),
                "model_retained": bool(model_retained),
            }

    @staticmethod
    def _record_native_resource_usage(job: GenerationJob, telemetry: dict) -> None:
        """Keep all child command evidence while reporting whole-job peaks."""
        if not isinstance(job.resource_usage, dict):
            job.resource_usage = telemetry
            return
        prior = job.resource_usage
        for section, fields in {
            "worker": ("peak_rss_gb", "peak_process_count"),
            "host": ("maximum_used_gb", "maximum_used_percent", "maximum_swap_used_gb"),
            "mlx": ("peak_active_gb", "peak_cache_gb", "reported_peak_gb"),
        }.items():
            target = prior.get(section)
            incoming = telemetry.get(section)
            if not isinstance(target, dict) or not isinstance(incoming, dict):
                continue
            for field_name in fields:
                before, after = target.get(field_name), incoming.get(field_name)
                if isinstance(after, (int, float)) and (
                    not isinstance(before, (int, float)) or after > before
                ):
                    target[field_name] = after
        sampling = prior.get("sampling")
        incoming_sampling = telemetry.get("sampling")
        if isinstance(sampling, dict) and isinstance(incoming_sampling, dict):
            sampling["samples"] = int(sampling.get("samples") or 0) + int(
                incoming_sampling.get("samples") or 0
            )
            for field_name, reducer in (("started_at", min), ("finished_at", max)):
                before, after = sampling.get(field_name), incoming_sampling.get(field_name)
                if isinstance(after, (int, float)) and (
                    not isinstance(before, (int, float))
                ):
                    sampling[field_name] = after
                elif isinstance(after, (int, float)):
                    sampling[field_name] = reducer(before, after)
        prior["command_count"] = int(prior.get("command_count") or 1) + 1

    def _dispatch_txt2speech(self, job: GenerationJob, output_path: Path) -> None:
        """Run native inference in the one clean child process.

        The server persists the run token before handing it to the child and
        remains the only process allowed to promote the partial file.
        """
        model = catalog.get_model(str(job.params.get("repo") or ""))
        text = str(job.params.get("text") or "").strip()
        if (
            model is not None
            and model.family in _SECTIONED_FAMILIES
            and text
        ):
            normalized = _normalized_speech_text(model.family, text, job.params)
            chunks = _internal_mlx_text_chunks(
                model.family, model.repo, normalized,
                max_chars_override=job.params.get("_resolved_section_max_characters"),
            )
            if len(chunks) > 1:
                self._dispatch_mlx_long_form(job, output_path, model, normalized, chunks)
                return
        value = self._run_native_payload(
            job,
            {"action": "generate", "job_id": job.job_id,
             "params": job.params, "output_path": str(output_path)},
        )
        job.resolved_seed = value.get("resolved_seed")
        job.chunk_index = value.get("chunk_index")
        job.chunk_total = value.get("chunk_total")
        job.progress = max(job.progress, float(value.get("progress") or 0))

    def _run_native_payload(self, job: GenerationJob, payload: dict) -> dict:
        """Dispatch one fenced native command and accept only its own result."""
        executor = getattr(self, "_native_executor", None)
        if executor is None:
            executor = ExclusiveNativeExecutor(_native_dispatch_payload)
            self._native_executor = executor
            self._native_loaded_model_keys = []

        def record_started(run_id: str, generation: int) -> None:
            job.execution_run_id = run_id
            job.executor_generation = generation
            self._persist(strict=True)

        try:
            outcome = executor.run(payload, job.cancel_event, on_started=record_started)
        finally:
            job.execution_run_id = None
            job.executor_generation = None
        value = outcome.value or {}
        if "loaded_models" in value:
            self._native_loaded_model_keys = [tuple(item) for item in value["loaded_models"]]
        telemetry = value.get("resource_usage")
        if (
            isinstance(telemetry, dict)
            and telemetry.get("schema") == resource_telemetry.SCHEMA
            and telemetry.get("schema_version") == resource_telemetry.SCHEMA_VERSION
        ):
            self._record_native_resource_usage(job, telemetry)
        if outcome.state == "done":
            if value.get("execution_error"):
                raise RuntimeError(str(value["execution_error"]))
            return value
        self._native_loaded_model_keys = [] if outcome.terminated else getattr(
            self, "_native_loaded_model_keys", []
        )
        if outcome.state == "cancelled":
            job.cancel_event.set()
            return {}
        if outcome.state == "uncertain":
            raise NativeExecutionUncertain(outcome.error or "native execution identity was lost")
        raise RuntimeError(outcome.error or "native execution failed")

    def _dispatch_mlx_long_form(self, job: GenerationJob, output_path: Path, model,
                                text: str, chunks: list[str]) -> None:
        """Keep section sequencing and atomic assembly in the server parent."""
        requested_seed = job.params.get("_qwen_attempt_seed", job.params.get("seed"))
        if requested_seed is None or int(requested_seed) < 0:
            import random
            job.resolved_seed = random.randint(0, 2**32 - 1)
        else:
            job.resolved_seed = int(requested_seed)
        segment_root = Path(tempfile.mkdtemp(prefix=f"voice_sections_{job.job_id}_"))
        segment_paths: list[Path] = []
        job.chunk_total = len(chunks)
        job.chunk_index = 0
        try:
            for index, chunk in enumerate(chunks, start=1):
                if job.cancel_event.is_set():
                    return
                job.chunk_index = index
                job.progress = max(job.progress, min(0.93, 0.08 + (index - 1) / len(chunks) * 0.85))
                self._persist()
                segment_path = segment_root / f"section_{index:03d}.wav"
                section_params = {
                    **job.params,
                    "text": chunk,
                    "_native_single_section": True,
                    "_qwen_attempt_seed": job.resolved_seed,
                    # The reused child keeps MLX RNG state between commands,
                    # matching the former parent loop which seeded once per job.
                    "_native_continue_rng": index > 1,
                }
                self._run_native_payload(
                    job,
                    {"action": "generate", "job_id": job.job_id,
                     "params": section_params, "output_path": str(segment_path)},
                )
                if job.cancel_event.is_set():
                    return
                if not segment_path.exists():
                    raise RuntimeError(f"{model.family} produced no audio for section {index}")
                segment_paths.append(segment_path)
                job.progress = max(job.progress, min(0.95, 0.08 + index / len(chunks) * 0.85))
                self._persist()
            _join_long_form_wavs(
                segment_paths, output_path, model.family,
                pause_s=_long_form_join_pause_s(model.family, model.repo),
                repo=model.repo,
                pause_sequence_s=(
                    _production_join_pauses_s(
                        text, chunks, float(job.params.get("speed") or 1.0),
                    )
                    if (
                        model.family == "omnivoice"
                        or (model.family == "qwen3-tts" and "1.7b-base" in model.repo.lower())
                    )
                    else None
                ),
                cancel_event=job.cancel_event,
            )
        finally:
            shutil.rmtree(segment_root, ignore_errors=True)

    @staticmethod
    def _apply_parent_output_speed(job: GenerationJob, output_path: Path) -> None:
        """Keep the FFmpeg child under the server job's cancellation custody."""
        model = catalog.get_model(str(job.params.get("repo") or ""))
        if model is not None and _speed_delivery(model.family) == "tempo":
            speed = float(job.params.get("speed") or 1.0)
            # Say so. The direct path logs its tempo pass, but every Hub job runs natively and comes
            # back through here, so a working speed control left no trace at all: proving that a
            # requested 2x actually reached the file meant comparing artifact durations by hand.
            if _apply_mlx_output_speed(output_path, speed, model.family,
                                       cancel_event=job.cancel_event):
                print(f"[gen] {model.family} applied pitch-preserving {speed:.2f}x tempo "
                      f"to the joined artifact", flush=True)

    def _dispatch_txt2speech_direct(self, job: GenerationJob, output_path: Path,
                                    *, postprocess_speed: bool = True) -> None:
        """Pick the right backend pipeline based on model family."""
        params = job.params
        repo = params["repo"]
        model = catalog.get_model(repo)
        if model is None:
            raise ValueError(f"Repo {repo} is not in the catalog")
        if cache.cache_state(repo) != "cached":
            raise ValueError(f"Model {repo} is not fully cached locally — download it first")

        family = model.family
        engine = engine_for_family(family)
        self._memory_preflight(model, job)
        if not self.runtime_ready_for_family(family):
            raise RuntimeError(
                f"The `{_ENGINE_RUNTIME_LABEL[engine]}` runtime is unavailable in this packaged "
                "app. Replace it with a tested standalone release that includes the runtime, "
                "then relaunch."
            )
        if engine == catalog.ENGINE_MLX_AUDIO:
            self._generate_mlx_audio(job, model, output_path, postprocess_speed=postprocess_speed)
        elif engine == catalog.ENGINE_TRANSFORMERS_VITS:
            self._generate_khmer_vits(job, model, output_path)
        elif engine == catalog.ENGINE_ONNX_VITS:
            self._generate_khmer_onnx(job, model, output_path)
        else:
            # Unreachable while `_validate_family_wiring` holds at import. Kept because a
            # dispatcher that silently falls through is how a family reaches an engine it was
            # never wired for; this names the engine and the file to edit instead.
            raise NotImplementedError(
                f"No worker implemented for engine '{engine}' (family '{family}'); add one to "
                "_WORKER_BY_ENGINE in components/voice/app/backend/generation.py"
            )

    # ──────────────────────────────────────────────────────────────────────
    # mlx-audio worker — shared across every mlx-audio-backed family
    # (qwen3-tts, voxcpm-mlx, chatterbox-mlx, omnivoice, fish-audio-mlx,
    # pocket-tts) and any future one.
    #
    # Architecture:
    # 1. _mlx_audio_snapshot_path: find the HF cache snapshot folder for a repo
    # 2. _mlx_audio_get_model: lazy-load + cache (one slot, evicts on switch)
    # 3. _generate_mlx_audio: main entry — calls the right mode resolver to
    #    build gen_kwargs, then invokes mlx-audio.generate_audio()
    # 4. _resolve_mlx_kwargs_*: per-mode kwarg builders (see MLX_AUDIO_FAMILIES)
    #
    # To add a new mlx-audio family: register it in MLX_AUDIO_FAMILIES, and if
    # its kwarg shape doesn't match an existing mode resolver, add a new one.
    # ──────────────────────────────────────────────────────────────────────

    def _mlx_audio_snapshot_path(self, repo: str) -> Path:
        """Find the snapshot folder to load for a cached repo.

        The verified package's revision comes first: it is the revision the manifest names, the one
        the app verified, and the one the job writes into its record as immutable evidence. The
        Hugging Face cache is only a fallback for repos that arrived another way, because it can hold
        several revisions at once - and this used to return `candidates[0]`, whichever folder the
        filesystem happened to list first. A Mac that had a newer revision verified but an older one
        lying in the cache could therefore load the older files while recording the newer revision,
        which makes the job's evidence wrong and the result irreproducible.
        """
        package = cache.package_snapshot(repo)
        if package is not None and package[1].is_dir():
            return package[1]
        repo_dir = cache.repo_cache_dir(repo)
        snapshots = repo_dir / "snapshots"
        if not snapshots.exists():
            raise RuntimeError(
                f"No snapshots folder for {repo} at {snapshots}. "
                "Re-download from the Models tab or re-import."
            )
        candidates = [s for s in snapshots.iterdir()
                      if s.is_dir() and not s.name.startswith(".")]
        if not candidates:
            raise RuntimeError(f"No snapshot subfolder under {snapshots}")
        if len(candidates) > 1:
            # Deterministic, and the best guess available: the most recently written revision.
            candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return candidates[0]

    def _mlx_audio_get_model(self, repo: str):
        """Lazy-load and cache the mlx-audio model for `repo`. Evicts any
        previously-loaded model when switching repos so unified memory is only
        ever holding one mlx-audio model at a time — important on Apple Silicon
        where all GPU memory comes out of the same pool as system RAM.

        One slot is enough across ALL mlx-audio families (qwen3-tts, voxcpm-mlx,
        chatterbox-mlx etc.) — loading two large mlx-audio models simultaneously
        would OOM anyway."""
        if self._mlx_audio_model_repo == repo and self._mlx_audio_model is not None:
            return self._mlx_audio_model

        # Evict previous model — important on Apple Silicon to avoid OOM.
        if self._mlx_audio_model is not None:
            print(f"[gen] evicting cached mlx-audio model ({self._mlx_audio_model_repo})", flush=True)
            try:
                del self._mlx_audio_model
            except Exception:
                pass
            self._mlx_audio_model = None
            self._mlx_audio_model_repo = None
            _release_device_memory("mps")

        from mlx_audio.tts.utils import load_model
        mlx_memory.bound_cache()
        snapshot_path = self._mlx_audio_snapshot_path(repo)
        print(f"[gen] loading mlx-audio model from {snapshot_path}", flush=True)
        # IMPORTANT: pass the Path object, NOT str(snapshot_path).
        # mlx-audio's get_model_name_parts walks Path.parts to extract name
        # hints (it finds the `models--<org>--<repo>` segment and parses the
        # name for engine clues). When given a string it only takes the last
        # "/" segment — which is just the snapshot hash like
        # "be15d8bf101a4a400c568b387fb69dce0d37239b" — so the dispatch falls
        # back to config.json's model_type alone, which is how a repo whose
        # config reports its LM backbone (`qwen2`) instead of the TTS engine
        # failed with `ValueError: Model type qwen2 not supported for tts.`
        # See v1.2.8 fix.
        entry = catalog.get_model(repo)
        model = load_model(snapshot_path)
        if entry is not None and entry.family in mlx_memory.PROFILES:
            mlx_memory.bound_model(entry.family, model)   # docs/mlx-memory-discipline.md
        if entry is not None and entry.family == "voxcpm-mlx":
            # Backport mlx-audio 3b37f335 without advancing the shared pin by
            # 93 unrelated commits: materialize this lazy constant before the
            # cached model is reused by a later generation worker thread.
            import mlx.core as mx
            decoder = getattr(getattr(model, "audio_vae", None), "decoder", None)
            sr_boundaries = getattr(decoder, "_sr_boundaries", None)
            if sr_boundaries is not None:
                mx.eval(sr_boundaries)
        self._mlx_audio_model = model
        self._mlx_audio_model_repo = repo
        self._last_model_activity_at = time.time()
        return model

    def _generate_mlx_audio(self, job: GenerationJob, model_entry, output_path: Path,
                            *, postprocess_speed: bool = True) -> None:
        """
        Unified worker for every mlx-audio-backed TTS family.

        Per-family behavior is configured in MLX_AUDIO_FAMILIES — this function
        is purely scaffolding (load model, build kwargs, call generate_audio,
        save output). The mode resolvers below decide which kwargs to pass.

        mlx-audio's `generate_audio()` writes wav files to an output directory
        rather than returning bytes. We request one joined file so newline and
        sentence segmentation never drops everything after the first segment.
        """
        import tempfile
        from mlx_audio.tts.generate import generate_audio

        params = job.params
        family = model_entry.family
        family_config = MLX_AUDIO_FAMILIES.get(family)
        if family_config is None:
            # Defensive — caller checks this, but keep the guard for tests.
            raise RuntimeError(f"Family {family!r} not in MLX_AUDIO_FAMILIES")

        text = (params.get("text") or "").strip()
        if not text:
            raise ValueError("text is required")
        text = _normalized_speech_text(family, text, params)

        speed = float(params.get("speed", 1.0))
        speed = max(long_form_policy.SPEED_MINIMUM,
                    min(speed, long_form_policy.SPEED_MAXIMUM))

        gen_kwargs: dict = {"text": text}
        output_duration_limit = _requested_output_duration_limit(params)
        qwen_clone = qwen_quality.is_qwen_base_clone(model_entry.repo, params)
        if qwen_clone:
            # Model-owned default: callers no longer need to remember a safety
            # ceiling. A supplied compatibility limit may tighten it further.
            gen_kwargs["max_tokens"] = qwen_quality.max_tokens_for_text(text, speed)
            if output_duration_limit is not None:
                gen_kwargs["max_tokens"] = min(
                    gen_kwargs["max_tokens"],
                    _qwen_max_tokens_for_duration(output_duration_limit, speed),
                )
        elif family == "qwen3-tts" and output_duration_limit is not None:
            gen_kwargs["max_tokens"] = _qwen_max_tokens_for_duration(output_duration_limit, speed)
        # Speed is passed upstream ONLY to an engine that applies the multiplier itself. For every
        # other family the argument is omitted entirely and one pitch-preserving tempo pass is
        # applied to the finished artifact - because the alternatives are all silent: Chatterbox
        # documents speed as "Ignored" and OmniVoice accepts and discards it. A control that is
        # accepted and dropped is worse than one that is refused, because the caller cannot tell.
        if _speed_delivery(family) == "native":
            gen_kwargs["speed"] = speed
        if family == "fish-audio-mlx":
            # Prevent a second model-owned split inside Voice Studio's private
            # 300-character sections. Fish still stops naturally at EOS.
            gen_kwargs["chunk_length"] = 100000

        # Dispatch to the per-mode resolver to populate voice / clone / instruct
        # kwargs. Each resolver may raise ValueError if required inputs are missing.
        mode = family_config["mode"]
        mode_label = self._resolve_mlx_kwargs(mode, family, model_entry, params, gen_kwargs)

        # Optional cfg knobs — currently only VoxCPM2 uses them.
        if family_config.get("uses_cfg"):
            gen_kwargs["cfg_value"] = max(0.5, min(float(params.get("cfg_value", 2.0)), 6.0))
            gen_kwargs["inference_timesteps"] = max(4, min(int(params.get("inference_timesteps", 7)), 50))
            gen_kwargs["warmup_patches"] = max(0, min(int(params.get("voxcpm_warmup_patches", 0)), 4))
            gen_kwargs["max_tokens"] = max(256, min(int(params.get("voxcpm_max_tokens", 2000)), 4096))

        # MLX models sample through mlx.core.random. Apply the recorded seed
        # immediately before inference so history reuse is genuinely repeatable.
        seed = params.get("_qwen_attempt_seed", params.get("seed"))
        if seed is None or seed < 0:
            import random
            seed = random.randint(0, 2**32 - 1)
        job.resolved_seed = int(seed)

        if job.cancel_event.is_set():
            return

        model = self._mlx_audio_get_model(model_entry.repo)

        # Unique temp dir per job so concurrent jobs (when we eventually allow
        # them) don't collide on the audio_000.wav name.
        temp_dir = Path(tempfile.mkdtemp(prefix=f"mlxaudio_{family}_{job.job_id}_"))
        try:
            import mlx.core as mx
            if not params.get("_native_continue_rng"):
                mx.random.seed(int(seed) % (2**32))
            log_extras = []
            if family_config.get("uses_cfg"):
                log_extras.append(f"steps={gen_kwargs['inference_timesteps']}")
                log_extras.append(f"cfg={gen_kwargs['cfg_value']}")
                log_extras.append(f"warmup={gen_kwargs['warmup_patches']}")
                log_extras.append(f"max_tokens={gen_kwargs['max_tokens']}")
            extras = f" [{', '.join(log_extras)}]" if log_extras else ""
            print(
                f"[gen] {family} {mode_label} ({len(text)} chars){extras}",
                flush=True,
            )
            internal_chunks = (
                [text]
                if params.get("_native_single_section")
                else _internal_mlx_text_chunks(
                    family,
                    model_entry.repo,
                    text,
                    max_chars_override=params.get("_resolved_section_max_characters"),
                )
            )
            if len(internal_chunks) > 1:
                self._generate_mlx_long_form_sections(
                    job, family, model, gen_kwargs, internal_chunks, temp_dir,
                    output_path, generate_audio, model_entry.repo,
                )
                if not job.cancel_event.is_set():
                    print(
                        f"[gen] {family} joined {len(internal_chunks)} stable sections: {output_path}",
                        flush=True,
                    )
            else:
                generate_audio(
                    model=model,
                    output_path=str(temp_dir),
                    join_audio=True,
                    **gen_kwargs,
                )

                produced = temp_dir / "audio.wav"
                if not produced.exists():
                    # mlx-audio sometimes uses a different naming scheme — find any wav.
                    candidates = sorted(temp_dir.glob("*.wav"))
                    if not candidates:
                        raise _no_wav_produced_error(temp_dir, family, gen_kwargs)
                    produced = candidates[0]

                trimmed_seconds = _trim_model_terminal_silence(
                    produced, model_entry.repo, family
                )
                if trimmed_seconds:
                    print(
                        f"[gen] {_terminal_silence_correction_label(model_entry.repo, family)} removed {trimmed_seconds:.2f}s "
                        "of pathological terminal silence",
                        flush=True,
                    )
                shutil.move(str(produced), str(output_path))
                sr = model_entry.sample_rate_hz or family_config["default_sample_rate"]
                print(f"[gen] {family} saved WAV at {sr} Hz: {output_path}", flush=True)

            if (postprocess_speed and not job.cancel_event.is_set()
                    and _speed_delivery(family) == "tempo"):
                if _apply_mlx_output_speed(output_path, speed, family):
                    print(
                        f"[gen] {family} applied pitch-preserving {speed:.2f}x tempo",
                        flush=True,
                    )
        finally:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass
            # Release MLX Metal cache + Python refs so the NEXT job starts from
            # a clean memory baseline. Without this, sequential VoxCPM2-mlx
            # voice-cloning calls accumulate activation tensors across jobs and
            # the second/third call hits Metal's per-buffer cap (~9.5 GB on M4
            # 16 GB) → Abort trap: 6. The cached `self._mlx_audio_model` stays
            # loaded (model weights are not the OOM trigger); only the
            # per-generation activation buffers get freed. See v1.2.7 fix.
            _release_device_memory("mps")

    @staticmethod
    def _mlx_audio_output_file(output_dir: Path, *, family: str = "",
                              gen_kwargs: Optional[dict] = None) -> Path:
        """Find the WAV written by mlx-audio across its output naming variants."""
        produced = output_dir / "audio.wav"
        if produced.exists():
            return produced
        candidates = sorted(output_dir.glob("*.wav"))
        if candidates:
            return candidates[0]
        raise _no_wav_produced_error(output_dir, family, gen_kwargs or {})

    def _generate_mlx_long_form_sections(self, job: GenerationJob, family: str, model,
                                         gen_kwargs: dict, chunks: list[str], temp_dir: Path,
                                         output_path: Path, generate_audio,
                                         model_repo: str = "") -> None:
        """Render independently verified sections, then join them into one WAV."""
        segment_paths: list[Path] = []
        job.chunk_total = len(chunks)
        job.chunk_index = 0
        for index, chunk in enumerate(chunks, start=1):
            if job.cancel_event.is_set():
                return
            job.chunk_index = index
            job.progress = max(job.progress, min(0.93, 0.08 + (index - 1) / len(chunks) * 0.85))
            segment_dir = temp_dir / f"section_{index:03d}"
            segment_dir.mkdir()
            print(
                f"[gen] {family} stable section {index}/{len(chunks)} ({len(chunk)} chars)",
                flush=True,
            )
            generate_audio(
                model=model,
                output_path=str(segment_dir),
                join_audio=True,
                **{
                    **gen_kwargs,
                    "text": chunk,
                    **(
                        {
                            "max_tokens": qwen_quality.max_tokens_for_text(
                                chunk,
                                float(job.params.get("speed") or 1.0),
                            )
                        }
                        if family == "qwen3-tts"
                        and qwen_quality.is_qwen_base_clone(model_repo, job.params)
                        else {}
                    ),
                },
            )
            if job.cancel_event.is_set():
                return
            segment_path = self._mlx_audio_output_file(
                segment_dir, family=family, gen_kwargs={**gen_kwargs, "text": chunk}
            )
            preserves_qwen_17b_frames = bool(
                family == "qwen3-tts"
                and long_form_policy.qwen_17b_assembly_invariants(model_repo)
            )
            trimmed_seconds = (
                0.0
                if preserves_qwen_17b_frames
                else _trim_model_terminal_silence(segment_path, model_repo, family)
            )
            if trimmed_seconds:
                print(
                    f"[gen] {_terminal_silence_correction_label(model_repo, family)} section {index}/{len(chunks)} removed "
                    f"{trimmed_seconds:.2f}s of pathological terminal silence",
                    flush=True,
                )
            if family == "omnivoice":
                removed_start, removed_end = _clean_omnivoice_section_edges(
                    segment_path,
                    float(job.params.get("speed") or 1.0),
                )
                if removed_start or removed_end:
                    print(
                        f"[gen] OmniVoice section {index}/{len(chunks)} removed "
                        f"{removed_start:.3f}s leading and {removed_end:.3f}s trailing "
                        "non-speech edge audio",
                        flush=True,
                    )
            segment_paths.append(segment_path)
            job.progress = max(job.progress, min(0.95, 0.08 + index / len(chunks) * 0.85))
        _join_long_form_wavs(
            segment_paths,
            output_path,
            family,
            pause_s=_long_form_join_pause_s(family, model_repo),
            repo=model_repo,
            pause_sequence_s=(
                _production_join_pauses_s(
                    str(gen_kwargs.get("text") or ""),
                    chunks,
                    float(job.params.get("speed") or 1.0),
                )
                if (
                    family == "omnivoice"
                    or (
                        family == "qwen3-tts"
                        and "1.7b-base" in model_repo.lower()
                    )
                )
                else None
            ),
        )

    def _generate_qwen_clone_long_form(self, job: GenerationJob, model, gen_kwargs: dict,
                                       chunks: list[str], temp_dir: Path, output_path: Path,
                                       generate_audio) -> None:
        """Compatibility wrapper for Qwen-specific regression tests."""
        self._generate_mlx_long_form_sections(
            job, "qwen3-tts", model, gen_kwargs, chunks, temp_dir,
            output_path, generate_audio,
            "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit",
        )

    def _resolve_mlx_kwargs(self, mode: str, family: str, model_entry, params: dict,
                            gen_kwargs: dict) -> str:
        """
        Per-mode kwarg resolver. Mutates `gen_kwargs` in place to add the
        family's specific kwargs (voice / instruct / ref_audio / ref_text /
        exaggeration / etc.) and returns a human-readable mode label for the
        log line.

        Modes (the full set; each maps to one entry in MLX_AUDIO_FAMILIES):
        - "qwen3"             — repo-name picks custom (preset) / design (text) / clone (ref)
        - "voxcpm_flex"       — any combo of ref_audio + ref_text + instruct
        - "clone_with_intensity" — needs ref_audio, optional exaggeration
        - "voice_or_clone"    — voice param OR ref_audio (zero-shot or clone)
        - "omnivoice"         — design prompt and/or clone
        - "fish_audio"        — optional ref_audio + optional instruct + full sampling
        """
        from . import voices as voices_module

        if mode == "qwen3":
            return self._mlx_kwargs_qwen3(model_entry, params, gen_kwargs, voices_module)
        if mode == "voxcpm_flex":
            return self._mlx_kwargs_voxcpm_flex(params, gen_kwargs, voices_module)
        if mode == "clone_with_intensity":
            return self._mlx_kwargs_clone_with_intensity(model_entry, params, gen_kwargs, voices_module)
        if mode == "voice_or_clone":
            return self._mlx_kwargs_voice_or_clone(params, gen_kwargs, voices_module)
        if mode == "design_clone_or_direct":
            return self._mlx_kwargs_design_clone_or_direct(params, gen_kwargs, voices_module)
        if mode == "omnivoice":
            return self._mlx_kwargs_omnivoice(params, gen_kwargs, voices_module)
        if mode == "fish_audio":
            return self._mlx_kwargs_fish_audio(params, gen_kwargs, voices_module)
        # A family in MLX_AUDIO_FAMILIES whose mode is not handled here is a wiring bug, and it
        # must name itself and both files rather than raise a bare "unknown mode" from inside a job.
        raise RuntimeError(
            f"Unknown mlx-audio mode {mode!r} for family {family!r}. "
            "Add a resolver here and the matching mode in MLX_AUDIO_FAMILIES "
            "(components/voice/app/backend/generation.py)."
        )

    # --- per-mode kwarg builders ---

    def _mlx_kwargs_qwen3(self, model_entry, params, gen_kwargs, voices_module) -> str:
        """Qwen3-TTS: repo-name selects between custom (preset speakers),
        design (natural-language prompt), and clone (reference audio)."""
        repo = model_entry.repo
        mode = _qwen3_mode_from_repo(repo)

        if mode == "custom":
            speaker = (params.get("preset_speaker") or "").strip()
            if not speaker:
                raise ValueError(
                    "CustomVoice mode needs a preset_speaker. Pick one from the speaker dropdown."
                )
            valid_speakers = {s["id"] for s in QWEN3_PRESET_SPEAKERS}
            if speaker not in valid_speakers:
                raise ValueError(
                    f"Unknown preset speaker {speaker!r}. Valid options: {sorted(valid_speakers)}"
                )
            gen_kwargs["voice"] = speaker
            instruct = (params.get("instruct") or "").strip()
            gen_kwargs["instruct"] = instruct or "Normal tone"
            return f"custom (speaker={speaker})"

        if mode == "design":
            instruct = (params.get("voice_design_prompt") or params.get("instruct") or "").strip()
            if not instruct:
                raise ValueError(
                    "VoiceDesign mode needs a voice description. Use natural language: "
                    "'deep gravelly male, slow' or pick a preset."
                )
            gen_kwargs["instruct"] = instruct
            return f"design ({len(instruct)} char prompt)"

        if mode == "clone":
            voice_id = (params.get("voice_library_id") or "").strip()
            if not voice_id and not params.get("_reference_audio_path"):
                raise ValueError(
                    "Voice cloning needs a reference voice or private reference upload."
                )
            if voice_id and "1.7b-base" in repo.lower():
                self._prepare_qwen_17b_library_reference(
                    repo, voice_id, params, voices_module
                )
            self._inject_voice_clone(voice_id, params, gen_kwargs, voices_module,
                                     fallback_transcript="")
            if not str(gen_kwargs.get("ref_text") or "").strip():
                raise ValueError(
                    "Qwen3-TTS 0.6B Base needs the exact transcript for its reference audio."
                )
            return f"clone (voice={voice_id or 'private-reference'})"

        raise RuntimeError(f"Unknown qwen3-tts mode {mode!r}")

    @staticmethod
    def _prepare_qwen_17b_library_reference(
        repo: str, voice_id: str, params: dict, voices_module
    ) -> None:
        """Prepare a saved Qwen 1.7B reference exactly like a private upload.

        Voice-library files are durable source assets, not model inputs.  The
        adapter receives only the bounded, normalized execution copy and its
        immutable preparation evidence.  A failed preparation never falls back
        to the source file.
        """
        voice = voices_module.library.get(voice_id)
        if voice is None:
            raise ValueError(f"Voice {voice_id} not found in library")
        source_path = voices_module.library.reference_path(voice_id)
        if source_path is None or not source_path.is_file():
            raise ValueError(f"Reference audio for voice {voice_id} is missing on disk")
        try:
            source_bytes = source_path.read_bytes()
        except OSError as exc:
            raise ValueError(f"Reference audio for voice {voice_id} cannot be read") from exc
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        declared_sha256 = str(getattr(voice, "audio_sha256", "") or "").lower()
        if declared_sha256 and declared_sha256 != source_sha256:
            raise ValueError(
                f"Reference audio for voice {voice_id} no longer matches its saved checksum"
            )
        transcript = str(params.get("ref_transcript") or "").strip()
        if not transcript:
            transcript = str(voices_module.library.transcript(voice_id) or "").strip()
        # The timings stored with the voice travel with it. Without them a clip longer than the
        # model's window is refused outright (`REFERENCE_TRANSCRIPT_ALIGNMENT_REQUIRED`), so a voice
        # shared across the fleet - or one prepared here from a long clip - would install correctly
        # and then fail to clone on every machine. `reference_audio` picks the window the transcript
        # describes, which is the whole reason the alignment is required.
        stored_segments = (voices_module.library.segments(voice_id)
                           if hasattr(voices_module.library, "segments") else [])
        try:
            prepared = reference_audio.prepare(
                audio_bytes=source_bytes,
                filename=source_path.name,
                model_id=repo,
                transcript=transcript,
                transcript_segments=stored_segments,
            )
        except reference_audio.ReferenceAudioError as exc:
            raise ValueError(exc.detail) from exc
        params.update({
            "_reference_audio_path": prepared["path"],
            "_reference_audio_sha256": prepared["derived_sha256"],
            "_reference_source_sha256": prepared["source_sha256"],
            "_reference_preparation_revision": prepared["preparation_revision"],
            "_reference_duration_s": prepared["duration_seconds"],
            "ref_transcript": prepared.get("transcript") or transcript,
            # A saved voice is prepared through the same model contract as a
            # private upload, so it publishes the same provenance: an un-audited
            # cloning path must be visible here too, not silently blank.
            "_reference_profile_audited": bool(prepared.get("profile_audited")),
            "_reference_profile_sources": prepared.get("profile_sources") or {},
        })

    def _mlx_kwargs_voxcpm_flex(self, params, gen_kwargs, voices_module) -> str:
        """VoxCPM2 MLX: zero-shot, voice design, reference cloning, or
        transcript-aware continuation cloning with optional style control."""
        design_prompt = (params.get("voice_design_prompt") or params.get("instruct") or "").strip()
        if design_prompt:
            if len(design_prompt) > 500:
                raise ValueError("VoxCPM2 voice design instructions must be 500 characters or fewer")
            gen_kwargs["instruct"] = design_prompt

        voice_id = (params.get("voice_library_id") or "").strip() or None
        if voice_id or params.get("_reference_audio_path"):
            self._inject_voice_clone(
                voice_id or "", params, gen_kwargs, voices_module,
                fallback_transcript="",
            )
            ref_path = gen_kwargs["ref_audio"]
            transcript = (gen_kwargs.get("ref_text") or "").strip()
            if transcript:
                # VoxCPM2 clones from `ref_audio` + `ref_text`. Keep the transcript:
                # when `ref_text` is missing, mlx-audio loads its own STT model
                # (mlx-community/whisper-large-v3-turbo-asr-fp16) to transcribe the
                # reference. That model is not a packaged dependency, so an offline
                # Mac has no copy and generation fails with "produced no audio" -
                # which reads as a memory problem and is not one.
                gen_kwargs["prompt_audio"] = str(ref_path)
                gen_kwargs["prompt_text"] = transcript
            else:
                # No saved transcript. Keep an empty `ref_text` rather than dropping
                # it: dropping it makes mlx-audio load its own STT model
                # (mlx-community/whisper-large-v3-turbo-asr-fp16) to transcribe the
                # reference, and that model is not a packaged dependency, so an
                # offline Mac failed with a misleading "produced no audio". An empty
                # transcript keeps the reference timbre with no STT load, which is
                # the owner's accepted trade (2026-09-22).
                gen_kwargs["ref_text"] = ""

        if "prompt_audio" in gen_kwargs and "instruct" in gen_kwargs:
            return "ultimate clone + style"
        if "prompt_audio" in gen_kwargs:
            return "ultimate clone (transcript-aware)"
        if "ref_audio" in gen_kwargs and "instruct" in gen_kwargs:
            return "reference clone + style"
        if "ref_audio" in gen_kwargs:
            return "reference clone"
        if "instruct" in gen_kwargs:
            return "voice-design"
        return "zero-shot"

    def _mlx_kwargs_clone_with_intensity(self, model_entry, params, gen_kwargs, voices_module) -> str:
        """Chatterbox (MLX): requires a reference audio for voice cloning,
        plus an optional `exaggeration` knob for emotion intensity."""
        voice_id = (params.get("voice_library_id") or "").strip()
        if not voice_id and not params.get("_reference_audio_path"):
            raise ValueError(
                "Chatterbox needs a reference voice or private reference upload."
            )
        language = (params.get("language") or "").strip().lower()
        catalog_entry = catalog.get_model(model_entry.repo)
        support = getattr(catalog_entry, "language_support", None)
        supported_languages = (
            support.codes if support is not None else catalog.CHATTERBOX_LANGUAGE_CODES
        )
        if not language:
            raise ValueError(
                "Chatterbox needs a language code. Choose one of: "
                + ", ".join(supported_languages)
            )
        if language not in supported_languages:
            raise ValueError(
                f"Unsupported Chatterbox language code: {language}. Choose one of: "
                + ", ".join(supported_languages)
            )
        self._inject_voice_clone(voice_id, params, gen_kwargs, voices_module,
                                 fallback_transcript="")
        gen_kwargs["lang_code"] = language
        # Exaggeration is a Chatterbox-specific dial (0.0–1.0, default 0.5).
        # Surface it via the generic `cfg_value` slot in params so the UI
        # doesn't need a brand-new field per family.
        turbo = "turbo" in model_entry.repo.lower()
        exaggeration = max(0.0, min(float(params.get("cfg_value", 0.5)), 1.0))
        if not turbo:
            gen_kwargs["exaggeration"] = exaggeration
        gen_kwargs["temperature"] = max(0.05, min(float(params.get("temperature", 0.8)), 2.0))
        gen_kwargs["repetition_penalty"] = max(
            1.0, min(float(params.get("chatterbox_repetition_penalty", 1.2)), 2.0)
        )
        gen_kwargs["top_p"] = max(0.05, min(float(params.get("chatterbox_top_p", 1.0)), 1.0))
        if not turbo:
            gen_kwargs["cfg_weight"] = max(
                0.0, min(float(params.get("chatterbox_cfg_weight", 0.5)), 1.0)
            )
            gen_kwargs["min_p"] = max(
                0.0, min(float(params.get("chatterbox_min_p", 0.05)), 1.0)
            )
        detail = "turbo sampling" if turbo else f"exaggeration={exaggeration:.2f}"
        return f"clone (voice={voice_id or 'private-reference'}, {detail})"

    def _mlx_kwargs_omnivoice(self, params, gen_kwargs, voices_module) -> str:
        """OmniVoice MLX supports voice design, cloning, or both together."""
        instruct = (params.get("voice_design_prompt") or "").strip()
        voice_id = (params.get("voice_library_id") or "").strip()
        has_reference = bool(voice_id or params.get("_reference_audio_path"))
        if not instruct and not has_reference:
            raise ValueError(
                "OmniVoice needs either a reference voice or voice traits such as "
                "'female, british accent'."
            )
        if instruct:
            gen_kwargs["instruct"] = instruct
        if has_reference:
            self._inject_voice_clone(
                voice_id, params, gen_kwargs, voices_module, fallback_transcript="."
            )
            gen_kwargs["ref_audio_max_duration_s"] = 10.0

        gen_kwargs["num_steps"] = max(
            4, min(int(params.get("omnivoice_num_steps", 32)), 64)
        )
        gen_kwargs["guidance_scale"] = max(
            0.0, min(float(params.get("omnivoice_guidance_scale", 2.0)), 8.0)
        )
        duration = params.get("omnivoice_duration_s")
        if duration is not None:
            gen_kwargs["duration_s"] = max(0.5, min(float(duration), 120.0))
        reference_label = voice_id or "private-reference"
        if has_reference and instruct:
            return f"combined (clone={reference_label} + traits)"
        if has_reference:
            return f"clone (voice={reference_label})"
        return f"design ({len(instruct)} char traits)"

    def _mlx_kwargs_fish_audio(self, params, gen_kwargs, voices_module) -> str:
        """Fish Audio S2 Pro: optional reference clone plus style/instruct.

        Fish's upstream API accepts a reference audio/transcript pair and a
        natural-language ``instruct``. Voice Studio keeps the reference
        transcript optional because Fish can clone from the clip alone.
        """
        voice_id = (params.get("voice_library_id") or "").strip()
        has_reference = bool(voice_id or params.get("_reference_audio_path"))
        instruct = (params.get("voice_design_prompt") or params.get("instruct") or "").strip()
        if instruct:
            gen_kwargs["instruct"] = instruct[:500]
        if has_reference:
            self._inject_voice_clone(
                voice_id, params, gen_kwargs, voices_module, fallback_transcript=""
            )

        gen_kwargs["max_tokens"] = max(
            128, min(int(params.get("fish_max_tokens", 1024)), 4096)
        )
        gen_kwargs["temperature"] = max(
            0.05, min(float(params.get("fish_temperature", 0.7)), 2.0)
        )
        gen_kwargs["top_p"] = max(
            0.05, min(float(params.get("fish_top_p", 0.7)), 1.0)
        )
        gen_kwargs["top_k"] = max(
            0, min(int(params.get("fish_top_k", 30)), 100)
        )
        reference_label = voice_id or "private-reference"
        if has_reference and instruct:
            return f"clone + style (voice={reference_label})"
        if has_reference:
            return f"clone (voice={reference_label})"
        if instruct:
            return f"zero-shot + style ({len(instruct)} char prompt)"
        return "zero-shot"

    def _mlx_kwargs_voice_or_clone(self, params, gen_kwargs, voices_module) -> str:
        """Pocket TTS (MLX) and any family with the same contract: EITHER a preset
        voice name OR a reference audio clip. If both are present the reference wins,
        which is what the engine itself does."""
        voice_id = (params.get("voice_library_id") or "").strip() or None
        if voice_id or params.get("_reference_audio_path"):
            self._inject_voice_clone(voice_id or "", params, gen_kwargs, voices_module,
                                     fallback_transcript=".")
            return f"clone (voice={voice_id or 'private-reference'})"

        voice = (params.get("voice") or params.get("preset_speaker") or "").strip()
        if voice:
            gen_kwargs["voice"] = voice
            return f"voice={voice}"

        # The engine's own default preset voice is used when neither is given.
        return "zero-shot"

    def _mlx_kwargs_design_clone_or_direct(self, params, gen_kwargs, voices_module) -> str:
        """Breeze TTS 2: a reference clip clones, an `instruct` designs or directs, a preset speaks.

        All three are the same engine call — `generate(text, voice, instruct, ref_audio, ref_text)`
        — so this is one resolver rather than three. Precedence follows what the engine itself does
        and what a reference means in this app: **a reference clip wins**, because the operator
        chose a voice and a design prompt beside it is a description of *that* voice rather than a
        request for a different one. With no reference, an instruction is the voice: Breeze designs
        one from a description, or takes a direction for how the line is read. With neither, the
        engine's own default voice is used, and that is a real mode it was measured in.
        """
        voice_id = (params.get("voice_library_id") or "").strip() or None
        if voice_id or params.get("_reference_audio_path"):
            # No fallback transcript here, unlike Pocket TTS. Breeze builds its clone prompt from
            # the reference *transcript* — `f"{speaker}{ref_text}"` goes to the text encoder beside
            # the encoded reference audio — and raises "Breeze voice cloning requires ref_text with
            # ref_audio" without one. A placeholder like "." is therefore not a degraded clone, it
            # is a clone conditioned on the wrong words. Where a transcript is genuinely missing,
            # the honest answer is to say so and name the one thing that fixes it, rather than
            # render a voice that will not resemble the reference and be read as a model failure.
            self._inject_voice_clone(voice_id or "", params, gen_kwargs, voices_module,
                                     fallback_transcript="")
            if not str(gen_kwargs.get("ref_text") or "").strip():
                raise ValueError(
                    "Breeze TTS 2 clones a voice from a reference clip **and its exact "
                    "transcript**, and this voice has no transcript saved. Add the transcript on "
                    "the Voices page, or choose a voice that has one."
                )
            return f"clone (voice={voice_id or 'private-reference'})"

        instruct = (params.get("voice_design_prompt") or params.get("instruct") or "").strip()
        if instruct:
            # One key, because Breeze reads one: it does not separate "design a voice" from
            # "direct this line", and inventing a distinction the engine does not make would be a
            # control that silently does nothing.
            gen_kwargs["instruct"] = instruct
            return f"design/direction ({len(instruct)} char prompt)"

        voice = (params.get("voice") or params.get("preset_speaker") or "").strip()
        if voice:
            gen_kwargs["voice"] = voice
            return f"voice={voice}"
        return "zero-shot"

    def _inject_voice_clone(self, voice_id: str, params: dict, gen_kwargs: dict,
                            voices_module, fallback_transcript: str) -> None:
        """Helper — look up `voice_id` in the voices library, validate the
        reference clip exists, and inject ref_audio + ref_text into gen_kwargs.
        Used by every clone-capable mode."""
        private_path = str(params.get("_reference_audio_path") or "").strip()
        if private_path:
            ref_path = Path(private_path)
            if not ref_path.is_file():
                raise ValueError("The private reference audio is no longer available")
        else:
            voice = voices_module.library.get(voice_id)
            if voice is None:
                raise ValueError(f"Voice {voice_id} not found in library")
            ref_path = voices_module.library.reference_path(voice_id)
            if ref_path is None or not ref_path.exists():
                raise ValueError(f"Reference audio for voice {voice_id} is missing on disk")
        gen_kwargs["ref_audio"] = str(ref_path)
        ref_text = (params.get("ref_transcript") or "").strip()
        if not ref_text and voice_id:
            ref_text = voices_module.library.transcript(voice_id) or ""
        gen_kwargs["ref_text"] = ref_text or fallback_transcript

    # ----- Khmer TTS (MMS VITS, transformers) -----

    def _khmer_vits_get_model(self, repo: str):
        """Lazy-load + cache the transformers VitsModel for `repo`.

        VITS runs on CPU: the model is tiny (83M) and the MPS backend has gaps
        in the stochastic-duration path for some VITS checkpoints. One cache
        slot; a repo switch evicts so `loaded_model_keys` stays truthful.
        """
        if self._khmer_vits_model_repo == repo and self._khmer_vits_model is not None:
            return self._khmer_vits_model
        if self._khmer_vits_model is not None:
            print(f"[gen] evicting cached Khmer VITS model ({self._khmer_vits_model_repo})", flush=True)
            self._khmer_vits_model = None
            self._khmer_vits_model_repo = None
        from transformers import AutoTokenizer, VitsModel
        snapshot_path = self._mlx_audio_snapshot_path(repo)
        print(f"[gen] loading Khmer VITS (transformers) from {snapshot_path}", flush=True)
        tokenizer = AutoTokenizer.from_pretrained(str(snapshot_path))
        model = VitsModel.from_pretrained(str(snapshot_path))
        model.eval()
        self._khmer_vits_model = (model, tokenizer)
        self._khmer_vits_model_repo = repo
        self._last_model_activity_at = time.time()
        return self._khmer_vits_model

    def _generate_khmer_vits(self, job: GenerationJob, model_entry, output_path: Path) -> None:
        """Meta MMS Khmer VITS through transformers.

        One built-in speaker, no reference clip, no presets. Long text is
        sectioned by the parent dispatcher (every shipped family is in
        `_SECTIONED_FAMILIES`); this worker renders exactly the text it is
        given and writes one 16 kHz WAV.
        """
        import numpy as np
        import soundfile as sf
        import torch

        params = job.params
        text = (params.get("text") or "").strip()
        if not text:
            raise ValueError("text is required")
        if (params.get("voice_library_id") or "").strip() or str(
            params.get("_reference_audio_path") or ""
        ).strip():
            raise ValueError(
                "Khmer TTS (MMS VITS) has a single built-in speaker and does not "
                "use a reference voice."
            )

        seed = params.get("seed")
        if seed is not None and int(seed) >= 0:
            torch.manual_seed(int(seed))
            job.resolved_seed = int(seed)

        model, tokenizer = self._khmer_vits_get_model(model_entry.repo)
        inputs = tokenizer(text, return_tensors="pt")
        with torch.no_grad():
            waveform = model(**inputs).waveform.squeeze().cpu().numpy()
        waveform = np.asarray(waveform, dtype="float32").reshape(-1)
        sample_rate = int(getattr(model.config, "sampling_rate", 16000) or 16000)
        sf.write(str(output_path), waveform, sample_rate, format="WAV", subtype="PCM_16")
        print(
            f"[gen] khmer-tts-mms saved WAV at {sample_rate} Hz, "
            f"{len(waveform)/sample_rate:.2f}s: {output_path}",
            flush=True,
        )

    # ----- Khmer TTS Female (VITS, ONNX Runtime) -----

    def _khmer_onnx_get_model(self, repo: str):
        """Lazy-load + cache the ONNX Runtime session for the female Khmer voice."""
        if self._khmer_onnx_model_repo == repo and self._khmer_onnx_model is not None:
            return self._khmer_onnx_model
        if self._khmer_onnx_model is not None:
            print(f"[gen] evicting cached Khmer ONNX model ({self._khmer_onnx_model_repo})", flush=True)
            self._khmer_onnx_model = None
            self._khmer_onnx_model_repo = None
        import json as _json

        import onnxruntime as ort

        snapshot_path = self._mlx_audio_snapshot_path(repo)
        onnx_path = snapshot_path / "khmer_tts_v3.onnx"
        meta_path = snapshot_path / "tts_meta.json"
        if not onnx_path.is_file() or not meta_path.is_file():
            raise RuntimeError(
                f"Khmer TTS Female (VITS/ONNX) files missing under {snapshot_path}. "
                "Re-download the model from the Models tab."
            )
        print(f"[gen] loading Khmer VITS (ONNX Runtime) from {onnx_path}", flush=True)
        meta = _json.loads(meta_path.read_text(encoding="utf-8"))
        session = ort.InferenceSession(
            str(onnx_path), providers=["CPUExecutionProvider"]
        )
        self._khmer_onnx_model = (session, meta)
        self._khmer_onnx_model_repo = repo
        self._last_model_activity_at = time.time()
        return self._khmer_onnx_model

    def _generate_khmer_onnx(self, job: GenerationJob, model_entry, output_path: Path) -> None:
        """sengtha's Coqui VITS export through ONNX Runtime.

        Grapheme tokenisation per the model card's `tts_meta.json` (blank token
        interleaved between every character). One built-in female speaker; the
        parent dispatcher sections long text.
        """
        import numpy as np
        import soundfile as sf

        params = job.params
        text = (params.get("text") or "").strip()
        if not text:
            raise ValueError("text is required")
        if (params.get("voice_library_id") or "").strip() or str(
            params.get("_reference_audio_path") or ""
        ).strip():
            raise ValueError(
                "Khmer TTS Female (VITS/ONNX) has a single built-in speaker and "
                "does not use a reference voice."
            )

        session, meta = self._khmer_onnx_get_model(model_entry.repo)
        vocab = list(meta.get("vocab") or [])
        id_of = {character: index for index, character in enumerate(vocab)}
        ids = [id_of[character] for character in text if character in id_of]
        if not ids:
            raise ValueError(
                "None of the characters in this text are in the Khmer voice's "
                "vocabulary. Enter Khmer text."
            )
        blank = id_of.get(str(meta.get("blank") or "@"))
        if meta.get("add_blank", True) and blank is not None:
            expanded: list[int] = [blank]
            for token in ids:
                expanded.extend((token, blank))
            ids = expanded
        x = np.asarray([ids], dtype=np.int64)
        x_lengths = np.asarray([len(ids)], dtype=np.int64)
        outputs = session.run(None, {"x": x, "x_lengths": x_lengths})
        waveform = np.asarray(outputs[0], dtype="float32").reshape(-1)
        sample_rate = int(meta.get("sample_rate") or 22050)
        sf.write(str(output_path), waveform, sample_rate, format="WAV", subtype="PCM_16")
        print(
            f"[gen] khmer-tts-onnx saved WAV at {sample_rate} Hz, "
            f"{len(waveform)/sample_rate:.2f}s: {output_path}",
            flush=True,
        )

    # ----- persistence -----

    def _persist(self, *, strict: bool = False) -> bool:
        try:
            with self._lock:
                OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                active = [j for j in self._jobs.values() if j.state in {
                    "queued", "running", "cancel_requested"
                }]
                terminal = [j for j in self._jobs.values() if j.state in {
                    "done", "error", "cancelled", "uncertain"
                }]
                active.sort(key=lambda j: j.created_at)
                # Past 90 days a finished record is dropped from memory and from the file; this runs on
                # every save, so a worker that stays up for weeks trims as it goes, not only at restart.
                for stale in [j for j in terminal if _history_expired(j)]:
                    self._jobs.pop(stale.job_id, None)
                terminal = [j for j in terminal if not _history_expired(j)]
                terminal.sort(key=lambda j: (j.finished_at or j.created_at), reverse=True)
                # Losing an old terminal row is routine retention. Losing an
                # active request loses its idempotency/recovery identity.
                payload = {"jobs": [self._to_disk(j) for j in (
                    active + terminal[:HISTORY_MAX]
                )]}
                tmp = HISTORY_FILE.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(payload, default=str))
                os.replace(tmp, HISTORY_FILE)
            return True
        except Exception as e:
            print(f"[gen] persist failed: {e}", file=sys.stderr, flush=True)
            if strict:
                raise PersistenceError("Generation history could not be saved") from e
            return False

    def _load_history(self) -> None:
        if not HISTORY_FILE.exists():
            return
        try:
            payload = json.loads(HISTORY_FILE.read_text())
            rows = payload.get("jobs", [])
            needs_cleanup = False
            for raw in rows:
                job = self._from_disk(raw)
                if job is not None and _history_expired(job):
                    needs_cleanup = True          # finished more than 90 days ago: the record goes
                    continue
                if job is not None:
                    if job.state in {"queued", "running", "cancel_requested"}:
                        # A new server cannot prove that the previous native process
                        # stopped or still owns this request. Never resume it.
                        if job.output_path:
                            try:
                                Path(job.output_path).unlink(missing_ok=True)
                            except OSError:
                                pass
                        if job.partial_output_path:
                            try:
                                Path(job.partial_output_path).unlink(missing_ok=True)
                            except OSError:
                                pass
                        job.output_path = None
                        job.partial_output_path = None
                        job.state = "uncertain"
                        job.error = "Generation status is uncertain after Voice Studio restarted."
                        job.error_code = "EXECUTION_IDENTITY_LOST"
                        job.finished_at = time.time()
                        needs_cleanup = True
                    self._jobs[job.job_id] = job
                if job is None or any(str(key).startswith("provider") for key in raw):
                    needs_cleanup = True
            if needs_cleanup:
                self._persist()
            print(f"[gen] loaded {len(self._jobs)} jobs from history", flush=True)
        except Exception as e:
            print(f"[gen] load history failed: {e}", file=sys.stderr, flush=True)

    @staticmethod
    def _to_disk(job: GenerationJob) -> dict:
        return {
            "job_id": job.job_id,
            "mode": job.mode,
            "state": job.state,
            "client_request_params": job.client_request_params,
            "progress": job.progress,
            "chunk_index": job.chunk_index,
            "chunk_total": job.chunk_total,
            "execution_run_id": job.execution_run_id,
            "executor_generation": job.executor_generation,
            "params": job.params,
            "created_at": job.created_at,
            "output_path": job.output_path,
            "partial_output_path": job.partial_output_path,
            "resolved_seed": job.resolved_seed,
            "error": job.error,
            "started_at": job.started_at,
            "cancel_requested_at": job.cancel_requested_at,
            "finished_at": job.finished_at,
            "receipt_acked_at": job.receipt_acked_at,
            "model_revision": job.model_revision,
            "voice_revision": job.voice_revision,
            "media_type": job.media_type,
            "format": job.format,
            "bytes": job.bytes,
            "sha256": job.sha256,
            "audio_duration_s": job.audio_duration_s,
            "audio_duration_ms": job.audio_duration_ms,
            "sample_rate_hz": job.sample_rate_hz,
            "channels": job.channels,
            "delivery": job.delivery,
            "delivery_fallback_reason": job.delivery_fallback_reason,
            "resource_usage": job.resource_usage,
            "quality_validation": job.quality_validation,
            "quality_retry_count": job.quality_retry_count,
            "quality_retry_history": job.quality_retry_history,
            "error_code": job.error_code,
            "origin": _origin(job.origin),
            "origin_device": _origin_device(job.origin_device),
        }

    @staticmethod
    def _from_disk(raw: dict) -> Optional["GenerationJob"]:
        try:
            params = raw.get("params") or {}
            if str(params.get("repo") or "").startswith("provider:"):
                return None
            output_path = raw.get("output_path")
            if output_path and not Path(output_path).exists():
                output_path = None
            return GenerationJob(
                job_id=raw["job_id"],
                mode=raw.get("mode", "txt2speech"),
                params=params,
                state=raw.get("state", "done"),
                client_request_params=raw.get("client_request_params"),
                created_at=(
                    raw.get("created_at")
                    or raw.get("started_at")
                    or raw.get("finished_at")
                    or time.time()
                ),
                progress=raw.get("progress", 1.0),
                chunk_index=raw.get("chunk_index"),
                chunk_total=raw.get("chunk_total"),
                execution_run_id=raw.get("execution_run_id"),
                executor_generation=raw.get("executor_generation"),
                output_path=output_path,
                partial_output_path=raw.get("partial_output_path"),
                resolved_seed=raw.get("resolved_seed"),
                error=raw.get("error"),
                started_at=raw.get("started_at"),
                cancel_requested_at=raw.get("cancel_requested_at"),
                finished_at=raw.get("finished_at"),
                receipt_acked_at=raw.get("receipt_acked_at"),
                model_revision=raw.get("model_revision"),
                voice_revision=raw.get("voice_revision"),
                media_type=raw.get("media_type"),
                format=raw.get("format"),
                bytes=raw.get("bytes"),
                sha256=raw.get("sha256"),
                audio_duration_s=raw.get("audio_duration_s"),
                audio_duration_ms=raw.get("audio_duration_ms"),
                sample_rate_hz=raw.get("sample_rate_hz"),
                channels=raw.get("channels"),
                delivery=raw.get("delivery"),
                delivery_fallback_reason=raw.get("delivery_fallback_reason"),
                resource_usage=raw.get("resource_usage"),
                quality_validation=raw.get("quality_validation"),
                quality_retry_count=int(raw.get("quality_retry_count") or 0),
                quality_retry_history=raw.get("quality_retry_history") or [],
                error_code=raw.get("error_code"),
                origin=_origin(raw.get("origin")),
                origin_device=_origin_device(raw.get("origin_device")),
            )
        except Exception:
            return None


_NATIVE_CHILD_MANAGER: GenerationManager | None = None


def _native_dispatch_payload(payload: dict, cancel_requested) -> dict:
    """Child entry point. It retains only native model caches across commands."""
    global _NATIVE_CHILD_MANAGER
    if _NATIVE_CHILD_MANAGER is None:
        runtime = object.__new__(GenerationManager)
        runtime._mlx_audio_model = None
        runtime._mlx_audio_model_repo = None
        runtime._khmer_vits_model = None
        runtime._khmer_vits_model_repo = None
        runtime._khmer_onnx_model = None
        runtime._khmer_onnx_model_repo = None
        runtime._last_model_activity_at = None
        _NATIVE_CHILD_MANAGER = runtime
    runtime = _NATIVE_CHILD_MANAGER
    if payload.get("action") == "release":
        released = runtime._evict_loaded_models(reason="parent-memory-release")
        return {"released": released, "loaded_models": runtime.loaded_model_keys()}
    if payload.get("action") != "generate":
        raise ValueError("unknown native executor action")
    job = GenerationJob(
        job_id=str(payload["job_id"]), mode="txt2speech",
        params=dict(payload["params"]), cancel_event=cancel_requested,
    )
    sampler = resource_telemetry.JobResourceSampler(lambda _value: None).start()
    try:
        runtime._dispatch_txt2speech_direct(
            job, Path(payload["output_path"]), postprocess_speed=False,
        )
        execution_error = None
    except Exception as exc:
        execution_error = f"{type(exc).__name__}: {exc}"
    telemetry = sampler.finish(
        state="error" if execution_error else "done",
        memory_failure=False,
        restart_scheduled=False,
        model_retained=runtime.has_loaded_model(),
    )
    return {
        "resolved_seed": job.resolved_seed,
        "chunk_index": job.chunk_index,
        "chunk_total": job.chunk_total,
        "progress": job.progress,
        "loaded_models": runtime.loaded_model_keys(),
        "resource_usage": telemetry,
        "execution_error": execution_error,
    }


# Spawned native workers import this module only to unpickle the child entry
# point. They must never construct the HTTP/history-owning singleton.
manager = None if os.environ.get("VOICE_NATIVE_EXECUTOR_CHILD") == "1" else GenerationManager()
