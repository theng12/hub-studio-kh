"""HTTP registration for the standalone Hub's generation statistics."""

from __future__ import annotations

import time

from fastapi import Query

from .backend import activity, broker, ledger, transcription_jobs


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
        activity_batches = dict(broker.batches)
        activity_batches.update({
            f"transcription:{batch_id}": {
                "model": batch.get("model"),
                "operation": "transcription",
                "items": batch.get("items") or [],
            }
            for batch_id, batch in transcription_jobs.batches.items()
        })
        result["fleet_activity"] = activity.fleet_snapshot(
            monitor.registry, monitor.status, activity_batches, since_s=since,
        )
        result["filters"] = {
            "source": source,
            "modality": modality,
            "machine": machine,
            "hours": hours,
        }
        return result
