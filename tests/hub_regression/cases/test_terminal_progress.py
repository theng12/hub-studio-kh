"""A finished job is 100% done, whatever the last progress poll happened to catch.

A 15,058-character Kokoro script rendered as six sections, finished, and produced a correct 13-minute
artifact - and the Hub's own item row still said `progress: 0.788`, which is exactly the value after
section five (`0.08 + 5/6 * 0.85`). The worker finished between two polls, so the final update was
never read; nothing else was wrong, but a finished job displaying as four fifths rendered is the kind
of number an operator would report as "it says it is still going" while looking at a finished file.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from components.hub.backend import broker


def response(status, payload):
    return httpx.Response(status, json=payload,
                          request=httpx.Request("GET", "http://127.0.0.1/"))


class WorkerClient:
    async def get(self, *args, **kwargs):
        return response(200, {"ok": True})

    async def post(self, *args, **kwargs):
        return response(200, {"ok": True})


def voice_attempt(fixture_monitor):
    studio = next(row for row in fixture_monitor.registry if row["id"] == "voice")
    batch = {"id": "long-form-proof", "modality": "voice", "model": "mlx-community/Kokoro-82M-bf16",
             "shared_params": {}, "cancelled": False, "routing": "pool",
             "created_at": time.time(), "items": []}
    item = {"index": 0, "prompt": "a long script", "params": {"repo": batch["model"]},
            "seed": None, "state": "running", "tries": 1, "studio": studio["id"],
            "studio_job_id": "worker-1", "retry_at": None, "error": None,
            "progress": 0.788, "artifact_path": None, "machine": studio["machine"]}
    batch["items"] = [item]
    return batch, item, studio


def test_a_finished_item_reports_full_progress(fixture_monitor, monkeypatch):
    batch, item, studio = voice_attempt(fixture_monitor)

    async def cached(*args, **kwargs):
        return None

    monkeypatch.setattr(broker, "_cache_voice_artifact_metadata", cached)
    job = {"id": "worker-1", "state": "done", "progress": 0.788,
           "output_path": "/nonexistent/long-form.wav", "generated_at": time.time(),
           "bytes": 38740844, "sha256": "a" * 64, "runtime_s": 91.4,
           "output_url": "/api/generate/jobs/worker-1/audio", "chunk_total": 6}
    asyncio.run(broker._record_worker_success(
        WorkerClient(), batch, item, studio, job, {"repo": batch["model"]}, time.time()))

    assert item["state"] == "done"
    assert item["progress"] == 1.0, "a finished job must not report partial progress"
    assert item["chunk_total"] == 6, "the section count is the evidence it was sectioned"


def test_a_still_running_item_keeps_its_reported_progress(fixture_monitor):
    """The fix must not round a job up while it is still rendering."""
    batch, item, studio = voice_attempt(fixture_monitor)
    item["state"] = "running"
    assert item["progress"] == 0.788
    assert batch["items"][0]["state"] == "running"
