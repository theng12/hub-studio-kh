"""A group accepts a model any of its Macs can run, not only one on the controller's own disk.

Measured 2026-10-04: group 1500 refused Fish S2 Pro 4-bit with 409 MODEL_NOT_READY while its node
1504 had it installed and ready - `_submission_readiness` read only the controller's own package
row. Now a joined Mac with the model cached, runtime-ready and within its RAM floor is enough, and
the broker sends the work to that Mac. A group where no Mac has it still refuses.
"""
from __future__ import annotations

import asyncio

import pytest

from components.hub import standalone
from components.hub.backend import broker

MODEL = "majentik/fishaudio-s2-pro-MLX-4bit"
CATALOG = {"voice-mlx:fish": {"engine": "voice-mlx", "model_id": MODEL}}
ROW = {"repo": MODEL, "cache": {"state": "cached"}, "runtime_ready": True, "available": True}


def _studio(machine: str) -> dict:
    local = machine == "local"
    return {"id": "voice" if local else f"voice@{machine}", "modality": "voice", "machine": machine,
            "local": local, "host": "127.0.0.1", "port": 1,
            "runtime_identity": {"id": "runtime", "compatible": True}}


class _Monitor:
    def __init__(self, cached_on: set[str]):
        self.registry = [_studio("local"), _studio("studio-mac-1504")]
        self.status = {studio["id"]: {"status": "up"} for studio in self.registry}
        self.rows = {studio["id"]: dict(ROW, cache={"state": "cached" if studio["machine"] in cached_on
                                                         else "not_installed"})
                     for studio in self.registry}
        self._catalog_cache, self._transcribe_cache = {}, {}

    def cached_catalog_entries(self, model, *, modality=None):
        return [{"studio": s, "entry": self.rows[s["id"]]} for s in self.registry
                if model == MODEL and modality in (None, "voice")]

    scheduling_catalog_entries = cached_catalog_entries

    async def scheduling_catalog(self, studio):
        return {"models": [self.rows[studio["id"]]]}


@pytest.fixture
def group(fixture_monitor, monkeypatch):
    def make(cached_on: set[str], controller_ready: bool):
        monitor = _Monitor(cached_on)
        broker.configure_monitor(monitor)
        monkeypatch.setattr(standalone, "_model_catalog", lambda: CATALOG)
        monkeypatch.setattr(standalone, "_model_state", lambda *a, **k: {
            "dispatch_state": "eligible" if controller_ready else "ineligible"})
        monkeypatch.setattr(broker, "_host_for_studio",
                            lambda studio: {"total_gb": 17.18, "available_gb": 12.0})
        monkeypatch.setattr(broker.peers, "dispatch_ready", lambda studio, generation=None: True)
        monkeypatch.setattr(broker.peers, "dispatch_generation", lambda studio: None)
        broker.configure_admission_guard(lambda batch: standalone._submission_readiness(batch, monitor))
        return monitor
    yield make
    broker.configure_admission_guard(None)
    broker.configure_monitor(fixture_monitor)


def _dispatched_to(monkeypatch) -> list[str]:
    """Submit one item, run the real dispatch loop briefly, and say which Studio it went to."""
    started: list[str] = []

    async def run_item(_client, _batch, item, studio):
        started.append(studio["id"])
        item["state"] = "done"
        broker._busy.discard(studio["id"])

    monkeypatch.setattr(broker, "_run_item", run_item)
    broker.submit_batch({"modality": "voice", "model": MODEL, "items": [{"text": "hello"}]})

    async def one_pass():
        broker._wakeup = asyncio.Event()
        task = asyncio.create_task(broker._dispatch_loop())
        for _ in range(50):
            await asyncio.sleep(0.02)
            if started:
                break
        task.cancel()

    asyncio.run(one_pass())
    return started


def test_a_model_only_a_joined_mac_has_is_accepted_and_runs_there(group, monkeypatch):
    monitor = group({"studio-mac-1504"}, controller_ready=False)
    envelope = {"modality": "voice", "model": MODEL}
    assert standalone._submission_readiness(envelope, monitor) is None
    assert _dispatched_to(monkeypatch) == ["voice@studio-mac-1504"]


def test_a_model_on_the_controller_still_works(group, monkeypatch):
    monitor = group({"local"}, controller_ready=True)
    assert standalone._submission_readiness({"modality": "voice", "model": MODEL}, monitor) is None
    assert _dispatched_to(monkeypatch) == ["voice"]


def test_a_group_where_no_mac_has_it_is_refused(group):
    monitor = group(set(), controller_ready=False)
    refusal = standalone._submission_readiness({"modality": "voice", "model": MODEL}, monitor)
    assert refusal["code"] == "MODEL_NOT_READY"


def test_a_joined_mac_below_the_ram_floor_or_not_runtime_ready_does_not_count(group, monkeypatch):
    monitor = group({"studio-mac-1504"}, controller_ready=False)
    monkeypatch.setattr(broker, "_host_for_studio",
                        lambda studio: {"total_gb": 8.59, "available_gb": 4.0})
    envelope = {"modality": "voice", "model": MODEL}
    assert standalone._submission_readiness(envelope, monitor)["code"] == "MODEL_NOT_READY"
    monkeypatch.setattr(broker, "_host_for_studio",
                        lambda studio: {"total_gb": 17.18, "available_gb": 12.0})
    monitor.rows["voice@studio-mac-1504"]["runtime_ready"] = False
    assert standalone._submission_readiness(envelope, monitor)["code"] == "MODEL_NOT_READY"
