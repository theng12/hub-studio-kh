"""Accepted Music jobs keep one durable worker identity across Hub failure."""
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
from components.music.app.backend.generation import validate_request


class StopBeforePoll(BaseException):
    pass


class WorkerClient:
    def __init__(self, accepted: dict | None = None, *, polled: dict | None = None,
                 cancel_status: int = 200):
        self.accepted = accepted
        self.polled = polled
        self.cancel_status = cancel_status
        self.posts = 0
        self.gets = 0
        self.deletes = 0

    async def post(self, *args, **kwargs):
        self.posts += 1
        return response(200, {"job": self.accepted})

    async def get(self, *args, **kwargs):
        self.gets += 1
        return response(200, {"job": self.polled})

    async def delete(self, *args, **kwargs):
        self.deletes += 1
        return response(self.cancel_status, {})


def response(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(
        status, json=payload, request=httpx.Request("GET", "http://127.0.0.1/")
    )


def music_attempt(fixture_monitor):
    studio = next(row for row in fixture_monitor.registry if row["id"] == "music")
    batch = {
        "id": "music-durability-proof",
        "client_request_id": "music-durability-request",
        "modality": "music",
        "model": "stabilityai/stable-audio-3-optimized",
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
        "id": "music-worker-1",
        "state": "queued",
        "worker_id": studio["id"],
        "machine_id": studio["machine"],
        "model_revision": "r" * 40,
        "runtime_revision": "a" * 64,
        "params": validate_request({"repo": batch["model"], "prompt": item["prompt"]}),
    }
    return batch, item, studio, accepted


async def noop(*args):
    return None


def test_music_acceptance_is_persisted_before_first_poll(fixture_monitor, monkeypatch):
    batch, item, studio, accepted = music_attempt(fixture_monitor)
    item.update(prompt="  proof  ", params={"duration": 10})
    accepted["params"] = validate_request({
        "repo": batch["model"], "prompt": item["prompt"], "duration": 10,
    })
    writes: list[dict] = []

    async def stop_before_poll(*args):
        raise StopBeforePoll()

    monkeypatch.setattr(ledger, "save_batch", lambda value: writes.append(copy.deepcopy(value)))
    monkeypatch.setattr(broker.asyncio, "sleep", stop_before_poll)
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker, "_maybe_finish", noop)
    monkeypatch.setattr(broker.ledger, "record_activity_ownership", lambda **kwargs: None)

    with pytest.raises(StopBeforePoll):
        asyncio.run(broker._run_item(WorkerClient(accepted), batch, item, studio))

    assert item["studio_job_id"] == accepted["id"]
    accepted_write = next(
        row for row in writes
        if row["items"][0].get("studio_job_id") == accepted["id"]
    )
    assert accepted_write["items"][0]["worker_accepted_params"] == accepted["params"]


def test_failed_music_cancel_after_acceptance_is_uncertain(fixture_monitor, monkeypatch):
    batch, item, studio, accepted = music_attempt(fixture_monitor)
    client = WorkerClient(accepted, cancel_status=500)
    original_post = client.post

    async def cancel_after_acceptance(*args, **kwargs):
        reply = await original_post(*args, **kwargs)
        batch["cancelled"] = True
        return reply

    monkeypatch.setattr(client, "post", cancel_after_acceptance)
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker, "_maybe_finish", noop)
    monkeypatch.setattr(broker.ledger, "record_activity_ownership", lambda **kwargs: None)

    asyncio.run(broker._run_item(client, batch, item, studio))

    assert client.posts == 1
    assert client.deletes == 1
    assert item["state"] == "uncertain"
    assert item["studio_job_id"] == accepted["id"]
    assert item["error_code"] == "MUSIC_OUTCOME_UNCERTAIN"
    assert item["retry_at"] is None
    assert studio["machine"] in broker.busy_machines()


def test_restart_reruns_music_job_and_watches_the_original(fixture_monitor):
    """Owner, 2026-10-05: free work is re-run after a Hub restart instead of fenced `uncertain`."""
    batch, item, studio, accepted = music_attempt(fixture_monitor)
    item["studio_job_id"] = accepted["id"]
    ledger.save_batch(batch)
    broker.batches.clear()
    broker._busy.clear()

    assert broker.restore_batches() == 1

    restored = broker.batches[batch["id"]]["items"][0]
    assert restored["state"] == "queued"
    assert restored["studio_job_id"] is None and restored["studio"] is None
    assert restored["retry_at"] is None
    assert studio["machine"] not in broker.busy_machines()
    assert [(row[2], row[3]) for row in broker._restored_attempts] == [(studio["id"], accepted["id"])]


def test_reconcile_music_adopts_only_the_original_verified_wav(
    fixture_monitor, monkeypatch
):
    batch, item, studio, accepted = music_attempt(fixture_monitor)
    item.update(prompt="  proof  ", params={"duration": 10})
    accepted["params"] = validate_request({
        "repo": batch["model"], "prompt": item["prompt"], "duration": 10,
    })
    item.update(
        state="uncertain",
        studio_job_id=accepted["id"],
        machine=studio["machine"],
        worker_id=studio["id"],
        model_revision=accepted["model_revision"],
        runtime_revision=accepted["runtime_revision"],
        worker_accepted_params=accepted["params"],
        recovery={"phase": "uncertain", "reason": "Hub restarted"},
    )
    broker._busy.discard(studio["id"])
    finished = {
        **accepted,
        "state": "done",
        "output_url": f"/api/generate/jobs/{accepted['id']}/audio",
        "output_path": f"/private/music/{accepted['id']}.wav",
        "media_type": "audio/wav",
        "generated_at": time.time(),
        "bytes": 4096,
        "sha256": "b" * 64,
    }
    adopted: list[str] = []

    async def adopt(client, received_batch, received_item, received_studio,
                    job, body, started_at, *, recovery=False):
        assert recovery is True
        assert received_item["studio_job_id"] == job["id"]
        assert body["repo"] == batch["model"] and body["prompt"] == item["prompt"]
        adopted.append(job["id"])
        received_item["state"] = "done"

    monkeypatch.setattr(broker, "_record_worker_success", adopt)
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker, "_maybe_finish", noop)
    client = WorkerClient(polled=finished)

    result = asyncio.run(
        broker.reconcile_music_item(batch["id"], item["index"], client=client)
    )

    assert result["reconciled"] is True
    assert adopted == [accepted["id"]]
    assert item["state"] == "done"
    assert studio["machine"] not in broker.busy_machines()
    assert client.posts == 0 and client.gets == 1


def test_reconcile_music_refuses_changed_canonical_params(fixture_monitor, monkeypatch):
    batch, item, studio, accepted = music_attempt(fixture_monitor)
    item.update(
        state="uncertain",
        studio_job_id=accepted["id"],
        machine=studio["machine"],
        worker_id=studio["id"],
        model_revision=accepted["model_revision"],
        runtime_revision=accepted["runtime_revision"],
        worker_accepted_params=accepted["params"],
        recovery={"phase": "uncertain", "reason": "Hub restarted"},
    )
    changed = copy.deepcopy(accepted)
    changed["state"] = "done"
    changed["params"]["duration_seconds"] += 1
    changed.update(
        output_url=f"/api/generate/jobs/{accepted['id']}/audio",
        output_path=f"/private/music/{accepted['id']}.wav",
        media_type="audio/wav",
        generated_at=time.time(),
        bytes=4096,
        sha256="b" * 64,
    )
    adopt = AsyncMock()
    monkeypatch.setattr(broker, "_record_worker_success", adopt)

    result = asyncio.run(
        broker.reconcile_music_item(
            batch["id"], item["index"], client=WorkerClient(polled=changed)
        )
    )

    assert result["reconciled"] is False
    assert item["state"] == "uncertain"
    assert "request settings" in item["recovery"]["reason"]
    assert studio["machine"] in broker.busy_machines()
    adopt.assert_not_awaited()


def test_legacy_music_receipt_without_canonical_params_stays_uncertain(fixture_monitor):
    batch, item, studio, accepted = music_attempt(fixture_monitor)
    item.update(
        state="uncertain",
        studio_job_id=accepted["id"],
        machine=studio["machine"],
        worker_id=studio["id"],
        model_revision=accepted["model_revision"],
        runtime_revision=accepted["runtime_revision"],
        recovery={"phase": "uncertain", "reason": "legacy receipt"},
    )
    finished = {
        **accepted,
        "state": "done",
        "output_url": f"/api/generate/jobs/{accepted['id']}/audio",
        "output_path": f"/private/music/{accepted['id']}.wav",
        "media_type": "audio/wav",
        "generated_at": time.time(),
        "bytes": 4096,
        "sha256": "b" * 64,
    }

    result = asyncio.run(
        broker.reconcile_music_item(
            batch["id"], item["index"], client=WorkerClient(polled=finished)
        )
    )

    assert result["reconciled"] is False
    assert item["state"] == "uncertain"
    assert "canonical request settings" in item["recovery"]["reason"]
    assert studio["machine"] in broker.busy_machines()


def test_polled_music_done_save_failure_reconciles_without_second_generation_post(
    fixture_monitor, monkeypatch
):
    batch, item, studio, accepted = music_attempt(fixture_monitor)
    finished = {
        **accepted,
        "state": "done",
        "output_url": f"/api/generate/jobs/{accepted['id']}/audio",
        "output_path": f"/private/music/{accepted['id']}.wav",
        "media_type": "audio/wav",
        "generated_at": time.time(),
        "bytes": 4096,
        "sha256": "b" * 64,
    }
    client = WorkerClient(accepted, polled=finished)
    original_save = ledger.save_batch
    failed = False

    def fail_first_terminal_save(value):
        nonlocal failed
        if not failed and value["items"][0]["state"] == "done":
            failed = True
            raise OSError("fixture: terminal receipt disk full")
        original_save(value)

    monkeypatch.setattr(ledger, "save_batch", fail_first_terminal_save)
    monkeypatch.setattr(broker, "POLL_S", 0)
    monkeypatch.setattr(broker.ledger, "record_activity_ownership", lambda **kwargs: None)

    with pytest.raises(OSError, match="terminal receipt disk full"):
        asyncio.run(broker._run_item(client, batch, item, studio))

    assert client.posts == 1
    assert item["state"] == "uncertain"
    assert item["studio_job_id"] == accepted["id"]
    assert item["worker_accepted_params"] == accepted["params"]
    assert studio["machine"] in broker.busy_machines()
    first_asset_id = item["asset_id"]
    assert (
        ledger.load_batch(batch["id"])["items"][0]["worker_accepted_params"]
        == accepted["params"]
    )
    broker.batches.clear()
    broker._busy.clear()
    assert broker.restore_batches() == 1
    batch = broker.batches[batch["id"]]
    item = batch["items"][0]
    assert item["worker_accepted_params"] == accepted["params"]
    assert studio["machine"] in broker.busy_machines()

    result = asyncio.run(
        broker.reconcile_music_item(batch["id"], item["index"], client=client)
    )

    assert result["reconciled"] is True
    assert item["state"] == "done"
    assert item["asset_id"] == first_asset_id
    assert client.posts == 1
    assert client.gets == 2
    assert studio["machine"] not in broker.busy_machines()


@pytest.mark.parametrize("terminal_state", ("done", "error", "cancelled"))
def test_terminal_music_save_failure_stays_fenced_until_reconcile_retry(
    fixture_monitor, monkeypatch, terminal_state
):
    batch, item, studio, accepted = music_attempt(fixture_monitor)
    item.update(
        state="uncertain",
        studio_job_id=accepted["id"],
        machine=studio["machine"],
        worker_id=studio["id"],
        model_revision=accepted["model_revision"],
        runtime_revision=accepted["runtime_revision"],
        worker_accepted_params=accepted["params"],
        recovery={"phase": "uncertain", "reason": "Hub restarted"},
    )
    broker._busy.discard(studio["id"])
    batch["item_webhook"] = "https://callback.invalid/item"
    ledger.save_batch(batch)
    finished = {**accepted, "state": terminal_state}
    if terminal_state == "done":
        finished.update(
            output_url=f"/api/generate/jobs/{accepted['id']}/audio",
            output_path=f"/private/music/{accepted['id']}.wav",
            media_type="audio/wav",
            generated_at=time.time(),
            bytes=4096,
            sha256="b" * 64,
        )
    else:
        finished.update(error=terminal_state, error_code=terminal_state.upper())
    client = WorkerClient(polled=finished)
    original_save = ledger.save_batch
    failed = False

    def fail_first_terminal_save(value):
        nonlocal failed
        if not failed and value["items"][0]["state"] in {"done", "error", "cancelled"}:
            failed = True
            raise OSError("fixture: terminal receipt disk full")
        original_save(value)

    monkeypatch.setattr(ledger, "save_batch", fail_first_terminal_save)

    with pytest.raises(OSError, match="terminal receipt disk full"):
        asyncio.run(
            broker.reconcile_music_item(batch["id"], item["index"], client=client)
        )

    assert item["state"] == "uncertain"
    assert item["error_code"] == "JOB_PERSISTENCE_UNCERTAIN"
    assert studio["machine"] in broker.busy_machines()
    persisted_uncertain = ledger.load_batch(batch["id"])
    assert persisted_uncertain["items"][0]["state"] == "uncertain"
    assert "_item_notified" not in persisted_uncertain["items"][0]
    assert not persisted_uncertain.get("done_notified")
    assert "finished_at" not in persisted_uncertain
    assert client.posts == 0
    first_asset_id = item.get("asset_id")

    result = asyncio.run(
        broker.reconcile_music_item(batch["id"], item["index"], client=client)
    )

    assert result["reconciled"] is True
    assert item["state"] == terminal_state
    assert studio["machine"] not in broker.busy_machines()
    assert client.posts == 1
    assert client.gets == 2
    persisted_terminal = ledger.load_batch(batch["id"])
    assert persisted_terminal["done_notified"] is True
    assert persisted_terminal["finished_at"] > batch["created_at"]
    assert persisted_terminal["items"][0]["_item_notified"] is True
    if terminal_state == "done":
        assert item.get("error") is None
        assert "error_code" not in item
        assert item["asset_id"] == first_asset_id
        with ledger._conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM assets WHERE batch_id = ? AND item_index = ?",
                (batch["id"], item["index"]),
            ).fetchone()[0]
        assert count == 1
    else:
        assert item["error"] == terminal_state
        assert item["error_code"] == terminal_state.upper()


def test_reconcile_music_rejects_an_incomplete_wav_receipt(fixture_monitor):
    batch, item, studio, accepted = music_attempt(fixture_monitor)
    item.update(
        state="uncertain",
        studio_job_id=accepted["id"],
        machine=studio["machine"],
        worker_id=studio["id"],
        model_revision=accepted["model_revision"],
        runtime_revision=accepted["runtime_revision"],
        recovery={"phase": "uncertain", "reason": "Hub restarted"},
    )
    broker._busy.discard(studio["id"])
    incomplete = {
        **accepted,
        "state": "done",
        "params": {"repo": batch["model"], "prompt": item["prompt"]},
        "output_url": f"/api/generate/jobs/{accepted['id']}/audio",
        "bytes": 4096,
    }

    result = asyncio.run(
        broker.reconcile_music_item(
            batch["id"], item["index"], client=WorkerClient(polled=incomplete)
        )
    )

    assert result["reconciled"] is False
    assert item["state"] == "uncertain"
    assert item["recovery"]["phase"] == "manual_action_required"
    assert studio["machine"] in broker.busy_machines()


def test_authenticated_reconcile_route_dispatches_music(fixture_monitor, monkeypatch):
    batch, item, _, accepted = music_attempt(fixture_monitor)
    item.update(state="uncertain", studio_job_id=accepted["id"])
    reply = {
        "batch_id": batch["id"],
        "clientRequestId": batch["client_request_id"],
        "reconciled": False,
        "item": {"state": "uncertain"},
    }
    reconcile = AsyncMock(return_value=reply)
    monkeypatch.setattr(broker, "reconcile_music_item", reconcile)
    app = standalone.create_app(start_background=False)
    path = f"/api/hub/jobs/{batch['id']}/items/{item['index']}/reconcile"
    token = auth.TOKEN_FILE.read_text().strip()

    with TestClient(app) as client:
        assert client.post(path).status_code == 401
        result = client.post(path, headers={"Authorization": f"Bearer {token}"})

    assert result.status_code == 200
    assert result.json() == reply
    reconcile.assert_awaited_once_with(
        batch["id"], item["index"], client=app.state.monitor._client
    )
