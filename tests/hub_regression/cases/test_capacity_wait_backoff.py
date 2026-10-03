"""A worker that keeps refusing for memory is retried with backoff, then handed back with a typed error.

A dying 8 GB worker was requeued every ~92 s for 45 minutes: a fixed 30 s retry plus the 90 s avoid
window, a `release_idle_siblings` (unloading the Mac's other studios) on every cycle, and
`last_progress_at` refreshed by each requeue so the job never looked stalled. Now it backs off, says
so (`capacity_waiting_s`), and after CAPACITY_HANDBACK_AFTER refusals the item errors with
`INSUFFICIENT_MEMORY` and a sentence naming the Mac, the memory it needs and what it has, so the
Gateway can send the job to another Mac.
"""
import asyncio

import httpx

from components.hub.backend import broker


def _setup(fixture_monitor, monkeypatch):
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
    batch["created_at"] = 10_000.0
    return batch, item, studio, Client(), releases, clock


def test_repeated_memory_refusals_back_off_then_hand_the_job_back(fixture_monitor, monkeypatch):
    batch, item, studio, client, releases, clock = _setup(fixture_monitor, monkeypatch)
    monkeypatch.setattr(broker, "_insufficient_memory_text",
                        lambda b, s, decision, n: "Mac mini 1101 needs about 3.2 GB free and has about 3.0 GB free")
    delays = []
    for attempt in range(broker.CAPACITY_HANDBACK_AFTER - 1):
        item.update(state="running", studio="image", tries=1)
        asyncio.run(broker._run_item(client, batch, item, studio))
        assert item["state"] == "queued"
        assert item["tries"] == 0, "a capacity wait must not consume a generation attempt"
        delays.append(item["retry_at"] - clock[0])
        assert item["avoid_machines"]["local"] - clock[0] >= max(broker.FAILED_WORKER_AVOID_S, delays[-1])
        clock[0] += 1000.0
    assert delays == [30.0, 60.0]
    assert item["error"].startswith("waiting for capacity:")
    summary = broker.batch_summary(batch)
    assert summary["capacity_refusals"] == 2 and summary["capacity_waiting_s"] == 2000.0

    # the next refusal hands it back
    item.update(state="running", studio="image", tries=1)
    asyncio.run(broker._run_item(client, batch, item, studio))
    assert item["state"] == "error"
    assert item["error_code"] == "INSUFFICIENT_MEMORY"
    assert "needs about 3.2 GB free" in item["error"]
    assert item["retry_at"] is None
    assert "capacity_failures" not in item, "a finished item carries no capacity-wait state"
    assert len(releases) == broker.CAPACITY_HANDBACK_AFTER - 1, "the hand-back must not unload siblings again"
    assert broker.machine_protection_snapshot() == {}, "a memory refusal is not a broken machine"
    public = broker.public_item(batch, item)
    assert public["error_code"] == "INSUFFICIENT_MEMORY"


def test_the_handback_sentence_names_the_mac_and_the_numbers(fixture_monitor, monkeypatch):
    batch, item, studio, client, releases, clock = _setup(fixture_monitor, monkeypatch)
    gate = "waiting for memory: needs ~3.2GB, ~3.0GB free"
    text = broker._insufficient_memory_text(batch, studio, gate, 3)
    assert "3.2 GB free" in text and "3.0 GB free" in text and "model-a" in text and "3 times" in text
    assert "does not have enough free memory" in broker._insufficient_memory_text(
        batch, studio, "MemoryGuardError: not enough memory", 3)


def test_the_handback_reports_the_numbers_the_worker_refused_on(fixture_monitor, monkeypatch):
    """2026-10-04: 116 of 272 hand-backs read "needs about 3.1 GB free and has about 4.5 GB free".
    The worker had refused on its own fresh reading; the sentence printed the Hub's snapshot of
    the Mac, taken at another moment. The sentence now carries the refusal's own numbers."""
    batch, item, studio, _client, releases, clock = _setup(fixture_monitor, monkeypatch)
    monkeypatch.setattr(broker, "_host_for_studio", lambda s: {"total_gb": 8.0, "available_gb": 4.5})

    class Worker:
        async def post(self, url, **kwargs):
            return httpx.Response(503, json={"detail": (
                "MemoryGuardError: Memory guard paused this job: 3.2 GB free is needed, but only "
                "2.1 GB is available. Close memory-heavy apps or choose a smaller model; the job "
                "was not sent to the engine.")})

    for _attempt in range(broker.CAPACITY_HANDBACK_AFTER):
        item.update(state="running", studio="image", tries=1)
        asyncio.run(broker._run_item(Worker(), batch, item, studio))
        clock[0] += 1000.0
    assert item["error_code"] == "INSUFFICIENT_MEMORY"
    assert "needs about 3.2 GB free and has about 2.1 GB free" in item["error"], item["error"]
    assert "4.5" not in item["error"]
