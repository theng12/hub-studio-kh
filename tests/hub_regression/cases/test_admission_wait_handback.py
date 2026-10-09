"""An admission wait on the Hub's own free-memory floor is bounded like three refusals.

The owner's rule (2026-10-01): a Mac that cannot give a job the memory it needs does not keep it for ever;
after about four minutes the Hub hands it back typed `INSUFFICIENT_MEMORY`, with the "needs X, has Y" sentence,
so the Gateway can route it to another Mac (a pinned job fails with the same, clear, error). The dispatcher's
`wait` decision (`_memory_gate`) used to defer silently with no clock at all.
"""
import asyncio

from components.hub.backend import broker

MEM = {"min_total": 8.0, "min_free": 3.0}


def _studio(fixture_monitor):
    return next(s for s in fixture_monitor.registry if s["id"] == "image")


def test_the_clock_runs_only_while_the_shortage_is_the_macs_own(fixture_monitor, monkeypatch):
    studio = _studio(fixture_monitor)
    host = {"total_gb": 8.0, "available_gb": 1.0}
    monkeypatch.setattr(broker, "_host_for_studio", lambda s: host)
    item = {}
    note = "waiting for memory: needs ~3.0GB, ~1.0GB free"
    assert broker._admission_wait_expired(item, studio, MEM, note, 1000.0) is False
    assert item["admission_wait_started_at"] == 1000.0
    assert broker._admission_wait_expired(item, studio, MEM, note, 1000.0 + broker.ADMISSION_WAIT_HANDBACK_S - 1) is False
    assert broker._admission_wait_expired(item, studio, MEM, note, 1000.0 + broker.ADMISSION_WAIT_HANDBACK_S) is True
    assert 200 < broker.ADMISSION_WAIT_HANDBACK_S <= 300, "about four minutes"
    # Memory comes back: the clock resets.
    host["available_gb"] = 5.0
    assert broker._admission_wait_expired(item, studio, MEM, note, 5000.0) is False
    assert "admission_wait_started_at" not in item
    # Our own work running on that Mac is ordinary queueing, never a hand-back.
    host["available_gb"] = 1.0
    broker._busy.add("voice")
    assert broker._admission_wait_expired(item, studio, MEM, note, 6000.0) is False
    assert broker._admission_wait_expired(item, studio, MEM, note, 60000.0) is False
    broker._busy.discard("voice")
    # A wait that is not the memory wait (a busy sibling) is not counted either.
    assert broker._admission_wait_expired(item, studio, MEM, "waiting for active sibling work on this machine", 70000.0) is False
    assert "admission_wait_started_at" not in item


def test_the_wait_is_one_minute_when_another_mac_offers_the_model(fixture_monitor, monkeypatch):
    """2026-10-09 review: 304 job-minutes went to 240 s waits on 8 GB Macs whose free memory never
    came back, while other Macs had the model. With somewhere else to go it is handed back after
    ADMISSION_WAIT_HANDBACK_ELSEWHERE_S; the only Mac that can run it keeps the four minutes."""
    studio = _studio(fixture_monitor)
    monkeypatch.setattr(broker, "_host_for_studio", lambda s: {"total_gb": 8.0, "available_gb": 1.0})
    note = "waiting for memory: needs ~3.0GB, ~1.0GB free"
    batch = {"modality": "image", "model": "model-a", "routing": "pool"}
    seen = []

    def pool(modality, model, routing="pool"):
        seen.append((modality, model, routing))
        return machines
    monkeypatch.setattr(broker, "pool_machines", pool)
    assert broker.ADMISSION_WAIT_HANDBACK_ELSEWHERE_S == 60.0

    machines = {"local", "node-b"}                     # another Mac offers it
    item = {}
    assert broker._admission_wait_expired(item, studio, MEM, note, 1000.0, batch) is False
    assert broker._admission_wait_expired(item, studio, MEM, note, 1059.0, batch) is False
    assert broker._admission_wait_expired(item, studio, MEM, note, 1060.0, batch) is True
    assert seen[-1] == ("image", "model-a", "pool")

    machines = {"local"}                               # this Mac is the only one
    item = {}
    assert broker._admission_wait_expired(item, studio, MEM, note, 1000.0, batch) is False
    assert broker._admission_wait_expired(item, studio, MEM, note, 1060.0, batch) is False
    assert broker._admission_wait_expired(item, studio, MEM, note, 1000.0 + broker.ADMISSION_WAIT_HANDBACK_S - 1, batch) is False
    assert broker._admission_wait_expired(item, studio, MEM, note, 1000.0 + broker.ADMISSION_WAIT_HANDBACK_S, batch) is True

    machines = set()                                   # pool unknown: nobody else either
    item = {}
    assert broker._admission_wait_expired(item, studio, MEM, note, 1000.0, batch) is False
    assert broker._admission_wait_expired(item, studio, MEM, note, 1100.0, batch) is False
    assert "(it waited 1 minute)" in broker._insufficient_memory_text(batch, studio, note, waited_s=60.0)


def test_a_job_that_waits_past_the_bound_is_handed_back_with_the_sentence(fixture_monitor, monkeypatch):
    submitted = broker.submit_batch({"modality": "image", "model": "model-a", "items": [{"prompt": "hello"}]})
    batch = broker.batches[submitted["batch_id"]]
    item = batch["items"][0]
    monkeypatch.setattr(broker, "ADMISSION_WAIT_HANDBACK_S", 0.4)
    fixture_monitor.status["image"]["status"] = "up"

    async def entry(_studio, _model):
        return {"repo": "model-a", "cached": True, "installed": True}

    async def no_release(_client, _studio):
        return {"attempted": 1, "released": 0, "busy": [], "failed": []}

    async def no_hook(*_args, **_kwargs):
        return None

    class Client:
        async def post(self, *_args, **_kwargs):
            raise AssertionError("nothing may be sent to a worker for a job that is handed back")

    monkeypatch.setattr(broker, "_catalog_entry", entry)
    monkeypatch.setattr(broker, "is_cached", lambda e: True)
    monkeypatch.setattr(broker, "_model_block_note", lambda *a, **k: None)
    monkeypatch.setattr(broker, "_admission_requirements", lambda model, e: dict(MEM))
    monkeypatch.setattr(broker, "_host_for_studio", lambda s: {"total_gb": 8.0, "available_gb": 1.0})
    monkeypatch.setattr(broker, "release_idle_siblings", no_release)
    monkeypatch.setattr(broker, "_post_item_webhook", no_hook)
    monkeypatch.setattr(broker, "_maybe_finish", no_hook)

    async def drive():
        task = asyncio.create_task(broker._dispatch_loop())
        try:
            for _ in range(60):
                broker._wakeup.set()
                await asyncio.sleep(0.1)
                if item["state"] != "queued":
                    break
            return item["state"]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    state = asyncio.run(drive())
    assert state == "error", (state, item, batch.get("governor_note"))
    assert item["error_code"] == "INSUFFICIENT_MEMORY"
    assert "needs about 3.0 GB free and has about 1.0 GB free" in item["error"]
    assert "waited" in item["error"] and "model-a" in item["error"]
    assert item["tries"] == 0, "nothing was attempted"
    assert "admission_wait_started_at" not in item and "_memory_handback" not in item
    assert broker.machine_protection_snapshot() == {}, "a memory shortage is not a broken machine"
