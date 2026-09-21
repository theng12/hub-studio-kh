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
    # The worker and recovery identity are gone on purpose: nothing may adopt this attempt back.
    for gone in ("studio", "studio_job_id", "worker_id", "worker_accepted_params",
                 "model_revision", "runtime_revision", "machine", "recovery"):
        assert gone not in item, f"{gone} must be cleared so nothing reconciles the attempt"
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
    assert "studio" not in saved and "studio_job_id" not in saved


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
