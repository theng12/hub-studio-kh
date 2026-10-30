"""An uncertain attempt can be abandoned, and abandoning it releases the Mac.

An uncertain item keeps its physical Mac reserved for reconciliation, because the accepted worker
attempt may still be running. When the worker is gone that uncertainty can never resolve by itself,
so the Mac is pinned and every later job waits behind it with no reason shown. Discard is the
operator's explicit decision to stop waiting: the item is cancelled for good and its worker and
recovery identity are cleared.

This is separate from cancellation and reconciliation, which must keep a live attempt fenced - the
tests in `test_image_uncertainty.py` pin that side.
"""
from __future__ import annotations

import time

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
        import asyncio
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


def test_stale_uncertain_is_discarded_automatically(fixture_monitor):
    batch, item, studio = uncertain_attempt(fixture_monitor)
    # Silent for longer than the automatic window: nobody is coming back for it.
    item["last_progress_at"] = time.time() - broker.UNCERTAIN_AUTO_DISCARD_SECONDS - 60
    assert studio["machine"] in broker.busy_machines()

    discarded = broker.discard_stale_uncertain()

    assert f"{batch['id']}:0" in discarded
    assert item["state"] == "cancelled"
    assert item["error_code"] == "DISCARDED_UNCERTAIN"
    assert "automatic" in item["error"].lower()
    assert studio["machine"] not in broker.busy_machines()


def test_fresh_uncertain_is_left_for_reconciliation(fixture_monitor):
    batch, item, studio = uncertain_attempt(fixture_monitor)
    item["last_progress_at"] = time.time()
    assert broker.discard_stale_uncertain() == []
    assert item["state"] == "uncertain"
    assert studio["machine"] in broker.busy_machines()


class TestLateResultTests:
    """A result arriving after automatic cleanup is discarded, never adopted."""

    def _client(self):
        import httpx

        class Client:
            async def get(self, *args, **kwargs):
                return httpx.Response(200, json={"ok": True},
                                      request=httpx.Request("GET", "http://127.0.0.1/"))

            async def post(self, *args, **kwargs):
                return httpx.Response(200, json={"ok": True},
                                      request=httpx.Request("POST", "http://127.0.0.1/"))

        return Client()

    def _job(self, batch):
        return {"id": "worker-1", "state": "done", "progress": 1.0,
                "params": {"client_request_id": f"studiohub:{batch['id']}:0"},
                "output_path": "/nonexistent/late.wav", "generated_at": time.time(),
                "bytes": 10, "sha256": "a" * 64, "runtime_s": 1.0,
                "output_url": "/api/generate/jobs/worker-1/audio"}

    def test_a_late_result_after_cleanup_is_not_adopted(self, fixture_monitor, monkeypatch):
        import asyncio
        batch, item, studio = uncertain_attempt(fixture_monitor, modality="voice")
        item["last_progress_at"] = time.time() - broker.UNCERTAIN_AUTO_DISCARD_SECONDS - 60
        assert broker.discard_stale_uncertain() == [f"{batch['id']}:0"]

        async def cached(*args, **kwargs):
            return None

        monkeypatch.setattr(broker, "_cache_voice_artifact_metadata", cached)
        asyncio.run(broker._record_worker_success(
            self._client(), batch, item, studio, self._job(batch),
            {"repo": batch["model"], "client_request_id": f"studiohub:{batch['id']}:0"},
            time.time(), recovery=True))
        assert item["state"] == "cancelled"
        assert item["error_code"] == "DISCARDED_UNCERTAIN"
        assert "discarded" in item["error"].lower()

    def test_a_newer_accepted_result_is_not_overwritten(self, fixture_monitor, monkeypatch):
        import asyncio
        batch, item, studio = uncertain_attempt(fixture_monitor, modality="voice")
        item["last_progress_at"] = time.time() - broker.UNCERTAIN_AUTO_DISCARD_SECONDS - 60
        broker.discard_stale_uncertain()
        item.update(state="done", asset_id="newer-asset", error_code=None)
        item.pop("error", None)

        async def cached(*args, **kwargs):
            return None

        monkeypatch.setattr(broker, "_cache_voice_artifact_metadata", cached)
        asyncio.run(broker._record_worker_success(
            self._client(), batch, item, studio, self._job(batch),
            {"repo": batch["model"], "client_request_id": f"studiohub:{batch['id']}:0"},
            time.time(), recovery=True))
        assert item["state"] == "done" and item["asset_id"] == "newer-asset"

    def test_no_late_result_leaves_the_recorded_reason(self, fixture_monitor):
        batch, item, _ = uncertain_attempt(fixture_monitor)
        item["last_progress_at"] = time.time() - broker.UNCERTAIN_AUTO_DISCARD_SECONDS - 60
        broker.discard_stale_uncertain()
        assert item["state"] == "cancelled"
        assert item["recovery"]["phase"] == "discarded"
        assert "late result is discarded" in item["error"].lower()

