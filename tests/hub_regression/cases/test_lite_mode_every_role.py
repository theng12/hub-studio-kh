"""Lite Mode works the same on every role (owner, 2026-10-04).

A "work Mac" is set up like any other Mac - standalone, controller or node - and Lite Mode is the one
extra tick. Every generation job it runs locally is governed, wherever it came from: a controller's
own-Mac share of its group's work, a standalone's own queue, a node's own queue (its peer work is
governed at the peer door, `test_workstation_mode.PeerSurface`). The scheduler treats this Mac like any
other Lite Mode Mac: standing by -> the group's work goes to its nodes; a model it does not run in Lite
Mode -> another Mac; its own free-before-start floor. With nowhere else to go, work waits - plainly.
With Lite Mode off, nothing changes.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from components.hub import workstation
from components.hub.backend import broker, control_plane, peers, transcription_jobs

LITE = "mlx-community/VoxCPM2-4bit"           # a Lite Mode model
FULL = "mlx-community/VoxCPM2-8bit"           # not one
ROLES = ("standalone", "controller", "agent")


def _studio(machine: str) -> dict:
    return {"id": f"voice@{machine}", "modality": "voice", "machine": machine,
            "local": machine == "local"}


class _Monitor:
    def __init__(self, machines):
        self.registry = [_studio(m) for m in machines]
        self.status = {s["id"]: {"status": "up"} for s in self.registry}

    def cached_catalog_entries(self, model, *, modality=None):
        return [{"studio": s, "entry": {"repo": model, "cached": True}} for s in self.registry]


@pytest.fixture
def lite(monkeypatch, tmp_path):
    """This Mac's Lite Mode in a throwaway file; nobody at the keyboard unless a test says so."""
    monkeypatch.setattr(workstation, "SETTINGS_FILE", tmp_path / "workstation.json")
    monkeypatch.setattr(workstation, "hid_idle_seconds", lambda: 999.0)
    monkeypatch.setattr(workstation, "screen_locked", lambda: False)
    workstation.reset_for_tests()
    yield workstation
    workstation.reset_for_tests()


@pytest.fixture
def group(fixture_monitor, monkeypatch):
    def make(machines, role="controller"):
        monkeypatch.setattr(control_plane, "load_settings",
                            lambda: {**control_plane.defaults(), "role": role})
        broker.configure_monitor(_Monitor(machines))
        for machine in machines:
            if machine != "local":
                peers._cache[machine] = (time.time(), {
                    "reachable": True, "auth": True, "status": "connected",
                    "workstation": {"state": "off", "enabled": False}})
    yield make
    broker.configure_monitor(fixture_monitor)


def _machines(modality="voice", model=LITE) -> list[str]:
    return [s["machine"] for s in broker._eligible_studios(modality, "pool", model)]


@pytest.mark.parametrize("role", ROLES)
def test_off_changes_nothing_on_any_role(group, lite, monkeypatch, role):
    group(["local", "n1"], role)
    workstation.save({"step_aside": True, "start_free_gb": 3.5, "quiet_hours": ["00:00-23:59"]})
    monkeypatch.setattr(workstation, "hid_idle_seconds", lambda: 0.1)
    assert _machines(model=FULL) == ["local", "n1"]
    assert peers.dispatch_ready(_studio("local"))
    assert broker._with_machine_floor({"min_free": 2.8}, _studio("local")) == {"min_free": 2.8}
    assert peers.lite_mode_wait(FULL) is None


@pytest.mark.parametrize("role", ROLES)
def test_every_rule_governs_this_macs_own_work_on_any_role(group, lite, monkeypatch, role):
    group(["local", "n1"], role)
    workstation.save({"enabled": True})
    assert _machines() == ["local", "n1"], "working: a Lite model runs here"
    assert _machines(model=FULL) == ["n1"], "a model it does not run in Lite Mode goes elsewhere"
    assert broker.pool_machines("voice", FULL) == {"n1"}
    workstation.save({"start_free_gb": 3.5})
    assert broker._with_machine_floor({"min_free": 2.8}, _studio("local")) == {"min_free": 3.5}

    workstation.save({"step_aside": True})
    monkeypatch.setattr(workstation, "hid_idle_seconds", lambda: 0.1)
    assert _machines() == ["n1"], "someone is using it: stands by"
    assert "someone is using this Mac" in peers.lite_mode_wait(LITE)
    monkeypatch.setattr(workstation, "hid_idle_seconds", lambda: 999.0)
    assert _machines() == ["local", "n1"], "left alone for the resume window: works again"

    workstation.save({"step_aside": False, "idle_only": True})
    monkeypatch.setattr(workstation, "hid_idle_seconds", lambda: 60.0)
    assert _machines() == ["n1"], "only when idle: 1 minute is not 3"
    monkeypatch.setattr(workstation, "screen_locked", lambda: True)
    assert _machines() == ["local", "n1"], "a locked screen is nobody at it"

    workstation.save({"idle_only": False, "quiet_hours": ["00:00-23:59"]})
    assert _machines() == ["n1"], "quiet hours"


def test_a_controller_standing_by_sends_its_groups_work_to_its_nodes(group, lite, monkeypatch):
    group(["local", "n1", "n2"], "controller")
    workstation.save({"enabled": True, "step_aside": True})
    monkeypatch.setattr(workstation, "hid_idle_seconds", lambda: 0.1)
    assert _machines() == ["n1", "n2"]
    assert broker.pool_machines("voice", LITE) == {"n1", "n2"}
    assert not broker.every_capable_mac_paused("voice", LITE), "standing by is busy, not paused"
    assert peers.standing_by("local"), "the capacity snapshot counts its own Mac busy"


def test_a_standalone_that_cannot_start_waits_plainly(group, lite, monkeypatch):
    group(["local"], "standalone")
    workstation.save({"enabled": True, "step_aside": True})
    monkeypatch.setattr(workstation, "hid_idle_seconds", lambda: 0.1)
    submitted = broker.submit_batch({"modality": "voice", "model": LITE, "items": [{"text": "one"}]})
    batch = broker.batches[submitted["batch_id"]]

    async def a_few_passes():
        task = asyncio.create_task(broker._dispatch_loop())
        for _ in range(10):
            await asyncio.sleep(0.02)
        task.cancel()

    asyncio.run(a_few_passes())
    assert batch["items"][0]["state"] == "queued", "nothing fails"
    assert batch["governor_note"].startswith("Waiting: Lite Mode")
    assert "someone is using this Mac" in batch["governor_note"]


def test_a_subtitle_queue_names_lite_mode_as_the_wait(fixture_monitor, lite):
    workstation.save({"enabled": True, "quiet_hours": ["00:00-23:59"]})
    fixture_monitor.status["subtitle"] = {"status": "up"}
    reasons: list[str] = []
    eligible = asyncio.run(transcription_jobs._eligible_studios(
        fixture_monitor, "mlx-community/whisper-large-v3-turbo", reasons=reasons))
    assert eligible == []
    assert any("Lite Mode" in reason and "quiet hours" in reason for reason in reasons), reasons


# ── a job this Mac hands back under critical memory pressure ─────────────────

class LocalWorker:
    """This Mac's own image worker: accepts the job; Lite Mode then cancels it."""

    async def post(self, url, **kwargs):
        job = {"id": "job-1", "state": "queued", "worker_id": "image", "machine_id": "local"}
        return httpx.Response(200, json={"job": job}, request=httpx.Request("POST", url))

    async def get(self, url, **kwargs):
        job = {"id": "job-1", "state": "cancelled", "worker_id": "image", "machine_id": "local"}
        return httpx.Response(200, json={"job": job}, request=httpx.Request("GET", url))


def _local_attempt(fixture_monitor, monkeypatch) -> tuple[dict, dict, dict]:
    monkeypatch.setattr(broker, "POLL_S", 0.0)

    async def noop(*args, **kwargs):
        return None
    monkeypatch.setattr(broker, "_post_item_webhook", noop)
    monkeypatch.setattr(broker, "_maybe_finish", noop)
    monkeypatch.setattr(broker, "_eligible_studios", lambda *args, **kwargs: [])
    studio = next(s for s in fixture_monitor.registry if s["id"] == "image")
    batch = {"id": "lite-yield", "modality": "image", "model": "fixture/model", "shared_params": {},
             "cancelled": False, "routing": "pool", "created_at": time.time(), "items": []}
    item = {"index": 0, "prompt": "proof", "params": {}, "seed": -1, "state": "running",
            "tries": 1, "studio": studio["id"], "studio_job_id": None, "retry_at": None, "error": None}
    batch["items"] = [item]
    broker.batches[batch["id"]] = batch
    broker._busy.add(studio["id"])
    workstation._yielded[("image", "job-1")] = {"reason": "pressure", "at": time.monotonic(),
                                                 "payload": None}
    return batch, item, studio


@pytest.mark.parametrize("role", ("standalone", "agent"))
def test_a_handed_back_own_job_waits_here_when_nothing_else_can_take_it(
        fixture_monitor, lite, monkeypatch, role):
    monkeypatch.setattr(control_plane, "load_settings",
                        lambda: {**control_plane.defaults(), "role": role})
    batch, item, studio = _local_attempt(fixture_monitor, monkeypatch)
    asyncio.run(broker._run_item(LocalWorker(), batch, item, studio))
    assert item["state"] == "queued", item.get("error")
    assert item["error"].startswith("waiting: WORKSTATION_YIELDED")
    assert item["tries"] == 0, "a hand-back is not an attempt"
    assert item["avoid_machines"]["local"] > time.time()


def test_a_controllers_handed_back_own_job_goes_to_the_gateway(fixture_monitor, lite, monkeypatch):
    monkeypatch.setattr(control_plane, "load_settings",
                        lambda: {**control_plane.defaults(), "role": "controller"})
    batch, item, studio = _local_attempt(fixture_monitor, monkeypatch)
    asyncio.run(broker._run_item(LocalWorker(), batch, item, studio))
    assert (item["state"], item["error_code"], item["tries"]) == ("error", "WORKSTATION_YIELDED", 0)


def test_the_lite_mode_loop_sees_this_queues_own_running_jobs(fixture_monitor):
    batch = {"id": "own", "modality": "image", "items": [
        {"state": "running", "studio": "image", "studio_job_id": "j1"},
        {"state": "running", "studio": "image@n1", "studio_job_id": "j2"},   # another Mac's
        {"state": "queued", "studio": "image", "studio_job_id": None},
        {"state": "done", "studio": "image", "studio_job_id": "j0"}]}
    broker.batches[batch["id"]] = batch
    assert broker.local_running_jobs() == [("image", "j1")]


# ── "Today: N jobs" counts every job this Mac ran in Lite Mode, its own queue's too ─

@pytest.mark.parametrize("role", ROLES)
def test_todays_count_includes_this_macs_own_queue(group, lite, monkeypatch, role):
    """It counted only a controller's jobs at the peer door, so a standalone (or a controller's own
    Mac) in Lite Mode said "Today: 0 jobs" after a day of work."""
    group(["local"], role)
    workstation.save({"enabled": True})
    started: list[str] = []

    async def ran(client, batch, item, studio):
        started.append(studio["machine"])
    monkeypatch.setattr(broker, "_run_item", ran)
    monkeypatch.setattr(broker, "admission_refusal", lambda batch: None)

    async def entry(studio, model):
        return {"repo": model, "cached": True}
    monkeypatch.setattr(broker, "_catalog_entry", entry)
    monkeypatch.setattr(broker, "_admission_requirements", lambda model, entry: None)
    monkeypatch.setattr(broker, "is_cached", lambda entry: True)
    monkeypatch.setattr(broker, "_model_block_note", lambda *a, **k: None)
    broker.submit_batch({"modality": "voice", "model": LITE, "items": [{"text": "one"}]})

    async def a_few_passes():
        task = asyncio.create_task(broker._dispatch_loop())
        for _ in range(10):
            await asyncio.sleep(0.02)
        task.cancel()
    asyncio.run(a_few_passes())
    batch = next(iter(broker.batches.values()))
    assert started == ["local"], (batch.get("governor_note"), batch["items"][0]["state"])
    assert workstation.status()["jobs_today"] == 1


def test_a_job_sent_to_another_mac_or_run_with_lite_mode_off_is_not_counted(lite):
    workstation.save({"enabled": True})
    peers.note_dispatch(_studio("n1"))
    assert workstation.status()["jobs_today"] == 0, "the node counts its own"
    peers.note_dispatch(_studio("local"))
    assert workstation.status()["jobs_today"] == 1
    workstation.save({"enabled": False})
    peers.note_dispatch(_studio("local"))
    workstation.save({"enabled": True})
    assert workstation.status()["jobs_today"] == 1
