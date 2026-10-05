"""Accepted Image jobs must keep their identity when the Hub loses a receipt."""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from components.hub.backend import broker, ledger


class WorkerClient:
    def __init__(self, post_result, poll_result=None):
        self.post_result = post_result
        self.poll_result = poll_result
        self.posts = 0
        self.deletes = 0

    async def post(self, *args, **kwargs):
        self.posts += 1
        if isinstance(self.post_result, Exception):
            raise self.post_result
        return self.post_result

    async def get(self, *args, **kwargs):
        if isinstance(self.poll_result, Exception):
            raise self.poll_result
        return self.poll_result

    async def delete(self, *args, **kwargs):
        self.deletes += 1
        return response(200, {"ok": True})


def response(status, payload):
    return httpx.Response(status, json=payload,
                          request=httpx.Request("GET", "http://127.0.0.1/"))


def image_attempt(fixture_monitor):
    studio = next(row for row in fixture_monitor.registry if row["id"] == "image")
    batch = {"id": "image-proof", "modality": "image", "model": "fixture/flux",
             "shared_params": {}, "cancelled": False, "routing": "pool",
             "created_at": time.time(), "items": []}
    item = {"index": 0, "prompt": "proof", "params": {}, "seed": -1,
            "state": "running", "tries": 1, "studio": studio["id"],
            "studio_job_id": None, "retry_at": None, "error": None}
    batch["items"] = [item]
    broker.batches[batch["id"]] = batch
    broker._busy.add(studio["id"])
    return batch, item, studio


def uncertain_attempt(fixture_monitor):
    batch, item, studio = image_attempt(fixture_monitor)
    batch["client_request_id"] = "original-image-request"
    item.update(state="uncertain", studio_job_id="worker-1",
                machine=studio["machine"], worker_id=studio["id"],
                model_revision="r" * 40, runtime_revision="a" * 64,
                recovery={"phase": "uncertain", "reason": "lost poll"})
    broker._busy.discard(studio["id"])
    job = {"id": "worker-1", "model_revision": item["model_revision"],
           "runtime_revision": item["runtime_revision"],
           "worker_id": studio["id"], "machine_id": studio["machine"],
           "params": {"repo": batch["model"], "prompt": item["prompt"], "seed": -1}}
    return batch, item, studio, job


def accepted_response(studio):
    return {"id": "worker-1", "state": "queued", "worker_id": studio["id"],
            "machine_id": studio["machine"], "model_revision": "r" * 40,
            "runtime_revision": "a" * 64}


@pytest.fixture
def no_delivery(monkeypatch):
    async def noop(*args):
        return None

    async def unresolved(*args):
        return False

    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker, "_maybe_finish", noop)
    monkeypatch.setattr(broker, "_recover_worker_job", unresolved)
    monkeypatch.setattr(broker, "POLL_S", 0)


def test_lost_and_malformed_image_acceptance_are_fenced(fixture_monitor, no_delivery):
    for post in (httpx.ReadTimeout("lost after POST"), response(200, {"unexpected": True})):
        batch, item, studio = image_attempt(fixture_monitor)
        client = WorkerClient(post)
        asyncio.run(broker._run_item(client, batch, item, studio))
        assert client.posts == 1
        assert item["state"] == "uncertain"
        assert item["tries"] == 1
        assert item["studio"] == studio["id"]
        assert item["machine"] == studio["machine"]
        assert item["retry_at"] is None
        assert studio["machine"] in broker.busy_machines()
        assert "another-machine" not in broker.busy_machines()


def test_a_worker_that_lost_the_accepted_job_is_rerun(fixture_monitor, no_delivery):
    """Owner decision 2026-10-03: free work is re-run when its worker no longer has it (it was
    `uncertain` and fenced until an operator acted)."""
    batch, item, studio = image_attempt(fixture_monitor)
    client = WorkerClient(response(200, {"job": accepted_response(studio)}),
                          response(404, {"detail": "not found"}))

    async def run():
        await broker._run_item(client, batch, item, studio)
        await asyncio.gather(*broker._abandoned_peer_cancels)
    asyncio.run(run())
    assert client.posts == 1
    assert item["state"] == "queued" and item["retry_at"] is not None
    assert item.get("studio_job_id") is None
    assert studio["machine"] not in broker.busy_machines()


def test_accepted_job_persistence_uncertainty_keeps_worker_id(
        fixture_monitor, no_delivery):
    for poll in (response(200, {"job": {"id": "worker-1", "state": "error",
                     "error": "Final PNG exists without a receipt",
                     "error_code": "JOB_PERSISTENCE_UNCERTAIN"}}),):
        batch, item, studio = image_attempt(fixture_monitor)
        client = WorkerClient(response(200, {"job": accepted_response(studio)}), poll)
        asyncio.run(broker._run_item(client, batch, item, studio))
        assert client.posts == 1
        assert item["state"] == "uncertain"
        assert item["studio_job_id"] == "worker-1"
        assert item["studio"] == studio["id"]
        assert item["tries"] == 1
        assert item["retry_at"] is None


def test_known_image_rejection_can_retry(fixture_monitor, no_delivery):
    batch, item, studio = image_attempt(fixture_monitor)
    client = WorkerClient(response(503, {"detail": "worker unavailable"}))
    asyncio.run(broker._run_item(client, batch, item, studio))
    assert client.posts == 1
    assert item["state"] == "queued"
    assert item["studio_job_id"] is None
    assert item["retry_at"] is not None


def test_restore_reruns_image_without_holding_its_machine(fixture_monitor):
    """Owner, 2026-10-05: a Hub restart re-runs free work (it used to fence it `uncertain`, which
    held the Mac for 30 minutes). The original is watched for adoption, see test_free_work_reroute."""
    batch, item, studio = image_attempt(fixture_monitor)
    item["studio_job_id"] = "worker-before-restart"
    ledger.save_batch(batch)
    broker.batches.clear()
    broker._busy.clear()
    broker.restore_batches()
    restored = broker.batches[batch["id"]]["items"][0]
    assert restored["state"] == "queued"
    assert restored["studio_job_id"] is None
    assert restored["tries"] == 1
    assert broker.busy_machines() == set()
    assert [row[3] for row in broker._restored_attempts] == ["worker-before-restart"]
    # Still one item: the restart re-runs it, it does not add another.
    assert len(broker.batches[batch["id"]]["items"]) == 1


def test_replay_of_uncertain_image_request_cannot_submit_a_second_job(fixture_monitor):
    envelope = {"modality": "image", "model": "fixture/flux",
                "clientRequestId": "image-proof-0001",
                "items": [{"prompt": "proof", "params": {"width": 1024, "height": 1024}}]}
    accepted = broker.submit_batch(envelope)
    batch = broker.batches[accepted["batch_id"]]
    studio = next(row for row in fixture_monitor.registry if row["id"] == "image")
    batch["items"][0].update(state="running", studio=studio["id"],
                             studio_job_id="worker-1", tries=1)
    ledger.save_batch(batch)
    broker.batches.clear()
    broker.restore_batches()
    replayed = broker.submit_batch(envelope)
    assert replayed == {"batch_id": accepted["batch_id"], "items": 1, "replayed": True}
    assert list(broker.batches) == [accepted["batch_id"]]
    # The restart re-ran the one item; the replay adds no second one.
    assert [row["state"] for row in broker.batches[accepted["batch_id"]]["items"]] == ["queued"]


def test_reconcile_keeps_missing_or_mismatched_original_uncertain(fixture_monitor):
    batch, item, studio, job = uncertain_attempt(fixture_monitor)
    item["studio_job_id"] = None
    client = WorkerClient(None)
    result = asyncio.run(broker.reconcile_image_item(batch["id"], 0, client))
    assert not result["reconciled"] and result["clientRequestId"] == "original-image-request"
    assert item["state"] == "uncertain" and client.posts == 0

    item["studio_job_id"] = "worker-1"
    bad = {**job, "state": "done", "runtime_revision": "wrong"}
    client = WorkerClient(None, response(200, {"job": bad}))
    result = asyncio.run(broker.reconcile_image_item(batch["id"], 0, client))
    assert not result["reconciled"]
    assert item["state"] == "uncertain"
    assert studio["machine"] in broker.busy_machines()
    assert client.posts == 0


def test_reconcile_original_active_then_known_error_releases_only_its_machine(
        fixture_monitor, no_delivery):
    batch, item, studio, job = uncertain_attempt(fixture_monitor)
    client = WorkerClient(None, response(200, {"job": {**job, "state": "running",
                                                      "progress": 0.5}}))
    result = asyncio.run(broker.reconcile_image_item(batch["id"], 0, client))
    assert not result["reconciled"] and item["state"] == "uncertain"
    assert item["progress"] == 0.5
    assert studio["machine"] in broker.busy_machines()

    client.poll_result = response(200, {"job": {**job, "state": "error",
        "error_code": "IMAGE_GENERATION_FAILED", "error": "known worker failure"}})
    result = asyncio.run(broker.reconcile_image_item(batch["id"], 0, client))
    assert result["reconciled"] and item["state"] == "error"
    assert result["item"]["error_code"] == "IMAGE_GENERATION_FAILED"
    assert studio["machine"] not in broker.busy_machines()
    assert client.posts == 0


def test_reconcile_adopts_only_verified_original_done(fixture_monitor, no_delivery,
                                                     monkeypatch):
    batch, item, studio, job = uncertain_attempt(fixture_monitor)
    adopted = []

    async def adopt(client, received_batch, received_item, received_studio,
                    received_job, body, started, *, recovery=False):
        assert recovery and received_job["id"] == item["studio_job_id"]
        assert received_studio["machine"] == studio["machine"]
        assert body["repo"] == batch["model"] and body["prompt"] == item["prompt"]
        adopted.append(received_job["id"])
        received_item["state"] = "done"

    monkeypatch.setattr(broker, "_record_worker_success", adopt)
    client = WorkerClient(None, response(200, {"job": {**job, "state": "done",
        "output_url": "/api/generate/jobs/worker-1/image", "output_path": "/tmp/worker-1.png",
        "media_type": "image/png", "generated_at": time.time(), "bytes": 123,
        "sha256": "a" * 64}}))
    result = asyncio.run(broker.reconcile_image_item(batch["id"], 0, client))
    assert result["reconciled"] and result["item"]["state"] == "done"
    assert adopted == ["worker-1"] and client.posts == 0
    assert studio["machine"] not in broker.busy_machines()


def test_reconcile_cancelled_is_terminal_but_incomplete_done_stays_fenced(
        fixture_monitor, no_delivery):
    batch, item, studio, job = uncertain_attempt(fixture_monitor)
    client = WorkerClient(None, response(200, {"job": {**job, "state": "done",
        "output_url": "/api/generate/jobs/worker-1/image", "bytes": 1}}))
    result = asyncio.run(broker.reconcile_image_item(batch["id"], 0, client))
    assert not result["reconciled"] and item["state"] == "uncertain"
    assert studio["machine"] in broker.busy_machines()

    client.poll_result = response(200, {"job": {**job, "state": "cancelled",
        "error": "Cancelled by owner", "error_code": "CANCELLED"}})
    result = asyncio.run(broker.reconcile_image_item(batch["id"], 0, client))
    assert result["reconciled"] and item["state"] == "cancelled"
    assert studio["machine"] not in broker.busy_machines()
    assert client.posts == 0


def test_expired_image_lease_requests_cancel_and_keeps_original_fenced(
        fixture_monitor, no_delivery, monkeypatch):
    batch, item, studio, job = uncertain_attempt(fixture_monitor)
    item["state"] = "running"
    batch["genstudio_execution"] = {"lease_expires_at": 1}
    monkeypatch.setattr(broker.execution_identity, "lease_expired", lambda _: True)
    assert broker._expire_genstudio_batch(batch)
    assert batch["cancelled"] and batch["lease_expired"]
    assert item["state"] == "uncertain" and item["studio_job_id"] == "worker-1"
    assert studio["machine"] in broker.busy_machines()
    ledger.save_batch(batch)
    broker.batches.clear()
    broker.restore_batches()
    assert broker.batches[batch["id"]]["items"][0]["state"] == "uncertain"

    client = WorkerClient(None, response(200, {"job": {**job, "state": "running"}}))
    result = asyncio.run(broker.reconcile_image_item(batch["id"], 0, client))
    assert not result["reconciled"] and client.deletes == 1 and client.posts == 0
    assert studio["machine"] in broker.busy_machines()
    client.poll_result = response(200, {"job": {**job, "state": "cancelled",
        "error": "cancelled", "error_code": "CANCELLED"}})
    assert asyncio.run(broker.reconcile_image_item(batch["id"], 0, client))["reconciled"]
    assert studio["machine"] not in broker.busy_machines()


def test_user_cancel_after_image_acceptance_without_a_worker_answer_stays_uncertain(
        fixture_monitor, no_delivery):
    """The stop signal landed but the worker cannot be asked what became of it: truly unknown."""
    batch, item, studio = image_attempt(fixture_monitor)
    client = WorkerClient(response(200, {"job": accepted_response(studio)}))
    original_post = client.post

    async def accepted_then_cancel(*args, **kwargs):
        result = await original_post(*args, **kwargs)
        batch["cancelled"] = True
        return result

    client.post = accepted_then_cancel
    asyncio.run(broker._run_item(client, batch, item, studio))
    assert client.posts == 1 and client.deletes >= 1
    assert item["state"] == "uncertain" and item["studio_job_id"] == "worker-1"
    assert item["tries"] == 1 and item["retry_at"] is None
    assert studio["machine"] in broker.busy_machines()


@pytest.mark.parametrize("corruption", ("wrong_id", "missing_id", "unknown_state",
                                         "missing_png_receipt"))
def test_malformed_normal_image_poll_never_adopts_or_changes_accepted_identity(
        fixture_monitor, no_delivery, corruption):
    batch, item, studio = image_attempt(fixture_monitor)
    accepted = accepted_response(studio)
    final = {**accepted, "state": "done",
             "output_url": "/api/generate/jobs/worker-1/image",
             "output_path": "/tmp/worker-1.png", "media_type": "image/png",
             "generated_at": time.time(), "bytes": 123, "sha256": "a" * 64}
    if corruption == "wrong_id":
        final["id"] = "different-worker-job"
    elif corruption == "missing_id":
        final.pop("id")
    elif corruption == "unknown_state":
        final["state"] = "mystery"
    else:
        final.pop("sha256")
    client = WorkerClient(response(200, {"job": accepted}), response(200, {"job": final}))
    asyncio.run(broker._run_item(client, batch, item, studio))
    assert client.posts == 1
    assert item["state"] == "uncertain" and item["retry_at"] is None
    assert item["studio_job_id"] == accepted["id"]
    assert item["model_revision"] == accepted["model_revision"]
    assert item["runtime_revision"] == accepted["runtime_revision"]
    assert item.get("asset_id") is None and item.get("artifact_path") is None
    assert studio["machine"] in broker.busy_machines()
