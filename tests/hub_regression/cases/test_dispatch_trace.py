"""Bounded dispatch evidence names the first gate without changing scheduling."""
from __future__ import annotations

import asyncio
import copy
import json
from unittest.mock import AsyncMock

import pytest

from components.hub.backend import broker, ledger


class StopDispatch(BaseException):
    pass


class ImmediateWake:
    def clear(self) -> None:
        pass

    async def wait(self) -> None:
        return None


def _batch() -> dict:
    return {
        "id": "dispatch-trace-proof",
        "modality": "music",
        "model": "mlx-community/YuE-s1-7B-anneal-en-cot-mlx",
        "routing": "pool",
        "created_at": 1.0,
        "cancelled": False,
        "items": [{
            "index": 0,
            "state": "queued",
            "retry_at": None,
            "tries": 0,
            "studio": None,
            "params": {},
        }],
    }


def test_first_eligibility_gate_names_busy_local_and_unready_peer(
    fixture_monitor, monkeypatch
):
    local = next(s for s in fixture_monitor.registry if s["id"] == "music")
    peer = {**local, "id": "music@1603", "machine": "1603", "local": False}
    monkeypatch.setattr(fixture_monitor, "registry", [local, peer])
    fixture_monitor.status = {
        local["id"]: {"status": "up"},
        peer["id"]: {"status": "up"},
    }
    broker._busy.add(local["id"])
    monkeypatch.setattr(broker, "busy_machines", lambda: set())
    monkeypatch.setattr(broker.routing_rule, "allows", lambda *_: True)
    monkeypatch.setattr(broker.peers, "dispatch_ready", lambda studio, *_: studio is local)
    monkeypatch.setattr(broker.peers, "lite_mode_allows", lambda *_: True)
    monkeypatch.setattr(broker, "machine_enabled", lambda *_: True)
    monkeypatch.setattr(broker, "studio_enabled", lambda *_: True)
    monkeypatch.setattr(broker, "_machine_blocked", lambda *_: False)

    trace: list[dict] = []
    eligible = broker._eligible_studios(
        "music", "pool", _batch()["model"], trace=trace
    )

    assert eligible == []
    assert trace == [
        {"machine": "local", "studio": "music", "gate": "studio_busy"},
        {
            "machine": "1603",
            "studio": "music@1603",
            "gate": "peer_not_dispatch_ready",
        },
    ]


def test_blocked_pass_retries_failed_write_records_only_actual_gate_and_keeps_snapshot(
    fixture_monitor, monkeypatch
):
    batch = _batch()
    batch["items"][0]["admission_wait_started_at"] = 0.0
    studio = next(s for s in fixture_monitor.registry if s["id"] == "music")
    fixture_monitor.status[studio["id"]]["status"] = "up"
    broker.batches[batch["id"]] = batch
    writes: list[dict] = []
    scans = 0

    def queued_batches():
        nonlocal scans
        scans += 1
        if scans == 7:
            raise StopDispatch()
        return [(1, batch)]

    catalog_calls = 0

    async def catalog_entry(*_args):
        nonlocal catalog_calls
        catalog_calls += 1
        if catalog_calls <= 2:
            return None
        return {"repo": batch["model"], "cache": {"state": "cached"}}

    memory_decisions = iter(("wait", "wait", "wait", "run"))
    wait_expirations = iter((False, False, True))

    async def prepare_memory(*_args):
        decision = next(memory_decisions)
        return decision, "fixture memory gate"

    def eligible(*_args, trace=None, **_kwargs):
        return [studio]

    def create_task(coroutine):
        coroutine.close()
        return None

    monkeypatch.setattr(broker, "_queued_batches", queued_batches)
    monkeypatch.setattr(broker, "_watch_restored_attempts", AsyncMock())
    monkeypatch.setattr(broker, "evict_finished_batches", lambda: 0)
    monkeypatch.setattr(broker, "discard_stale_uncertain", lambda **_: 0)
    monkeypatch.setattr(broker, "_expire_genstudio_batch", lambda *_: False)
    monkeypatch.setattr(broker, "every_capable_mac_paused", lambda *_: False)
    monkeypatch.setattr(broker, "admission_refusal", lambda *_: None)
    monkeypatch.setattr(broker, "_eligible_studios", eligible)
    monkeypatch.setattr(broker, "_supports_genstudio_voice_evidence", lambda *_: True)
    monkeypatch.setattr(broker, "_catalog_entry", catalog_entry)
    monkeypatch.setattr(broker, "_catalog_matches_genstudio_revision", lambda *_: True)
    monkeypatch.setattr(broker, "_model_block_note", lambda *_: None)
    monkeypatch.setattr(broker, "is_cached", lambda *_: True)
    monkeypatch.setattr(broker, "_admission_requirements", lambda *_: {"required": 1})
    monkeypatch.setattr(broker, "prepare_machine_memory", prepare_memory)
    monkeypatch.setattr(
        broker, "_admission_wait_expired", lambda *_: next(wait_expirations)
    )
    monkeypatch.setattr(broker, "busy_machines", lambda: set())
    monkeypatch.setattr(
        broker,
        "higher_priority_work_waiting",
        AsyncMock(side_effect=(True, False)),
    )
    monkeypatch.setattr(broker.execution_identity, "lease_expired", lambda *_: False)
    monkeypatch.setattr(broker, "machine_enabled", lambda *_: True)
    monkeypatch.setattr(broker, "studio_enabled", lambda *_: True)
    monkeypatch.setattr(broker, "_item_allows_studio", lambda *_: True)
    monkeypatch.setattr(broker, "_wait_for_faster_mac", lambda *_: False)
    monkeypatch.setattr(broker.peers, "is_local_studio", lambda *_: False)
    monkeypatch.setattr(broker.peers, "dispatch_generation", lambda *_: "generation")
    monkeypatch.setattr(broker.peers, "dispatch_ready", lambda *_: True)
    monkeypatch.setattr(broker.peers, "note_dispatch", lambda *_: None)
    save_attempts = 0

    def save_batch(value):
        nonlocal save_attempts
        save_attempts += 1
        if save_attempts == 1:
            raise OSError("fixture trace write failed")
        writes.append(copy.deepcopy(value))

    monkeypatch.setattr(broker.ledger, "save_batch", save_batch)
    monkeypatch.setattr(broker, "_wakeup", ImmediateWake())
    monkeypatch.setattr(broker.asyncio, "create_task", create_task)
    monkeypatch.setattr(broker.asyncio, "sleep", AsyncMock())

    with pytest.raises(StopDispatch):
        asyncio.run(broker._dispatch_loop())

    assert save_attempts == 5
    assert len(writes) == 4
    assert [write["dispatch_trace"]["candidates"][0]["gate"] for write in writes[:3]] == [
        "catalog_missing",
        "memory_wait",
        "higher_priority_work",
    ]
    final = writes[-1]["dispatch_trace"]
    assert final["candidates"][0]["gate"] == "higher_priority_work"
    assert final["selected"] == {
        "at": final["selected"]["at"],
        "item_index": 0,
        "machine": "local",
        "studio": "music",
    }


def test_trace_is_bounded_safe_and_survives_terminal_reload(reset):
    batch = _batch()
    rows = [
        {
            "machine": f"machine-{index}",
            "studio": f"music@machine-{index}",
            "gate": "catalog_missing",
        }
        for index in range(80)
    ]
    rows[0] = {
        "machine": "https://user:secret@example.test/private",
        "studio": "/Users/owner/.ssh/id_ed25519",
        "gate": "catalog_missing",
        "detail": "prompt and credential must never be stored",
    }

    assert broker._store_blocked_dispatch_trace(
        batch, batch["items"], rows, now=10.0
    ) is True
    trace = batch["dispatch_trace"]
    encoded = json.dumps(trace).encode()
    assert len(trace["candidates"]) <= 65
    assert len(encoded) <= 32 * 1024
    assert trace["truncated"] is True
    assert set(trace) == {
        "version", "blocked_at", "queued_items", "candidates", "truncated"
    }
    assert all(set(row) == {"machine", "studio", "gate"} for row in trace["candidates"])
    assert not any(
        forbidden in encoded.decode()
        for forbidden in ("https://", "/Users/", "secret", "credential", "prompt")
    )

    batch["items"][0].update(state="done", runtime_s=1.0, finished_at=11.0)
    batch["finished_at"] = 11.0
    ledger.save_batch(batch)
    restored = ledger.load_batch(batch["id"])

    assert restored is not None
    assert broker.batch_summary(restored)["dispatch_trace"] == trace
