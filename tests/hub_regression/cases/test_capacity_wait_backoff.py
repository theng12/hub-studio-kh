"""A worker that keeps refusing for memory is retried with backoff and is not shown as progressing.

A dying 8 GB worker was requeued every ~92 s for 45 minutes: a fixed 30 s retry plus the 90 s avoid
window, a `release_idle_siblings` (unloading the Mac's other studios) on every cycle, and
`last_progress_at` refreshed by each requeue so the job never looked stalled. The wait stays
unbounded - the owner wants the work queued, not failed - but it now backs off and tells the truth.
"""
import asyncio

import httpx

from components.hub.backend import broker


def test_repeated_memory_refusals_back_off_and_do_not_count_as_progress(fixture_monitor, monkeypatch):
    submitted = broker.submit_batch({"modality": "image", "model": "model-a", "items": [{"prompt": "hello"}]})
    batch = broker.batches[submitted["batch_id"]]
    item = batch["items"][0]
    studio = next(s for s in fixture_monitor.registry if s["id"] == "image")
    releases = []

    class Client:
        async def post(self, url, **kwargs):
            return httpx.Response(503, json={"detail": "MemoryGuardError: not enough memory to load this model right now"})

    async def no_finish(*_args, **_kwargs):
        return None

    async def release(_client, _studio):
        releases.append(1)
        return {"attempted": 1, "released": 0, "busy": [], "failed": []}

    monkeypatch.setattr(broker, "_post_item_webhook", no_finish)
    monkeypatch.setattr(broker, "_maybe_finish", no_finish)
    monkeypatch.setattr(broker, "release_idle_siblings", release)
    clock = [10_000.0]
    monkeypatch.setattr(broker.time, "time", lambda: clock[0])

    delays = []
    batch["created_at"] = 10_000.0
    for attempt in range(6):
        item.update(state="running", studio="image", tries=1, last_progress_at=5.0)
        asyncio.run(broker._run_item(Client(), batch, item, studio))
        assert item["state"] == "queued"
        assert item["tries"] == 0, "a capacity wait must not consume a generation attempt"
        delays.append(item["retry_at"] - clock[0])
        clock[0] += 1000.0

    assert delays == [30.0, 60.0, 120.0, 120.0, 120.0, 120.0]
    assert len(releases) == broker.CAPACITY_RELEASE_ATTEMPTS, \
        "a repeating refusal kept unloading the Mac's other studios"
    assert item["capacity_failures"] == 6
    # The attempts re-stamp the item's own progress clock; the capacity clock is not reset by them,
    # so after the stall window the batch reads as stalled with its first refusal as the start.
    summary = broker.batch_summary(batch)
    assert summary["capacity_refusals"] == 6
    assert summary["capacity_waiting_s"] == clock[0] - 10_000.0
    assert summary["stalled"] is True
    assert summary["stalled_seconds"] >= summary["capacity_waiting_s"] > 0
    assert item["error"].startswith("waiting for capacity:")
