"""Qwen prefers the fastest free Mac inside a site (owner decision 2026-10-09: "Prefer fast Macs for Qwen").

The Gateway (0.2.156) ranks sites by expected finish time for any `qwen-image` model; the Hub picks
the Mac inside the site, so it applies the same rule with the same numbers (20-step medians: M2 Max
228 s, M4 605 s, M2 812 s, M1 949 s). A slower free Mac takes a Qwen item only when it would finish
before a busy faster Mac would after its current work. Every other model keeps `_placement_key`, and
a Mac with Image switched off or put below another function is never waited for.
"""
from __future__ import annotations

import pytest

from components.hub.backend import broker

QWEN = "mlx-community/Qwen-Image-2.1-mflux-q4"
FLUX = "black-forest-labs/FLUX.2-klein"
HOSTS = {
    "max": {"chip": "Apple M2 Max", "total_gb": 68.72},
    "m4": {"chip": "Apple M4", "total_gb": 17.18},
    "m1": {"chip": "Apple M1", "total_gb": 17.18},
}


class _Monitor:
    def __init__(self, studios):
        self.registry = studios
        self.status = {studio["id"]: {"status": "up"} for studio in studios}

    def cached_catalog_entries(self, model, modality=None):
        return [{"studio": s, "entry": {"repo": model, "cached": True}}
                for s in self.registry if modality in (None, s["modality"])]

    scheduling_catalog_entries = cached_catalog_entries


def _studio(machine: str) -> dict:
    return {"id": f"image@{machine}", "modality": "image", "machine": machine,
            "host": "127.0.0.1", "port": 1, "local": False,
            "runtime_identity": {"id": "runtime", "compatible": True}}


def _batch(batch_id: str, model: str = QWEN, created_at: float = 1.0, items: int = 1) -> dict:
    return {"id": batch_id, "created_at": created_at, "cancelled": False, "modality": "image",
            "model": model, "routing": "pool", "shared_params": {},
            "items": [{"index": i, "state": "queued", "retry_at": None, "params": {},
                       "tries": 0, "studio": None, "studio_job_id": None} for i in range(items)]}


@pytest.fixture
def fleet(monkeypatch):
    studios = [_studio(m) for m in ("m1", "m4", "max")]
    state = {"busy": set(), "enabled": {}, "priority": {}, "samples": []}
    monkeypatch.setattr(broker, "_monitor", lambda: _Monitor(studios))
    monkeypatch.setattr(broker, "_busy", state["busy"])
    monkeypatch.setattr(broker, "batches", {})
    monkeypatch.setattr(broker, "busy_machines", lambda: set())
    monkeypatch.setattr(broker.peers, "dispatch_ready", lambda studio, *a: True)
    monkeypatch.setattr(broker.peers, "lite_mode_allows", lambda studio, model: True)
    monkeypatch.setattr(broker, "machine_enabled", lambda machine: True)
    monkeypatch.setattr(broker, "studio_enabled",
                        lambda machine, sid: state["enabled"].get(machine, True))
    monkeypatch.setattr(broker, "_machine_blocked", lambda machine: False)
    monkeypatch.setattr(broker, "_can_run", lambda studio, entry, model, routing: True)
    monkeypatch.setattr(broker, "_published_memory_tier_gb", lambda model, modality: None)
    monkeypatch.setattr(broker, "_host_for_studio", lambda s: HOSTS[s["machine"]])
    monkeypatch.setattr(broker, "priority_order", lambda m: state["priority"].get(m, []))
    monkeypatch.setattr(broker, "_speed_samples", lambda model: state["samples"])
    monkeypatch.setattr(broker, "admission_refusal", lambda b: None)
    monkeypatch.setattr(broker, "memory_admission_dispatchable", lambda *a: True)
    return {s["machine"]: s for s in studios} | {"state": state}


def _order(model):
    return [s["machine"] for s in broker._eligible_studios("image", "pool", model)]


def test_the_fastest_free_mac_comes_first(fleet):
    assert _order(QWEN) == ["max", "m4", "m1"]
    assert _order("ddalcu/Qwen-Image-2.1-MLX-Serve-4bit")[0] == "max"


def test_this_hubs_own_history_beats_the_chip_table(fleet, monkeypatch):
    fleet["state"]["samples"][:] = [("m1", 200.0), ("m1", 220.0)]
    assert _order(QWEN)[0] == "m1"
    # A class median covers a Mac of the same class with no history of its own.
    fleet["state"]["samples"][:] = [("other-m4", 150.0)]
    assert broker._mac_seconds("image", QWEN)("m4") == 605.0, "an unknown Mac is not a class"
    # Its own median first, then its class's: two M1 16 GB Macs, one measured slow.
    monkeypatch.setitem(HOSTS, "m4", HOSTS["m1"])
    fleet["state"]["samples"][:] = [("m1", 200.0), ("m4", 1000.0)]
    seconds = broker._mac_seconds("image", QWEN)
    assert (seconds("m1"), seconds("m4"), seconds("max")) == (200.0, 1000.0, 228.0)
    fleet["state"]["samples"][:] = [("m1", 200.0), ("m1", 300.0)]
    assert broker._mac_seconds("image", QWEN)("m4") == 250.0


def test_other_models_keep_the_smallest_machine_first(fleet):
    assert _order(FLUX) == ["m1", "m4", "max"]
    fleet["state"]["busy"].add("image@max")
    batch = _batch("flux", FLUX)
    assert not broker._wait_for_faster_mac(batch, batch["items"][0], fleet["m1"])


def test_a_slow_mac_waits_for_a_busy_fast_one_that_finishes_sooner(fleet):
    """The Gateway's example: This Mac busy and nothing queued gives 342 s, so an idle M1
    (949 s) and an idle M4 (605 s) leave the job for it."""
    fleet["state"]["busy"].add("image@max")
    batch = _batch("one")
    broker.batches["one"] = batch
    item = batch["items"][0]
    assert broker._wait_for_faster_mac(batch, item, fleet["m1"])
    assert broker._wait_for_faster_mac(batch, item, fleet["m4"])
    assert "faster Mac" in broker._no_candidate_note(batch, [item], fleet["m1"])
    # The fast Mac itself never waits, and an idle fast Mac is not waited for.
    assert not broker._wait_for_faster_mac(batch, item, fleet["max"])
    fleet["state"]["busy"].clear()
    assert not broker._wait_for_faster_mac(batch, item, fleet["m1"])


def test_a_slow_mac_takes_the_job_when_it_finishes_sooner(fleet):
    """Three queued ahead: This Mac gives 342 + 3 x 228 = 1,026 s, so the M1 (949 s) takes it."""
    fleet["state"]["busy"].add("image@max")
    broker.batches["ahead"] = _batch("ahead", created_at=1.0, items=3)
    late = broker.batches["late"] = _batch("late", created_at=2.0)
    assert not broker._wait_for_faster_mac(late, late["items"][0], fleet["m1"])
    # With one ahead (570 s) the M4 (605 s) still waits; with two (798 s) it takes it.
    broker.batches["ahead"]["items"][1:] = []
    assert broker._wait_for_faster_mac(late, late["items"][0], fleet["m4"])
    broker.batches["ahead"]["items"].append(dict(broker.batches["ahead"]["items"][0], index=1))
    assert not broker._wait_for_faster_mac(late, late["items"][0], fleet["m4"])


def test_function_priority_and_image_switch_still_win(fleet):
    """0.6.156/0.6.157 owner settings: a Mac that puts another function above Image, or has
    Image switched off, is never waited for, so it is never handed extra Image work."""
    fleet["state"]["busy"].add("image@max")
    batch = _batch("one")
    broker.batches["one"] = batch
    item = batch["items"][0]
    fleet["state"]["priority"]["max"] = ["chat", "image"]
    assert not broker._wait_for_faster_mac(batch, item, fleet["m1"])
    fleet["state"]["priority"]["max"] = ["image", "chat"]
    assert broker._wait_for_faster_mac(batch, item, fleet["m1"])
    fleet["state"]["enabled"]["max"] = False
    assert not broker._wait_for_faster_mac(batch, item, fleet["m1"])
    fleet["state"]["busy"].clear()
    assert _order(QWEN) == ["m4", "m1"]


def test_a_waiting_slow_mac_does_not_hold_up_its_other_functions(fleet):
    """The per-Mac ranker must not count a Qwen item the M1 would leave for This Mac as the
    M1's next work, or it would keep lower-ranked work off an idle M1."""
    fleet["state"]["busy"].add("image@max")
    broker.batches["one"] = _batch("one")
    assert broker._broker_dispatchable_work("m1") == []
    fleet["state"]["busy"].clear()
    assert [w[2] for w in broker._broker_dispatchable_work("m1")] == ["broker:one"]


def test_history_is_read_from_this_hubs_ledger(monkeypatch):
    from components.hub.backend import ledger
    studios = [_studio("m1"), _studio("max")]
    monkeypatch.setattr(broker, "_monitor", lambda: _Monitor(studios))
    monkeypatch.setattr(broker, "_speed_cache", {})
    for batch_id, model, studio, runtime in (("q1", QWEN, "image@m1", 900.0),
                                             ("q2", QWEN, "image@max", 230.0),
                                             ("f1", FLUX, "image@m1", 90.0)):
        batch = _batch(batch_id, model)
        batch["items"][0].update(state="done", studio=studio, runtime_s=runtime)
        ledger.save_batch(batch)
    assert sorted(broker._speed_samples(QWEN)) == [("m1", 900.0), ("max", 230.0)]
    assert "f1" not in {b["id"] for b in ledger.load_finished_batches(payload_like="%qwen-image%")}
