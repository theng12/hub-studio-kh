"""HTTP registration for the standalone Hub's generation statistics."""

from __future__ import annotations

import time

from fastapi import Query

from .backend import activity, ledger


def register_routes(app, monitor) -> None:
    """Register the authenticated Hub statistics endpoint on ``app``.

    Authentication remains the standalone app's existing middleware concern;
    this small module only composes the retained ledger and activity views.
    """

    @app.get("/api/hub/stats")
    def hub_stats(
        hours: int | None = Query(None, ge=1, description="limit to last N hours"),
        source: str = Query(
            "all",
            pattern="^(all|job|direct)$",
            description="all | job (Hub-dispatched) | direct (in-studio)",
        ),
        modality: str | None = Query(None, description="filter to one operation type"),
        machine: str | None = Query(None, description="filter to one machine"),
        activity_since: float | None = Query(
            None, ge=0,
            description="count fleet activity (done/failed/median) only from this epoch second; "
                        "the Gateway's 'Reset counters' baseline. The job history is untouched.",
        ),
    ):
        """Generation analytics and the matching live fleet activity view."""
        since = time.time() - hours * 3600 if hours else None
        bucket = 300 if hours == 1 else (3600 if hours == 24 else 86400)
        result = ledger.stats(
            since_s=since, source=source, op=modality, machine=machine,
        )
        result["timeline"] = ledger.timeline(
            since, bucket, source=source, op=modality, machine=machine,
        )
        # One producer-side source for "which queues contribute live activity" - the same call
        # the monitor's poll cycle makes, so the route and the durable ledger cannot disagree
        # about which queues exist. This used to be a second copy, and both omitted Chat.
        # The per-machine counters' window: the later of `hours` and the caller's baseline.
        counted_since = max((v for v in (since, activity_since) if v is not None), default=None)
        result["fleet_activity"] = activity.fleet_snapshot(
            monitor.registry, monitor.status, activity.live_batches(), since_s=counted_since,
        )
        result["filters"] = {
            "source": source,
            "modality": modality,
            "machine": machine,
            "hours": hours,
        }
        return result
