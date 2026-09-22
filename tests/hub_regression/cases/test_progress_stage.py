"""The studio's phase label reaches the public job item."""
from __future__ import annotations

from components.hub.backend import broker


def test_progress_stage_is_recorded_with_progress():
    item: dict = {}
    broker._record_worker_progress(item, 0.45, "DiT sampling")
    assert item["progress"] == 0.45
    assert item["progress_stage"] == "DiT sampling"
    assert "last_progress_at" in item


def test_stage_is_clipped_and_optional():
    item: dict = {}
    broker._record_worker_progress(item, 0.2, "x" * 500)
    assert len(item["progress_stage"]) == 120
    # A studio that reports no label leaves the previous one untouched.
    broker._record_worker_progress(item, 0.3)
    assert item["progress_stage"] == "x" * 120


def test_public_item_carries_the_stage():
    batch = {"id": "stage-proof", "modality": "image", "model": "fixture/model",
             "items": []}
    item = {"index": 0, "state": "running", "progress": 0.8,
            "progress_stage": "SAME decoder"}
    batch["items"] = [item]
    public = broker.public_item(batch, item)
    assert public["progress_stage"] == "SAME decoder"
    assert public["progress"] == 0.8
