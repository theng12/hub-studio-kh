"""A shop Mac that hands a job back is moved at once, never waited on (workstation mode, 2026-10-03).

A workstation stops a running job only when macOS reports critical memory pressure for 20 seconds;
its peer surface then answers the job's status as ``error`` / ``WORKSTATION_YIELDED``. The controller
must not spend one of the item's tries, must not wait out the 120 s recovery window, and must not mark
the Mac failed: another free Mac of this group takes the item now, or - when there is none - the item
goes straight back to the Gateway, typed, so the Gateway moves it to another group.
"""
from __future__ import annotations

import asyncio
import time

import httpx

from components.hub.backend import broker


def _studio(machine: str) -> dict:
    return {"id": f"image@{machine}", "modality": "image", "machine": machine,
            "host": "10.0.0.9", "port": 51201, "hub_port": 51201, "hub_token": "fleet-token",
            "local": False, "runtime_identity": {"id": f"fleet:{machine}", "compatible": True}}


class YieldingNode:
    """Accepts the job, reports it running once, then reports it handed back."""

    def __init__(self):
        self.polls = 0

    async def post(self, url, **kwargs):
        job = {"id": "job-1", "state": "queued", "worker_id": "w", "machine_id": "local"}
        return httpx.Response(200, json={"job": job}, request=httpx.Request("POST", url))

    async def get(self, url, **kwargs):
        self.polls += 1
        job = {"id": "job-1", "state": "running", "worker_id": "w", "machine_id": "local"}
        if self.polls > 1:
            job.update(state="error", error_code="WORKSTATION_YIELDED",
                       error="WORKSTATION_YIELDED: this Mac stepped aside (macOS reported critical "
                             "memory pressure for 20 seconds); nothing was kept")
        return httpx.Response(200, json={"job": job}, request=httpx.Request("GET", url))


def _attempt(monkeypatch) -> tuple[dict, dict, dict]:
    monkeypatch.setattr(broker, "POLL_S", 0.0)

    async def noop(*args, **kwargs):
        return None
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker, "_maybe_finish", noop)
    studio = _studio("shop-1")
    batch = {"id": "workstation-yield", "modality": "image", "model": "fixture/model",
             "shared_params": {}, "cancelled": False, "routing": "pool",
             "created_at": time.time(), "items": []}
    item = {"index": 0, "prompt": "proof", "params": {}, "seed": -1, "state": "running",
            "tries": 1, "studio": studio["id"], "studio_job_id": None, "retry_at": None, "error": None}
    batch["items"] = [item]
    broker.batches[batch["id"]] = batch
    broker._busy.add(studio["id"])
    return batch, item, studio


def test_a_yield_with_no_other_free_mac_goes_straight_back_to_the_gateway(fixture_monitor, monkeypatch):
    batch, item, studio = _attempt(monkeypatch)
    monkeypatch.setattr(broker, "_eligible_studios", lambda *args, **kwargs: [])
    started = time.monotonic()
    asyncio.run(broker._run_item(YieldingNode(), batch, item, studio))
    assert time.monotonic() - started < 5, "no recovery window"
    assert item["state"] == "error"
    assert item["error_code"] == "WORKSTATION_YIELDED"
    assert item["tries"] == 0, "a hand-back is not an attempt"
    assert not broker._machine_blocked("shop-1"), "the Mac did nothing wrong"


def test_a_yield_moves_to_another_free_mac_of_the_group(fixture_monitor, monkeypatch):
    batch, item, studio = _attempt(monkeypatch)
    monkeypatch.setattr(broker, "_eligible_studios", lambda *args, **kwargs: [_studio("shop-2")])
    asyncio.run(broker._run_item(YieldingNode(), batch, item, studio))
    assert item["state"] == "queued", item.get("error")
    assert item["tries"] == 0
    assert item["avoid_machines"]["shop-1"] > time.time(), "the Mac that stepped aside is skipped"
    assert broker._item_allows_studio(item, _studio("shop-2"), time.time())
