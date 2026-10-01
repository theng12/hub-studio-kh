"""A cancel the worker confirms is `cancelled`, not `uncertain` (audit 2026-10-01).

Accepted image/music/video items used to be marked `uncertain` the moment a cancel reached them,
which fenced the Mac for up to 30 minutes and made the Gateway file an unknown-outcome hold. The
worker's DELETE answer and its own terminal state say what happened, exactly as for Voice.
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


class Client:
    """POST accepts; DELETE is configurable; GET walks a script of worker states."""

    def __init__(self, modality, states, *, delete_ok=True):
        self.modality = modality
        self.states = list(states)
        self.delete_ok = delete_ok
        self.deletes = 0
        self.batch = None

    def job(self, studio, state, **extra):
        return {"id": "worker-1", "state": state, "worker_id": studio["id"],
                "machine_id": studio["machine"], "model_revision": "r" * 40,
                "runtime_revision": "a" * 64,
                **({"params": {"repo": "fixture/model", "prompt": "p", "seed": -1}}
                   if self.modality == "music" else {}), **extra}

    async def post(self, *args, **kwargs):
        studio = self.studio
        self.batch["cancelled"] = True  # the owner cancels right after acceptance
        return response(200, {"job": self.job(studio, "queued")})

    async def get(self, *args, **kwargs):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        extra = {"error": "cancelled", "error_code": "CANCELLED"} if state == "cancelled" else {}
        return response(200, {"job": self.job(self.studio, state, **extra)})

    async def delete(self, *args, **kwargs):
        self.deletes += 1
        if not self.delete_ok:
            raise httpx.ConnectError("worker unreachable")
        return response(200, {"ok": True})


@pytest.fixture
def quiet(monkeypatch):
    async def noop(*args):
        return None

    async def unresolved(*args):
        return False

    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker, "_maybe_finish", noop)
    monkeypatch.setattr(broker, "_recover_worker_job", unresolved)
    monkeypatch.setattr(broker, "POLL_S", 0)


def attempt(fixture_monitor, modality):
    studio = next(row for row in fixture_monitor.registry if row["id"] == modality)
    batch = {"id": f"cancel-{modality}", "modality": modality, "model": "fixture/model",
             "shared_params": {}, "cancelled": False, "routing": "pool",
             "created_at": time.time(), "items": []}
    item = {"index": 0, "prompt": "p", "params": {}, "seed": -1, "state": "running", "tries": 1,
            "studio": studio["id"], "studio_job_id": None, "retry_at": None, "error": None}
    batch["items"] = [item]
    broker.batches[batch["id"]] = batch
    broker._busy.add(studio["id"])
    return batch, item, studio


@pytest.mark.parametrize("modality", ("image", "music"))
def test_a_confirmed_stop_ends_cancelled_and_frees_the_mac(fixture_monitor, quiet, modality):
    batch, item, studio = attempt(fixture_monitor, modality)
    client = Client(modality, ["running", "cancelled"])
    client.studio, client.batch = studio, batch
    asyncio.run(broker._run_item(client, batch, item, studio))
    assert client.deletes == 1
    assert item["state"] == "cancelled"
    assert item["error_code"] == "CANCELLED"
    assert studio["machine"] not in broker.busy_machines()
    assert broker.batch_summary(batch)["cancelled_items"] == 1


@pytest.mark.parametrize("modality", ("image", "music"))
def test_the_mac_stays_reserved_while_the_worker_winds_down(fixture_monitor, quiet, modality,
                                                            monkeypatch):
    """An image render cannot be interrupted mid-step: `cancel_requested` until the worker says."""
    batch, item, studio = attempt(fixture_monitor, modality)
    client = Client(modality, ["running"])
    client.studio, client.batch = studio, batch
    seen = []

    original = client.get

    async def get(*args, **kwargs):
        seen.append(item["state"])
        if len(seen) == 3:
            client.states = ["cancelled"]
        return await original(*args, **kwargs)

    client.get = get
    asyncio.run(broker._run_item(client, batch, item, studio))
    assert "cancel_requested" in seen
    assert item["state"] == "cancelled"
    assert studio["machine"] not in broker.busy_machines()


def test_a_stop_signal_that_never_lands_stays_uncertain(fixture_monitor, quiet):
    batch, item, studio = attempt(fixture_monitor, "music")
    client = Client("music", ["running"], delete_ok=False)
    client.studio, client.batch = studio, batch
    asyncio.run(broker._run_item(client, batch, item, studio))
    assert item["state"] == "uncertain"
    assert studio["machine"] in broker.busy_machines()


def test_a_worker_that_never_confirms_becomes_uncertain_after_the_window(
        fixture_monitor, quiet, monkeypatch):
    batch, item, studio = attempt(fixture_monitor, "image")
    monkeypatch.setattr(broker, "CANCEL_CONFIRM_S", 0.0)
    client = Client("image", ["running"])
    client.studio, client.batch = studio, batch
    original = client.get

    async def get(*args, **kwargs):
        await asyncio.sleep(0.01)
        return await original(*args, **kwargs)

    client.get = get
    asyncio.run(broker._run_item(client, batch, item, studio))
    assert item["state"] == "uncertain"
    assert "did not confirm" in item["error"]
