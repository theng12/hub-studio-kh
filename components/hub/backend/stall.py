"""A worker that has gone quiet while still holding a job.

**Why this exists, and why it is not a timeout.** The owner asked the question that separated the
two:

    "if we raise it too high for, say subtitle and it failed, could we find out or we have to wait
     2 hours to findout?"

A deadline cannot answer that. Shortening one does not detect a hang — it only kills jobs that may
have been working, which *manufactures* failures. A stall check notices **silence**, in minutes,
no matter how long the ceiling is. So the two settings stop fighting each other: the deadline can be
generous about honest work while this stays impatient about dishonest silence.

**The signal already existed.** `monitor.py` records `activity_received_at` per studio — the moment
the Hub last received that studio's activity snapshot — and `backend/activity.py` validates those
snapshots (`kh-studio.activity.v1`). Nothing new is collected here; this module only decides what a
stale receipt *means* when a job is in flight.

**What it must never do is kill the job.** A stall becomes **visible**. Killing a merely-quiet job
would lose work that a long deadline exists to protect, so the caller is expected to surface this,
not to act on it destructively.
"""

from __future__ import annotations

# The job states in which a stall is meaningful: the worker has accepted the work and has not
# finished it. `queued` is excluded deliberately — nothing is running yet, so silence is expected —
# and every terminal state is excluded because there is nothing left to wait for.
ACTIVE_STATES = frozenset({"running", "cancel_requested"})

# How long a studio may go without reporting while holding an active job before that counts as a
# stall. **This must exceed the workers' own report interval with room to spare**, or a healthy
# worker is flagged between two snapshots; it is deliberately far larger than any plausible
# interval rather than tuned to the edge, because a false "stalled" on a working job is worse than
# a late one. Read the interval from the workers before lowering it.
STALL_AFTER_S = 120.0


def stall_seconds(state: object, activity_received_at: object, now: float,
                  threshold_s: float = STALL_AFTER_S) -> float | None:
    """How long this job's studio has been silent, or ``None`` when it is not stalled.

    Returns ``0.0``-or-more only when the job is active *and* the silence exceeds the threshold, so
    a caller can use truthiness or compare directly without a second condition.
    """
    if str(state or "") not in ACTIVE_STATES:
        return None
    try:
        received = float(activity_received_at)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        # A studio that has never reported has no receipt to be stale. Absence is not a stall —
        # treating it as one would flag every worker during its first polls after a restart.
        return None
    if received <= 0:
        return None
    silent_for = float(now) - received
    # Strictly past the threshold: exactly *at* it is not yet "longer than", and padding the edge
    # by a second in the safe direction costs nothing when the threshold is minutes.
    if silent_for <= float(threshold_s):
        return None
    return silent_for


def is_stalled(state: object, activity_received_at: object, now: float,
               threshold_s: float = STALL_AFTER_S) -> bool:
    """Whether this job's studio has gone quiet for longer than the threshold."""
    return stall_seconds(state, activity_received_at, now, threshold_s) is not None
