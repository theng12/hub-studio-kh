"""The one memory-mode table and the one ``auto`` rule, shared by every Studio.

Every Studio offers the same four fixed answers — ``performance`` (never releases),
``balanced`` (600 s idle), ``memory_saver`` (120 s) and ``immediate`` (0 s) — and until
this module existed each of them also carried its own copy of that table, kept honest by
``tests/test_memory_policy_contract.py`` comparing the copies entry for entry.  A fifth
mode whose answer depends on the machine cannot be kept honest that way: the rule *is*
the contract, so the table and the rule live here and every worker imports them.

**Why ``auto`` is the default.**  Every Studio used to default to ``immediate``, for a
measured reason: the fleet's 8 GB machines share one Mac between 3-5 Studios, so a model
held between jobs by one is memory the next job cannot have (2026-08-07: 16 of 19
machines below the memory guard's 3.2 GB floor with 1.5-4.4 GB of swap burned; a
2026-08-09 five-machine run measured the following Voice job on an M4 16 GB falling from
52.7 s to 4.0 s once the prior Studio released immediately).  That answer is wrong on a
16/24/32 GB Mac, where it buys a full model reload per job to free memory nobody was
short of.  ``auto`` is the
one default that is right on both: the machine answers for itself.

**The rule, identical everywhere.**  At 16 GiB of unified memory and above, ``auto``
behaves as ``balanced``; below it, as ``immediate``.  A machine whose memory cannot be
measured must not hold a model — a Mac that cannot prove it has room gets ``immediate``,
never ``balanced`` by default.

**A released/held decision never preempts work.**  Nothing here changes that rule: a
release refuses while a job is queued or running, and the idle timer asks the same
question first.  ``auto`` only chooses a threshold; it cannot override a refusal.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from components import preferred_hold_clock

# The mode an operator selects when they have not chosen one. It is a real entry in the
# table below, not a sentinel: status() publishes it as `mode` and publishes the machine's
# answer as `resolved_mode` beside it.
AUTO = "auto"
DEFAULT_MODE = AUTO

# The line between "can afford to hold a model between jobs" and "cannot". 16 GiB is the
# smallest Mac in this fleet that runs a Studio beside its siblings without swapping; the
# 8 GB tier is the one the measurements above are about.
AUTO_BALANCED_MIN_BYTES = 16 * 1024**3

# The fleet contract, entry for entry. `auto` carries `idle_seconds: None` because it has
# no threshold of its own — what it resolves to is published per machine, in `status()` and
# in the `auto` entry of `options()`, never left for a UI to re-derive.
MODES = {
    AUTO: {"idle_seconds": None, "label": "Auto"},
    "performance": {"idle_seconds": None, "label": "Performance"},
    "balanced": {"idle_seconds": 600, "label": "Balanced"},
    "memory_saver": {"idle_seconds": 120, "label": "Memory Saver"},
    "immediate": {"idle_seconds": 0, "label": "Immediate"},
}


def machine_bytes() -> int:
    """This Mac's unified memory in bytes, or 0 when it cannot be measured.

    One reader for every Studio, so "this machine has room" means the same thing in all of
    them. 0 is not a size: it is the answer "unknown", and :func:`machine_mode` treats it
    the way an 8 GB Mac is treated.
    """
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 0


def machine_mode(total_bytes: int | None = None) -> str:
    """What ``auto`` resolves to on this machine: ``balanced`` or ``immediate``.

    Small machines must not hold a model between jobs; large ones can afford the reload
    saving. An unreadable or zero memory size resolves to ``immediate``: a machine that
    cannot prove it has room must not hold a model.
    """
    total = machine_bytes() if total_bytes is None else int(total_bytes)
    if total >= AUTO_BALANCED_MIN_BYTES:
        return "balanced"
    return "immediate"


def resolved_mode(mode: str, machine: str | None = None) -> str:
    """The mode actually in force for a selected ``mode``.

    ``machine`` is ``auto``'s answer for this Mac (:func:`machine_mode`); it is measured
    when not supplied. Any mode that is not in the table is treated as the default, the
    same way a persisted mode file falls back.
    """
    if mode not in MODES:
        mode = DEFAULT_MODE
    if mode != AUTO:
        return mode
    return machine_mode() if machine is None else machine


def idle_seconds(mode: str, machine: str | None = None) -> int | None:
    """The threshold ``mode`` is actually running with.

    Never ``None`` when the selection is ``auto``: a UI that reads ``None`` renders "keeps
    the model loaded until you release it", which for auto would be a lie.
    """
    return MODES[resolved_mode(mode, machine)]["idle_seconds"]


def _window(seconds: int | None) -> str:
    """The idle window as an operator reads it, derived from the table's own number."""
    if seconds is None:
        return "never on its own"
    if seconds <= 0:
        return "as soon as each job finishes"
    minutes, remainder = divmod(int(seconds), 60)
    if remainder:
        return f"after {seconds} idle seconds"
    return f"after {minutes} idle minute" + ("" if minutes == 1 else "s")


def option_detail(mode: str, machine: str | None = None) -> str:
    """One sentence per mode, saying what the operator gets if they pick it.

    ``machine`` is ``auto``'s answer for this Mac. ``auto``'s sentence names that answer
    and the threshold it means, because "Follows this Mac" alone tells an operator on an
    8 GB Mac nothing about what will happen.
    """
    if mode not in MODES:
        raise KeyError(mode)
    if mode == AUTO:
        answer = machine_mode() if machine is None else machine
        return (f"Follows this Mac: releases "
                f"{_window(MODES[answer]['idle_seconds'])} here")
    if MODES[mode]["idle_seconds"] is None:
        return "Keeps the model loaded until you release it"
    return f"Releases the model {_window(MODES[mode]['idle_seconds'])}"


def options(machine: str | None = None) -> list[dict]:
    """Every mode as a picker renders it, with every threshold already resolved.

    ``idle_seconds`` here is the mode's own resolved threshold — for ``auto`` that is this
    machine's answer, not the table's ``None`` — so a UI can render all five without
    re-deriving the rule, and cannot disagree with what the worker will actually do.
    """
    answer = machine_mode() if machine is None else machine
    resolved = resolved_mode(AUTO, answer)
    return [
        {
            "mode": mode,
            "label": value["label"],
            "idle_seconds": MODES[resolved if mode == AUTO else mode]["idle_seconds"],
            "detail": option_detail(mode, answer),
        }
        for mode, value in MODES.items()
    ]


def _preferred_flags_row(worker_data_dir) -> dict:
    """The Hub's ``"local"`` machine_flags row beside this worker, or ``{}``.

    Packaged layout only: the Hub runs with ``STUDIOHUB_DATA_DIR=<root>/hub`` and every worker
    gets ``--data-dir <root>`` and keeps its own state in ``<root>/<studio>``, so the row this
    Mac's scheduler owns is the sibling ``hub/machine_flags.json``. A missing file, malformed
    JSON, a non-dict document or a non-dict row all read as ``{}``: the worker falls back to
    its saved memory mode exactly as before. Dev/qualification layouts without that sibling
    simply fail open.
    """
    try:
        path = Path(worker_data_dir).resolve().parent / "hub" / "machine_flags.json"
        flags = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    row = flags.get("local") if isinstance(flags, dict) else None
    return row if isinstance(row, dict) else {}


def _preferred_policy(row: dict, modality: str) -> dict | None:
    """The enabled preference for this worker's own modality, bounds-clamped, or ``None``.

    Only a real boolean ``true`` counts as enabled — the Hub writes exactly that, and a
    hand-edited truthy value (``1``, ``"yes"``) reads as off rather than enabling a hold the
    operator never chose. A legacy row with no ``enabled`` key reads off, as before.
    """
    policy = row.get("preferred_work") if isinstance(row, dict) else None
    if not isinstance(policy, dict) or policy.get("enabled") is not True:
        return None
    if policy.get("modality") != modality:
        return None
    low, high = preferred_hold_clock.PREFERRED_WORK_BOUNDS
    seconds = policy.get("idle_seconds")
    if not isinstance(seconds, int) or isinstance(seconds, bool) \
            or not low <= seconds <= high:
        seconds = preferred_hold_clock.PREFERRED_WORK_DEFAULT_S
    return {"modality": modality, "idle_seconds": int(seconds)}


def hold_exempt(worker_data_dir, modality: str, stored_mode: str) -> bool:
    """Whether this worker's own idle release should wait for the preferred-work hold.

    True only while the sibling Hub row says this worker's modality is the enabled preference
    AND a bounded hold or dispatch reservation for it is still alive. An explicitly stored
    ``immediate`` mode is authoritative and always returns False, as does any missing or
    malformed row. This never overrides manual release or memory pressure; callers keep those
    branches unconditional. It declines only the idle timer, and never promises a warm model.
    """
    if stored_mode == "immediate":
        return False
    row = _preferred_flags_row(worker_data_dir)
    policy = _preferred_policy(row, modality)
    if policy is None:
        return False
    if preferred_hold_clock.hold_remaining_s(policy, row.get("preferred_hold")) > 0.0:
        return True
    reservation = row.get("preferred_reservation")
    if not isinstance(reservation, dict) or reservation.get("modality") != modality:
        return False
    return preferred_hold_clock.reservation_remaining_s(reservation) > 0.0

