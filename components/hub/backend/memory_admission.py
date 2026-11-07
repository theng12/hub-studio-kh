"""Persisted, operator-visible RAM admission policy for local model dispatch.

A model's RAM requirement is a **hard eligibility floor**, per model (owner decision,
2026-09-30, reversing the 2026-09 "a tier is not a wall" rule): a Mac whose total memory is
below it is not a candidate for that model, and the scheduler counts it out of the model's
*pool* (`broker.pool_machines`). The requirement is, in order:

1. the operator's override for the model, else
2. a **measured** peak the fleet has proven (``_FLEET_DEFAULTS`` - a per-model measurement, empty
   today, for a run that proves a tier lower than the manifest states), else
3. the model manifest's ``qualification.minimum_unified_memory_gb``, else
4. the worker's own catalog row (``min_unified_memory_gb`` / ``min_total_memory_gb``).

**The manifest is the number for a shipped model (2026-10-28).** FLUX.2 Klein 4-bit and Breeze
TTS 2 4-bit used to state 16 GB there while this table carried the fleet's measured 8, so the same
model had two floors and a re-capture could not correct the one being enforced. Both manifests now
state 8 with the measurement in their own ``memory_evidence``, and the table is empty.

A model that publishes nothing has no floor, and a Mac whose RAM is unknown is still eligible:
the worker's own MemoryGuard stays the final authority.

Separately, the *live* free-memory requirement is a hard gate too. "This Mac has 8 GB of RAM" is
a fact that does not change, but "this Mac has 0.4 GB free right now because three Studios are
holding models" is a fact about this moment, and starting a generation into it is what turns a
slow job into a killed worker. The free floor has exactly one home: the model's own published row
(``min_free_memory_gb`` / ``required_free_memory_gb``, which ``workload_policy`` reads). The
operator's override on the Models screen is the only other number, and it wins. Nothing in the Hub
keeps a third copy - a Hub-side table that outranks the row is how an edit to the row became
invisible.

The effective policy is site-local; it never changes a worker catalog or
GenStudio's global ownership decisions.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import threading
import time
from functools import lru_cache
from pathlib import Path

from . import model_ids, workload_policy
from .registry import DATA_DIR


SETTINGS_FILE = DATA_DIR / "memory_admission_overrides.json"
DEFAULT_MIN_FREE_MEMORY_GB = 2.0

# The execution modes the fleet memory-admission wire names (proposal.r5, mode vocabulary).
MODES = ("txt2img", "edit")


class TierUnknown(ValueError):
    """The physical tier cannot be resolved because the published table is unreadable."""

# One lock guards every read-modify-write: the legacy local writes, the fleet CAS compare and
# write, and revision computation. Re-entrant because a CAS `change` calls the writers below.
_LOCK = threading.RLock()

# The published reference table is data, not a Python import: importing the Image worker's
# `generation` module would pull PIL, mflux and the model registry into the Hub process. The
# table is the same file `generation.reference_tier`/`effective_reference_policy` read, so the
# Hub resolves the identical physical band and published rows without that import.
REFERENCE_LIMITS_PATH = (
    Path(__file__).resolve().parents[3]
    / "components" / "image" / "app" / "backend" / "reference-limits.json"
)

# These are measured fleet defaults for the *total* memory tier, intentionally allowed to be less
# conservative than a worker catalog. FLUX.2 Klein 4B MLX 4-bit has completed thousands of
# generations on the owner's 8 GB Apple-silicon nodes, so 8 GB is its requirement (the first rung
# of `requirement_gb`) whatever its row and manifest say (both say 16).
#
# The live-free floor is deliberately NOT in this table any more. It had a copy here as well as on
# each model's own row, and `describe` gave this table precedence, so an edit to the row was
# silently ignored - two copies of one measurement, and only one of them enforced. The row is the
# one home (`components/image/app/backend/generation.py` carries 2.8 for FLUX.2 Klein 4-bit,
# `components/voice/app/backend/catalog.py` carries 3.2 for Breeze TTS 2 4-bit), the operator's
# override is the only other number, and `min_free_source` says which of the two is in force.
# **Empty as of 2026-10-28, and kept as the shape a future measurement needs.** This table held
# the two models the fleet had proven below their published tier - FLUX.2 Klein 4-bit and Breeze
# TTS 2 4-bit, both 8 GB against a manifest that said 16. Those two numbers now live in the
# models' own manifests (`qualification.minimum_unified_memory_gb`, with the measurement in
# `memory_evidence`), which `_manifest_tiers()` below already reads and which the enumeration
# order here already prefers. Keeping a third copy meant an edit to the manifest could be
# outranked by a table nobody had touched since the day it was measured - the exact defect the
# live-free floor had (owner rule: one home per number).
#
# The mechanism stays because a measurement is a different *claim* from a manifest's tier: a
# manifest states what a model needs to be offered at all, while this table records what the
# fleet proved it needs on a smaller Mac than that. A future measurement that a manifest cannot
# express - a tier lowered below what the manifest says *because* of a fleet run - is one entry
# here, and `describe` reports it as `fleet_default` with its reason.
_FLEET_DEFAULTS: dict[str, dict] = {}

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
                   use_override: bool = True, mode: str | None = None) -> tuple[float | None, str]:
    """The total RAM this model needs, and where the number came from.

    Returns ``(gb, source)`` with ``source`` one of ``operator_override``, ``measured``,
    ``manifest``, ``catalog``, ``reference_lane`` or ``none`` (``gb`` is ``None``: nothing was
    published, so nothing is enforced). This is the eligibility floor the scheduler uses; see the
    module docstring.

    ``mode='edit'`` adds one rung: a model's reference lane peaks far higher than its
    text-to-image lane (FLUX.2 Klein 4-bit: 12.44 GB measured against ~3 GB bounded), and the
    worker publishes that lane's own floor in its lane contract. The strictest of the two wins, so
    a Mac that can render text-to-image is still not a candidate for an edit item until it clears
    the edit floor.
    """
    key = _normalized(model or catalog_entry.get("repo"))
    override, _scope = _ram_override(key, mode) if use_override else (None, None)
    if override:
        gb, source = override["min_total_memory_gb"], "operator_override"
    elif (measured := _FLEET_DEFAULTS.get(key)):
        gb, source = measured["min_total_memory_gb"], "measured"
    elif (tier := _manifest_tiers().get(key)) is not None:
        gb, source = tier, "manifest"
    elif (tier := workload_policy.required_total_memory_gb(model, catalog_entry)) is not None:
        gb, source = tier, "catalog"
    else:
        gb, source = None, "none"
    if mode == "edit":
        reference = workload_policy.required_reference_total_memory_gb(model, catalog_entry)
        if reference is not None and (gb is None or reference > gb):
            return reference, "reference_lane"
    return gb, source



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


def _clean_cell(value) -> dict | None:
    """One stored/published reference cell with only the fields it actually states.

    ``false``, ``0`` and ``0.0`` survive verbatim; a null or absent field means "no statement",
    never a coerced zero.
    """
    if not isinstance(value, dict):
        return None
    cell: dict = {}
    if type(value.get("allowed")) is bool:
        cell["allowed"] = value["allowed"]
    maximum = value.get("max_reference_images")
    if isinstance(maximum, int) and not isinstance(maximum, bool) and maximum >= 0:
        cell["max_reference_images"] = maximum
    swap = value.get("swap_allowance_gb")
    if (isinstance(swap, (int, float)) and not isinstance(swap, bool)
            and math.isfinite(swap) and swap >= 0):
        cell["swap_allowance_gb"] = float(swap)
    if not cell:
        return None
    cell["updated_at"] = _number(value.get("updated_at")) or 0.0
    return cell


def _clean_mode_cells(raw: dict) -> dict:
    """One tier's mode -> {generic, counts} view, dropping cells that state nothing."""
    cleaned: dict = {}
    for mode in MODES:
        block = raw.get(mode)
        if not isinstance(block, dict):
            continue
        generic = _clean_cell(block.get("generic"))
        counts: dict[str, dict] = {}
        raw_counts = block.get("counts")
        if isinstance(raw_counts, dict):
            for count, cell in raw_counts.items():
                try:
                    number = int(count)
                except (TypeError, ValueError):
                    continue
                cell = _clean_cell(cell)
                if number >= 1 and cell is not None:
                    counts[str(number)] = cell
        if generic is not None or counts:
            cleaned[mode] = {"generic": generic, "counts": counts}
    return cleaned


def _reference_record(key: str, value: dict) -> dict | None:
    """A stored reference record, or ``None`` when it states nothing usable.

    Storage keeps every physical tier's settings under its own tier block, so a write on one
    tier never discards or reinterprets another tier's values. The older shape (``cells`` keyed
    by mode under one record-level ``tier_gb``) still loads: it is read as that tier's block.
    """
    tier = value.get("tier_gb")
    if isinstance(tier, bool) or not isinstance(tier, (str, int)):
        return None
    raw_cells = value.get("cells")
    if not isinstance(raw_cells, dict):
        return None
    if any(mode in raw_cells for mode in MODES):
        by_tier = {str(tier): _clean_mode_cells(raw_cells)}
    else:
        by_tier = {str(level): _clean_mode_cells(block)
                   for level, block in raw_cells.items() if isinstance(block, dict)}
    by_tier = {level: cells for level, cells in by_tier.items() if cells}
    if not by_tier:
        return None
    return {
        "model": str(value.get("model") or key.split("::", 1)[0]),
        "tier_gb": str(tier),
        "cells": by_tier,
        "updated_at": _number(value.get("updated_at")) or 0.0,
    }


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
            normalized = _normalized(key)
            if normalized.endswith("::reference"):
                record = _reference_record(normalized, value)
                if record is not None:
                    _cache[normalized] = record
                continue
            total = _number(value.get("min_total_memory_gb"))
            free = _number(value.get("min_free_memory_gb"))
            # The pair filter is for legacy/mode RAM records only; reference records above are
            # not RAM and must not be discarded by it.
            if total is None or free is None:
                continue
            mode = value.get("mode")
            if mode is not None and mode not in MODES:
                continue
            record = {
                "model": str(value.get("model") or key),
                "min_total_memory_gb": total,
                "min_free_memory_gb": free,
                "updated_at": _number(value.get("updated_at")) or 0.0,
            }
            if mode is not None:
                record["mode"] = mode
            _cache[normalized] = record
    return _cache


def _ram_override(model_key: str, mode: str | None) -> tuple[dict | None, str | None]:
    """The RAM override that feeds this scope, and its label.

    A mode read prefers the ``model::mode`` record, then the model-wide legacy record, so a
    surviving model-wide override is reported as ``override_scope: 'model'`` (legacy fallback),
    never as a mode-specific setting. A model-wide read only ever uses the legacy record.
    """
    records = _read()
    if mode:
        record = records.get(f"{model_key}::{mode}")
        if isinstance(record, dict) and record.get("mode") == mode:
            return record, "mode"
    record = records.get(model_key)
    if isinstance(record, dict) and "mode" not in record:
        return record, "model"
    return None, None



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


def describe(model: str, catalog_entry: dict, mode: str | None = None) -> dict:
    """Return catalog, model-default, and effective site-local RAM floors.

    ``effective_min_total_memory_gb`` is the model's requirement (`requirement_gb`) and is
    enforced: dispatch refuses a Mac below it, the scheduler leaves that Mac out of the model's
    pool, and the published capacity counts only Macs that meet it. It is 0 for a model that
    publishes no floor at all. ``min_total_source`` names where the number came from.

    ``effective_min_free_memory_gb`` is the live-free floor and ``min_free_source`` says whose
    number it is: ``operator_override`` (the Models screen's own setting), ``model_default`` (the
    floor the model's row publishes), or ``default`` (the model publishes none, so the generic
    ``DEFAULT_MIN_FREE_MEMORY_GB`` applies). Two states, never one sentence for both: a model
    enforcing the generic floor must not read as though its own row had stated it.

    ``_FLEET_DEFAULTS`` is a per-model *measurement* of the **total** tier — a run that proved a
    model needs less than its manifest states — so it cannot be derived from anything, and a model
    nobody has measured keeps what its row, manifest or catalog states. ``fleet_default_measured``
    says which of the two is in force and ``default_reason`` names the absence, so the Models
    screen can show "nobody has measured this one yet". The table is empty today because the two
    numbers it held (FLUX.2 Klein 4-bit, Breeze TTS 2 4-bit) are now in their manifests; a row that
    is below the manifest's own tier still reports ``fleet_default``/``measured`` through
    ``workload_default`` below, so the screen loses nothing. It deliberately does not carry the
    live-free floor: that number has one home, the model's own row.
    """
    key = _normalized(model or catalog_entry.get("repo"))
    catalog_total = _published_total_memory_gb(catalog_entry)
    catalog_free = _number(catalog_entry.get("min_free_memory_gb"))
    qualified_total = workload_policy.required_total_memory_gb(model, catalog_entry)
    qualified_free = workload_policy.required_free_memory_gb(model, catalog_entry)
    # The reference (edit) lane's own floors, published in the worker's lane contract. Absent for
    # every model without that lane, and for every text-to-image item on a model with one.
    reference_total = workload_policy.required_reference_total_memory_gb(model, catalog_entry)
    reference_free = workload_policy.required_reference_free_memory_gb(model, catalog_entry)
    editing = mode == "edit"
    fleet_default = _FLEET_DEFAULTS.get(key)
    workload_default = bool(
        (qualified_total is not None and qualified_total != catalog_total)
        or (qualified_free is not None and qualified_free != catalog_free)
    )

    default_total = requirement_gb(model, catalog_entry, use_override=False, mode=mode)[0]
    # The row's own published floor, and nothing else: a Hub-side copy of it would outrank the row
    # and make an edit to the row invisible.
    default_free = max(DEFAULT_MIN_FREE_MEMORY_GB, qualified_free or 0.0)
    if editing and reference_free is not None:
        default_free = max(default_free, reference_free)
    override, override_scope = _ram_override(key, mode)
    required_total, total_source = requirement_gb(model, catalog_entry, mode=mode)
    effective_total = required_total or 0.0
    base_free = override["min_free_memory_gb"] if override else default_free
    # A reference job's own floor is a measurement of the edit lane, not a site preference: it is
    # enforced even over an operator override that is lower, and `min_free_source` says so, because
    # otherwise the screen would name a number the gate is not using.
    if editing and reference_free is not None and reference_free >= base_free:
        effective_free = reference_free
        min_free_source = "reference_lane"
    else:
        effective_free = base_free
        min_free_source = (
            "operator_override" if override
            else "model_default" if qualified_free is not None
            else "default"
        )

    source = "operator_override" if override else (
        # Compatibility with the published contract: a Hub production floor reports as
        # `fleet_default`, and the measurement question ("has anybody measured this model?") is
        # answered by `fleet_default_measured` below rather than by reusing this string.
        "reference_lane" if total_source == "reference_lane"
        else "fleet_default" if fleet_default or workload_default else "catalog"
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
        # The lane these floors describe. `edit` adds the reference lane's rung; absent means the
        # model's own (text-to-image) floors, exactly as before this lane existed.
        "mode": mode or "txt2img",
        "catalog_min_total_memory_gb": catalog_total,
        "catalog_min_free_memory_gb": catalog_free,
        "reference_min_total_memory_gb": reference_total,
        "reference_min_free_memory_gb": reference_free,
        "default_min_total_memory_gb": default_total,
        "default_min_free_memory_gb": default_free,
        "effective_min_total_memory_gb": effective_total,
        "min_total_source": total_source,
        "effective_min_free_memory_gb": effective_free,
        # Whose live-free number this is: the operator's setting, the model's own row, or the
        # generic floor because the model publishes none. The Models screen shows this verbatim.
        "min_free_source": min_free_source,
        "source": source,
        # Whether a per-model fleet measurement exists for this model. False is not an error:
        # it is the honest state of a model nobody has measured yet.
        "fleet_default_measured": fleet_default is not None,
        "default_reason": default_reason,
        "overridden": override is not None,
        # Which record feeds this answer: a mode record, the model-wide legacy fallback, or
        # nothing (published values only). The screen must never present a model-wide number as
        # a mode-specific one.
        "override_scope": override_scope,
        "updated_at": override.get("updated_at") if override else None,
    }


def set_override(model: str, *, min_total_memory_gb: float,
                 min_free_memory_gb: float, catalog_entry: dict,
                 mode: str | None = None) -> dict:
    """Write one RAM override: the model-wide legacy record (default) or a mode record.

    The local Models-screen route keeps calling this without ``mode`` and writes the same
    legacy record it always has. The fleet route passes ``mode``; a mode reset never touches
    the model-wide record and vice versa.
    """
    key = _normalized(model)
    if not key:
        raise ValueError("model is required")
    if mode is not None and mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    global _cache
    with _LOCK:
        values = dict(_read())
        record = {
            "model": model,
            "min_total_memory_gb": float(min_total_memory_gb),
            "min_free_memory_gb": float(min_free_memory_gb),
            "updated_at": time.time(),
        }
        if mode is not None:
            record["mode"] = mode
        values[f"{key}::{mode}" if mode else key] = record
        _write(values)
        _cache = values
    return describe(model, catalog_entry, mode=mode)


def worker_free_floor_gb(model: str, mode: str | None = None) -> float | None:
    """The live-free floor the Voice worker's own preflight must use in place of its own row: the
    operator's override, else ``None`` (the worker reads the model's row itself).

    Only an override is forwarded, and that is the whole point of this function. It used to carry
    the fleet measurement too, because the worker's preflight computed a *formula* (``size_gb +
    1.25``): Breeze's worker asked 2.9 + 1.25 = 4.15 GB free while the Hub admitted it at 3.2, so an
    8 GB Mac (3.3-3.5 GB free) would refuse every job it was sent. The worker now reads the same
    row the Hub does (``components/voice/app/backend/generation.py``, ``_required_free_memory_gb``),
    so the row needs no forwarding and the two guards agree by construction. An override is
    site-local and is on no row, so it is the one number that still has to travel.

    ``mode`` is optional: absent keeps the model-wide legacy answer every existing caller uses;
    present prefers that mode's record and falls back to the model-wide record."""
    override, _scope = _ram_override(_normalized(model), mode)
    return override["min_free_memory_gb"] if override else None


def reset_override(model: str, catalog_entry: dict, mode: str | None = None) -> dict:
    """Clear the declared RAM record for this scope only; no reset sequence number exists.

    Without ``mode`` this is the legacy model-wide clear (API unchanged). With ``mode`` it
    removes only ``model::mode``; a surviving model-wide record then reads as
    ``override_scope: 'model'``. Reference rows are never touched.
    """
    key = _normalized(model)
    if mode is not None and mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    global _cache
    with _LOCK:
        values = dict(_read())
        values.pop(f"{key}::{mode}" if mode else key, None)
        if values:
            _write(values)
        else:
            try:
                SETTINGS_FILE.unlink()
            except FileNotFoundError:
                pass
        _cache = values
    return describe(model, catalog_entry, mode=mode)


@lru_cache(maxsize=1)
def _reference_limit_table() -> dict | None:
    """The published per-tier reference table, or ``None`` when this install cannot read it.

    This is the same file the Image worker resolves through ``generation.reference_tier`` and
    ``generation.effective_reference_policy``; reading the data here keeps the Hub free of the
    worker's heavy import while using the one published authority. A missing/unreadable table
    makes the readback ``available: false`` (loud), never an empty object and never a guess.
    """
    try:
        table = json.loads(REFERENCE_LIMITS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(table, dict) or table.get("schema") != "studiohub.image-reference-limits.v1":
        return None
    return table


def physical_tier(total_memory_bytes: int | None) -> str | None:
    """The physical band this host falls in (``'8' | '16' | '24' | '32' | '<8'``).

    Identical arithmetic to ``generation.reference_tier``: decimal fleet bytes -> binary GiB,
    rounded, placed in the first published band it clears. ``None`` means the published table is
    unreadable, so no band is known and a reference write must be refused (never guessed).
    """
    table = _reference_limit_table()
    if table is None:
        return None
    gib = round((total_memory_bytes or 0) / 1024 ** 3)
    bands: set[int] = set()
    for row in (table or {}).get("rows") or []:
        for tier in (row.get("tiers") or {}):
            try:
                bands.add(int(tier))
            except (TypeError, ValueError):
                continue
    for floor in sorted(bands, reverse=True):
        if gib >= floor:
            return str(floor)
    return "<8"


def _published_reference_rows(model: str) -> list[dict]:
    table = _reference_limit_table()
    if table is None:
        return []
    wanted = {str(model), _normalized(model)}
    return [row for row in table.get("rows") or []
            if isinstance(row, dict)
            and (row.get("model_id") in wanted or row.get("package_id") in wanted)]


def _published_cell(row: dict | None, tier: str) -> dict | None:
    if not isinstance(row, dict):
        return None
    cell = (row.get("tiers") or {}).get(tier)
    return _clean_cell(cell)


def _override_cells(model: str, mode: str, tier: str) -> tuple:
    """The stored overrides for this model/mode at this tier, plus per-row stale flags.

    Other tiers' blocks stay stored but are never applied here. A row with no current-tier
    setting but a setting on another tier reports ``stale_tier_ignored`` for that row.
    """
    record = _read().get(f"{_normalized(model)}::reference")
    if not isinstance(record, dict):
        return None, {}, False, set()
    by_tier = record.get("cells") or {}
    block = (by_tier.get(tier) or {}).get(mode)
    generic = block.get("generic") if isinstance(block, dict) else None
    counts: dict[int, dict] = {}
    for count, cell in ((block or {}).get("counts") or {}).items():
        try:
            number = int(count)
        except (TypeError, ValueError):
            continue
        if number >= 1 and isinstance(cell, dict):
            counts[number] = cell
    stale_generic = False
    stale_counts: set[int] = set()
    for level, cells in by_tier.items():
        if level == tier:
            continue
        other = (cells or {}).get(mode)
        if not isinstance(other, dict):
            continue
        if other.get("generic"):
            stale_generic = True
        for count in other.get("counts") or {}:
            try:
                number = int(count)
            except (TypeError, ValueError):
                continue
            if number not in counts:
                stale_counts.add(number)
    return generic, counts, (generic is None and stale_generic), stale_counts


def _intrinsic_cap(rows: list[dict], table: dict) -> tuple[int | None, str | None]:
    package = next((row.get("package_id") for row in rows if row.get("package_id")), None)
    entry = (table.get("engine_intrinsic_cap") or {}).get(package) if package else None
    if isinstance(entry, dict):
        value = entry.get("max")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value, None
    return None, "the engine intrinsic cap is unknown for this model; no clamp was applied"


def reference_policy(model: str, mode: str, total_memory_bytes: int | None, *,
                     model_default: dict | None = None) -> dict:
    """The effective reference rows for one (model, mode, this Mac's physical tier).

    Resolution order per row and per field (proposal.r5): exact-count override, exact published
    row, generic override, generic published row, model default. A generic override never
    answers for a count that has an exact published row or an exact override. ``false``, ``0``
    and ``0.0`` survive verbatim; a field no rung states stays ``null`` with its reason.

    ``model_default`` is the Image worker's own fallback (``generation.reference_max`` / no
    lane) for a model absent from the table; r5's source vocabulary has no separate default
    label, so those values report as ``published`` while the ``published`` row stays null.
    """
    table = _reference_limit_table()
    if table is None:
        return {"available": False,
                "reason": ("the published reference limits table is not readable at "
                           f"{REFERENCE_LIMITS_PATH}")}
    if mode not in MODES:
        return {"available": False,
                "reason": f"mode {mode!r} is not one of {', '.join(MODES)}"}
    tier = physical_tier(total_memory_bytes)
    if tier is None:
        return {"available": False,
                "reason": ("the published reference limits table is not readable at "
                           f"{REFERENCE_LIMITS_PATH}")}
    rows = [row for row in _published_reference_rows(model) if row.get("mode") == mode]
    generic_published = next((row for row in rows if row.get("reference_count") is None), None)
    published = {
        row["reference_count"]: row for row in rows
        if isinstance(row.get("reference_count"), int)
        and not isinstance(row.get("reference_count"), bool)
        and row["reference_count"] >= 1
    }
    generic_cell = _published_cell(generic_published, tier)
    exact_cells = {count: _published_cell(row, tier) for count, row in published.items()}
    generic_override, override_counts, stale_generic, stale_counts = _override_cells(
        model, mode, tier)
    intrinsic, intrinsic_reason = _intrinsic_cap(rows, table)
    default_cell = _clean_cell(model_default) if isinstance(model_default, dict) else None

    def one(count: int | None, exact_override: dict | None,
            exact_published: dict | None) -> dict:
        rungs: list[tuple[str, dict | None]] = []
        if count is not None:
            rungs += [("override", exact_override), ("published", exact_published)]
        rungs += [("override", generic_override), ("published", generic_cell)]
        if default_cell is not None:
            rungs.append(("published", default_cell))

        def pick(field: str) -> tuple:
            for source, cell in rungs:
                if isinstance(cell, dict) and field in cell:
                    return cell[field], source, None
            return (None, "absent",
                    f"no override, published row or model default states {field} for "
                    f"reference_count {count} at physical tier {tier!r}")

        allowed, allowed_source, allowed_reason = pick("allowed")
        maximum, max_source, max_reason = pick("max_reference_images")
        swap, swap_source, swap_reason = pick("swap_allowance_gb")
        clamped_reason = None
        if isinstance(maximum, int) and intrinsic is not None and maximum > intrinsic:
            maximum = intrinsic
            clamped_reason = f"clamped to the engine intrinsic cap {intrinsic}"
        sources = {"allowed": allowed_source,
                   "max_reference_images": max_source,
                   "swap_allowance_gb": swap_source}
        return {
            "reference_count": count,
            "effective": {"allowed": allowed, "max_reference_images": maximum,
                          "swap_allowance_gb": swap},
            "sources": sources,
            "unknown_reasons": {"allowed": allowed_reason,
                                "max_reference_images": max_reason,
                                "swap_allowance_gb": swap_reason},
            "published": (published.get(count) if count is not None else generic_published),
            "override": exact_override if count is not None else generic_override,
            "max_intrinsic": intrinsic,
            "intrinsic_unknown_reason": intrinsic_reason,
            "clamped_reason": clamped_reason,
            "overridden": "override" in sources.values(),
            "stale_tier_ignored": (stale_generic if count is None else count in stale_counts),
        }

    counts = sorted(set(published) | set(override_counts))
    return {
        "available": True,
        "physical_tier": tier,
        "physical_tier_gb": int(tier) if tier != "<8" else None,
        "total_memory_bytes": (int(total_memory_bytes)
                               if isinstance(total_memory_bytes, (int, float))
                               and not isinstance(total_memory_bytes, bool) else None),
        "mode": mode,
        "intrinsic_max": intrinsic,
        "intrinsic_unknown_reason": intrinsic_reason,
        "rows": [one(None, None, None)]
        + [one(count, override_counts.get(count), exact_cells.get(count)) for count in counts],
    }


def set_reference_override(model: str, mode: str, reference_count: int | None,
                           value: dict | None, *, total_memory_bytes: int) -> None:
    """Write or clear exactly one reference row on this Mac; sibling rows are untouched.

    ``reference_count`` is ``None`` for the generic row or a strict positive int for an exact
    row. ``value`` ``None`` clears the selected row; an absent field retains its stored value
    and an explicit ``null`` clears only that field. Other tiers' settings stay stored and
    restorable; only the current tier's selected identity is mutated.
    """
    key = _normalized(model)
    if not key:
        raise ValueError("model is required")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    if (reference_count is not None
            and (isinstance(reference_count, bool)
                 or not isinstance(reference_count, int) or reference_count < 1)):
        raise ValueError("reference_count must be null or a positive integer")
    if (isinstance(total_memory_bytes, bool)
            or not isinstance(total_memory_bytes, (int, float)) or total_memory_bytes < 0):
        raise ValueError("total_memory_bytes is required to resolve the physical tier")
    if _reference_limit_table() is None:
        raise TierUnknown(
            "TIER_UNKNOWN: the published reference limits table is unreadable")
    tier = physical_tier(total_memory_bytes)
    if tier is None:
        raise TierUnknown(
            "TIER_UNKNOWN: the published reference limits table is unreadable")
    updates: dict = {}
    if value is not None:
        if not isinstance(value, dict):
            raise ValueError("reference value must be an object or null")
        unknown = set(value) - {"allowed", "max_reference_images", "swap_allowance_gb"}
        if unknown:
            raise ValueError(f"unknown reference field(s): {', '.join(sorted(unknown))}")
        if not value:
            raise ValueError("reference value is empty")
        if ("allowed" in value and value["allowed"] is not None
                and type(value["allowed"]) is not bool):
            raise ValueError("allowed must be a boolean or null")
        if ("max_reference_images" in value and value["max_reference_images"] is not None
                and (isinstance(value["max_reference_images"], bool)
                     or not isinstance(value["max_reference_images"], int)
                     or value["max_reference_images"] < 0)):
            raise ValueError("max_reference_images must be an integer >= 0 or null")
        swap = value.get("swap_allowance_gb")
        if (swap is not None and (isinstance(swap, bool) or not isinstance(swap, (int, float))
                                  or not math.isfinite(swap) or swap < 0)):
            raise ValueError("swap_allowance_gb must be a finite number >= 0 or null")
        updates = {field: (float(value[field]) if field == "swap_allowance_gb"
                           and value[field] is not None else value[field])
                   for field in ("allowed", "max_reference_images", "swap_allowance_gb")
                   if field in value}
    global _cache
    with _LOCK:
        values = json.loads(json.dumps(_read()))
        record_key = f"{key}::reference"
        record = values.get(record_key)
        if not isinstance(record, dict):
            record = {"model": model, "tier_gb": tier, "cells": {}, "updated_at": 0.0}
        by_tier = record.setdefault("cells", {})
        tier_cells = by_tier.setdefault(tier, {})
        block = tier_cells.setdefault(mode, {"generic": None, "counts": {}})
        selected = (block.get("generic") if reference_count is None
                    else (block.get("counts") or {}).get(str(reference_count)))
        if value is None:
            cell = None
        else:
            # A partial write changes only the fields it names; an explicit null clears only
            # that field, and a cell left with no fields is the selected row's clear.
            cell = dict(selected) if isinstance(selected, dict) else {}
            cell.pop("updated_at", None)
            for field, field_value in updates.items():
                if field_value is None:
                    cell.pop(field, None)
                else:
                    cell[field] = field_value
            if cell:
                cell["updated_at"] = time.time()
            else:
                cell = None
        if reference_count is None:
            block["generic"] = cell
        else:
            counts = block.setdefault("counts", {})
            if cell is None:
                counts.pop(str(reference_count), None)
            else:
                counts[str(reference_count)] = cell
        if not block.get("generic") and not block.get("counts"):
            tier_cells.pop(mode, None)
        if not tier_cells:
            by_tier.pop(tier, None)
        if not by_tier:
            values.pop(record_key, None)
        else:
            record["tier_gb"] = tier
            record["updated_at"] = time.time()
            values[record_key] = record
        _write(values)
        _cache = values


_REVISION_COVERS_MODEL = ["node_id", "model", "mode", "ram_effective", "ram_sources",
                          "override_scope", "model_wide_fallback", "published_ram"]
_REVISION_COVERS_MODE = _REVISION_COVERS_MODEL + ["physical_tier", "reference_rows"]


class RevisionConflict(Exception):
    """The expected revision no longer describes this scope; ``current`` is the fresh state."""

    def __init__(self, current: dict):
        super().__init__("revision conflict")
        self.current = current


def _bound_state(description: dict, model_key: str, mode: str | None,
                 reference: dict | None, node_id: str) -> dict:
    """The effective state a revision token binds; updated_at and write order are excluded."""
    state = {
        "node_id": node_id,
        "model": model_key,
        "mode": mode,
        "ram_effective": {
            "min_total_memory_gb": description["effective_min_total_memory_gb"],
            "min_free_memory_gb": description["effective_min_free_memory_gb"],
        },
        "ram_sources": {
            "min_total_source": description["min_total_source"],
            "min_free_source": description["min_free_source"],
        },
        "override_scope": description["override_scope"],
        "published_ram": {
            "catalog_min_total_memory_gb": description["catalog_min_total_memory_gb"],
            "catalog_min_free_memory_gb": description["catalog_min_free_memory_gb"],
            "default_min_total_memory_gb": description["default_min_total_memory_gb"],
            "default_min_free_memory_gb": description["default_min_free_memory_gb"],
            "reference_min_total_memory_gb": description["reference_min_total_memory_gb"],
            "reference_min_free_memory_gb": description["reference_min_free_memory_gb"],
        },
        "model_wide_fallback": None,
    }
    if mode and description["override_scope"] == "model":
        record = _read().get(model_key)
        if isinstance(record, dict) and "mode" not in record:
            state["model_wide_fallback"] = {
                "min_total_memory_gb": record["min_total_memory_gb"],
                "min_free_memory_gb": record["min_free_memory_gb"],
            }
    if reference is not None:
        if reference.get("available"):
            state["physical_tier"] = {
                "tier": reference["physical_tier"],
                "tier_gb": reference["physical_tier_gb"],
                "total_memory_bytes": reference["total_memory_bytes"],
            }
            state["reference_rows"] = reference["rows"]
        else:
            # An unreadable table is an honest unavailable state, not a crash and not a guess.
            state["physical_tier"] = {"tier": None, "tier_gb": None,
                                      "total_memory_bytes": None}
            state["reference_rows"] = {"available": False,
                                       "reason": reference.get("reason")}
    return state


def _without_timestamps(value):
    """Strip ``updated_at`` at every depth: write time is not part of the effective state."""
    if isinstance(value, dict):
        return {key: _without_timestamps(item) for key, item in value.items()
                if key != "updated_at"}
    if isinstance(value, list):
        return [_without_timestamps(item) for item in value]
    return value


def _revision(state: dict) -> str:
    payload = json.dumps(_without_timestamps(state), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def effective_state(model: str, catalog_entry: dict, *, node_id: str,
                    mode: str | None = None, total_memory_bytes: int | None = None,
                    model_default: dict | None = None) -> dict:
    """One readback: the existing describe spread plus override_scope, reference_policy (mode
    only), the state token and the groups that token covers.

    This is the small function the fleet route, catalog inventory and submit/dispatch consumers
    call, so they all resolve one effective policy. No route accepts writes until its enforcers
    are wired; this module only exposes the shared resolver.
    """
    if mode is not None and mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    description = describe(model, catalog_entry, mode=mode)
    model_key = _normalized(model or catalog_entry.get("repo"))
    reference = (reference_policy(model, mode, total_memory_bytes,
                                  model_default=model_default) if mode else None)
    state = _bound_state(description, model_key, mode, reference, str(node_id))
    payload = dict(description)
    if reference is not None:
        payload["reference_policy"] = reference
    payload["revision"] = _revision(state)
    payload["revision_covers"] = list(
        _REVISION_COVERS_MODE if mode else _REVISION_COVERS_MODEL)
    return payload


def compare_and_write(model: str, catalog_entry: dict, *, node_id: str,
                      expected_revision: str, mode: str | None = None,
                      total_memory_bytes: int | None = None,
                      model_default: dict | None = None, change) -> dict:
    """Compare the scope token, then run ``change`` and return the fresh readback.

    Compare and write are one critical section under the module lock, the same lock every local
    legacy write takes, so a fleet CAS and a local Models-screen write cannot interleave. A
    mismatch raises :class:`RevisionConflict` carrying the current state. There is no sequence
    number and no replay.
    """
    if (not isinstance(expected_revision, str) or len(expected_revision) != 64
            or any(char not in "0123456789abcdef" for char in expected_revision)):
        raise ValueError("expected_revision must be 64 lowercase hex characters")
    if not callable(change):
        raise ValueError("change must be callable")
    with _LOCK:
        current = effective_state(model, catalog_entry, node_id=node_id, mode=mode,
                                  total_memory_bytes=total_memory_bytes,
                                  model_default=model_default)
        if expected_revision != current["revision"]:
            raise RevisionConflict(current)
        change()
        return effective_state(model, catalog_entry, node_id=node_id, mode=mode,
                                total_memory_bytes=total_memory_bytes,
                                model_default=model_default)


def reset_for_tests() -> None:
    global _cache, _manifest_cache
    _cache = None
    _manifest_cache = None
    _reference_limit_table.cache_clear()
