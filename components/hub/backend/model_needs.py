"""How much RAM each model needs, stated **before** an install instead of learned from a refusal.

The owner's rule (2026-11-25): RAM is the only limit on which model a Mac can run, the requirement
is shown *before* the install, and a refusal names the shortfall instead of hiding the model.

Until now the Hub published file sizes and a memory *tier* (``minimum_unified_memory_gb`` - which
machine class the model is qualified at). Neither answers "how much RAM does this model need", so
the Gateway only ever learned it reactively, by scraping the Hub's own refusal sentence
(``broker._insufficient_memory_text``). ``needs_gb`` is that number, per model, with the source
that produced it:

``declared``
    The model's own shipped bytes (every file in its manifest) plus a **constant** per-engine
    runtime overhead. The overhead is one number per engine, never per model: a figure tuned to one
    model is a floor invented for it, which is how VoxCPM2's formula floor came to disagree with
    the voice worker's measurement (``components/voice/app/backend/generation.py``,
    ``_required_free_memory_gb``). It is a declared floor, not a measured peak.

``measured``
    A real statement about the running model, strongest first:

    1. a figure this module learned from a real worker refusal (``note_refusal``), kept so declared
       and learned converge instead of competing;
    2. the model manifest's own ``qualification.min_free_memory_gb``, a measured free-before-start
       floor shipped with the package;
    3. the worker's catalog row (``min_free_memory_gb`` / ``required_free_memory_gb``, read through
       ``workload_policy.required_free_memory_gb``), which AGENTS.md requires to be set from a clean
       run and never a formula.

An engine this table has not been taught still gets a number (``_DEFAULT_OVERHEAD_GB``), and
``needs_source`` stays ``declared``: the contract has no ``unknown`` in it by acceptance, and
``tests/test_model_needs.py`` fails if a shipped engine ever reaches the fallback.

``needs_gb`` is a published figure, not an admission gate. The gates keep their own homes (the
manifest tier, the model's row, the operator override), because a floor must never be a formula.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from pathlib import Path

SOURCES = ("declared", "measured")
STATE_FILE = "model-needs.json"
#: Old values kept per package, so a change of figure can be explained rather than just observed.
HISTORY_LIMIT = 5

#: Runtime overhead above the model's own bytes, in GB, one constant per engine.
#:
#: What it covers: everything the engine holds resident besides the weight files - MLX/PyTorch
#: runtime and allocator, tokenizer, KV cache or attention workspace, and one decode step's
#: activations. Each figure is the smallest that covers the engines' published context and job
#: shapes, not a per-model measurement. Calibration where the repo has it:
#:
#: * ``voice-mlx`` 1.0 - five measured free-before-start floors land between +0.3 and +1.1 GB above
#:   their own weight bytes (Breeze 2.9->3.2, VoxCPM2 2.3->2.8, OmniVoice 2.0->2.8, Qwen3-TTS 1.7
#:   and 1.9->2.8 in ``components/voice/app/backend/catalog.py``). 1.0 is the top of that band, so
#:   a declared figure never under-promises against a measurement the fleet already has. The one
#:   outlier, Fish S2 Pro 4-bit's 8.4, is an operator headroom choice for 8 GB Macs and stays a
#:   ``measured`` floor on its own row rather than being folded into this constant.
#: * ``voice-vits`` 0.5 - torch VITS/MMS: a small runtime, no KV cache.
#: * ``embed-mlx`` 0.5 - BGE-small is 0.06 GiB of weights; what remains is the runtime and a short
#:   sequence's activations.
#: * ``subtitle-mlx`` 1.0 - Whisper decodes segments with a bounded beam and holds mel features for
#:   a chunk; the same 1 GB covers base (0.14 GiB) and turbo (1.51 GiB).
#: * ``vision-mlx`` 1.0 - Florence/DINO-family image encoders plus a short generation.
#: * ``chat-mlx`` 2.0 - KV cache for the published context windows plus the MLX runtime. Grows with
#:   context, not with weights, which is why it is one constant across 5.6-19 GiB packages and why
#:   the manifest's tier (not this) stays the eligibility gate.
#: * ``image-mflux`` 2.0 / ``image-mlx-serve`` 3.0 - latent diffusion keeps the text encoders and a
#:   1024² latent pass resident; the serve engine holds a persistent process on top (Qwen-Image 2.1
#:   MLX-Serve publishes its own 12.0 GB floor for 9.95 GiB of weights, +2.05).
#: * ``music-mlx`` 3.0 / ``music-yue2`` 2.5 - audio diffusion decodes long latents; YuE2 adds a 3B
#:   language model's KV over the lyric prompt plus the VAE decoder.
ENGINE_OVERHEAD_GB: dict[str, float] = {
    "chat-mlx": 2.0,
    "embed-mlx": 0.5,
    "image-mflux": 2.0,
    "image-mlx-serve": 3.0,
    "music-mlx": 3.0,
    "music-yue2": 2.5,
    "subtitle-mlx": 1.0,
    "vision-mlx": 1.0,
    "voice-mlx": 1.0,
    "voice-vits": 0.5,
}
#: The largest shipped overhead, for an engine this table has not been taught yet. A test walks the
#: real catalog so a new engine is a failing test, not a silently under-declared model.
_DEFAULT_OVERHEAD_GB = 3.0

# The two sentences a worker or peer Hub refuses a job for memory with, each carrying the figure it
# decided on (``broker._MEMORY_DECISION_NUMBERS`` reads the same shapes to build the sentence).
# Only a worker's own "GB free is needed" or a forwarded peer refusal ("needs about X GB free and
# has about Y GB free") is a measurement; the Hub's local gate wait ("needs ~X GB, ~Y GB free")
# repeats a floor the Hub itself supplied, so learning from it would just confirm the declaration.
_REFUSAL_NEED = (
    re.compile(r"([\d.]+)\s*GB free is needed"),
    re.compile(r"needs (?:about |~)([\d.]+)\s*GB free and has about"),
)

_LOCK = threading.Lock()


def weight_gb(manifest: dict) -> float:
    """The model's own shipped bytes, in GiB: every file in its manifest, companions included."""
    total = 0
    for entry in manifest.get("files") or ():
        if isinstance(entry, dict):
            size = entry.get("size")
            if isinstance(size, int) and not isinstance(size, bool) and size > 0:
                total += size
    return total / 1024 ** 3


def overhead_gb(engine: object) -> float:
    """The engine's runtime overhead constant, or the fallback for an untaught engine."""
    return ENGINE_OVERHEAD_GB.get(str(engine or "").strip(), _DEFAULT_OVERHEAD_GB)


def declared_gb(manifest: dict) -> float:
    """Weights plus the engine's constant overhead: the figure before anything measured the model."""
    return round(weight_gb(manifest) + overhead_gb(manifest.get("engine")), 2)


def state_path(data_dir: Path | None = None) -> Path:
    return Path(data_dir or _data_dir()) / STATE_FILE


def _data_dir() -> Path:
    from .registry import DATA_DIR
    return DATA_DIR


def read_state(data_dir: Path | None = None) -> dict:
    """``{package_id: {"needs_gb", "source", "at", "why", "history": [...]}}``.

    A file that cannot be read is an empty state, not a corrupt one: the next measurement writes
    itself over it, and refusing the whole catalogue because one file was damaged would blank every
    model picker on every Mac.
    """
    try:
        value = json.loads(state_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_state(value: dict, data_dir: Path | None = None) -> None:
    path = state_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _finite(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def record_measured(package_id: str, needs_gb: float, why: str,
                    data_dir: Path | None = None, *,
                    previous_needs_gb: float | None = None,
                    previous_source: str | None = None) -> dict | None:
    """Record a figure the runtime stated, keeping the value it replaces.

    A write happens only when the figure actually changes, so a fleet of refusing Macs costs one
    file write rather than one per refusal. When a package moves from ``declared`` to ``measured``
    for the first time, ``previous_needs_gb`` / ``previous_source`` is preserved in ``history`` so
    the jump is explainable. Returns the record it wrote, or ``None``.
    """
    package_id = str(package_id or "").strip()
    gb = _finite(needs_gb)
    if not package_id or gb is None:
        return None
    gb = round(gb, 2)
    with _LOCK:
        state = read_state(data_dir)
        current = state.get(package_id) if isinstance(state.get(package_id), dict) else {}
        if current.get("needs_gb") == gb:
            return None
        history = []
        if current.get("needs_gb") is not None:
            history.append({"needs_gb": current.get("needs_gb"),
                            "source": current.get("source"),
                            "at": current.get("at"), "why": current.get("why")})
        elif (prev_gb := _finite(previous_needs_gb)) is not None:
            history.append({"needs_gb": round(prev_gb, 2),
                            "source": str(previous_source or "declared"),
                            "at": None, "why": "prior figure before runtime measurement"})
        history = (history + list(current.get("history") or []))[:HISTORY_LIMIT]
        record = {"needs_gb": gb, "source": "measured", "at": time.time(),
                  "why": str(why or ""), "history": history}
        state[package_id] = record
        try:
            _write_state(state, data_dir)
        except OSError:
            return None
    return record


def _resolve_manifest(model: object) -> dict | None:
    from . import model_ids
    manifests = model_ids.load_manifests()
    key = str(model or "").strip().lower()
    model_id = model_ids.load_index().get(key)
    return manifests.get(model_id or "") or manifests.get(str(model or ""))


def note_refusal(model: object, message: object, data_dir: Path | None = None) -> dict | None:
    """Learn from a worker's real memory refusal: the runtime stated the figure it needed.

    Never raises into the caller's job path - a refusal is already a hand-back, and losing the
    lesson is cheaper than losing the sentence the operator reads.
    """
    try:
        text = str(message or "")
        need = next((float(match.group(1)) for pattern in _REFUSAL_NEED
                     if (match := pattern.search(text))), None)
        if need is None:
            return None
        manifest = _resolve_manifest(model)
        if not manifest:
            return None
        package_id = str(manifest.get("package_id") or "")
        prev_gb, prev_source = needs_for(manifest, data_dir=data_dir)
        return record_measured(package_id, need, f"worker refused: {text.strip()[:200]}", data_dir,
                               previous_needs_gb=prev_gb, previous_source=prev_source)
    except Exception:  # noqa: BLE001 - learning a figure must never fail the refusal path
        return None


def note_load(model: object, measured_gb: float, why: str = "",
              data_dir: Path | None = None) -> dict | None:
    """Learn from a real model load whose measured memory need differs from its declared figure.

    Never raises into the caller's job path. Keeps the previous figure in ``history`` so every
    convergence from ``declared`` to ``measured`` is explainable.
    """
    try:
        manifest = _resolve_manifest(model)
        if not manifest:
            return None
        package_id = str(manifest.get("package_id") or "")
        prev_gb, prev_source = needs_for(manifest, data_dir=data_dir)
        return record_measured(package_id, measured_gb,
                               str(why or "measured during model load")[:200], data_dir,
                               previous_needs_gb=prev_gb, previous_source=prev_source)
    except Exception:  # noqa: BLE001 - learning a figure must never fail the load path
        return None


def published_floor_for(manifest: dict, free_floors: dict[str, float] | None) -> float | None:
    """Look up a worker-published free-memory floor for ``manifest`` by model_id or package_id."""
    if not free_floors:
        return None
    for key in (manifest.get("model_id"), manifest.get("package_id")):
        text = str(key or "").strip()
        if text in free_floors:
            return free_floors[text]
    return None


def needs_for(manifest: dict, published_free_gb: float | None = None,
              data_dir: Path | None = None) -> tuple[float, str]:
    """``(needs_gb, needs_source)`` for one model: always a number, never ``unknown``.

    Strongest statement first: a figure learned from a real refusal or load, the manifest's own
    measured free floor, the worker's published free floor, then the declared bytes-plus-overhead
    figure.
    """
    package_id = str(manifest.get("package_id") or manifest.get("model_id") or "")
    learned = read_state(data_dir).get(package_id)
    if isinstance(learned, dict) and (gb := _finite(learned.get("needs_gb"))) is not None:
        return round(gb, 2), "measured"
    qualification = manifest.get("qualification")
    if isinstance(qualification, dict):
        if (gb := _finite(qualification.get("min_free_memory_gb"))) is not None:
            return round(gb, 2), "measured"
    if (gb := _finite(published_free_gb)) is not None:
        return round(gb, 2), "measured"
    return declared_gb(manifest), "declared"


def needs_for_row(row: dict, manifest: dict | None = None,
                  data_dir: Path | None = None) -> tuple[float, str]:
    """``(needs_gb, needs_source)`` for an aggregate `/api/hub/catalog` or `/api/hub/models` row.

    Uses the matching manifest when available, plus any live-free floor the worker published on the
    row itself. A row without a manifest still receives a real number (measured floor if published,
    else declared from row size plus engine overhead) so no catalogue entry ever carries ``unknown``.
    """
    from . import workload_policy
    model = str(row.get("model_id") or row.get("repo") or "")
    try:
        published_floor = workload_policy.required_free_memory_gb(model, row)
    except ValueError:
        published_floor = None
    if isinstance(manifest, dict):
        return needs_for(manifest, published_floor, data_dir=data_dir)
    if (gb := _finite(published_floor)) is not None:
        return round(gb, 2), "measured"
    size_gb = _finite(row.get("size_gb")) or 0.0
    return round(size_gb + overhead_gb(row.get("engine")), 2), "declared"
