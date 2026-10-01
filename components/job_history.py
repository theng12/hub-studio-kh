"""How long a worker keeps its finished-job records (`jobs.json`): 90 days, then they are dropped.

Each worker rewrites its whole `jobs.json` on every update, so an unbounded history is also a growing
write cost. Output files have their own, shorter retention; this only bounds the records. A job that
is not finished is never dropped, and a record with no usable time is kept.
"""
from __future__ import annotations

import time

RETENTION_DAYS = 90
TERMINAL = {"done", "error", "cancelled"}


def expired(job: object, now: float | None = None, days: int = RETENTION_DAYS) -> bool:
    """True for a finished job record whose last timestamp is older than `days`."""
    if not isinstance(job, dict) or job.get("state") not in TERMINAL:
        return False
    stamp = job.get("finished_at") or job.get("created_at")
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
        return False
    return float(stamp) < (time.time() if now is None else now) - days * 86400
