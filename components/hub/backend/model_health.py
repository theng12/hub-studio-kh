"""Per-(machine, model) health with a machine-readable reason.

The Hub already knows, from each worker's own catalogue row, whether the model's
files are cached (``cache.state``), whether the worker reported its runtime ready,
whether the machine meets the model's memory tier, and whether the worker called
the model available.  This module reduces that direct evidence to one closed
vocabulary state per (machine, model) and folds in the OS and chip gates the
packaged readiness code already computes (``standalone_config.model_readiness``).

Missing evidence yields ``unknown``.  It is never turned into a plausible
default: a row that did not say a model is ready is not reported as ready.
"""

from __future__ import annotations

from .. import standalone_config
from . import broker, model_ids

# Closed vocabulary.  Nothing outside this set may be returned as a state.
STATES = (
    "ready",
    "download_required",
    "runtime_missing",
    "unsupported_os",
    "unsupported_chip",
    "insufficient_memory",
    "quarantined",
    "unknown",
)
_STATE_SET = frozenset(STATES)


def _bool(value) -> bool | None:
    return value if isinstance(value, bool) else None


def _cache_evidence(entry: dict) -> tuple[bool | None, str | None]:
    cache = entry.get("cache")
    if isinstance(cache, dict):
        state = cache.get("state")
        if isinstance(state, str) and state:
            return state == "cached", state
    return _bool(entry.get("hub_cached")), None


def _paused_index(index: dict[str, str]) -> dict[tuple[str, str], dict]:
    paused: dict[tuple[str, str], dict] = {}
    for row in broker.model_protection_snapshot():
        studio = row.get("studio")
        model = row.get("model")
        if not isinstance(studio, str) or not isinstance(model, str):
            continue
        paused[(studio, model)] = row
        canonical = model_ids.canonical(model, index)
        paused[(studio, canonical)] = row
    return paused


def _row(entry: dict, repo: str, model_id: str, machine: str,
         manifests: dict[str, dict], paused: dict[tuple[str, str], dict],
         registry: dict[str, dict]) -> dict:
    studio_id = str(entry.get("hub_studio") or "")
    observed_at = entry.get("hub_catalog_observed_at")
    if not isinstance(observed_at, (int, float)) or isinstance(observed_at, bool):
        observed_at = None

    cached, cache_state = _cache_evidence(entry)
    runtime_ready = _bool(entry.get("runtime_ready"))
    available = _bool(entry.get("available"))
    memory_eligible = _bool(entry.get("memory_eligible"))
    hub_ready = _bool(entry.get("hub_ready"))

    manifest = manifests.get(model_id)
    readiness = None
    if manifest is not None:
        worker = registry.get(studio_id) or {}
        runtime_identity = worker.get("runtime_identity")
        if not isinstance(runtime_identity, dict):
            runtime_identity = {"compatible": False}
        readiness = standalone_config.model_readiness({
            "files_state": "files_verified" if cached else "not_installed",
            "minimum_macos": manifest.get("minimum_macos"),
            "runtime_identity": runtime_identity,
        })
    os_state = readiness.get("os_state") if readiness else None
    chip_state = readiness.get("chip_state") if readiness else None
    runtime_state = readiness.get("runtime_state") if readiness else None
    local = machine == "local"

    pause = paused.get((studio_id, repo)) or paused.get((studio_id, model_id))
    state = "unknown"
    reason: str | None = None
    hint: str | None = None

    if pause is not None:
        state = "quarantined"
        code = pause.get("error_code")
        reason = str(code)[:120] if isinstance(code, str) and code else "model_paused"
        text = pause.get("reason")
        hint = (str(text)[:500] if isinstance(text, str) and text
                else "The Hub paused this model after repeated failures.")
    elif cached is False:
        state = "download_required"
        reason = "model_files_not_cached"
        label = cache_state or "not cached"
        hint = f"The model files are {label} on {machine}."
    elif (runtime_ready is False
          or entry.get("runtime_compatible") is False
          or (local and runtime_state == "incompatible")):
        state = "runtime_missing"
        reason = "runtime_not_ready"
        hint = "This Studio's runtime for the model is not ready."
    elif local and os_state == "unsupported":
        state = "unsupported_os"
        reason = "macos_below_minimum"
        hint = f"Requires macOS {manifest.get('minimum_macos')} or newer."
    elif local and chip_state == "unsupported":
        state = "unsupported_chip"
        reason = "chip_unsupported"
        hint = "This model requires Apple silicon."
    elif memory_eligible is False:
        state = "insufficient_memory"
        reason = "insufficient_memory"
        hint = "This Mac does not meet the model's memory floor."
    elif available is True or hub_ready is True:
        state = "ready"
    # Anything else stays "unknown": the evidence did not say the model is
    # usable, so it is not reported as ready.

    return {
        "model_id": model_id,
        "machine": machine,
        "state": state if state in _STATE_SET else "unknown",
        "reason": reason,
        "hint": hint,
        "observed_at": observed_at,
    }


def snapshot(monitor) -> list[dict]:
    """One health row per (machine, model) from cached catalogue evidence."""
    aggregate = monitor.cached_aggregate_catalog()
    index = model_ids.load_index()
    manifests = model_ids.load_manifests()
    paused = _paused_index(index)
    registry = {str(studio.get("id")): studio for studio in monitor.registry}

    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for entry in aggregate.get("models") or []:
        if not isinstance(entry, dict):
            continue
        repo = entry.get("repo")
        if not isinstance(repo, str) or not repo:
            continue
        model_id = model_ids.canonical(repo, index)
        machine = str(entry.get("hub_machine") or "local")
        key = (machine, model_id)
        if key in seen:
            continue
        seen.add(key)
        rows.append(_row(entry, repo, model_id, machine, manifests, paused, registry))
    rows.sort(key=lambda row: (row["machine"], row["model_id"]))
    return rows
