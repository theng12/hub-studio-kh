"""Accepted Voice jobs keep one durable worker identity across Hub failure."""
from __future__ import annotations

import asyncio
import copy
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from components.hub import standalone
from components.hub.backend import auth, broker, ledger


class StopBeforePoll(BaseException):
    pass


class StopDispatch(BaseException):
    pass


class WorkerClient:
    def __init__(self, accepted: dict | None = None, polled: dict | None = None):
        self.accepted = accepted
        self.polled = polled
        self.posts = 0
        self.gets = 0

    async def post(self, *args, **kwargs):
        self.posts += 1
        return response(200, {"job": self.accepted})

    async def get(self, *args, **kwargs):
        self.gets += 1
        return response(200, {"job": self.polled})


def response(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(
        status, json=payload, request=httpx.Request("GET", "http://127.0.0.1/")
    )


def voice_attempt(fixture_monitor, *, accepted_state: str = "queued"):
    studio = next(row for row in fixture_monitor.registry if row["id"] == "voice")
    batch = {
        "id": "voice-durability-proof",
        "client_request_id": "voice-durability-request",
        "modality": "voice",
        "model": "fixture/voice",
        "shared_params": {},
        "cancelled": False,
        "routing": "pool",
        "created_at": time.time(),
        "items": [],
    }
    item = {
        "index": 0,
        "prompt": "proof",
        "params": {},
        "seed": None,
        "state": "running",
        "tries": 1,
        "studio": studio["id"],
        "studio_job_id": None,
        "retry_at": None,
        "error": None,
    }
    batch["items"] = [item]
    broker.batches[batch["id"]] = batch
    broker._busy.add(studio["id"])
    accepted = {
        "id": "voice-worker-1",
        "state": accepted_state,
        "worker_id": studio["id"],
        "machine_id": studio["machine"],
        "model_revision": "r" * 40,
        "runtime_revision": "a" * 64,
    }
    if accepted_state == "running":
        accepted["started_at"] = time.time()
    return batch, item, studio, accepted


@pytest.mark.parametrize("accepted_state", ("queued", "running"))
def test_voice_acceptance_is_persisted_before_first_poll(
    fixture_monitor, monkeypatch, accepted_state
):
    batch, item, studio, accepted = voice_attempt(
        fixture_monitor, accepted_state=accepted_state
    )
    writes: list[dict] = []

    async def stop_before_poll(*args):
        raise StopBeforePoll()

    async def noop(*args):
        return None

    monkeypatch.setattr(
        ledger, "save_batch", lambda value: writes.append(copy.deepcopy(value))
    )
    monkeypatch.setattr(broker.asyncio, "sleep", stop_before_poll)
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker, "_maybe_finish", noop)
    monkeypatch.setattr(broker.ledger, "record_activity_ownership", lambda **kwargs: None)

    with pytest.raises(StopBeforePoll):
        asyncio.run(broker._run_item(WorkerClient(accepted), batch, item, studio))

    persisted = [row["items"][0].get("studio_job_id") for row in writes]
    assert item["studio_job_id"] == accepted["id"]
    assert accepted["id"] in persisted


@pytest.mark.parametrize("accepted_state", ("queued", "running"))
def test_voice_acceptance_persistence_failure_never_requeues(
    fixture_monitor, monkeypatch, accepted_state
):
    batch, item, studio, accepted = voice_attempt(
        fixture_monitor, accepted_state=accepted_state
    )
    client = WorkerClient(accepted)

    def fail_accepted_write(value):
        if value["items"][0].get("studio_job_id"):
            raise OSError("fixture: acceptance receipt disk full")

    async def noop(*args):
        return None

    monkeypatch.setattr(ledger, "save_batch", fail_accepted_write)
    monkeypatch.setattr(broker.ledger, "record_activity_ownership", lambda **kwargs: None)
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker, "_maybe_finish", noop)

    asyncio.run(broker._run_item(client, batch, item, studio))

    assert client.posts == 1
    assert item["studio_job_id"] == accepted["id"]
    assert item["state"] == "uncertain"
    assert item["retry_at"] is None
    assert studio["machine"] in broker.busy_machines()


def test_voice_dispatch_is_durable_before_worker_task_starts(
    fixture_monitor, monkeypatch
):
    batch, item, studio, _ = voice_attempt(fixture_monitor)
    item.update(state="queued", tries=0, studio=None)
    observed: list[dict] = []
    created: list[object] = []

    async def catalog_entry(*args):
        return {"repo": batch["model"], "cache": {"state": "cached"}}

    async def stop_after_failed_fence(*args):
        raise StopDispatch()

    def fail_fence(value):
        observed.append(copy.deepcopy(value))
        raise OSError("fixture: pre-dispatch receipt disk full")

    monkeypatch.setattr(broker, "_eligible_studios", lambda *args: [studio])
    monkeypatch.setattr(broker, "_catalog_entry", catalog_entry)
    monkeypatch.setattr(broker, "_admission_requirements", lambda *args: None)
    monkeypatch.setattr(broker, "busy_machines", lambda: set())
    monkeypatch.setattr(broker.peers, "dispatch_generation", lambda value: "generation")
    monkeypatch.setattr(broker.peers, "dispatch_ready", lambda *args: True)
    monkeypatch.setattr(ledger, "save_batch", fail_fence)
    monkeypatch.setattr(broker.asyncio, "sleep", stop_after_failed_fence)
    monkeypatch.setattr(
        broker.asyncio, "create_task", lambda coroutine: created.append(coroutine)
    )

    with pytest.raises(StopDispatch):
        asyncio.run(broker._dispatch_loop())

    assert observed[0]["items"][0]["state"] == "running"
    assert observed[0]["items"][0]["studio"] == studio["id"]
    assert observed[0]["items"][0]["tries"] == 1
    assert created == []
    assert item["state"] == "queued"
    assert item["studio"] is None
    assert item["tries"] == 0


def test_restart_reconciles_exact_voice_job_without_another_post(
    fixture_monitor, monkeypatch
):
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    item.update(
        studio_job_id=accepted["id"],
        machine=studio["machine"],
        worker_id=studio["id"],
        model_revision=accepted["model_revision"],
        runtime_revision=accepted["runtime_revision"],
    )
    ledger.save_batch(batch)
    broker.batches.clear()
    broker._busy.clear()
    assert broker.restore_batches() == 1
    restored = broker.batches[batch["id"]]["items"][0]
    assert restored["state"] == "uncertain"
    assert restored["studio_job_id"] == accepted["id"]

    adopted: list[str] = []

    async def adopt(client, received_batch, received_item, received_studio,
                    job, body, started_at, *, recovery=False):
        assert recovery is True
        assert received_item["studio_job_id"] == job["id"]
        adopted.append(job["id"])
        received_item["state"] = "done"

    async def noop(*args):
        return None

    finished = {**accepted, "state": "done", "params": {
        "repo": batch["model"],
        "text": item["prompt"],
        "client_request_id": f"studiohub:{batch['id']}:{item['index']}",
    }}
    client = WorkerClient(polled=finished)
    monkeypatch.setattr(broker, "_record_worker_success", adopt)
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker, "_maybe_finish", noop)

    result = asyncio.run(
        broker.reconcile_voice_item(batch["id"], item["index"], client=client)
    )

    assert result["reconciled"] is True
    assert adopted == [accepted["id"]]
    assert client.posts == 0
    assert client.gets == 1


@pytest.mark.parametrize("state", ("running", "cancel_requested"))
def test_reconcile_does_not_take_ownership_from_active_voice_poll(
    fixture_monitor, state
):
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    item.update(
        state=state,
        studio_job_id=accepted["id"],
        machine=studio["machine"],
        worker_id=studio["id"],
    )
    client = WorkerClient(polled={**accepted, "state": state})

    with pytest.raises(ValueError, match="requires an uncertain job"):
        asyncio.run(
            broker.reconcile_voice_item(batch["id"], item["index"], client=client)
        )

    assert item["state"] == state
    assert "recovery" not in item
    assert broker._voice_recovery_owns(item) is False
    assert studio["machine"] in broker.busy_machines()
    assert client.posts == 0
    assert client.gets == 0


def test_contradictory_voice_terminal_receipt_stays_safe_and_uncertain(
    fixture_monitor,
):
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    item.update(
        state="uncertain",
        studio_job_id=accepted["id"],
        machine=studio["machine"],
        worker_id=studio["id"],
    )
    leaked_error = "Authorization: Bearer fixture-secret /Users/example/model.bin"
    contradictory = {
        **accepted,
        "state": "done",
        "error": leaked_error,
        "params": {
            "repo": batch["model"],
            "text": item["prompt"],
            "client_request_id": f"studiohub:{batch['id']}:{item['index']}",
        },
    }
    client = WorkerClient(polled=contradictory)

    result = asyncio.run(
        broker.reconcile_voice_item(batch["id"], item["index"], client=client)
    )

    serialized = str(result)
    assert result["reconciled"] is False
    assert item["state"] == "uncertain"
    assert item["recovery"]["phase"] == "manual_action_required"
    assert "fixture-secret" not in serialized
    assert "/Users/example" not in serialized
    assert studio["machine"] in broker.busy_machines()
    assert client.posts == 0
    assert client.gets == 1


def test_authenticated_reconcile_route_dispatches_voice_without_post(
    fixture_monitor, monkeypatch
):
    batch, item, _, accepted = voice_attempt(fixture_monitor)
    item.update(state="uncertain", studio_job_id=accepted["id"])
    reply = {
        "batch_id": batch["id"],
        "clientRequestId": batch["client_request_id"],
        "reconciled": False,
        "item": {"state": "uncertain"},
    }
    reconcile = AsyncMock(return_value=reply)
    monkeypatch.setattr(broker, "reconcile_voice_item", reconcile)
    app = standalone.create_app(start_background=False)
    path = f"/api/hub/jobs/{batch['id']}/items/{item['index']}/reconcile"
    token = auth.TOKEN_FILE.read_text().strip()

    with TestClient(app) as client:
        assert client.post(path).status_code == 401
        response = client.post(
            path, headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200
    assert response.json() == reply
    reconcile.assert_awaited_once_with(
        batch["id"], item["index"], client=app.state.monitor._client
    )
