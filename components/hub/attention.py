"""This Mac's own attention state, measured on an interval and published with its measurement time.

Owner, 2026-11-17: *"make sure the Hub's health is sending out signals every 30 minutes so we don't
have to keep checking."* The fleet page could not tell two true statements apart — six Macs read
"needs attention" while the job store had no storage failures for hours, and five of the six were
`last_known` — because nothing said **when** a verdict was measured. So every answer here carries
`measured_at`, and a cached answer says so rather than pretending to be current.

Two callers:
- the background loop (unprompted, every `interval()` seconds) refreshes the cache, so a cleared
  condition clears the flag with nobody touching the Mac;
- `GET /api/hub/attention?now=1` re-measures inside a bounded time for the fleet page.

The interval is a setting (default 30 minutes), not a constant buried in code.
"""
from __future__ import annotations

import json
import logging
import time

from .backend import ledger

log = logging.getLogger(__name__)

SETTINGS_FILE = "attention-settings.json"
DEFAULT_INTERVAL_S = 1800
MIN_INTERVAL_S = 60
MAX_INTERVAL_S = 24 * 3600

_CACHE: dict = {"result": None, "checked_in_at": None}
#: The closed set of reason codes. The Gateway maps these; anything outside renders neutral on its
#: page (Gateway - Lead - 001, 2026-11-17), so this list is the contract, not a suggestion.
REASON_CODES = frozenset({
    "OUTPUT_STORAGE_RED", "OUTPUT_STORAGE_STOP", "OUTPUT_STORAGE_FULL", "LAST_JOB_FAILED",
    "MEASURE_TIMEOUT", "SWAP_GROWTH_EXCEEDED", "INSUFFICIENT_MEMORY", "LOCATION_UNREACHABLE",
    "HEALTH_DOWN", "UNKNOWN",
})

#: Storage tiers that need a person. `amber` is a warning the governor itself acts on, not a fault.
STORAGE_REASONS = {
    "stop": ("OUTPUT_STORAGE_STOP", "Output storage is full enough that the Hub has stopped taking work."),
    "red": ("OUTPUT_STORAGE_RED", "Output storage is low enough that the Hub is not dispatching work."),
}
#: Activity states that mean someone should look. The words are `backend.activity`'s own.
ACTIVITY_REASONS = {
    "needs_attention": ("LAST_JOB_FAILED", "The last job on this Mac failed or a Studio is degraded."),
}


def settings() -> dict:
    """`{"interval_s": n}` from `<data>/attention-settings.json`; the default when absent or unreadable."""
    try:
        raw = json.loads((ledger.DATA_DIR / SETTINGS_FILE).read_text())
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    value = raw.get("interval_s", DEFAULT_INTERVAL_S)
    if type(value) is not int or not MIN_INTERVAL_S <= value <= MAX_INTERVAL_S:
        value = DEFAULT_INTERVAL_S
    return {"interval_s": value}


def set_interval(seconds: object) -> dict:
    """Change how often this Mac reports unprompted. Refuses anything outside 1 minute .. 24 hours."""
    if type(seconds) is not int or not MIN_INTERVAL_S <= seconds <= MAX_INTERVAL_S:
        from .output_storage import TrashRefused
        raise TrashRefused(400, "BAD_INTERVAL",
                           f"The interval must be a whole number of seconds from {MIN_INTERVAL_S} to {MAX_INTERVAL_S}.")
    data = ledger.DATA_DIR
    data.mkdir(parents=True, exist_ok=True)
    path = data / SETTINGS_FILE
    path.write_text(json.dumps({"interval_s": seconds}))
    path.chmod(0o600)
    return settings()


def measure(*, disk: dict | None = None, activity_state: str | None = None, now: float | None = None,
            activity_detail: str | None = None) -> dict:
    """This Mac's attention verdict now, with the time it was measured.

    `disk` is `disk_governor.snapshot()`'s object and `activity_state` is this Mac's own activity
    word (`backend.activity`), both injectable so the rule is testable without a real volume or fleet.
    `activity_detail` lets the caller say *which* job and for how long, when it knows; the code and
    the fallback sentence stay this module's.
    """
    from . import disk_governor
    moment = time.time() if now is None else now
    disk = disk_governor.snapshot() if disk is None else disk
    reasons: list[dict] = []
    storage = disk.get("state")
    if storage in STORAGE_REASONS:
        code, detail = STORAGE_REASONS[storage]
        reasons.append({"code": code, "detail": detail})
    if activity_state in ACTIVITY_REASONS:
        code, detail = ACTIVITY_REASONS[activity_state]
        reasons.append({"code": code, "detail": activity_detail or detail})
    return {
        "state": "attention" if reasons else ("unknown" if storage == "unknown" else "ok"),
        "reasons": reasons,
        "measured_at": moment,
        # Contact, not verdict: the last moment this Hub answered for itself. The Gateway keeps its
        # own contact time too; this one is what a row can carry when it is read off the Mac.
        "checked_in_at": moment,
        "interval_s": settings()["interval_s"],
        "source": "measured",
        # Storage carries its own time so a 507 from yesterday cannot read as today's.
        "storage": {
            "free_gb": disk.get("free_gb"),
            "total_gb": disk.get("total_gb"),
            "state": storage,
            "measured_at": moment,
        },
    }


def refresh(*, disk: dict | None = None, activity_state: str | None = None,
            now: float | None = None, activity_detail: str | None = None) -> dict:
    """Measure and keep it: what the background loop calls every interval."""
    result = measure(disk=disk, activity_state=activity_state, now=now,
                     activity_detail=activity_detail)
    _CACHE["result"] = result
    _CACHE["checked_in_at"] = time.time()
    return result


def cached() -> dict:
    """The last measurement, **with its own time and `source: "cached"`** — or a first measurement if
    nothing has been taken yet. A cached answer without a time is the thing the owner objected to."""
    result = _CACHE["result"]
    if result is None:
        return refresh()
    _CACHE["checked_in_at"] = time.time()          # answering is contact, even when the verdict is old
    return {**result, "source": "cached", "checked_in_at": _CACHE["checked_in_at"]}


def reset_cache() -> None:
    """Tests only: forget the last measurement."""
    _CACHE["result"] = None
    _CACHE["checked_in_at"] = None
