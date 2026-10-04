"""A group whose every capable Mac is paused never keeps work (owner, 2026-10-04: never strand work).

Measured 2026-10-04: group 1500 had all five Macs paused; the Gateway sent it 21-25 TTS jobs, the Hub
accepted them and they sat queued ~7,000 s while 8 GB Macs elsewhere idled - pauses were only read when
choosing a Mac, never at accept. Now a new batch is refused `503 NO_AVAILABLE_MACHINE`, and queued
items are handed back with that code (nothing charged) the moment a pause leaves no capable Mac.
A Mac that is offline but not paused still counts: it may come back.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from components.hub.backend import broker, registry

MODEL = "fixture/tts-8gb"


def _studio(machine: str) -> dict:
    return {"id": f"voice@{machine}", "modality": "voice", "machine": machine, "local": False}


class _Monitor:
    def __init__(self, machines, *, offline=()):
        self.registry = [_studio(m) for m in machines]
        self.status = {s["id"]: {"status": "down" if s["machine"] in offline else "up"}
                       for s in self.registry}

    def cached_catalog_entries(self, model, *, modality=None):
        return [{"studio": s, "entry": {"repo": MODEL, "cached": True}}
                for s in self.registry if model == MODEL and modality in (None, "voice")]


@pytest.fixture
def group(fixture_monitor, monkeypatch):
    def make(machines, **kwargs):
        broker.configure_monitor(_Monitor(machines, **kwargs))
        return machines
    yield make
    broker.configure_monitor(fixture_monitor)


def test_only_pauses_and_drains_count_as_not_allowed(group):
    group(["n1", "n2"], offline=["n2"])
    registry.set_machine_enabled("n1", False)
    assert not broker.every_capable_mac_paused("voice", MODEL), "an offline, unpaused Mac may come back"
    registry.set_machine_enabled("n2", False)
    assert broker.every_capable_mac_paused("voice", MODEL)
    registry.set_machine_enabled("n2", True)
    broker.set_maintenance("voice@n2", True)
    assert broker.every_capable_mac_paused("voice", MODEL), "a drained Studio takes nothing either"
    assert not broker.every_capable_mac_paused("voice", "fixture/unknown"), "no known capable Mac: no refusal"


def test_queued_items_are_handed_back_typed_and_running_ones_finish(group, monkeypatch):
    group(["n1"])
    registry.set_machine_enabled("n1", False)
    hooks = []

    async def hook(_client, _b, item, **_kwargs):
        hooks.append(item["index"])

    monkeypatch.setattr(broker, "_post_item_webhook", hook)
    batch = {"id": "paused-1", "modality": "voice", "model": MODEL, "routing": "pool",
             "created_at": time.time(), "cancelled": False, "item_webhook": "http://gw/hook",
             "items": [{"index": 0, "state": "running", "tries": 1},
                       {"index": 1, "state": "queued", "tries": 1, "retry_at": time.time() + 60},
                       {"index": 2, "state": "queued", "tries": 0}]}
    asyncio.run(broker._hand_back_queued(None, batch))
    assert batch["items"][0]["state"] == "running"
    for item in batch["items"][1:]:
        assert item["state"] == "error" and item["error_code"] == broker.NO_AVAILABLE_MACHINE
        assert item["error"] == broker.NO_AVAILABLE_MACHINE_TEXT and item["retry_at"] is None
    assert [i["tries"] for i in batch["items"]] == [1, 1, 0], "nothing is charged"
    assert hooks == [1, 2]
    assert broker.public_item(batch, batch["items"][2])["error_code"] == broker.NO_AVAILABLE_MACHINE


def test_the_dispatcher_hands_back_when_a_pause_leaves_no_capable_mac(group, monkeypatch):
    group(["n1"])
    submitted = broker.submit_batch({"modality": "voice", "model": MODEL,
                                     "items": [{"text": "one"}, {"text": "two"}]})
    batch = broker.batches[submitted["batch_id"]]
    registry.set_machine_enabled("n1", False)

    async def one_pass():
        task = asyncio.create_task(broker._dispatch_loop())
        for _ in range(50):
            await asyncio.sleep(0.02)
            if all(item["state"] == "error" for item in batch["items"]):
                break
        task.cancel()

    asyncio.run(one_pass())
    assert [item.get("error_code") for item in batch["items"]] == [broker.NO_AVAILABLE_MACHINE] * 2


def test_a_new_batch_is_refused_503_when_every_capable_mac_is_paused(group, monkeypatch):
    from fastapi.testclient import TestClient
    from components.hub import standalone

    monkeypatch.setattr(standalone, "_submission_readiness", lambda envelope, monitor: None)
    app = standalone.create_app(start_background=False)
    with TestClient(app) as client:
        group(["n1", "n2"])
        registry.set_machine_enabled("n1", False)
        registry.set_machine_enabled("n2", False)
        headers = {"Authorization": "Bearer st02-regression-token"}
        envelope = {"modality": "voice", "model": MODEL, "items": [{"text": "hello"}]}
        response = client.post("/api/hub/jobs", json=envelope, headers=headers)
        assert response.status_code == 503, response.text
        assert response.json() == {"error": broker.NO_AVAILABLE_MACHINE_TEXT,
                                   "code": "NO_AVAILABLE_MACHINE",
                                   "detail": broker.NO_AVAILABLE_MACHINE_TEXT}
        assert not broker.batches, "nothing was queued"
        registry.set_machine_enabled("n2", True)
        accepted = client.post("/api/hub/jobs", json=envelope, headers=headers)
        assert accepted.status_code == 200, accepted.text


# ── transcription: its own queue, the same rule through the same helper ──────────────────────────
WHISPER = "mlx-community/whisper-large-v3-turbo"


def _up_whisper(monitor):
    """The packaged `subtitle` worker, up, with Whisper cached, as the broker's monitor."""
    broker.configure_monitor(monitor)
    studio = next(row for row in monitor.registry if row["id"] == "subtitle")
    monitor.status[studio["id"]] = {"status": "up", "last_seen": time.time(), "last_checked": time.time()}
    monitor._transcribe_cache[studio["id"]] = (time.time(), {
        "available": True, "models": [{"repo": WHISPER, "cached": True}]})
    return studio


def _transcription_batch(states):
    from components.hub.backend import transcription_jobs
    batch = {"id": "stt-paused", "idempotency_key": "k", "created_at": time.time(),
             "updated_at": time.time(), "finished_at": None, "cancelled": False,
             "modality": "transcription", "model": WHISPER, "routing": "pool",
             "items": [{"index": i, "item_id": f"c{i}", "filename": f"c{i}.wav", "state": state,
                        "tries": 0, "studio": None, "error": None, "avoid_machines": {}}
                       for i, state in enumerate(states)]}
    transcription_jobs.batches[batch["id"]] = batch
    return batch


def test_transcription_queued_items_are_handed_back_when_every_capable_mac_is_paused(fixture_monitor):
    from components.hub.backend import transcription_jobs
    machine = _up_whisper(fixture_monitor)["machine"]
    batch = _transcription_batch(["running", "queued"])
    assert not broker.every_capable_mac_paused("transcription", WHISPER), "an unpaused Mac keeps its work"
    registry.set_machine_enabled(machine, False)
    try:
        assert broker.every_capable_mac_paused("transcription", WHISPER)
        asyncio.run(transcription_jobs.dispatch_once(fixture_monitor))
    finally:
        registry.set_machine_enabled(machine, True)
    assert batch["items"][0]["state"] == "running", "running work finishes"
    handed = transcription_jobs.summary(batch)["items"][1]
    assert (handed["state"], handed["error_code"], handed["error"], handed["tries"]) == (
        "error", broker.NO_AVAILABLE_MACHINE, broker.NO_AVAILABLE_MACHINE_TEXT, 0)
    transcription_jobs.retry_failed_item(batch["id"], 1)
    assert batch["items"][1]["error_code"] is None, "a retried item does not carry the old code"


def test_a_new_transcription_batch_is_refused_503_when_every_capable_mac_is_paused(fixture_monitor, monkeypatch):
    from fastapi.testclient import TestClient
    from components.hub import standalone
    from components.hub.backend import transcription_jobs

    monkeypatch.setattr(standalone, "_submission_readiness", lambda envelope, monitor: None)
    app = standalone.create_app(start_background=False)
    headers = {"Authorization": "Bearer st02-regression-token"}
    form = {"model": WHISPER, "item_ids": "job-1"}
    files = {"files": ("a.wav", b"RIFF....WAVEfmt ", "audio/wav")}
    with TestClient(app) as client:
        machine = _up_whisper(fixture_monitor)["machine"]  # after create_app, which installs its own
        registry.set_machine_enabled(machine, False)
        try:
            response = client.post("/api/hub/transcription/jobs", data=form, files=files, headers=headers)
        finally:
            registry.set_machine_enabled(machine, True)
        assert response.status_code == 503, response.text
        assert response.json()["detail"] == {"error": broker.NO_AVAILABLE_MACHINE_TEXT,
                                             "code": "NO_AVAILABLE_MACHINE",
                                             "detail": broker.NO_AVAILABLE_MACHINE_TEXT}
        assert not transcription_jobs.batches, "nothing was queued"
        accepted = client.post("/api/hub/transcription/jobs", data=form, files=files, headers=headers)
        assert accepted.status_code == 200, accepted.text
