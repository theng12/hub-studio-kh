"""Persisted, operator-visible RAM admission policy for local model dispatch.

A model's RAM requirement is a **hard eligibility floor**, per model (owner decision,
2026-09-30, reversing the 2026-09 "a tier is not a wall" rule): a Mac whose total memory is
below it is not a candidate for that model, and the scheduler counts it out of the model's
*pool* (`broker.pool_machines`). The requirement is, in order:

1. the operator's override for the model, else
2. the **measured** peak the fleet has proven (`_FLEET_DEFAULTS` - FLUX.2 Klein 4-bit runs on
   the owner's 8 GB Macs, so its requirement is 8 GB whatever the catalog says), else
3. the model manifest's ``qualification.minimum_unified_memory_gb``, else
4. the worker's own catalog row (``min_unified_memory_gb`` / ``min_total_memory_gb``).

A model that publishes nothing has no floor, and a Mac whose RAM is unknown is still eligible:
the worker's own MemoryGuard stays the final authority.

Separately, the *live* free-memory requirement is a hard gate too. "This Mac has 8 GB of RAM" is
a fact that does not change, but "this Mac has 0.4 GB free right now because three Studios are
holding models" is a fact about this moment, and starting a generation into it is what turns a
slow job into a killed worker. The free floor is what the Models screen tunes.

The effective policy is site-local; it never changes a worker catalog or
GenStudio's global ownership decisions.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from . import model_ids, workload_policy
from .registry import DATA_DIR


SETTINGS_FILE = DATA_DIR / "memory_admission_overrides.json"
DEFAULT_MIN_FREE_MEMORY_GB = 2.0

# These are measured fleet defaults, intentionally allowed to be less
# conservative than a worker catalog. FLUX.2 Klein 4B MLX 4-bit has completed
# thousands of generations on the owner's 8 GB Apple-silicon nodes, so 8 GB is its
# requirement (the first rung of `requirement_gb`); the free-memory floor stays the
# live safety guard.
_FLEET_DEFAULTS = {
    "aitrader/flux2-klein-4b-mlx-4bit": {
        "min_total_memory_gb": 8.0,
        # Free memory needed before start, from the real 8 GB M1+M2 runs 2026-10-02 (started cleanly from 3.76-4.14 GB
        # free, peak 2.16-2.68 GB, ~0 swap): the free memory a job needs to begin, NOT its peak. This table outranks the
        # worker's catalog row for the free floor (`describe`), so 2.8 (owner's choice 2026-10-02, for headroom) is set here as well as in the catalog. Z-Image-Turbo
        # 4-bit has no entry on purpose: it would also override its manifest's total tier; its catalog row carries 3.0.
        "min_free_memory_gb": 2.8,
        "reason": "Fleet-qualified on 8 GB Apple-silicon Macs",
    },
    # Breeze TTS 2 4-bit (owner decision 2026-10-04): manifest and catalog said 16 GB / 5.6 GB free, which 0 of the 33
    # 8 GB Macs ever have. Measured 2026-10-04 on an 8 GB M1 (Hub 0.6.143, `_orchestra/audit-20261001/breeze-8gb/results/
    # TTS 8GB Run 20261004-230549`): 2/2 S2 clips completed, every section accepted, each clip STARTED with 3.31-3.46 GB
    # free, worker peak 4.69 GB, Mac max used 5.2-5.3 GB, min free 1.19-1.32 GB, pressure warning (never critical), swap
    # <= +0.54 GB, RTF 4.1 (a 16 GB M1 runs 4.9-5.1), max stall 0.6 s. 3.2 GB free to start is just under the measured
    # 3.31 GB. The Voice worker's own preflight takes this same floor (`worker_free_floor_gb`), not its size estimate.
    "mlx-community/breeze-tts-2-mlx-4bit": {
        "min_total_memory_gb": 8.0,
        "min_free_memory_gb": 3.2,
        "reason": "Measured on an 8 GB M1 (2026-10-04): started from 3.31 GB free, peak 4.69 GB, never critical",
    },
}

_cache: dict[str, dict] | None = None


def _normalized(model: str) -> str:
    return str(model or "").strip().lower()


def applies_to(modality: str | None = None) -> bool:
    """Whether this queue uses the broker's local inference RAM governor.

    It uses it for every modality, and the modality argument is accepted only so callers can
    keep naming what they are asking about.

    This was a closed list of five modalities, and ``chat`` was not in it, so the Hub skipped
    the 9B text model in its own queue ranking (``broker._batch_memory_constraint_gb``) and
    published no admission evidence for it in ``monitor`` or the capability contract. The gate
    that actually means something is the floor the worker publishes for the model it serves -
    Image, Voice and Music publish ``min_unified_memory_gb``; Chat and Subtitle publish
    ``min_total_memory_gb``; every worker may publish ``min_free_memory_gb``. A modality list
    can only ever omit the next modality, so it is not the gate any more.
    """
    return True


def _published_total_memory_gb(catalog_entry: dict) -> float | None:
    """The total-memory tier the worker published for this model, in either spelling.

    ``min_unified_memory_gb`` is the Hub's older name for it and what Image, Voice and Music
    send; Chat and Subtitle send ``min_total_memory_gb``. Reading only one spelling is how the
    9B text model ended up with no published floor at all.
    """
    for key in ("min_unified_memory_gb", "min_total_memory_gb",
                "qualified_minimum_memory_gb"):
        value = _number(catalog_entry.get(key))
        if value is not None:
            return value
    return None


_manifest_cache: tuple[str, float, dict[str, float]] | None = None


def _manifest_tiers() -> dict[str, float]:
    """Lower-cased model id or package id -> the manifest's ``minimum_unified_memory_gb``.

    Read through `model_ids` and kept for a few seconds: the scheduler asks per Mac per pass, and
    a directory listing each time was most of a scheduling pass. A manifest added or edited shows
    up within the interval; `reset_for_tests` drops it at once.
    """
    global _manifest_cache
    root = str(model_ids.manifest_root())
    now = time.monotonic()
    if _manifest_cache and _manifest_cache[0] == root and now - _manifest_cache[1] < 5.0:
        return _manifest_cache[2]
    manifests = model_ids.load_manifests()
    tiers: dict[str, float] = {}
    for alias, model_id in model_ids.load_index().items():
        tier = _number(((manifests.get(model_id) or {}).get("qualification") or {}).get(
            "minimum_unified_memory_gb"))
        if tier is not None:
            tiers[alias] = tier
    _manifest_cache = (root, now, tiers)
    return tiers


def requirement_gb(model: str, catalog_entry: dict, *,
                   use_override: bool = True) -> tuple[float | None, str]:
    """The total RAM this model needs, and where the number came from.

    Returns ``(gb, source)`` with ``source`` one of ``operator_override``, ``measured``,
    ``manifest``, ``catalog`` or ``none`` (``gb`` is ``None``: nothing was published, so nothing
    is enforced). This is the eligibility floor the scheduler uses; see the module docstring.
    """
    key = _normalized(model or catalog_entry.get("repo"))
    override = _read().get(key) if use_override else None
    if override:
        return override["min_total_memory_gb"], "operator_override"
    measured = _FLEET_DEFAULTS.get(key)
    if measured:
        return measured["min_total_memory_gb"], "measured"
    tier = _manifest_tiers().get(key)
    if tier is not None:
        return tier, "manifest"
    tier = workload_policy.required_total_memory_gb(model, catalog_entry)
    if tier is not None:
        return tier, "catalog"
    return None, "none"


def publish_floor(row: dict, total_gb: float | None) -> dict:
    """Make a worker's published tier and ``memory_eligible`` the floor dispatch enforces.

    A worker's own tier is a literal in its catalog, and it went stale: Voice published 16 GB for
    VoxCPM2 4-bit after the manifest (8 GB) had replaced it, so every 8 GB Mac read
    ``memory_eligible: false`` to the Gateway and the published capacity counted only 16 GB Macs
    while the Hub admitted - and finished - its jobs on 8 GB. The Hub's aggregate catalog now
    publishes `requirement_gb` instead, so there is one number. It is the default floor (no
    operator override): an override is site-local and is never written back into a catalog row,
    which `requirement_gb` itself reads.

    ``total_gb`` is the Mac's RAM as its worker reported it. A worker's ``memory_eligible: false``
    is flipped only when its own stricter tier explains it; Image, Music, Chat and Subtitle use
    the same field for "not Apple Silicon / macOS 14", which no floor can overrule.
    """
    model = str(row.get("repo") or row.get("model_id") or "")
    try:
        worker_tier = workload_policy.required_total_memory_gb(model, row)
        floor = requirement_gb(model, row, use_override=False)[0]
    except ValueError:
        return row  # an unreadable published floor stays visible as published
    if floor is None:
        return row
    for key in ("min_unified_memory_gb", "min_total_memory_gb", "qualified_minimum_memory_gb"):
        if key == "min_unified_memory_gb" or key in row:
            row[key] = floor
    total = _number(total_gb) if not isinstance(total_gb, bool) else None
    if total is not None and type(row.get("memory_eligible")) is bool:
        if total < floor:
            row["memory_eligible"] = False
        elif row["memory_eligible"] is False and worker_tier is not None and total < worker_tier:
            row["memory_eligible"] = True
    return row


def _number(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _read() -> dict[str, dict]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        raw = json.loads(SETTINGS_FILE.read_text()) if SETTINGS_FILE.exists() else {}
    except (OSError, json.JSONDecodeError):
        raw = {}
    _cache = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            total = _number(value.get("min_total_memory_gb"))
            free = _number(value.get("min_free_memory_gb"))
            if total is None or free is None:
                continue
            _cache[_normalized(key)] = {
                "model": str(value.get("model") or key),
                "min_total_memory_gb": total,
                "min_free_memory_gb": free,
                "updated_at": _number(value.get("updated_at")) or 0.0,
            }
    return _cache


def _write(values: dict[str, dict]) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{SETTINGS_FILE.name}-", dir=SETTINGS_FILE.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(values, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        temporary.replace(SETTINGS_FILE)
    finally:
        if temporary.exists():
            temporary.unlink()


def describe(model: str, catalog_entry: dict) -> dict:
    """Return catalog, fleet-default, and effective site-local RAM floors.

    ``effective_min_total_memory_gb`` is the model's requirement (`requirement_gb`) and is
    enforced: dispatch refuses a Mac below it, the scheduler leaves that Mac out of the model's
    pool, and the published capacity counts only Macs that meet it. It is 0 for a model that
    publishes no floor at all. ``min_total_source`` names where the number came from.

    ``_FLEET_DEFAULTS`` is a per-model *measurement* — the models the owner has proven below
    their published tier — so it cannot be derived from anything, and a model nobody has
    measured gets the generic free floor instead. That absence used to be invisible: the row
    said ``source: "catalog"`` and nothing said whether a measurement existed. Every row now
    carries ``fleet_default_measured`` plus a ``default_reason`` that names the absence, so the
    Models screen can show "nobody has measured this one yet" instead of implying the generic
    floor is a measurement.
    """
    key = _normalized(model or catalog_entry.get("repo"))
    catalog_total = _published_total_memory_gb(catalog_entry)
    catalog_free = _number(catalog_entry.get("min_free_memory_gb"))
    qualified_total = workload_policy.required_total_memory_gb(model, catalog_entry)
    qualified_free = workload_policy.required_free_memory_gb(model, catalog_entry)
    fleet_default = _FLEET_DEFAULTS.get(key)
    workload_default = bool(
        (qualified_total is not None and qualified_total != catalog_total)
        or (qualified_free is not None and qualified_free != catalog_free)
    )

    default_total = requirement_gb(model, catalog_entry, use_override=False)[0]
    default_free = (
        fleet_default["min_free_memory_gb"] if fleet_default
        else max(DEFAULT_MIN_FREE_MEMORY_GB, qualified_free or 0.0)
    )
    override = _read().get(key)
    required_total, total_source = requirement_gb(model, catalog_entry)
    effective_total = required_total or 0.0
    effective_free = (
        override["min_free_memory_gb"] if override else default_free
    )
    source = "operator_override" if override else (
        # Compatibility with the published contract: a Hub production floor reports as
        # `fleet_default`, and the measurement question ("has anybody measured this model?") is
        # answered by `fleet_default_measured` below rather than by reusing this string.
        "fleet_default" if fleet_default or workload_default else "catalog"
    )
    if fleet_default:
        default_reason = str(fleet_default.get("reason") or "Fleet-measured override")
    elif workload_default:
        default_reason = "Hub production qualification"
    else:
        # Two different situations used to share one sentence: a model that published its own
        # floor, and one that published nothing and therefore gets the generic default. Saying
        # "the generic 2.0 GB floor applies" about a model enforcing 3.2 GB is simply false, and
        # it is the sentence an operator reads when asking why a job is waiting.
        published = catalog_free if catalog_free is not None else qualified_free
        if published is not None:
            default_reason = (
                f"No fleet measurement names this model; its worker publishes a "
                f"{float(published):.2f} GB live-free floor and that is what is enforced"
            )
        else:
            default_reason = (
                f"No fleet measurement names this model and its worker publishes no live-free "
                f"floor; the generic {DEFAULT_MIN_FREE_MEMORY_GB:.1f} GB default applies"
            )
    return {
        "model": str(model or catalog_entry.get("repo") or ""),
        "catalog_min_total_memory_gb": catalog_total,
        "catalog_min_free_memory_gb": catalog_free,
        "default_min_total_memory_gb": default_total,
        "default_min_free_memory_gb": default_free,
        "effective_min_total_memory_gb": effective_total,
        "min_total_source": total_source,
        "effective_min_free_memory_gb": effective_free,
        "source": source,
        # Whether a per-model fleet measurement exists for this model. False is not an error:
        # it is the honest state of a model nobody has measured yet.
        "fleet_default_measured": fleet_default is not None,
        "default_reason": default_reason,
        "overridden": override is not None,
        "updated_at": override.get("updated_at") if override else None,
    }


def set_override(model: str, *, min_total_memory_gb: float,
                 min_free_memory_gb: float, catalog_entry: dict) -> dict:
    key = _normalized(model)
    if not key:
        raise ValueError("model is required")
    values = dict(_read())
    values[key] = {
        "model": model,
        "min_total_memory_gb": float(min_total_memory_gb),
        "min_free_memory_gb": float(min_free_memory_gb),
        "updated_at": time.time(),
    }
    _write(values)
    global _cache
    _cache = values
    return describe(model, catalog_entry)


def worker_free_floor_gb(model: str) -> float | None:
    """The live-free floor the Voice worker's own preflight must use in place of its size estimate:
    the operator's override, else this model's fleet measurement, else ``None`` (the worker keeps its
    estimate). Without the measurement here, Breeze's worker asked 2.9 + 1.25 = 4.15 GB free while the
    Hub admitted it at 3.2, so an 8 GB Mac (3.3-3.5 GB free) would refuse every job it was sent."""
    key = _normalized(model)
    override = _read().get(key)
    if override:
        return override["min_free_memory_gb"]
    measured = _FLEET_DEFAULTS.get(key)
    return measured["min_free_memory_gb"] if measured else None


def reset_override(model: str, catalog_entry: dict) -> dict:
    key = _normalized(model)
    values = dict(_read())
    values.pop(key, None)
    if values:
        _write(values)
    else:
        try:
            SETTINGS_FILE.unlink()
        except FileNotFoundError:
            pass
    global _cache
    _cache = values
    return describe(model, catalog_entry)


def reset_for_tests() -> None:
    global _cache, _manifest_cache
    _cache = None
    _manifest_cache = None
