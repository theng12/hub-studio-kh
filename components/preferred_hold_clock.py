"""Pure clock arithmetic for the preferred-work hold and its in-flight reservation.

Stdlib only, no Hub imports: this leaf is shared by the Hub scheduler (re-exported through
``components.hub.backend.preferred_hold``) and by every worker's memory policy (through
``components.memory_modes``). A worker must never import ``components.hub.backend.registry``:
that module binds the Hub's data directory at import time, while workers clear ``STUDIOHUB_*``
and set their own. The leaf therefore carries the bounds and the clamp math, and nothing else.

Both windows are bounded, not trusted:

* the hold is clamped to the configured window;
* the reservation is clamped to its own stored budget.

Each falls back to the wall clock when its boot-scoped monotonic stamp is not plausible
(across a restart). The wall clock is a plausibility source, not a trusted clock: there is no
guaranteed reboot detection, and the guarantee is the clamp — at most one window/budget.

Because these functions are read by worker timers from a row another process can corrupt,
they never raise on stored data: a non-dict policy/hold/reservation, a missing/non-numeric
stamp or window, an integer too large for a float (``OverflowError``), or a NaN/infinite
stamp all read as no window (``0.0``). A corrupt row therefore falls back to the worker's
normal idle behaviour; it is never read as a fresh window.
"""
from __future__ import annotations

import math
import time

# The owner-facing bounds, defined once. ``registry`` aliases these names so the scheduler,
# the API and the native UI cannot drift; workers read them through this leaf.
PREFERRED_WORK_BOUNDS = (1, 3600)
PREFERRED_WORK_DEFAULT_S = 300

# A monotonic stamp is treated as belonging to this boot when it is non-negative and younger
# than this. A larger (or negative) delta means a previous boot's epoch: use the wall clock.
_SAME_BOOT_LIMIT_S = 90.0 * 24 * 3600


def _finite(value: object) -> float | None:
    """The value as a finite float, or ``None`` for anything non-numeric/non-finite."""
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def hold_remaining_s(policy: dict | None, hold: dict | None, *,
                     now_wall: float | None = None,
                     now_steady: float | None = None) -> float:
    """Seconds of hold left for a stored window, bounded to the policy's own window.

    Same boot: remaining = window - (steady now - steady finish). Across a restart the steady
    stamp belongs to a previous boot (a fresh ``time.monotonic()`` reads it as far negative),
    so the wall clock answers, clamped in both directions: a clock that jumps backwards shows
    at most a full window, and a jump forwards expires the hold rather than extending it.
    Stored data is never trusted: a non-dict policy/hold or any non-numeric/non-finite field
    reads as no hold (``0.0``) instead of raising from a worker timer.
    """
    if not isinstance(policy, dict) or not isinstance(hold, dict):
        return 0.0
    seconds = _finite(policy.get("idle_seconds"))
    if seconds is None:
        return 0.0
    seconds = int(seconds)
    if seconds <= 0:
        return 0.0
    ended_wall = _finite(hold.get("ended_wall"))
    boot_steady = _finite(hold.get("boot_steady"))
    # Both stamps must be plausible even when only one branch reads them: a row that carries
    # NaN/infinite garbage is corrupt, not a fresh window.
    if ended_wall is None or boot_steady is None:
        return 0.0
    wall = _finite(time.time() if now_wall is None else now_wall)
    steady = _finite(time.monotonic() if now_steady is None else now_steady)
    if wall is None or steady is None:
        return 0.0
    elapsed_steady = steady - boot_steady
    if 0.0 <= elapsed_steady < _SAME_BOOT_LIMIT_S:
        # Same boot: the monotonic stamp is plausible against this process's own clock.
        elapsed = elapsed_steady
    else:
        elapsed = wall - ended_wall
    return max(0.0, min(float(seconds), float(seconds) - elapsed))


def hold_expires_wall(policy: dict | None, hold: dict | None, *,
                      now_wall: float | None = None) -> float | None:
    """The wall clock a running hold ends at, for a UI countdown; ``None`` when not holding."""
    remaining = hold_remaining_s(policy, hold, now_wall=now_wall,
                                 now_steady=0.0 if now_wall is not None else None)
    if remaining <= 0.0:
        return None
    wall = time.time() if now_wall is None else float(now_wall)
    return wall + remaining


def reservation_remaining_s(reservation: dict | None, *,
                            now_wall: float | None = None,
                            now_steady: float | None = None) -> float:
    """Seconds left on one in-flight reservation, clamped to its own stored budget.

    A reservation is written once immediately before the worker POST and adopted once if the
    worker states a larger deadline. Same boot reads the monotonic stamp; across a restart the
    wall clock answers. Either way the result is ``[0, budget_s]``: a clock jump can neither
    extend a reservation nor make it live past its stored bound. A missing, malformed,
    non-finite or non-positive row reads as no reservation (today's behavior) and never raises.
    """
    if not isinstance(reservation, dict):
        return 0.0
    budget = _finite(reservation.get("budget_s"))
    started_wall = _finite(reservation.get("started_wall"))
    started_steady = _finite(reservation.get("started_steady"))
    if budget is None or budget <= 0.0 \
            or started_wall is None or started_steady is None:
        return 0.0
    wall = _finite(time.time() if now_wall is None else now_wall)
    steady = _finite(time.monotonic() if now_steady is None else now_steady)
    if wall is None or steady is None:
        return 0.0
    elapsed_steady = steady - started_steady
    if 0.0 <= elapsed_steady < _SAME_BOOT_LIMIT_S:
        elapsed = elapsed_steady
    else:
        elapsed = wall - started_wall
    return max(0.0, min(budget, budget - elapsed))
