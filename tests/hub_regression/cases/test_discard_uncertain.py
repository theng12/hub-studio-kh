"""An uncertain attempt can be abandoned two different ways, and they are not the same.

Automatic age-out only releases the machine reservation: the item stays `uncertain` with
its exact accepted identity and a durable ``recovery.machine_released_at`` marker, so a
late result of the same attempt can still be reconciled. Explicit operator discard/cancel
remains the separate, permanent decision: the item is cancelled for good (DISCARDED_UNCERTAIN)
and a late result is recognisably this attempt's and is refused.

This is separate from cancellation and reconciliation, which must keep a live attempt fenced -
the tests in `test_image_uncertainty.py` pin that side.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from components.hub.backend import broker, ledger


def uncertain_attempt(fixture_monitor, modality="image", batch_id="discard-proof"):
    studio = next(row for row in fixture_monitor.registry if row["modality"] == modality)
    batch = {"id": batch_id, "modality": modality, "model": "fixture/model",
             "shared_params": {}, "cancelled": False, "routing": "pool",
             "created_at": time.time(), "items": []}
    item = {"index": 0, "prompt": "proof", "params": {}, "seed": -1,
            "state": "uncertain", "tries": 1, "studio": studio["id"],
            "studio_job_id": "worker-1", "retry_at": None, "error": None,
            "machine": studio["machine"], "worker_id": studio["id"],
            "model_revision": "r" * 40, "runtime_revision": "a" * 64,
            "worker_accepted_params": {"repo": batch["model"]},
            "recovery": {"phase": "uncertain", "reason": "lost poll"}}
    batch["items"] = [item]
    broker.batches[batch["id"]] = batch
    return batch, item, studio


def go_stale(item):
    item["last_progress_at"] = time.time() - broker.UNCERTAIN_AUTO_DISCARD_SECONDS - 60


@pytest.mark.parametrize("modality", ["image", "music", "voice"])
def test_discard_cancels_and_releases_the_machine(fixture_monitor, modality):
    batch, item, studio = uncertain_attempt(fixture_monitor, modality)
    assert studio["machine"] in broker.busy_machines()

    broker._wakeup.clear()
    result = broker.discard_uncertain_item(batch["id"], 0)

    assert result == {"ok": True, "batch_id": batch["id"], "item_index": 0}
    assert item["state"] == "cancelled"
    assert item["error_code"] == "DISCARDED_UNCERTAIN"
    assert item["error"] and "discard" in item["error"].lower()
    assert item["retry_at"] is None and item["finished_at"]
    # The worker identity is kept so a late result can be recognised and discarded; the machine
    # is released because the item is no longer uncertain.
    for kept in ("studio", "studio_job_id", "worker_id", "model_revision", "runtime_revision"):
        assert kept in item, f"{kept} must stay so a late result is identifiable"
    assert item["recovery"]["phase"] == "discarded"
    assert studio["machine"] not in broker.busy_machines()
    assert broker._wakeup.is_set(), "the dispatcher must be woken so later work can run"


def test_discard_persists_the_terminal_state(fixture_monitor):
    batch, item, _ = uncertain_attempt(fixture_monitor)
    broker.discard_uncertain_item(batch["id"], 0)
    persisted = ledger.load_batch(batch["id"])
    assert persisted is not None
    saved = persisted["items"][0]
    assert saved["state"] == "cancelled"
    assert saved["error_code"] == "DISCARDED_UNCERTAIN"
    assert saved["studio_job_id"] == "worker-1", "the identity is kept for a late result"


def test_discard_takes_a_discarded_item_out_of_reconciliation(fixture_monitor):
    batch, item, _ = uncertain_attempt(fixture_monitor)
    broker.discard_uncertain_item(batch["id"], 0)
    # The reconcile route only accepts an uncertain item, so a discarded one can never be adopted.
    with pytest.raises(ValueError):
        asyncio.run(broker.reconcile_image_item(batch["id"], 0))
    assert item["state"] == "cancelled"


def test_discard_refuses_anything_that_is_not_uncertain(fixture_monitor):
    batch, item, _ = uncertain_attempt(fixture_monitor)
    for state in ("queued", "running", "done", "error", "cancelled"):
        item["state"] = state
        with pytest.raises(ValueError):
            broker.discard_uncertain_item(batch["id"], 0)
    item["state"] = "uncertain"
    with pytest.raises(ValueError):
        broker.discard_uncertain_item("no-such-batch", 0)
    with pytest.raises(ValueError):
        broker.discard_uncertain_item(batch["id"], 7)


def test_stale_uncertain_releases_the_machine_but_stays_uncertain(fixture_monitor):
    batch, item, studio = uncertain_attempt(fixture_monitor)
    go_stale(item)
    assert studio["machine"] in broker.busy_machines()

    released = broker.release_stale_uncertain()

    assert released == [f"{batch['id']}:0"]
    # Automatic age-out is not a decision about adoption eligibility.
    assert item["state"] == "uncertain"
    assert item.get("error_code") is None
    for kept in ("studio", "studio_job_id", "worker_id", "model_revision", "runtime_revision"):
        assert kept in item, f"{kept} must stay so a late result is identifiable"
    assert item["recovery"]["phase"] == "manual_action_required"
    assert "machine reservation was released" in item["recovery"]["reason"]
    marker = item["recovery"]["machine_released_at"]
    assert type(marker) in (int, float) and marker > 0
    assert studio["machine"] not in broker.busy_machines()
    persisted = ledger.load_batch(batch["id"])["items"][0]
    saved = persisted["recovery"]["machine_released_at"]
    assert type(saved) in (int, float) and saved == pytest.approx(marker)
    # The marker is durable before capacity: release it twice and nothing changes.
    assert broker.release_stale_uncertain() == []


def test_fresh_uncertain_is_left_for_reconciliation(fixture_monitor):
    batch, item, studio = uncertain_attempt(fixture_monitor)
    item["last_progress_at"] = time.time()
    assert broker.release_stale_uncertain() == []
    assert item["state"] == "uncertain"
    assert studio["machine"] in broker.busy_machines()


def test_ageout_persistence_failure_leaves_the_hold_in_place(fixture_monitor, monkeypatch):
    batch, item, studio = uncertain_attempt(fixture_monitor)
    go_stale(item)

    def refuse(*_args, **_kwargs):
        raise RuntimeError("storage down")

    monkeypatch.setattr(ledger, "save_batch", refuse)
    assert broker.release_stale_uncertain() == []
    assert item["recovery"].get("machine_released_at") is None
    assert item["state"] == "uncertain"
    assert studio["machine"] in broker.busy_machines()


def test_ageout_marker_survives_recovery_updates(fixture_monitor):
    batch, item, studio = uncertain_attempt(fixture_monitor, modality="voice")
    go_stale(item)
    assert broker.release_stale_uncertain() == [f"{batch['id']}:0"]
    marker = item["recovery"]["machine_released_at"]

    broker.set_voice_recovery(item, "health_wait", "The original Voice job is still active.")

    assert item["recovery"]["machine_released_at"] == marker
    assert item["recovery"]["phase"] == "health_wait"
    assert studio["machine"] not in broker.busy_machines()


class _ReconcileClient:
    """Answers the original job read; optionally yields so races can interleave."""

    def __init__(self, job, before_get=None):
        self.job = job
        self.before_get = before_get
        self.posts = 0

    async def get(self, *args, **kwargs):
        if self.before_get is not None:
            await self.before_get()
        return httpx.Response(200, json={"job": self.job},
                              request=httpx.Request("GET", "http://127.0.0.1/"))

    async def post(self, *args, **kwargs):
        self.posts += 1
        return httpx.Response(200, json={"ok": True},
                              request=httpx.Request("POST", "http://127.0.0.1/"))


def _voice_done_job(batch):
    return {"id": "worker-1", "state": "done", "progress": 1.0,
            "params": {"client_request_id": f"studiohub:{batch['id']}:0"},
            "output_path": "/nonexistent/late.wav", "generated_at": time.time(),
            "bytes": 10, "sha256": "a" * 64, "runtime_s": 1.0,
            "output_url": "/api/generate/jobs/worker-1/audio"}


def _arm_voice_adoption(monkeypatch, assets, notified, *, yield_in_metadata=True):
    async def metadata(*args):
        if yield_in_metadata:
            await asyncio.sleep(0)

    async def webhook(client, b, item, *, reserved=False):
        notified.append(item.get("studio_job_id"))

    monkeypatch.setattr(broker, "_cache_voice_artifact_metadata", metadata)
    monkeypatch.setattr(ledger, "record_asset",
                        lambda **fields: assets.append(fields) or "asset-1")
    monkeypatch.setattr(broker, "_post_item_webhook", webhook)


class TestAgeOutLateResultTests:
    """Automatic age-out keeps the original attempt reconcilable; operators do not."""

    def test_a_late_result_after_ageout_still_adopts_the_same_attempt(
            self, fixture_monitor, monkeypatch):
        batch, item, studio = uncertain_attempt(fixture_monitor, modality="voice")
        batch["client_request_id"] = "voice-proof"
        go_stale(item)
        assert broker.release_stale_uncertain() == [f"{batch['id']}:0"]
        assets, notified = [], []
        _arm_voice_adoption(monkeypatch, assets, notified)

        result = asyncio.run(broker.reconcile_voice_item(
            batch["id"], 0, _ReconcileClient(_voice_done_job(batch))))

        assert result["reconciled"] is True
        assert item["state"] == "done"
        assert item["studio_job_id"] == "worker-1"
        assert item["recovery"]["phase"] == "reconciled"
        assert len(assets) == 1 and notified == ["worker-1"]
        assert studio["machine"] not in broker.busy_machines()

    def test_an_explicitly_discarded_result_is_not_adopted(
            self, fixture_monitor, monkeypatch):
        batch, item, studio = uncertain_attempt(fixture_monitor, modality="voice")
        batch["client_request_id"] = "voice-proof"
        go_stale(item)
        broker.discard_uncertain_item(batch["id"], 0)
        assets, notified = [], []
        _arm_voice_adoption(monkeypatch, assets, notified)

        with pytest.raises(ValueError):
            asyncio.run(broker.reconcile_voice_item(
                batch["id"], 0, _ReconcileClient(_voice_done_job(batch))))

        assert item["state"] == "cancelled"
        assert item["error_code"] == "DISCARDED_UNCERTAIN"
        assert assets == [] and notified == []

    def test_a_newer_accepted_result_is_not_overwritten(
            self, fixture_monitor, monkeypatch):
        batch, item, studio = uncertain_attempt(fixture_monitor, modality="voice")
        go_stale(item)
        broker.release_stale_uncertain()
        item.update(state="done", asset_id="newer-asset", error_code=None)
        item.pop("error", None)
        assets, notified = [], []
        _arm_voice_adoption(monkeypatch, assets, notified)

        adopted = asyncio.run(broker._record_worker_success(
            _ReconcileClient(_voice_done_job(batch)), batch, item, studio,
            _voice_done_job(batch),
            {"repo": batch["model"], "client_request_id": f"studiohub:{batch['id']}:0"},
            time.time(), recovery=True))

        assert adopted is False
        assert item["state"] == "done" and item["asset_id"] == "newer-asset"
        assert assets == [] and notified == []

    def test_no_late_result_leaves_the_recorded_reason(self, fixture_monitor):
        batch, item, _ = uncertain_attempt(fixture_monitor)
        go_stale(item)
        broker.release_stale_uncertain()
        assert item["state"] == "uncertain"
        assert item["recovery"]["phase"] == "manual_action_required"
        assert "machine reservation was released" in item["recovery"]["reason"]
        assert type(item["recovery"]["machine_released_at"]) in (int, float)

    def test_discard_during_metadata_await_leaves_the_loser_silent(
            self, fixture_monitor, monkeypatch):
        """The winner is an operator discard that lands mid-read: no asset, no clobber."""
        batch, item, studio = uncertain_attempt(fixture_monitor, modality="voice")
        batch["client_request_id"] = "voice-proof"
        assets, notified = [], []

        async def metadata(*args):
            await asyncio.sleep(0)
            broker.discard_uncertain_item(batch["id"], 0)

        monkeypatch.setattr(broker, "_cache_voice_artifact_metadata", metadata)
        monkeypatch.setattr(ledger, "record_asset",
                            lambda **fields: assets.append(fields) or "asset-1")

        async def webhook(*args, **kwargs):
            notified.append(1)

        monkeypatch.setattr(broker, "_post_item_webhook", webhook)

        result = asyncio.run(broker.reconcile_voice_item(
            batch["id"], 0, _ReconcileClient(_voice_done_job(batch))))

        assert result["reconciled"] is False
        assert item["state"] == "cancelled"
        assert item["error_code"] == "DISCARDED_UNCERTAIN"
        assert "discard" in item["error"].lower()
        assert item["recovery"]["phase"] == "discarded"
        assert assets == [] and notified == []

    def test_discard_during_review_candidate_await_writes_no_terminal_state(
            self, fixture_monitor, monkeypatch):
        batch, item, studio = uncertain_attempt(fixture_monitor, modality="voice")
        batch["client_request_id"] = "voice-proof"
        job = {**_voice_done_job(batch), "state": "error", "error": "section failed",
               "error_code": "TTS_SECTION_REJECTED"}

        async def attach(client, b, i, s, code):
            broker.discard_uncertain_item(b["id"], i["index"])

        monkeypatch.setattr(broker, "_attach_review_candidate", attach)
        monkeypatch.setattr(broker, "_cache_voice_artifact_metadata",
                            lambda *a, **k: asyncio.sleep(0))

        result = asyncio.run(broker.reconcile_voice_item(
            batch["id"], 0, _ReconcileClient(job)))

        assert result["reconciled"] is False
        assert item["state"] == "cancelled"
        assert item["error_code"] == "DISCARDED_UNCERTAIN"
        assert "discard" in item["error"].lower()
        assert "_review_candidate" not in item

    def test_concurrent_reconcile_of_one_attempt_is_one_asset_one_notification(
            self, fixture_monitor, monkeypatch):
        batch, item, studio = uncertain_attempt(fixture_monitor, modality="voice")
        batch["client_request_id"] = "voice-proof"
        batch["item_webhook"] = "http://fixture.invalid/item"
        assets, notified = [], []
        _arm_voice_adoption(monkeypatch, assets, notified)
        client = _ReconcileClient(_voice_done_job(batch),
                                  before_get=lambda: asyncio.sleep(0))

        async def both():
            return await asyncio.gather(
                broker.reconcile_voice_item(batch["id"], 0, client=client),
                broker.reconcile_voice_item(batch["id"], 0, client=client),
                return_exceptions=True)

        first, second = asyncio.run(both())

        reconciled = [first, second]
        assert all(not isinstance(reply, Exception) for reply in reconciled), reconciled
        assert sum(bool(reply["reconciled"]) for reply in reconciled) == 1, reconciled
        assert item["state"] == "done" and item["asset_id"] == "asset-1"
        assert len(assets) == 1, "exactly one asset may be recorded"
        assert len(notified) == 1, "exactly one completion notification"
