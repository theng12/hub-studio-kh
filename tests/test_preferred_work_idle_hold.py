"""Preferred-work idle hold: isolated deterministic checks, no model inference or live state.

Fake-clock proofs of the four owner-facing behaviours, against the real modules:

1. OFF is byte-for-byte the previous arbitration (no gate, no hold state).
2. Preferred demand wins over the ranked order while eligible (and over a third function).
3. The hold opens only on a real terminal completion and bounds expiry against a fake clock,
   including clock jumps backwards/forwards and a restart (new monotonic epoch).
4. Validation and persistence: bounds 1-3600, default 300, malformed reads as off, switching
   or disabling clears the stored window; the wire API validates and does not partially mutate.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

PROOF = r'''
import asyncio
import json
import os
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from components import preferred_hold_clock
from components.hub.backend import broker, chat_jobs, preferred_hold, registry
from components.hub.backend import peers as peers_module
from components.hub.backend.vocabulary import NODE_MODALITIES

# Import `standalone` first, before the fixture binds its own monitor below. Its module-level
# `app = create_app()` runs `broker.configure_monitor(<real StudioMonitor>)` once; a later lane
# call (broker._begin_preferred_reservation -> _preferred_reservation_budget) imports it lazily
# anyway. Importing it here, at the top, lets the fixture's `broker.configure_monitor(monitor)`
# calls below win, so the completion hooks that read `_monitor()` see the fixture's roster.
from components.hub import standalone  # noqa: E402,F401

# --- vocabulary contract: generation choices derive from real lanes, render excluded ---------
assert "render" not in preferred_hold.GENERATION_CHOICES
assert "transcription" not in preferred_hold.GENERATION_CHOICES
assert "embeddings" not in preferred_hold.GENERATION_CHOICES
assert "vision" not in preferred_hold.GENERATION_CHOICES
assert {"chat", "image", "voice", "music", "video"} <= preferred_hold.GENERATION_CHOICES
assert preferred_hold.GENERATION_CHOICES <= NODE_MODALITIES
# The choices are exactly the broker's generation lanes (its MODALITY table plus the
# Chat lane, which owns its own job module) minus render — so a new broker generation
# lane must be classified here deliberately, and a non-generating one can never sneak in.
broker_generation = (set(broker.MODALITY) | {"chat"}) - {"render"}
assert preferred_hold.GENERATION_CHOICES == broker_generation & set(NODE_MODALITIES), \
    (preferred_hold.GENERATION_CHOICES, broker_generation)

# --- clock arithmetic: fake clock, same boot, restart, jumps ---------------------------------
policy = {"modality": "chat", "idle_seconds": 300}
BASE_STEADY = 10_000.0
BASE_WALL = 1_000_000.0
hold = {"ended_wall": BASE_WALL, "boot_steady": BASE_STEADY}

# Same boot: full window right after the finish, 0 after expiry.
assert preferred_hold.hold_remaining_s(
    policy, hold, now_wall=BASE_WALL + 5, now_steady=BASE_STEADY + 5) == 295.0
assert preferred_hold.hold_remaining_s(
    policy, hold, now_wall=BASE_WALL + 300, now_steady=BASE_STEADY + 300) == 0.0
assert preferred_hold.hold_remaining_s(
    policy, hold, now_wall=BASE_WALL + 301, now_steady=BASE_STEADY + 301) == 0.0

# Wall clock jumped backwards: clamped to at most the full window, never negative.
assert preferred_hold.hold_remaining_s(
    policy, hold, now_wall=BASE_WALL - 500, now_steady=BASE_STEADY + 5) == 295.0

# Restart: a fresh monotonic epoch reads the old stamp as far negative, so the wall clock
# answers; the remaining time is still bounded by the window.
assert preferred_hold.hold_remaining_s(
    policy, hold, now_wall=BASE_WALL + 60, now_steady=42.0) == 240.0
assert preferred_hold.hold_remaining_s(
    policy, hold, now_wall=BASE_WALL + 400, now_steady=42.0) == 0.0

# Missing/malformed hold or policy answers 0 (never holds without evidence).
assert preferred_hold.hold_remaining_s(policy, None, now_wall=BASE_WALL,
                                       now_steady=BASE_STEADY) == 0.0
assert preferred_hold.hold_remaining_s(None, hold, now_wall=BASE_WALL,
                                       now_steady=BASE_STEADY) == 0.0
assert preferred_hold.hold_remaining_s(
    {"modality": "chat", "idle_seconds": 0}, hold,
    now_wall=BASE_WALL, now_steady=BASE_STEADY) == 0.0

# --- persistence: bounds, default, malformed, switch clears ----------------------------------
with tempfile.TemporaryDirectory(prefix="studiohub-preferred-hold-") as state:
    os.environ["STUDIOHUB_DATA_DIR"] = state
    registry._flags_cache = None

    # OFF default: no preference anywhere.
    assert registry.preferred_work("local") is None
    assert preferred_hold.active(registry.preferred_work("local")) is None

    # Bounds and validation.
    for bad in (0, -5, 3601, True, 2.5, "300"):
        try:
            registry.set_preferred_work("local", "chat", bad, enabled=True)
            raise AssertionError(f"idle_seconds={bad!r} was accepted")
        except ValueError:
            pass
    try:
        registry.set_preferred_work("local", "render", 300, enabled=True)
        raise AssertionError("render was accepted as a preference")
    except ValueError:
        pass
    try:
        registry.set_preferred_work("local", "not-a-modality", 300, enabled=True)
        raise AssertionError("unknown modality was accepted")
    except ValueError:
        pass

    registry.set_preferred_work("local", "chat", 90, enabled=True)
    saved = registry.preferred_work("local")
    assert saved == {"enabled": True, "modality": "chat", "idle_seconds": 90}, saved

    # A hold may now be recorded and read back exactly.
    registry.set_preferred_hold("local", BASE_WALL, BASE_STEADY)
    assert registry.preferred_hold("local") == {"ended_wall": BASE_WALL,
                                                "boot_steady": BASE_STEADY}
    assert preferred_hold.hold_remaining_s(
        preferred_hold.active(registry.preferred_work("local")),
        registry.preferred_hold("local"),
        now_wall=BASE_WALL + 30, now_steady=BASE_STEADY + 30) == 60.0

    # Switching the preference clears the stored window: the hold belongs to the function
    # that opened it, and must not gate the new preference's rivals.
    registry.set_preferred_work("local", "image", 300, enabled=True)
    assert registry.preferred_hold("local") is None
    assert registry.preferred_work("local") == {"enabled": True, "modality": "image",
                                                "idle_seconds": 300}

    # Re-saving the same policy (a duration edit included) keeps a real hold running:
    # only a disable or an actual modality change retires it.
    registry.set_preferred_work("local", "image", 300, enabled=True)
    registry.set_preferred_hold("local", BASE_WALL, BASE_STEADY)
    registry.set_preferred_work("local", "image", 600, enabled=True)
    assert registry.preferred_hold("local") == {"ended_wall": BASE_WALL,
                                                "boot_steady": BASE_STEADY}
    assert registry.preferred_work("local")["idle_seconds"] == 600

    # Disabling clears the window too and keeps no stale modality behind an off flag.
    registry.set_preferred_hold("local", BASE_WALL, BASE_STEADY)
    registry.set_preferred_work("local", None, registry.PREFERRED_WORK_DEFAULT_S, enabled=False)
    assert registry.preferred_work("local") is None
    assert registry.preferred_hold("local") is None

    # Malformed persisted rows read as off, never as a hold.
    registry._flags_cache = {"local": {"preferred_work": {"modality": "chat"}}}
    assert preferred_hold.active(registry.preferred_work("local")) is None
    registry._flags_cache = {"local": {"preferred_work": "yes"}}
    assert registry.preferred_work("local") is None
    registry._flags_cache = {"local": {"preferred_hold": {"ended_wall": "x"}}}
    assert registry.preferred_hold("local") is None

    # A truthy non-dict PERSISTED preference is tolerated by the write path too: the next
    # save must not raise on it, and the malformed row can neither own nor pass on a window.
    registry.FLAGS_FILE.write_text(json.dumps({"local": {
        "preferred_work": "yes",
        "preferred_hold": {"ended_wall": BASE_WALL, "boot_steady": BASE_STEADY}}}))
    registry._flags_cache = None
    assert registry.preferred_work("local") is None, "a bad row reads as off"
    registry.set_preferred_work("local", "chat", 90, enabled=True)   # must not raise
    assert registry.preferred_work("local") == {"enabled": True, "modality": "chat",
                                                "idle_seconds": 90}
    assert registry.preferred_hold("local") is None, \
        "a malformed row cannot own or inherit a hold"
    # Restore the file's OFF baseline the API section below reads from disk.
    registry.set_preferred_work("local", None, registry.PREFERRED_WORK_DEFAULT_S,
                                enabled=False)
    assert registry.preferred_work("local") is None
    registry._flags_cache = None

    # --- scheduler arbitration: demand beats ranked order; hold gates third functions ----
    workers = [
        {"id": "chat", "title": "Chat", "modality": "chat", "machine": "local", "local": True},
        {"id": "image", "title": "Image", "modality": "image", "machine": "local", "local": True},
    ]
    monitor = SimpleNamespace(
        registry=workers,
        status={row["id"]: {"status": "up"} for row in workers},
        _client=None,
    )

    async def catalog(_studio):
        return {"models": [{"repo": "model", "cache": {"state": "cached"},
                            "runtime_ready": True, "available": True}]}

    monitor.scheduling_catalog = catalog
    monitor.cached_catalog_entries = lambda model, modality=None: [
        {"studio": row, "entry": {"repo": model, "cache": {"state": "cached"},
                                  "runtime_ready": True, "available": True}}
        for row in monitor.registry if modality in (None, row.get("modality"))]
    monitor.scheduling_catalog_entries = monitor.cached_catalog_entries
    broker.configure_monitor(monitor)
    broker.batches.clear()
    broker._busy.clear()
    broker._external_machine_leases.clear()

    registry._flags_cache = {"local": {
        "priority_order": ["image"],
        "preferred_work": {"enabled": True, "modality": "chat", "idle_seconds": 300},
        "preferred_hold": {"ended_wall": time.time(), "boot_steady": time.monotonic()},
    }}

    async def arbitration_proof():
        # Preferred demand beats the ranked order: with eligible queued Chat on this Mac
        # (has_queued_work True, candidate returned), Chat wins although Image outranks it.
        with patch.object(broker, "_broker_dispatchable_work",
                          return_value=[("image", 1.0, "broker:image")]), \
             patch.object(chat_jobs, "has_queued_work", return_value=True), \
             patch.object(chat_jobs, "dispatchable_candidate",
                          return_value=(2.0, "batch-1")):
            winner = await broker.next_work("local")
            assert winner is not None and winner[3] == "chat", winner

        # Without eligible Chat demand the ranked order answers unchanged (never starve).
        with patch.object(broker, "_broker_dispatchable_work",
                          return_value=[("image", 1.0, "broker:image")]), \
             patch.object(chat_jobs, "has_queued_work", return_value=False):
            winner = await broker.next_work("local")
            assert winner is not None and winner[3] == "image", winner

        # The hold gates the non-preferred candidate while the window runs (with the hold
        # open and no eligible Chat demand, a queued Image still waits for the window).
        with patch.object(broker, "_broker_dispatchable_work",
                          return_value=[("image", 1.0, "broker:image")]), \
             patch.object(chat_jobs, "has_queued_work", return_value=False):
            assert await broker.higher_priority_work_waiting(
                "local", "image", 1.0, "broker:image") is True
            # ... and never the preferred candidate itself: the hold arm refuses a different
            # function only, so the preferred function is never refused by its own hold.
            assert broker.candidate_loses_hold("local", "chat", "image") is False
            # With no eligible Chat demand the preferred candidate is not the winner, so the
            # legacy ranked arm answers unchanged (Image outranks Chat here): never starve.
            assert await broker.higher_priority_work_waiting(
                "local", "chat", 2.0, "chat:batch") is True
        # With eligible Chat demand the preferred candidate is the ranked winner, and the whole
        # gate clears it end to end (hold open, a real queued Chat candidate present).
        with patch.object(broker, "_broker_dispatchable_work",
                          return_value=[("image", 1.0, "broker:image")]), \
             patch.object(chat_jobs, "has_queued_work", return_value=True), \
             patch.object(chat_jobs, "dispatchable_candidate",
                          return_value=(2.0, "batch-1")):
            assert await broker.higher_priority_work_waiting(
                "local", "chat", 2.0, "chat:batch-1") is False

        # OFF (or expired hold): the ranked order answers exactly as before.
        registry._flags_cache = {"local": {"priority_order": ["image"]}}
        with patch.object(broker, "_broker_dispatchable_work",
                          return_value=[("image", 1.0, "broker:image")]), \
             patch.object(chat_jobs, "has_queued_work", return_value=False):
            winner = await broker.next_work("local")
            assert winner is not None and winner[3] == "image", winner
            assert await broker.higher_priority_work_waiting(
                "local", "image", 1.0, "broker:image") is False
            # The same Chat question the hold answered above is the legacy ranked answer alone
            # with the preference off: byte-for-byte the previous arbitration.
            assert await broker.higher_priority_work_waiting(
                "local", "chat", 2.0, "chat:batch") is True

        # Remote peer: the node owns the clock, so the controller rebuilds the hold from the
        # published remaining seconds — not from a raw stamp that never crosses the wire.
        peers_registry = [{"id": "chat", "title": "Chat", "modality": "chat",
                           "machine": "node-mac", "local": False}]
        peer_monitor = SimpleNamespace(
            registry=peers_registry,
            status={"chat": {"status": "up"}},
            _client=None,
        )
        peer_monitor.scheduling_catalog = catalog
        peer_monitor.cached_catalog_entries = lambda model, modality=None: [
            {"studio": row, "entry": {"repo": model, "cache": {"state": "cached"},
                                      "runtime_ready": True, "available": True}}
            for row in peer_monitor.registry if modality in (None, row.get("modality"))]
        peer_monitor.scheduling_catalog_entries = peer_monitor.cached_catalog_entries
        broker.configure_monitor(peer_monitor)
        broker.batches.clear()
        broker._busy.clear()
        broker._external_machine_leases.clear()
        registry._flags_cache = {"local": {"priority_order": ["image"]}}
        with patch.object(peers_module, "cached",
                          return_value={
                              "preferred_work": {"enabled": True, "modality": "chat",
                                                 "idle_seconds": 300},
                              "preferred_hold_remaining_s": 150.0}), \
             patch.object(broker, "_broker_dispatchable_work",
                          return_value=[("image", 1.0, "broker:image")]), \
             patch.object(chat_jobs, "has_queued_work", return_value=False):
            # The published remaining reads back through the controller's own clock math
            # (rebuilt stamps cost one monotonic read of jitter, so compare within a second).
            _left = broker.hold_remaining("node-mac")
            assert abs(_left - 150.0) < 1.0, _left
            # ... the ranked Image winner is gated while the window runs...
            assert await broker.higher_priority_work_waiting(
                "node-mac", "image", 1.0, "broker:image") is True
            # ... and never the preferred candidate itself: the hold arm refuses a different
            # function only, so the preferred function is never refused by its own hold.
            assert broker.candidate_loses_hold("node-mac", "chat", "image") is False
            # With no eligible Chat demand the preferred candidate is not the winner, so the
            # legacy ranked arm answers unchanged (Image is older at the same rank): never starve.
            assert await broker.higher_priority_work_waiting(
                "node-mac", "chat", 2.0, "chat:batch") is True
            # ... and once the node publishes expiry the ranked order resumes.
            with patch.object(peers_module, "cached",
                              return_value={
                                  "preferred_work": {"enabled": True, "modality": "chat",
                                                     "idle_seconds": 300},
                                  "preferred_hold_remaining_s": 0.0}):
                assert await broker.higher_priority_work_waiting(
                    "node-mac", "image", 1.0, "broker:image") is False

    asyncio.run(arbitration_proof())

    # --- completion note: a real terminal transition opens the hold ----------------------
    broker.configure_monitor(monitor)   # back to the local roster for the completion section
    broker.batches.clear()
    broker._busy.clear()
    broker._external_machine_leases.clear()
    registry._flags_cache = {"local": {
        "preferred_work": {"enabled": True, "modality": "chat", "idle_seconds": 300},
    }}
    broker._note_preferred_completion({"machine": "local"}, "chat")
    assert registry.preferred_hold("local") is not None
    # Non-preferred and remote completions write nothing.
    registry.set_preferred_hold("local", None, 0.0)
    broker._note_preferred_completion({"machine": "local"}, "image")
    assert registry.preferred_hold("local") is None
    broker._note_preferred_completion({"machine": "other-mac", "local": False}, "chat")
    assert registry.preferred_hold("local") is None
    # No policy: a completion writes nothing.
    registry._flags_cache = {"local": {}}
    broker._note_preferred_completion({"machine": "local"}, "chat")
    assert registry.preferred_hold("local") is None

    # --- API validation shapes (pure functions, no HTTP) --------------------------------
    from components.hub import operator_controls

    def policy_monitor():
        return SimpleNamespace(
            registry=[{"id": "chat", "title": "Chat", "modality": "chat",
                       "machine": "local", "local": True}],
            status={"chat": {"status": "up"}},
            _client=None,
        )

    mon = policy_monitor()
    broker.configure_monitor(mon)
    registry._flags_cache = None
    registry.set_preferred_work("local", None, registry.PREFERRED_WORK_DEFAULT_S,
                                enabled=False)   # start the API block from a clean OFF row
    answer = operator_controls.work_policy(mon)
    assert answer["preferred_choices"] == ["chat"], answer["preferred_choices"]
    assert answer["preferred_work"] == {"enabled": False, "modality": None,
                                        "idle_seconds": registry.PREFERRED_WORK_DEFAULT_S}
    assert answer["preferred_state"] == {"phase": "off", "remaining_s": None,
                                         "expires_at": None}

    saved = operator_controls.save_work_policy(mon, {
        "preferred_work": {"enabled": True, "modality": "chat", "idle_seconds": 120}})
    assert saved["preferred_work"] == {"enabled": True, "modality": "chat",
                                       "idle_seconds": 120}
    assert registry.preferred_work("local")["idle_seconds"] == 120

    from fastapi import HTTPException
    for bad_body in (
        {"preferred_work": {"enabled": True, "modality": "render", "idle_seconds": 60}},
        {"preferred_work": {"enabled": True, "modality": "chat", "idle_seconds": 0}},
        {"preferred_work": {"enabled": True, "modality": "chat", "idle_seconds": True}},
        {"preferred_work": {"enabled": True, "modality": "chat", "extra": 1}},
        {"preferred_work": {"enabled": False, "modality": "chat", "idle_seconds": 60}},
        {"preferred_work": {"enabled": "yes", "modality": "chat", "idle_seconds": 60}},
    ):
        try:
            operator_controls.save_work_policy(mon, bad_body)
            raise AssertionError(f"accepted {bad_body}")
        except HTTPException as error:
            assert error.status_code == 400, bad_body

    # Legacy bool-only adapter still lands on the same flag row.
    saved = operator_controls.save_work_policy(mon, {"chat_priority": True})
    assert saved["priority_order"] == ["chat"]

    # A combined save with an invalid order mutates nothing: the preference half is not
    # written because validation of the whole request happens before any persistence.
    registry._flags_cache = {"local": {}}   # no saved preference yet
    registry.set_preferred_work("local", "chat", 90, enabled=True)
    registry._flags_cache = None            # reload the real row from disk
    before = registry.preferred_work("local")
    for bad_combined in (
        {"priority_order": ["nope"], "preferred_work": {"enabled": False}},
        {"priority_order": "chat", "preferred_work": {"enabled": False}},
        {"priority_order": ["chat", "chat"],
         "preferred_work": {"enabled": False}},
    ):
        try:
            operator_controls.save_work_policy(mon, bad_combined)
            raise AssertionError(f"accepted {bad_combined}")
        except HTTPException as error:
            assert error.status_code == 400, bad_combined
    assert registry.preferred_work("local") == before, "combined save mutated flags"
    registry._flags_cache = None

    # The control for that order: a VALID combined save persists both halves.
    combined = operator_controls.save_work_policy(mon, {
        "priority_order": ["chat"],
        "preferred_work": {"enabled": True, "modality": "chat", "idle_seconds": 150}})
    assert registry.priority_order("local") == ["chat"]
    assert registry.preferred_work("local") == {"enabled": True, "modality": "chat",
                                                "idle_seconds": 150}
    assert combined["preferred_work"]["idle_seconds"] == 150
    registry._flags_cache = None

    # --- completion hooks: terminal evidence only, exactly once per transition -----------
    # These drive the REAL production lane bodies and their own finally blocks: deleting
    # or reordering the hook call inside chat_jobs._run_pack / broker._run_item fails the
    # assertions below. The lane's HTTP client is a fake object; no worker runs, no
    # network request leaves this process and no sleep is used. The broker's terminal
    # branch is its real Voice reference-conflict refusal; its unknown control is the real
    # uncertain-adoption early return through the same finally.
    broker.configure_monitor(mon)   # local roster (one local row is all the note needs)
    broker.batches.clear(); broker._busy.clear(); broker._external_machine_leases.clear()
    registry._flags_cache = {"local": {
        "preferred_work": {"enabled": True, "modality": "chat", "idle_seconds": 300}}}

    class RaisingChatClient:
        async def post(self, url, **kwargs):
            raise RuntimeError("worker unreachable in this source-level proof")

    class EmptyContentChatClient:
        async def post(self, url, **kwargs):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"choices": [{"message": {"content": ""}}]})

    def chat_pack(tries):
        pack = {"index": 0, "pack_id": "pack-1", "scene_ids": ["s1"], "results": {},
                "messages": [], "params": {}, "tries": tries, "state": "running",
                "error": None, "retry_at": None, "raw_responses": []}
        batch = {"id": "chat-hook-batch", "model": "model", "kind": "scene",
                 "messages": [], "created_at": time.time(), "idempotency_key": "k",
                 "packs": [pack], "cancelled": False, "params": {}}
        return batch, pack

    chat_studio = {"id": "chat", "machine": "local", "modality": "chat", "local": True,
                   "host": "127.0.0.1", "port": 1, "hub_token": "t"}
    chat_monitor = SimpleNamespace(registry=mon.registry, status=mon.status, _client=None)

    # A real terminal error out of the lane's own finally opens the window once.
    registry.set_preferred_hold("local", None, 0.0)
    batch, pack = chat_pack(tries=1)
    chat_monitor._client = RaisingChatClient()
    asyncio.run(chat_jobs._run_pack(chat_monitor, batch, pack, chat_studio, "owner"))
    assert pack["state"] == "error", pack
    assert registry.preferred_hold("local") is not None, \
        "a terminal pack must open the hold through the real finally"
    # A queued retry returns through the same finally and writes nothing.
    registry.set_preferred_hold("local", None, 0.0)
    batch, pack = chat_pack(tries=1)
    chat_monitor._client = EmptyContentChatClient()
    asyncio.run(chat_jobs._run_pack(chat_monitor, batch, pack, chat_studio, "owner"))
    assert pack["state"] == "queued", pack
    assert registry.preferred_hold("local") is None, \
        "a queued retry must not open the hold"

    # The broker lane's real body and finally: its Voice reference-conflict refusal is a
    # real terminal error and opens the window; an uncertain attempt returns through the
    # same real finally without one.
    registry._flags_cache = {"local": {
        "preferred_work": {"enabled": True, "modality": "voice", "idle_seconds": 300}}}
    voice_studio = {"id": "voice", "machine": "local", "modality": "voice", "local": True,
                    "host": "127.0.0.1", "port": 1, "hub_token": "t"}

    async def noop_async(*args, **kwargs):
        return None

    async def drive_broker_item(state, *, finish=noop_async, reserved=0.0, client=None,
                                params=None):
        item = {"index": 0, "prompt": "proof", "seed": None, "state": state,
                "tries": 1, "studio": "voice", "studio_job_id": None,
                "retry_at": None, "error": None, "_reserved": reserved,
                "params": params if params is not None else
                {"voice_reference_asset_id": "asset-1",
                 "voice_library_id": "library-1"}}
        batch = {"id": f"voice-hook-{state}", "modality": "voice", "model": "model",
                 "shared_params": {}, "cancelled": False, "items": [item]}
        broker.batches[batch["id"]] = batch
        broker._reserved["gb"] = reserved
        with patch.object(broker, "_post_item_webhook", new=noop_async), \
             patch.object(broker, "_maybe_finish", new=finish):
            await broker._run_item(client, batch, item, voice_studio)
        return item

    registry.set_preferred_hold("local", None, 0.0)
    broker_item = asyncio.run(drive_broker_item("running"))
    assert broker_item["state"] == "error", broker_item
    assert registry.preferred_hold("local") is not None, \
        "a terminal item must open the hold through the real finally"
    registry.set_preferred_hold("local", None, 0.0)
    broker_item = asyncio.run(drive_broker_item("uncertain"))
    assert broker_item["state"] == "uncertain", broker_item
    assert registry.preferred_hold("local") is None, \
        "an uncertain item must not open the hold"

    # Back to the Chat preference the peer-door section below uses.
    registry._flags_cache = {"local": {
        "preferred_work": {"enabled": True, "modality": "chat", "idle_seconds": 300}}}

    # The three peer-door release paths, with fake worker responses: the hold opens on real
    # terminal evidence (synchronous completion, status poll terminal, raw artifact) and on
    # nothing else (refusal 404, running poll, TTL/disconnect never call the hook).
    from components.hub import standalone
    standalone._PEER_LEASES.clear()
    standalone._PEER_JOBS.clear()
    standalone._PEER_UNCERTAIN_CHAT.clear()
    with standalone._PEER_LEASE_LOCK:
        standalone._PEER_LEASES["peer:chat:batch-9"] = ("local", time.monotonic() + 60.0)
        standalone._PEER_JOBS[("chat", "job-9")] = "peer:chat:batch-9"

    class FakeResponse:
        def __init__(self, status_code=200, headers=None, content=b""):
            self.status_code = status_code
            self.headers = headers or {}
            self.content = content

    def terminal_json(job_id, state):
        return FakeResponse(headers={"content-type": "application/json"},
                            content=json.dumps({"job": {"id": job_id, "state": state}}).encode())

    # Synchronous completion: _settle_peer_lease opens the hold on a terminal job payload...
    registry.set_preferred_hold("local", None, 0.0)
    standalone._settle_peer_lease("peer:chat:batch-9", "chat", terminal_json("x", "done"))
    assert registry.preferred_hold("local") is not None, "sync terminal must open the hold"
    assert "peer:chat:batch-9" not in standalone._PEER_LEASES, "lease released with it"
    # ... and never on a refusal (404 releases the lease with no hold) ...
    with standalone._PEER_LEASE_LOCK:
        standalone._PEER_LEASES["peer:chat:batch-9"] = ("local", time.monotonic() + 60.0)
        standalone._PEER_JOBS[("chat", "job-9")] = "peer:chat:batch-9"
    registry.set_preferred_hold("local", None, 0.0)
    standalone._settle_peer_lease("peer:chat:batch-9", "chat", FakeResponse(status_code=404))
    assert registry.preferred_hold("local") is None, "refusal must not open the hold"
    # ... or on an accepted async job still running (lease kept, nothing observed).
    with standalone._PEER_LEASE_LOCK:
        standalone._PEER_LEASES["peer:chat:batch-9"] = ("local", time.monotonic() + 60.0)
        standalone._PEER_JOBS[("chat", "job-9")] = "peer:chat:batch-9"
    standalone._settle_peer_lease("peer:chat:batch-9", "chat", terminal_json("job-9", "running"))
    assert registry.preferred_hold("local") is None, "accepted-running is not terminal"
    assert ("chat", "job-9") in standalone._PEER_JOBS, "lease kept for the running job"

    # Status poll: _observe_peer_response opens the hold on terminal / raw artifact only.
    with standalone._PEER_LEASE_LOCK:
        standalone._PEER_LEASES["peer:chat:batch-9"] = ("local", time.monotonic() + 60.0)
        standalone._PEER_JOBS[("chat", "job-9")] = "peer:chat:batch-9"
    registry.set_preferred_hold("local", None, 0.0)
    standalone._observe_peer_response("chat", "/api/generate/jobs/job-9",
                                      terminal_json("job-9", "done"))
    assert registry.preferred_hold("local") is not None, "observed terminal opens the hold"
    with standalone._PEER_LEASE_LOCK:
        standalone._PEER_LEASES["peer:chat:batch-9"] = ("local", time.monotonic() + 60.0)
        standalone._PEER_JOBS[("chat", "job-9")] = "peer:chat:batch-9"
    registry.set_preferred_hold("local", None, 0.0)
    standalone._observe_peer_response("chat", "/api/generate/jobs/job-9", FakeResponse(
        headers={"content-type": "image/png"}, status_code=200, content=b"\x89PNG"))
    assert registry.preferred_hold("local") is not None, "raw artifact means finished"
    with standalone._PEER_LEASE_LOCK:
        standalone._PEER_LEASES["peer:chat:batch-9"] = ("local", time.monotonic() + 60.0)
        standalone._PEER_JOBS[("chat", "job-9")] = "peer:chat:batch-9"
    standalone._observe_peer_response("chat", "/api/generate/jobs/job-9",
                                      terminal_json("job-9", "running"))
    assert registry.preferred_hold("local") is None, "running poll is not terminal"
    assert ("chat", "job-9") in standalone._PEER_JOBS

    # Uncertain-chat worker evidence through the REAL _release_finished_peer_leases loop:
    # the worker's own activity answer confirming the job over opens the hold once and
    # frees the lease; the same loop with the request still active opens nothing.
    from components.hub.backend import chat_priority
    standalone._PEER_JOBS.clear()   # no accepted-job rows for this branch
    mon.registry[0].update({"base_url": "http://127.0.0.1:1", "hub_token": "t"})

    class PeerEvidenceClient:
        def __init__(self, evidence):
            self.evidence = evidence

        async def get(self, url, **kwargs):
            return SimpleNamespace(status_code=200,
                                   headers={"content-type": "application/json"},
                                   json=lambda: self.evidence)

    owner, request_id = "peer:chat:batch-9", "req-9"
    registry.set_preferred_hold("local", None, 0.0)
    with standalone._PEER_LEASE_LOCK:
        standalone._PEER_LEASES[owner] = ("local", time.monotonic() + 60.0)
        standalone._PEER_UNCERTAIN_CHAT[owner] = request_id
    chat_priority.requests[request_id] = {"id": request_id, "state": "unknown",
                                          "lease_held": True}
    mon._client = PeerEvidenceClient(
        {"latest": {"id": request_id, "state": "done"}, "active": None})
    with patch.object(standalone, "_release_peer_lease",
                      wraps=standalone._release_peer_lease) as release_spy:
        asyncio.run(standalone._release_finished_peer_leases(mon))
    assert release_spy.called
    assert owner not in standalone._PEER_LEASES
    assert owner not in standalone._PEER_UNCERTAIN_CHAT
    assert registry.preferred_hold("local") is not None, \
        "worker evidence opens the hold through the real loop"
    registry.set_preferred_hold("local", None, 0.0)
    with standalone._PEER_LEASE_LOCK:
        standalone._PEER_LEASES[owner] = ("local", time.monotonic() + 60.0)
        standalone._PEER_UNCERTAIN_CHAT[owner] = request_id
    chat_priority.requests[request_id] = {"id": request_id, "state": "unknown",
                                          "lease_held": True}
    mon._client = PeerEvidenceClient(
        {"latest": {"id": request_id, "state": "done"},
         "active": {"id": request_id}})
    asyncio.run(standalone._release_finished_peer_leases(mon))
    assert owner in standalone._PEER_LEASES, "an active job keeps its lease"
    assert registry.preferred_hold("local") is None, "active evidence is not terminal"
    chat_priority.requests.pop(request_id, None)

    # TTL is a backstop, never completion evidence: pruning an expired lease opens nothing.
    registry.set_preferred_hold("local", None, 0.0)
    with standalone._PEER_LEASE_LOCK:
        standalone._PEER_LEASES["peer:chat:ttl"] = ("local", time.monotonic() - 1.0)
    standalone._prune_peer_leases()
    assert "peer:chat:ttl" not in standalone._PEER_LEASES
    assert registry.preferred_hold("local") is None, "TTL must not fabricate a completion"
    standalone._PEER_LEASES.clear(); standalone._PEER_JOBS.clear()
    standalone._PEER_UNCERTAIN_CHAT.clear()

    # --- a failed optional hold write must never skip cleanup (R4) ---------------------
    # The hold write is optional bookkeeping that runs inside the real lane finally blocks
    # and peer release paths. Inject the expected persistence failure (a full/unwritable
    # data dir) at registry.set_preferred_hold and drive the REAL functions: everything
    # after the hook must still run.

    # Chat lane: busy release, pack-task pop, batch save and dispatcher wake all survive.
    registry._flags_cache = {"local": {
        "preferred_work": {"enabled": True, "modality": "chat", "idle_seconds": 300}}}
    registry.set_preferred_hold("local", None, 0.0)
    chat_jobs.busy_studios.add("chat")
    chat_jobs._wakeup.clear()
    batch, pack = chat_pack(tries=1)
    chat_jobs._pack_tasks[(batch["id"], pack["index"])] = object()
    chat_monitor._client = RaisingChatClient()
    with patch.object(registry, "set_preferred_hold",
                      side_effect=OSError("no space left on device")) as failing:
        asyncio.run(chat_jobs._run_pack(chat_monitor, batch, pack, chat_studio, "owner"))
    failing.assert_called_once()
    assert pack["state"] == "error", pack
    assert "chat" not in chat_jobs.busy_studios, "busy cleanup must still run"
    assert (batch["id"], pack["index"]) not in chat_jobs._pack_tasks, \
        "pack-task cleanup must still run"
    assert "updated_at" in batch, "pack save must still run"
    assert chat_jobs._wakeup.is_set(), "the chat dispatcher must still wake"
    assert registry.preferred_hold("local") is None, "the failed write stored nothing"

    # Broker lane: busy release, reservation release, dispatcher wake and post-final
    # publication all survive.
    registry._flags_cache = {"local": {
        "preferred_work": {"enabled": True, "modality": "voice", "idle_seconds": 300}}}
    registry.set_preferred_hold("local", None, 0.0)
    broker._busy.add("voice")
    broker._wakeup.clear()
    finished = []

    async def recording_finish(*args, **kwargs):
        finished.append(True)

    with patch.object(registry, "set_preferred_hold",
                      side_effect=OSError("no space left on device")) as failing:
        broker_item = asyncio.run(drive_broker_item(
            "running", finish=recording_finish, reserved=0.5))
    failing.assert_called_once()
    assert broker_item["state"] == "error", broker_item
    assert "voice" not in broker._busy, "busy cleanup must still run"
    assert broker._reserved["gb"] == 0.0, "reservation cleanup must still run"
    assert broker_item["_reserved"] == 0.0
    assert broker._wakeup.is_set(), "the broker dispatcher must still wake"
    assert finished == [True], "post-final publication must still run"
    assert registry.preferred_hold("local") is None, "the failed write stored nothing"

    # Peer release on an accepted synchronous completion: the lease must still be freed.
    registry._flags_cache = {"local": {
        "preferred_work": {"enabled": True, "modality": "chat", "idle_seconds": 300}}}
    registry.set_preferred_hold("local", None, 0.0)
    with standalone._PEER_LEASE_LOCK:
        standalone._PEER_LEASES[owner] = ("local", time.monotonic() + 60.0)
    with patch.object(registry, "set_preferred_hold",
                      side_effect=OSError("no space left on device")) as failing:
        standalone._settle_peer_lease(owner, "chat", terminal_json("x", "done"))
    failing.assert_called_once()
    assert owner not in standalone._PEER_LEASES, \
        "peer lease release must still run after a failed hold write"
    assert registry.preferred_hold("local") is None

    # Peer release on an observed terminal status poll: same guarantee.
    registry.set_preferred_hold("local", None, 0.0)
    with standalone._PEER_LEASE_LOCK:
        standalone._PEER_LEASES[owner] = ("local", time.monotonic() + 60.0)
        standalone._PEER_JOBS[("chat", "job-9")] = owner
    with patch.object(registry, "set_preferred_hold",
                      side_effect=OSError("no space left on device")) as failing:
        standalone._observe_peer_response("chat", "/api/generate/jobs/job-9",
                                          terminal_json("job-9", "done"))
    failing.assert_called_once()
    assert owner not in standalone._PEER_LEASES, \
        "observed-terminal lease release must still run"
    assert ("chat", "job-9") not in standalone._PEER_JOBS
    assert registry.preferred_hold("local") is None

    # Peer release on this Mac's own worker evidence (the uncertain-chat loop): same.
    registry.set_preferred_hold("local", None, 0.0)
    standalone._PEER_JOBS.clear()
    with standalone._PEER_LEASE_LOCK:
        standalone._PEER_LEASES[owner] = ("local", time.monotonic() + 60.0)
        standalone._PEER_UNCERTAIN_CHAT[owner] = request_id
    chat_priority.requests[request_id] = {"id": request_id, "state": "unknown",
                                          "lease_held": True}
    mon._client = PeerEvidenceClient(
        {"latest": {"id": request_id, "state": "done"}, "active": None})
    with patch.object(registry, "set_preferred_hold",
                      side_effect=OSError("no space left on device")) as failing:
        asyncio.run(standalone._release_finished_peer_leases(mon))
    failing.assert_called_once()
    assert owner not in standalone._PEER_LEASES, \
        "worker-evidence lease release must still run"
    assert owner not in standalone._PEER_UNCERTAIN_CHAT
    assert registry.preferred_hold("local") is None
    chat_priority.requests.pop(request_id, None)
    standalone._PEER_LEASES.clear(); standalone._PEER_JOBS.clear()
    standalone._PEER_UNCERTAIN_CHAT.clear()

    # The control: only persistence I/O is best-effort. An unrelated failure from the same
    # call propagates instead of being swallowed.
    registry._flags_cache = {"local": {
        "preferred_work": {"enabled": True, "modality": "chat", "idle_seconds": 300}}}
    with patch.object(registry, "set_preferred_hold",
                      side_effect=RuntimeError("unrelated executor bug")) as exploding:
        try:
            broker._note_preferred_completion({"machine": "local"}, "chat")
            raise AssertionError("an unrelated failure was swallowed")
        except RuntimeError:
            pass
    exploding.assert_called_once()

    # --- bounded dispatch reservation: pre-POST, owner-fenced, one atomic completion -----
    # Start clean: disabling retires both the hold and any reservation from earlier drives.
    registry.set_preferred_work("local", None, 300, enabled=False)
    registry.set_preferred_work("local", "chat", 300, enabled=True)
    assert registry.PREFERRED_WORK_BOUNDS == preferred_hold_clock.PREFERRED_WORK_BOUNDS == (1, 3600)
    assert registry.PREFERRED_WORK_DEFAULT_S == preferred_hold_clock.PREFERRED_WORK_DEFAULT_S == 300

    # Reservation clock: same boot, expiry, restart fallback and both clamps.
    now_wall, now_steady = 2_000_000.0, 50_000.0
    reservation = {"modality": "chat", "owner": "tok", "started_wall": now_wall,
                   "started_steady": now_steady, "budget_s": 120.0}
    assert preferred_hold_clock.reservation_remaining_s(
        reservation, now_wall=now_wall + 30, now_steady=now_steady + 30) == 90.0
    assert preferred_hold_clock.reservation_remaining_s(
        reservation, now_wall=now_wall + 500, now_steady=now_steady + 500) == 0.0
    assert preferred_hold_clock.reservation_remaining_s(
        reservation, now_wall=now_wall + 10, now_steady=5.0) == 110.0
    assert preferred_hold_clock.reservation_remaining_s(
        reservation, now_wall=now_wall - 999, now_steady=5.0) == 120.0
    assert preferred_hold_clock.reservation_remaining_s(
        {"budget_s": "junk"}, now_wall=0, now_steady=0) == 0.0
    assert preferred_hold_clock.reservation_remaining_s(None) == 0.0

    # R5-1: corrupted stored rows read as no window and never raise from a worker timer.
    # A valid hold still answers; a corrupt row can never masquerade as a fresh window.
    good_policy = {"idle_seconds": 300}
    good_hold = {"ended_wall": now_wall, "boot_steady": now_steady}
    assert preferred_hold_clock.hold_remaining_s(
        good_policy, good_hold, now_wall=now_wall + 30, now_steady=now_steady + 30) == 270.0
    for bad_policy in (None, "yes", {}, {"idle_seconds": "junk"},
                       {"idle_seconds": None},
                       {"idle_seconds": float("nan")},
                       {"idle_seconds": float("inf")},
                       {"idle_seconds": 10**400}):
        assert preferred_hold_clock.hold_remaining_s(
            bad_policy, good_hold) == 0.0, bad_policy
    for bad_hold in (None, "yes", {}, {"ended_wall": now_wall},
                     {"boot_steady": now_steady},
                     {"ended_wall": "junk", "boot_steady": now_steady},
                     {"ended_wall": now_wall, "boot_steady": "junk"},
                     {"ended_wall": float("nan"), "boot_steady": now_steady},
                     {"ended_wall": float("inf"), "boot_steady": now_steady},
                     {"ended_wall": now_wall, "boot_steady": float("nan")},
                     {"ended_wall": now_wall, "boot_steady": float("-inf")},
                     {"ended_wall": 10**400, "boot_steady": now_steady},
                     {"ended_wall": now_wall, "boot_steady": 10**400}):
        assert preferred_hold_clock.hold_remaining_s(
            good_policy, bad_hold) == 0.0, bad_hold
    assert preferred_hold_clock.hold_remaining_s(
        good_policy, {"ended_wall": now_wall - 10**6, "boot_steady": 0.0},
        now_wall=now_wall, now_steady=now_steady) == 0.0, \
        "an expired hold is not refreshed"
    for bad_reservation in (
            {}, {"budget_s": "junk"}, {"budget_s": 0.0},
            {"budget_s": float("nan"), "started_wall": now_wall,
             "started_steady": now_steady},
            {"budget_s": float("inf"), "started_wall": now_wall,
             "started_steady": now_steady},
            {"budget_s": 120.0, "started_wall": "junk", "started_steady": now_steady},
            {"budget_s": 120.0, "started_wall": now_wall, "started_steady": "junk"},
            {"budget_s": 120.0, "started_wall": float("nan"),
             "started_steady": now_steady},
            {"budget_s": 120.0, "started_wall": now_wall,
             "started_steady": float("inf")},
            {"budget_s": 120.0, "started_wall": now_wall},
            {"budget_s": 10**400, "started_wall": now_wall,
             "started_steady": now_steady},
            {"budget_s": 120.0, "started_wall": 10**400,
             "started_steady": now_steady},
            {"budget_s": 120.0, "started_wall": now_wall,
             "started_steady": 10**400}):
        assert preferred_hold_clock.reservation_remaining_s(
            bad_reservation, now_wall=now_wall, now_steady=now_steady) == 0.0, \
            bad_reservation

    # Registry round-trip; a malformed persisted row reads as no reservation.
    registry.set_preferred_reservation("local", "chat", "tok-1",
                                       now_wall, now_steady, 120.0)
    assert registry.preferred_reservation("local") == reservation | {"owner": "tok-1"}
    registry._flags_cache = {"local": {"preferred_reservation": "yes"}}
    assert registry.preferred_reservation("local") is None
    registry._flags_cache = None

    # R5-2: only a real boolean true enables a persisted preference; legacy/malformed rows
    # read off exactly as the field-missing case always has.
    for enabled in (1, "yes", "true", [True]):
        registry._flags_cache = {"local": {"preferred_work": {
            "enabled": enabled, "modality": "chat", "idle_seconds": 300}}}
        assert registry.preferred_work("local")["enabled"] is False, enabled
    registry._flags_cache = {"local": {"preferred_work": {
        "modality": "chat", "idle_seconds": 300}}}
    assert registry.preferred_work("local")["enabled"] is False, \
        "a legacy row with no enabled key reads off"
    registry._flags_cache = {"local": {"preferred_work": {
        "enabled": True, "modality": "chat", "idle_seconds": 300}}}
    assert registry.preferred_work("local")["enabled"] is True

    # The registry read boundary rejects non-finite hold/reservation rows; the write boundary
    # refuses a non-finite budget or stamp before any disk/cache mutation, leaving the
    # existing reservation intact.
    for bad_reservation in (
            {"modality": "chat", "owner": "tok", "started_wall": now_wall,
             "started_steady": now_steady, "budget_s": float("inf")},
            {"modality": "chat", "owner": "tok", "started_wall": now_wall,
             "started_steady": now_steady, "budget_s": float("nan")},
            {"modality": "chat", "owner": "tok", "started_wall": float("nan"),
             "started_steady": now_steady, "budget_s": 120.0},
            {"modality": "chat", "owner": "tok", "started_wall": now_wall,
             "started_steady": "junk", "budget_s": 120.0},
            {"modality": "chat", "owner": "tok", "started_wall": now_wall,
             "started_steady": now_steady, "budget_s": 10**400},
            {"modality": "chat", "owner": "tok", "started_wall": 10**400,
             "started_steady": now_steady, "budget_s": 120.0},
            {"modality": None, "owner": "tok", "started_wall": now_wall,
             "started_steady": now_steady, "budget_s": 120.0}):
        registry._flags_cache = {"local": {"preferred_reservation": bad_reservation}}
        assert registry.preferred_reservation("local") is None, bad_reservation
    registry._flags_cache = {"local": {
        "preferred_hold": {"ended_wall": float("nan"), "boot_steady": now_steady}}}
    assert registry.preferred_hold("local") is None
    registry._flags_cache = {"local": {
        "preferred_hold": {"ended_wall": now_wall, "boot_steady": float("inf")}}}
    assert registry.preferred_hold("local") is None
    registry._flags_cache = {"local": {
        "preferred_hold": {"ended_wall": 10**400, "boot_steady": now_steady}}}
    assert registry.preferred_hold("local") is None
    registry._flags_cache = None
    assert registry.preferred_reservation("local")["owner"] == "tok-1"
    before_flags = registry.FLAGS_FILE.read_bytes()
    for bad in ((now_wall, now_steady, float("nan")),
                (now_wall, now_steady, float("inf")),
                (now_wall, now_steady, 0.0),
                (now_wall, now_steady, -1.0),
                ("junk", now_steady, 120.0),
                (now_wall, float("nan"), 120.0),
                (float("inf"), now_steady, 120.0),
                (10**400, now_steady, 120.0),
                (now_wall, now_steady, 10**400)):
        try:
            registry.set_preferred_reservation("local", "chat", "tok-bad", *bad)
            raise AssertionError("an invalid reservation write must be refused")
        except ValueError:
            pass
    assert registry.FLAGS_FILE.read_bytes() == before_flags, \
        "a refused write must not touch the flags file"
    assert registry.preferred_reservation("local")["owner"] == "tok-1"

    # One call writes the hold and conditionally clears the reservation by exact owner.
    registry.set_preferred_reservation("local", "chat", "attempt-new",
                                       now_wall, now_steady, 120.0)
    registry.set_preferred_hold("local", now_wall, now_steady,
                                clear_reservation_owner="attempt-old")
    assert registry.preferred_reservation("local")["owner"] == "attempt-new", \
        "an older attempt must not clear a newer reservation"
    assert registry.preferred_hold("local") is not None, \
        "the completion hold is written in the same call"
    registry.set_preferred_hold("local", None, 0.0,
                                clear_reservation_owner="attempt-new")
    assert registry.preferred_reservation("local") is None
    assert registry.preferred_hold("local") is None

    # Disable and modality switch clear both; a same-modality re-save keeps both.
    registry.set_preferred_reservation("local", "chat", "tok-2",
                                       now_wall, now_steady, 120.0)
    registry.set_preferred_hold("local", now_wall, now_steady)
    registry.set_preferred_work("local", "voice", 300, enabled=True)
    assert registry.preferred_reservation("local") is None
    assert registry.preferred_hold("local") is None
    registry.set_preferred_reservation("local", "voice", "tok-3",
                                       now_wall, now_steady, 120.0)
    registry.set_preferred_hold("local", now_wall, now_steady)
    registry.set_preferred_work("local", "voice", 600, enabled=True)
    assert registry.preferred_reservation("local")["owner"] == "tok-3"
    assert registry.preferred_hold("local") is not None
    registry.set_preferred_work("local", None, 300, enabled=False)
    assert registry.preferred_reservation("local") is None
    assert registry.preferred_hold("local") is None

    # Budgets reuse the Hub's published lane deadline table plus grace; Image adopts its
    # worker-stated deadline up to that worker's own 7,200 s ceiling.
    assert broker._preferred_reservation_budget("voice") == 2820.0   # 2,700 + grace
    assert broker._preferred_reservation_budget("chat") == 1920.0    # 1,800 + grace
    assert broker._preferred_reservation_budget("image") == 1920.0   # floor 1,800 + grace
    assert broker._preferred_reservation_budget("image", 5000.0) == 5120.0
    assert broker._preferred_reservation_budget("image", 60.0) == 1920.0

    # The broker lane writes the reservation before the actual POST, through the real
    # _run_item (the fake client records and then fails as a transport error).
    registry._flags_cache = {"local": {
        "preferred_work": {"enabled": True, "modality": "voice", "idle_seconds": 300}}}

    class RecordingClient:
        def __init__(self):
            self.calls = []

        async def post(self, *args, **kwargs):
            self.calls.append("post")
            raise httpx.ConnectError("refused in this source-level proof")

    real_reservation = registry.set_preferred_reservation
    order = []

    def reservation_spy(*args, **kwargs):
        order.append("reserve")
        return real_reservation(*args, **kwargs)

    recording = RecordingClient()
    with patch.object(registry, "set_preferred_reservation", side_effect=reservation_spy):
        broker_item = asyncio.run(drive_broker_item(
            "running", client=recording, params={}))
    assert order[:2] == ["reserve", "post"], \
        "the reservation must be written immediately before the POST"
    assert recording.calls == ["post"], broker_item
    assert broker_item["state"] in ("queued", "error"), broker_item
    order.clear()

    # The Chat lane does the same through the real _run_pack.
    registry._flags_cache = {"local": {
        "preferred_work": {"enabled": True, "modality": "chat", "idle_seconds": 300}}}

    class RecordingChatClient:
        def __init__(self):
            self.calls = []

        async def post(self, *args, **kwargs):
            self.calls.append("post")
            raise RuntimeError("stopped after the POST for ordering")

    chat_recording = RecordingChatClient()
    registry.set_preferred_hold("local", None, 0.0)
    batch, pack = chat_pack(tries=1)
    chat_monitor._client = chat_recording
    with patch.object(registry, "set_preferred_reservation", side_effect=reservation_spy):
        asyncio.run(chat_jobs._run_pack(chat_monitor, batch, pack, chat_studio, "owner"))
    assert order[:2] == ["reserve", "post"]
    assert chat_recording.calls == ["post"]
    assert pack["state"] == "error", pack
    order.clear()

    # A failed dispatch reservation write is fail-safe: the POST still happens, the lane
    # finishes, and no exemption is recorded.
    registry.set_preferred_hold("local", None, 0.0)
    registry.set_preferred_work("local", "voice", 300, enabled=True)
    recording = RecordingClient()
    with patch.object(registry, "set_preferred_reservation",
                      side_effect=OSError("no space left on device")) as failing:
        broker_item = asyncio.run(drive_broker_item(
            "running", client=recording, params={}))
    failing.assert_called_once()
    assert recording.calls == ["post"], "the job was still dispatched"
    assert registry.preferred_reservation("local") is None
    assert broker_item["state"] in ("queued", "error"), broker_item

    # An older attempt's terminal may open the scheduling hold but must not clear the newer
    # attempt's reservation; the owning attempt does clear it.
    registry._flags_cache = {"local": {
        "preferred_work": {"enabled": True, "modality": "chat", "idle_seconds": 300}}}
    registry.set_preferred_hold("local", None, 0.0)
    registry.set_preferred_reservation("local", "chat", "attempt-2",
                                       time.time(), time.monotonic(), 300.0)
    broker._note_preferred_completion({"machine": "local"}, "chat",
                                      reservation_owner="attempt-1")
    assert registry.preferred_reservation("local")["owner"] == "attempt-2", \
        "a delayed older completion must not clear the newer reservation"
    assert registry.preferred_hold("local") is not None, \
        "the old terminal still opens the scheduling hold (frozen semantics)"
    broker._note_preferred_completion({"machine": "local"}, "chat",
                                      reservation_owner="attempt-2")
    assert registry.preferred_reservation("local") is None
    assert registry.preferred_hold("local") is not None

    # A failed atomic completion write leaves the old bounded reservation and writes no
    # hold: one write, no split clear-then-hold fiction.
    registry.set_preferred_hold("local", None, 0.0)
    registry.set_preferred_reservation("local", "chat", "attempt-3",
                                       time.time(), time.monotonic(), 300.0)
    with patch.object(registry, "set_preferred_hold",
                      side_effect=OSError("no space left on device")) as failing:
        broker._note_preferred_completion({"machine": "local"}, "chat",
                                          reservation_owner="attempt-3")
    failing.assert_called_once()
    assert registry.preferred_reservation("local")["owner"] == "attempt-3", \
        "the old reservation remains, bounded by its own budget"
    assert registry.preferred_hold("local") is None, "no partial or new hold was written"

    # The peer door reserves through _reserve_peer_machine, before the caller's proxied POST,
    # using its own per-lease owner as the reservation token; the real terminal-observation
    # path clears that exact reservation in the same atomic write as the hold.
    registry._flags_cache = {"local": {
        "preferred_work": {"enabled": True, "modality": "image", "idle_seconds": 300}}}
    broker.batches.clear()
    broker._external_machine_leases.clear()
    standalone._PEER_LEASES.clear()
    registry.set_preferred_hold("local", None, 0.0)
    peer_owner = standalone._reserve_peer_machine("image", "local")
    assert peer_owner is not None and peer_owner.startswith("peer:image:")
    peer_reservation = registry.preferred_reservation("local")
    assert peer_reservation["owner"] == peer_owner
    assert peer_reservation["modality"] == "image"
    assert peer_reservation["budget_s"] == broker._preferred_reservation_budget("image")
    standalone._open_preferred_hold_on_release(peer_owner, "image")
    assert registry.preferred_reservation("local") is None, \
        "the peer attempt's own terminal clears its reservation"
    assert registry.preferred_hold("local") is not None
    standalone._release_peer_lease(peer_owner)
    assert peer_owner not in standalone._PEER_LEASES

    print("preferred idle hold: clock, persistence, arbitration and API shapes proved")
'''


class PreferredWorkIdleHoldTests(unittest.TestCase):
    def test_hold_clock_persistence_and_arbitration(self) -> None:
        with tempfile.TemporaryDirectory(prefix="studiohub-preferred-hold-") as state:
            # The packaged Hub resolves its own worker registry from STUDIOHUB_WORKERS_FILE
            # (standalone_config.load_workers). This proof lazily imports `standalone`, whose
            # module-level `app = create_app()` builds a StudioMonitor -> load_registry, so that
            # file and its one local Chat descriptor must exist or startup raises. Private file:
            # owner-only, regular (standalone_config._private_file).
            workers_file = Path(state) / "workers.json"
            workers_file.write_text(json.dumps([{
                "id": "chat", "title": "Chat", "modality": "chat",
                "host": "127.0.0.1", "port": 51131, "machine": "local", "local": True,
                "runtime_identity": {"id": "fixture-chat-runtime", "compatible": True},
                "hub_token": "fixture-hub-token"}]))
            workers_file.chmod(0o600)
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith("STUDIOHUB_")}
            env.update(
                STUDIOHUB_DATA_DIR=state,
                STUDIOHUB_MODELS_DIR=str(Path(state) / "models"),
                STUDIOHUB_MODEL_MANIFESTS=str(Path(state) / "manifests"),
                STUDIOHUB_WORKERS_FILE=str(workers_file),
                STUDIOHUB_PACKAGED="1",
                PYTHONPATH=str(ROOT),
            )
            runtime = os.environ.get(
                "STUDIOHUB_TEST_RUNTIME", sys.executable)
            result = subprocess.run(
                [runtime, "-c", PROOF], cwd=ROOT, env=env,
                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("preferred idle hold: clock, persistence, arbitration and API shapes proved",
                      result.stdout)


if __name__ == "__main__":
    unittest.main()
