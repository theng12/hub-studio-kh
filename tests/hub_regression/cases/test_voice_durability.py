"""Accepted Voice jobs keep one durable worker identity across Hub failure."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
import time
import wave
from datetime import UTC, datetime, timedelta

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
    def __init__(self, accepted: object = None, polled: object = None,
                 cancel_status: int = 200, artifact: bytes | None = None,
                 accepted_responses: list[object] | None = None,
                 polled_responses: list[object] | None = None):
        self.accepted = accepted
        self.polled = polled
        self.cancel_status = cancel_status
        self.artifact = artifact
        self.accepted_responses = list(accepted_responses or [])
        self.polled_responses = list(polled_responses or [])
        self.posts = 0
        self.gets = 0
        self.artifact_gets = 0
        self.deletes = 0
        self.created_jobs: dict[str, dict] = {}

    async def post(self, *args, **kwargs):
        self.posts += 1
        body = kwargs.get("json")
        if body is None and isinstance(kwargs.get("data"), dict):
            try:
                body = json.loads(kwargs["data"].get("request_json") or "{}")
            except (TypeError, ValueError):
                body = {}
        body = dict(body or {})
        request_id = str(body.get("client_request_id") or f"post-{self.posts}")
        original = self.created_jobs.get(request_id)
        if original is not None and original != body:
            return response(409, {
                "detail": "client_request_id was already used for a different request"
            })
        self.created_jobs.setdefault(request_id, body)
        accepted = (
            self.accepted_responses.pop(0)
            if self.accepted_responses else self.accepted
        )
        return response(200, {"job": accepted})

    async def get(self, url, *args, **kwargs):
        if str(url).endswith("/audio"):
            self.artifact_gets += 1
            return httpx.Response(
                200,
                content=self.artifact if self.artifact is not None else b"not-wave",
                headers={"Content-Type": "audio/wav"},
                request=httpx.Request("GET", str(url)),
            )
        self.gets += 1
        polled = (
            self.polled_responses.pop(0)
            if self.polled_responses else self.polled
        )
        if isinstance(polled, BaseException):
            raise polled
        if isinstance(polled, httpx.Response):
            return polled
        return response(200, {"job": polled})

    async def delete(self, *args, **kwargs):
        self.deletes += 1
        return response(self.cancel_status, {})


def response(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(
        status, json=payload, request=httpx.Request("GET", "http://127.0.0.1/")
    )


def invalid_json_response() -> httpx.Response:
    return httpx.Response(
        200,
        content=b"{not-json",
        headers={"Content-Type": "application/json"},
        request=httpx.Request("GET", "http://127.0.0.1/"),
    )


def wav_bytes() -> bytes:
    target = io.BytesIO()
    with wave.open(target, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0" * 1600)
    return target.getvalue()


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
        # Every real item carries the id it was submitted with; the fixture omitted it, so the
        # test asked the restored item for an id production always has.
        "client_request_id": f"studiohub:{batch['id']}:0",
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


def completed_voice_job(batch: dict, item: dict, accepted: dict) -> dict:
    audio = wav_bytes()
    return {
        **accepted,
        "state": "done",
        "params": {
            "repo": batch["model"],
            "text": item["prompt"],
            "client_request_id": f"studiohub:{batch['id']}:{item['index']}",
        },
        "output_url": f"/api/generate/jobs/{accepted['id']}/audio",
        "output_path": f"/tmp/{accepted['id']}.wav",
        "media_type": "audio/wav",
        "bytes": len(audio),
        "sha256": hashlib.sha256(audio).hexdigest(),
        "generated_at": time.time(),
        "finished_at": time.time(),
        "runtime_s": 0.1,
    }


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


def test_failed_voice_cancel_after_acceptance_waits_for_worker_truth(
    fixture_monitor, monkeypatch
):
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    client = WorkerClient(accepted, cancel_status=500)
    original_post = client.post

    async def cancel_after_acceptance(*args, **kwargs):
        reply = await original_post(*args, **kwargs)
        batch["cancelled"] = True
        return reply

    async def noop(*args):
        return None

    monkeypatch.setattr(client, "post", cancel_after_acceptance)
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker, "_maybe_finish", noop)
    monkeypatch.setattr(broker.ledger, "record_activity_ownership", lambda **kwargs: None)

    asyncio.run(broker._run_item(client, batch, item, studio))

    assert client.posts == 1
    assert client.deletes == 1
    assert item["state"] == "uncertain"
    assert item["studio_job_id"] == accepted["id"]
    assert item["error_code"] == "VOICE_CANCEL_OUTCOME_UNCERTAIN"
    assert item["retry_at"] is None
    assert studio["machine"] in broker.busy_machines()


def test_confirmed_voice_cancel_preserves_worker_terminal_error(
    fixture_monitor, monkeypatch
):
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    worker_error = {
        **accepted,
        "state": "error",
        "error": "fixture generation failed",
        "error_code": "FIXTURE_FAILURE",
        "params": {
            "repo": batch["model"],
            "text": item["prompt"],
            "client_request_id": f"studiohub:{batch['id']}:{item['index']}",
        },
    }
    client = WorkerClient(accepted, polled=worker_error)
    original_post = client.post

    async def cancel_after_acceptance(*args, **kwargs):
        reply = await original_post(*args, **kwargs)
        batch["cancelled"] = True
        return reply

    async def continue_to_poll(*args):
        return None

    async def noop(*args):
        return None

    monkeypatch.setattr(client, "post", cancel_after_acceptance)
    monkeypatch.setattr(broker.asyncio, "sleep", continue_to_poll)
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker, "_maybe_finish", noop)
    monkeypatch.setattr(broker.ledger, "record_activity_ownership", lambda **kwargs: None)

    asyncio.run(broker._run_item(client, batch, item, studio))

    assert client.posts == 1
    assert client.deletes == 1
    assert client.gets == 1
    assert item["state"] == "error"
    assert item["error"] == worker_error["error"]
    assert item["error_code"] == worker_error["error_code"]


def test_voice_cancel_during_polling_waits_for_worker_truth(
    fixture_monitor, monkeypatch
):
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    client = WorkerClient(accepted, cancel_status=500)

    async def cancel_while_waiting(*args):
        batch["cancelled"] = True

    async def noop(*args):
        return None

    monkeypatch.setattr(broker.asyncio, "sleep", cancel_while_waiting)
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker, "_maybe_finish", noop)
    monkeypatch.setattr(broker.ledger, "record_activity_ownership", lambda **kwargs: None)

    asyncio.run(broker._run_item(client, batch, item, studio))

    assert client.posts == 1
    assert client.gets == 0
    assert client.deletes == 1
    assert item["state"] == "uncertain"
    assert item["studio_job_id"] == accepted["id"]
    assert studio["machine"] in broker.busy_machines()


def test_voice_cancel_racing_failed_poll_waits_for_worker_truth(
    fixture_monitor, monkeypatch
):
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    client = WorkerClient(accepted)

    async def continue_to_poll(*args):
        return None

    async def fail_while_cancelled(*args, **kwargs):
        client.gets += 1
        batch["cancelled"] = True
        raise httpx.ConnectError("fixture: worker unavailable")

    async def noop(*args):
        return None

    monkeypatch.setattr(broker.asyncio, "sleep", continue_to_poll)
    monkeypatch.setattr(client, "get", fail_while_cancelled)
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker, "_maybe_finish", noop)
    monkeypatch.setattr(broker.ledger, "record_activity_ownership", lambda **kwargs: None)

    asyncio.run(broker._run_item(client, batch, item, studio))

    assert client.posts == 1
    assert client.gets == 1
    assert item["state"] == "uncertain"
    assert item["studio_job_id"] == accepted["id"]
    assert studio["machine"] in broker.busy_machines()


@pytest.mark.parametrize("has_receipt", (True, False))
def test_restart_keeps_expired_dispatched_voice_job_uncertain_and_fenced(
    fixture_monitor, has_receipt
):
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    batch["genstudio_execution"] = {
        "lease_expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    }
    item.update(
        machine=studio["machine"],
        worker_id=studio["id"],
    )
    if has_receipt:
        item["studio_job_id"] = accepted["id"]
    ledger.save_batch(batch)
    broker.batches.clear()
    broker._busy.clear()

    assert broker.restore_batches() == 1

    restored = broker.batches[batch["id"]]["items"][0]
    assert restored["state"] == "uncertain"
    assert restored["studio_job_id"] == (accepted["id"] if has_receipt else None)
    assert restored["error_code"] == "VOICE_CANCEL_OUTCOME_UNCERTAIN"
    assert studio["machine"] in broker.busy_machines()
    if not has_receipt:
        with pytest.raises(ValueError, match="identity is unavailable"):
            broker.voice_recovery_item(batch["id"], item["index"])
        assert restored["recovery"]["phase"] == "manual_action_required"


def test_worker_done_receipt_wins_lease_expiry_race(
    fixture_monitor, monkeypatch
):
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    batch["genstudio_execution"] = {
        "lease_expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    }
    done = completed_voice_job(batch, item, accepted)
    client = WorkerClient(accepted, polled=done, artifact=wav_bytes())

    async def continue_to_poll(*args):
        return None

    original_get = client.get

    async def finish_as_lease_expires(*args, **kwargs):
        batch["genstudio_execution"]["lease_expires_at"] = (
            datetime.now(UTC) - timedelta(seconds=1)
        ).isoformat()
        return await original_get(*args, **kwargs)

    async def noop(*args):
        return None

    monkeypatch.setattr(broker.asyncio, "sleep", continue_to_poll)
    monkeypatch.setattr(client, "get", finish_as_lease_expires)
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker, "_maybe_finish", noop)
    monkeypatch.setattr(broker.ledger, "record_activity_ownership", lambda **kwargs: None)

    asyncio.run(broker._run_item(client, batch, item, studio))

    assert client.posts == 1
    assert client.gets == 1
    assert item["state"] == "done"
    assert item["studio_job_id"] == accepted["id"]


def test_cancelled_voice_batch_keeps_active_worker_fenced_during_reconcile(
    fixture_monitor, monkeypatch
):
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    batch["cancelled"] = True
    item.update(
        state="uncertain",
        studio_job_id=accepted["id"],
        machine=studio["machine"],
        worker_id=studio["id"],
    )
    broker._busy.clear()
    active = {
        **accepted,
        "state": "running",
        "params": {
            "client_request_id": f"studiohub:{batch['id']}:{item['index']}",
        },
    }
    client = WorkerClient(polled=active)
    leaked_error = "Authorization: Bearer fixture-secret /Users/example/model.bin"

    async def fail_cancel(*args, **kwargs):
        client.deletes += 1
        raise httpx.ConnectError(leaked_error)

    monkeypatch.setattr(client, "delete", fail_cancel)

    result = asyncio.run(
        broker.reconcile_voice_item(batch["id"], item["index"], client=client)
    )

    serialized = str(result)
    assert result["reconciled"] is False
    assert item["state"] == "uncertain"
    # The operator cancelled the batch: the reconciler writes no recovery or terminal
    # state from the worker receipt, which belongs to the cancellation lifecycle.
    assert "recovery" not in item and item.get("error") is None
    assert studio["machine"] in broker.busy_machines()
    assert "fixture-secret" not in serialized
    assert "/Users/example" not in serialized
    assert client.posts == 0
    assert client.deletes == 1
    assert client.gets == 1


def test_cancelled_voice_batch_settles_nothing_from_the_worker_receipt(fixture_monitor):
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    batch["cancelled"] = True
    item.update(
        state="uncertain",
        studio_job_id=accepted["id"],
        machine=studio["machine"],
        worker_id=studio["id"],
    )
    broker._busy.clear()
    cancelled = {
        **accepted,
        "state": "cancelled",
        "error": "Cancelled by worker",
        "params": {
            "client_request_id": f"studiohub:{batch['id']}:{item['index']}",
        },
    }
    client = WorkerClient(polled=cancelled, cancel_status=500)

    result = asyncio.run(
        broker.reconcile_voice_item(batch["id"], item["index"], client=client)
    )

    assert result["reconciled"] is False
    assert item["state"] == "uncertain"
    assert item["studio_job_id"] == accepted["id"]
    assert item.get("recovery") is None
    assert studio["machine"] in broker.busy_machines()
    assert client.posts == 0
    assert client.deletes == 1
    assert client.gets == 1


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
    monkeypatch.setattr(broker, "_admission_requirements",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(broker, "busy_machines", lambda: set())
    monkeypatch.setattr(broker.peers, "dispatch_generation", lambda value: "generation")
    monkeypatch.setattr(broker.peers, "dispatch_ready", lambda *args: True)
    monkeypatch.setattr(ledger, "save_batch", fail_fence)
    monkeypatch.setattr(broker.asyncio, "sleep", stop_after_failed_fence)
    monkeypatch.setattr(
        broker.asyncio, "create_task", lambda coroutine: created.append(coroutine)
    )
    fixture_monitor.status[studio["id"]]["status"] = "up"

    with pytest.raises(StopDispatch):
        asyncio.run(broker._dispatch_loop())

    assert observed[0]["items"][0]["state"] == "running"
    assert observed[0]["items"][0]["studio"] == studio["id"]
    assert observed[0]["items"][0]["tries"] == 1
    assert created == []
    assert item["state"] == "queued"
    assert item["studio"] is None
    assert item["tries"] == 0


def test_restart_adopts_the_exact_finished_voice_job_without_another_post(
    fixture_monitor, monkeypatch
):
    """Owner, 2026-10-05: a Hub restart re-runs free work instead of fencing it, and the original
    (watched by its own request id) is adopted when it had finished - with no second POST."""
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    item.update(
        studio_job_id=accepted["id"],
        machine=studio["machine"],
        worker_id=studio["id"],
        model_revision=accepted["model_revision"],
        runtime_revision=accepted["runtime_revision"],
    )
    finished = completed_voice_job(batch, item, accepted)
    ledger.save_batch(batch)
    broker.batches.clear()
    broker._busy.clear()
    assert broker.restore_batches() == 1
    restored = broker.batches[batch["id"]]["items"][0]
    assert restored["state"] == "queued"
    # The restored item keeps the id it was submitted with, so the original is adopted by that id
    # and nothing is generated twice (0.6.242). The adopt callback below asserts the same id.
    assert restored["client_request_id"] == f"studiohub:{batch['id']}:0"
    assert studio["machine"] not in broker.busy_machines()

    adopted: list[str] = []

    async def adopt(client, received_batch, received_item, received_studio,
                    job, body, started_at, *, recovery=False, adoption_item=None):
        assert adoption_item is restored and received_item is not restored
        assert received_item["studio_job_id"] == job["id"]
        assert body["client_request_id"] == f"studiohub:{batch['id']}:0"
        adopted.append(job["id"])
        received_item["state"] = "done"

    async def noop(*args, **kwargs):
        return None

    client = WorkerClient(polled=finished)
    monkeypatch.setattr(broker, "_record_worker_success", adopt)
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker, "_maybe_finish", noop)

    async def watch():
        await broker._watch_restored_attempts(client)
        await asyncio.gather(*broker._abandoned_peer_cancels)

    asyncio.run(watch())

    assert adopted == [accepted["id"]]
    assert restored["state"] == "done"
    assert restored["client_request_id"] == f"studiohub:{batch['id']}:0", "the original's own id"
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


@pytest.mark.parametrize(
    "mutate",
    (
        lambda job: job.update(state="unknown"),
        lambda job: job.update(state=[]),
        lambda job: job.pop("output_url"),
        lambda job: job.update(id="different-worker-job"),
        lambda job: job["params"].update(client_request_id="different-request"),
        lambda job: job.update(model_revision="different-model-revision"),
    ),
    ids=(
        "unknown-state",
        "non-scalar-state",
        "missing-output-url",
        "changed-job-id",
        "changed-request-id",
        "changed-model-revision",
    ),
)
def test_live_voice_poll_rejects_malformed_receipt_and_persists_uncertainty(
    fixture_monitor, monkeypatch, mutate
):
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    finished = completed_voice_job(batch, item, accepted)
    mutate(finished)
    client = WorkerClient(accepted, polled=finished, artifact=wav_bytes())

    async def continue_to_poll(*args):
        return None

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(broker.asyncio, "sleep", continue_to_poll)
    monkeypatch.setattr(broker, "RECOVERY_WINDOW_S", 0)
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker.ledger, "record_activity_ownership", lambda **kwargs: None)

    asyncio.run(broker._run_item(client, batch, item, studio))

    persisted = ledger.load_batch(batch["id"])["items"][0]
    assert client.posts == 1
    assert len(client.created_jobs) == 1
    assert item["state"] == persisted["state"] == "uncertain"
    assert item["studio_job_id"] == accepted["id"]
    assert item.get("asset_id") is None
    assert broker.terminal_result(batch, item) is None
    assert item["retry_at"] is None


def test_remote_non_scalar_voice_state_is_fenced_without_regeneration(
    fixture_monitor, monkeypatch
):
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    finished = completed_voice_job(batch, item, accepted)
    finished["state"] = []
    client = WorkerClient(accepted, polled=finished, artifact=wav_bytes())

    async def continue_to_poll(*args):
        return None

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(
        broker, "studio_request",
        lambda _studio, path: (f"http://remote.invalid{path}", {}),
    )
    monkeypatch.setattr(broker.peers, "is_local_studio", lambda _studio: False)
    monkeypatch.setattr(broker.asyncio, "sleep", continue_to_poll)
    monkeypatch.setattr(broker, "RECOVERY_WINDOW_S", 0)
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker.ledger, "record_activity_ownership", lambda **kwargs: None)

    asyncio.run(broker._run_item(client, batch, item, studio))

    persisted = ledger.load_batch(batch["id"])["items"][0]
    assert client.posts == 1
    assert len(client.created_jobs) == 1
    assert item["state"] == persisted["state"] == "uncertain"
    assert item["studio_job_id"] == accepted["id"]
    assert item["studio"] == studio["id"]
    assert item["retry_at"] is None


@pytest.mark.parametrize("remote", (False, True), ids=("local", "remote"))
def test_invalid_json_voice_poll_is_fenced_without_repost(
    fixture_monitor, monkeypatch, remote
):
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    client = WorkerClient(
        accepted,
        polled_responses=[invalid_json_response()],
    )

    async def continue_to_poll(*args):
        return None

    async def noop(*args, **kwargs):
        return None

    if remote:
        monkeypatch.setattr(
            broker, "studio_request",
            lambda _studio, path: (f"http://remote.invalid{path}", {}),
        )
        monkeypatch.setattr(broker.peers, "is_local_studio", lambda _studio: False)
    monkeypatch.setattr(broker.asyncio, "sleep", continue_to_poll)
    monkeypatch.setattr(broker, "RECOVERY_WINDOW_S", 0)
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker.ledger, "record_activity_ownership", lambda **kwargs: None)

    asyncio.run(broker._run_item(client, batch, item, studio))

    persisted = ledger.load_batch(batch["id"])["items"][0]
    assert client.posts == 1
    assert len(client.created_jobs) == 1
    assert item["state"] == persisted["state"] == "uncertain"
    assert item["error_code"] == "VOICE_RECEIPT_INVALID"
    assert item["studio_job_id"] == accepted["id"]
    assert item["retry_at"] is None


def test_remote_transport_then_malformed_recovery_receipt_is_not_regenerated(
    fixture_monitor, monkeypatch
):
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    client = WorkerClient(
        accepted,
        polled_responses=[
            httpx.ConnectError(
                "fixture disconnect",
                request=httpx.Request("GET", "http://remote.invalid/status"),
            ),
            response(200, {"job": {"state": "running"}}),
        ],
    )

    async def continue_to_poll(*args):
        return None

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(
        broker, "studio_request",
        lambda _studio, path: (f"http://remote.invalid{path}", {}),
    )
    monkeypatch.setattr(broker.peers, "is_local_studio", lambda _studio: False)
    monkeypatch.setattr(broker.asyncio, "sleep", continue_to_poll)
    monkeypatch.setattr(broker, "RECOVERY_WINDOW_S", 1)
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker.ledger, "record_activity_ownership", lambda **kwargs: None)

    asyncio.run(broker._run_item(client, batch, item, studio))

    persisted = ledger.load_batch(batch["id"])["items"][0]
    assert client.posts == 1
    assert len(client.created_jobs) == 1
    assert client.gets == 2
    assert item["state"] == persisted["state"] == "uncertain"
    assert item["error_code"] == "VOICE_RECEIPT_INVALID"
    assert item["studio_job_id"] == accepted["id"]
    assert item["studio"] == studio["id"]
    assert item["retry_at"] is None


@pytest.mark.parametrize(
    "mutate",
    (
        lambda job: job.pop("output_url"),
        lambda job: job.update(runtime_revision="different-runtime-revision"),
    ),
    ids=("missing-output-url", "changed-runtime-revision"),
)
def test_voice_reconcile_rejects_malformed_terminal_receipt_without_new_job(
    fixture_monitor, mutate
):
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    item.update(
        state="uncertain",
        studio_job_id=accepted["id"],
        machine=studio["machine"],
        worker_id=studio["id"],
        model_revision=accepted["model_revision"],
        runtime_revision=accepted["runtime_revision"],
    )
    finished = completed_voice_job(batch, item, accepted)
    mutate(finished)
    client = WorkerClient(polled=finished, artifact=wav_bytes())

    result = asyncio.run(
        broker.reconcile_voice_item(batch["id"], item["index"], client=client)
    )

    persisted = ledger.load_batch(batch["id"])["items"][0]
    assert result["reconciled"] is False
    assert client.posts == 0
    assert len(client.created_jobs) == 0
    assert item["state"] == persisted["state"] == "uncertain"
    assert item["recovery"]["phase"] == "manual_action_required"
    assert item.get("asset_id") is None
    assert broker.terminal_result(batch, item) is None


@pytest.mark.parametrize(
    "accepted_receipt",
    (None, [], {}, {"id": 123, "state": "queued"}),
    ids=("missing", "non-object", "missing-id", "non-string-id"),
)
def test_malformed_voice_acceptance_is_fenced_before_another_worker_dispatch(
    fixture_monitor, monkeypatch, accepted_receipt
):
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    second_studio = {**studio, "id": "voice@second", "machine": "second"}
    terminal = {
        **accepted,
        "state": "error",
        "error": "fixture terminal failure",
        "params": {
            "repo": batch["model"],
            "text": item["prompt"],
            "client_request_id": f"studiohub:{batch['id']}:{item['index']}",
        },
    }
    first_client = WorkerClient(accepted=accepted_receipt)
    second_client = WorkerClient(accepted=accepted, polled=terminal)
    ledger.save_batch(batch)  # dispatcher persists Voice attempt ownership before POST

    async def continue_to_poll(*args):
        return None

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(broker.asyncio, "sleep", continue_to_poll)
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker.ledger, "record_activity_ownership", lambda **kwargs: None)

    asyncio.run(broker._run_item(first_client, batch, item, studio))
    if item["state"] == "queued":
        item.update(state="running", studio=second_studio["id"], tries=2)
        asyncio.run(broker._run_item(second_client, batch, item, second_studio))

    persisted = ledger.load_batch(batch["id"])["items"][0]
    assert first_client.posts == 1
    assert len(first_client.created_jobs) == 1
    assert second_client.posts == 0
    assert len(second_client.created_jobs) == 0
    assert item["state"] == persisted["state"] == "uncertain"
    assert item["error_code"] == "VOICE_RECEIPT_INVALID"
    assert item["studio"] == studio["id"]
    assert item["studio_job_id"] is None
    assert item["retry_at"] is None


def test_cancel_racing_malformed_voice_acceptance_stays_uncertain(
    fixture_monitor, monkeypatch
):
    batch, item, studio, _ = voice_attempt(fixture_monitor)

    class CancelOnAcceptanceClient(WorkerClient):
        async def post(self, *args, **kwargs):
            accepted = await super().post(*args, **kwargs)
            batch["cancelled"] = True
            return accepted

    client = CancelOnAcceptanceClient(accepted={})

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker, "_maybe_finish", noop)

    asyncio.run(broker._run_item(client, batch, item, studio))

    assert client.posts == 1
    assert len(client.created_jobs) == 1
    assert item["state"] == "uncertain"
    assert item["error_code"] == "VOICE_RECEIPT_INVALID"
    assert item["studio"] == studio["id"]
    assert item["studio_job_id"] is None
    assert item["retry_at"] is None


@pytest.mark.parametrize(
    "receipt_change, artifact",
    (
        (lambda job: job.update(bytes={"invalid": True}), wav_bytes()),
        (lambda job: job.update(bytes=1644.5), wav_bytes()),
        (lambda job: job.update(bytes=1), wav_bytes()),
        (lambda job: job.update(sha256="0" * 64), wav_bytes()),
    ),
    ids=(
        "invalid-byte-count", "fractional-byte-count", "mismatched-byte-count", "mismatched-hash",
    ),
)
def test_voice_artifact_verification_failure_remains_terminal_and_unverified(
    fixture_monitor, monkeypatch, receipt_change, artifact
):
    batch, item, studio, accepted = voice_attempt(fixture_monitor)
    finished = completed_voice_job(batch, item, accepted)
    receipt_change(finished)
    client = WorkerClient(accepted, polled=finished, artifact=artifact)

    async def continue_to_poll(*args):
        return None

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(broker.asyncio, "sleep", continue_to_poll)
    monkeypatch.setattr(broker, "RECOVERY_WINDOW_S", 0)
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker.ledger, "record_activity_ownership", lambda **kwargs: None)

    asyncio.run(broker._run_item(client, batch, item, studio))

    persisted = ledger.load_batch(batch["id"])["items"][0]
    assert client.posts == 1
    assert len(client.created_jobs) == 1
    assert client.artifact_gets == 1
    assert item["state"] == persisted["state"] == "done"
    assert item.get("asset_id")
    assert item.get("artifact_metadata_error")
    assert item.get("audio_duration_s") is None


def test_infinite_voice_artifact_byte_count_is_an_unverified_metadata_error(
    fixture_monitor
):
    studio = next(row for row in fixture_monitor.registry if row["id"] == "voice")
    item: dict = {}
    client = WorkerClient(artifact=wav_bytes())

    asyncio.run(broker._cache_voice_artifact_metadata(
        client,
        item,
        studio,
        "/api/generate/jobs/voice-worker-1/audio",
        float("inf"),
        None,
    ))

    assert client.artifact_gets == 1
    assert item.get("artifact_metadata_error")
    assert item.get("audio_duration_s") is None
