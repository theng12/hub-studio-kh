#!/usr/bin/env python3
"""Final render in a Mac's ranked work order, and the render-only pause exemption.

Owner, 2026-10-28:

* render is one rankable item among the others ("LLM, render, audio, transcript, image"), with
  the same semantics: never interrupt a running job, take the highest-ranked waiting work when the
  Mac frees;
* a Mac whose saved order does not mention render keeps today's behaviour exactly (render first),
  and **no stored order is rewritten**;
* a Mac paused for generation can be switched, per Mac and off by default, to keep final rendering.

Every case below is one of those sentences, plus the ones that must NOT change.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.hub.backend import (  # noqa: E402
    broker, chat_jobs, chat_priority, registry, transcription_jobs, vocabulary,
)


WORKERS = [
    {"id": "image", "title": "Image", "modality": "image", "machine": "local", "local": True},
    {"id": "voice", "title": "Voice", "modality": "voice", "machine": "local", "local": True},
    {"id": "chat", "title": "Chat", "modality": "chat", "machine": "local", "local": True},
    {"id": "subtitle", "title": "Subtitle", "modality": "transcription",
     "machine": "local", "local": True},
]


class RenderCandidate:
    """A render executor with one waiting job, standing in for `RenderExecutor`."""

    def __init__(self, created_at: float = 1.0) -> None:
        self.created_at = created_at
        self.dispatched = 0

    async def dispatchable_candidate(self, machine):
        return (self.created_at, "job-a") if machine == "local" else None

    def pending_for_machine(self, machine):
        return machine == "local"


class Fixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.data = tempfile.TemporaryDirectory(prefix="render-ranking-")
        self.addCleanup(self.data.cleanup)
        root = Path(self.data.name)
        for name, value in (("FLAGS_FILE", root / "machine_flags.json"),
                            ("LABELS_FILE", root / "machine_labels.json"),
                            ("REGISTRY_FILE", root / "studios.json")):
            patcher = mock.patch.object(registry, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("_flags_cache", "_labels_cache"):
            setattr(registry, name, None)
        self.monitor = SimpleNamespace(
            registry=[dict(row) for row in WORKERS],
            status={row["id"]: {"status": "up"} for row in WORKERS},
            _client=None,
        )

        async def catalog(_studio):
            return {"models": [{"repo": "model", "cache": {"state": "cached"},
                                "runtime_ready": True, "available": True}]}

        entries = lambda model, modality=None: [
            {"studio": row,
             "entry": {"repo": model, "cache": {"state": "cached"},
                       "runtime_ready": True, "available": True}}
            for row in self.monitor.registry if modality in (None, row.get("modality"))]
        self.monitor.scheduling_catalog = catalog
        self.monitor.cached_catalog_entries = entries
        self.monitor.scheduling_catalog_entries = entries
        broker.configure_monitor(self.monitor)
        broker.batches.clear()
        broker._busy.clear()
        broker._external_machine_leases.clear()
        chat_jobs.batches.clear()
        chat_jobs.busy_studios.clear()
        chat_priority.requests.clear()
        transcription_jobs.batches.clear()
        broker.configure_render_executor(None)

    def tearDown(self) -> None:
        broker.configure_render_executor(None)
        broker.batches.clear()
        broker._external_machine_leases.clear()
        broker._busy.clear()

    def executor(self, created_at: float = 1.0) -> RenderCandidate:
        candidate = RenderCandidate(created_at)
        broker.configure_render_executor(candidate)
        return candidate

    async def winner(self, machine: str = "local", *, lanes: tuple[str, ...] = ()):
        """`next_work` with a waiting render competing against the lanes named in `lanes`.

        A lane is only a candidate when its own module says it has queued work, so each one is
        given a real candidate rather than patched into existence beside a `has_queued_work`
        that says no.
        """
        chat_candidate = (2.0, "chat-batch") if "chat" in lanes else None
        subtitle_candidate = (2.0, "subtitle-batch") if "transcription" in lanes else None
        with mock.patch.object(broker, "_broker_dispatchable_work", return_value=[]), \
                mock.patch.object(chat_jobs, "has_queued_work",
                                  return_value=chat_candidate is not None), \
                mock.patch.object(chat_jobs, "dispatchable_candidate",
                                  new=mock.AsyncMock(return_value=chat_candidate)), \
                mock.patch.object(transcription_jobs, "has_queued_work",
                                  return_value=subtitle_candidate is not None), \
                mock.patch.object(transcription_jobs, "dispatchable_candidate",
                                  new=mock.AsyncMock(return_value=subtitle_candidate)):
            return await broker.next_work(machine)


class VocabularyTests(unittest.TestCase):
    def test_render_is_rankable_and_is_not_a_modality(self) -> None:
        """It joins the saved order; it does not become a peer route a node may serve."""
        self.assertIn(vocabulary.RENDER_MODALITY, vocabulary.PRIORITY_MODALITIES)
        self.assertNotIn(vocabulary.RENDER_MODALITY, vocabulary.NODE_MODALITIES)
        self.assertNotIn(vocabulary.RENDER_MODALITY, vocabulary.REQUIRED_WORKERS)

    def test_the_plain_name_is_owned_by_one_place(self) -> None:
        self.assertEqual(vocabulary.PRIORITY_TITLES[vocabulary.RENDER_MODALITY], "Final render")

    def test_there_is_one_named_default_order(self) -> None:
        """One constant in the vocabulary owns the default order `with_render_default` applies."""
        self.assertEqual(vocabulary.DEFAULT_PRIORITY_ORDER, (vocabulary.RENDER_MODALITY,))
        self.assertEqual(registry.with_render_default(["chat"]),
                         [*vocabulary.DEFAULT_PRIORITY_ORDER, "chat"])


class RankingTests(Fixture):
    async def test_a_saved_order_that_names_render_is_authoritative(self) -> None:
        """The owner's own example: Chat above render above the rest."""
        self.executor()
        registry.set_priority_order("local", ["chat", "render", "voice", "transcription", "image"])
        # Chat has work waiting and the owner ranked it first, so Chat takes the Mac; the render
        # (waiting longer) is second, not first.
        self.assertEqual((await self.winner(lanes=("chat",)))[3], "chat")
        self.assertEqual(registry.priority_order("local"),
                         ["chat", "render", "voice", "transcription", "image"])

    async def test_the_same_order_with_render_above_chat_lets_render_take_it(self) -> None:
        """The order decides, not which lane happens to be older."""
        self.executor()
        registry.set_priority_order("local", ["render", "chat", "voice"])
        self.assertEqual((await self.winner(lanes=("chat",)))[3], "render")

    async def test_an_order_omitting_render_keeps_render_ahead_of_a_waiting_chat(self) -> None:
        self.executor()
        registry.set_priority_order("local", ["image"])
        self.assertEqual((await self.winner(lanes=("chat",)))[3], "render")

    async def test_a_ranked_order_takes_turns_in_its_own_sequence(self) -> None:
        """Whichever ranked function has work waiting wins, not whichever is oldest."""
        self.executor()
        registry.set_priority_order(
            "local", ["chat", "render", "transcription", "voice", "image"])
        self.assertEqual((await self.winner(lanes=("chat", "transcription")))[3], "chat",
                         "Chat is ranked first and has work")
        # With Chat quiet, render is still ranked above transcription and is waiting, so it is the
        # next eligible work. That is the owner's order being obeyed, not a regression.
        self.assertEqual((await self.winner(lanes=("transcription",)))[3], "render")
        # Take the render out of the waiting set and transcription is next.
        broker.configure_render_executor(None)
        self.assertEqual((await self.winner(lanes=("transcription",)))[3], "transcription")

    async def test_an_order_that_ranks_transcription_above_render_gives_it_the_turn(self) -> None:
        self.executor()
        registry.set_priority_order("local", ["chat", "transcription", "render"])
        self.assertEqual((await self.winner(lanes=("transcription",)))[3], "transcription")

    async def test_render_first_when_the_saved_order_puts_it_first(self) -> None:
        self.executor(created_at=99.0)
        registry.set_priority_order("local", ["render", "chat", "image"])
        winner = await self.winner()
        self.assertEqual(winner[3], "render")

    async def test_an_order_that_omits_render_reads_it_first_without_being_rewritten(self) -> None:
        """Today's behaviour for every existing Mac, and the stored order is untouched."""
        self.executor(created_at=99.0)
        registry.set_priority_order("local", ["image"])
        self.assertEqual((await self.winner())[3], "render")
        # Read-time default only: what is on disk is exactly what was saved.
        self.assertEqual(registry.priority_order("local"), ["image"])
        stored = json.loads((Path(self.data.name) / "machine_flags.json").read_text())
        self.assertEqual(stored["local"]["priority_order"], ["image"])

    async def test_an_empty_order_means_no_preference_and_stays_equal(self) -> None:
        """`[]` is a deliberate "all enabled functions are equal", not a Mac that forgot render.

        It is pinned by `test_render_physical_admission`: a newer render must not jump older
        equal-priority work. Inserting a rank the owner never gave would break that.
        """
        self.executor(created_at=99.0)
        registry.set_priority_order("local", [])
        self.assertEqual(registry.with_render_default([]), [],
                         "no preference is not a place to insert a rank the owner never gave")
        # The lease half keeps the answer it has always given for "no preference": a waiting
        # render takes the next free moment, which is today's behaviour and is pinned by
        # tests/test_render_physical_admission.py. Only ranking treats the order as empty.
        self.assertTrue(broker.render_wins_next_free("local", "chat"))

    async def test_an_older_flags_row_reads_the_same_way(self) -> None:
        """A row written before render was rankable: `chat_priority` true, no `priority_order`."""
        (Path(self.data.name) / "machine_flags.json").write_text(
            json.dumps({"local": {"chat_priority": True}}))
        registry._flags_cache = None
        self.assertEqual(registry.priority_order("local"), ["chat"])
        self.executor(created_at=99.0)
        self.assertEqual((await self.winner())[3], "render")

    def test_the_public_order_and_the_arbitration_order_are_different_functions(self) -> None:
        """The work-policy API and the fleet rows keep showing what the owner saved.

        `broker.priority_order` answers "what did this Mac save" and `ranked_priority_order`
        answers "what does arbitration take first". Collapsing them would have reported a saved
        order the Mac never saved, on every Mac that never mentioned render.
        """
        registry.set_priority_order("local", ["image"])
        self.assertEqual(broker.priority_order("local"), ["image"])
        self.assertEqual(broker.ranked_priority_order("local"), ["render", "image"])
        self.assertFalse(broker.chat_priority_enabled("local"),
                         "the legacy Chat projection reads the saved order")
        registry.set_priority_order("local", ["chat"])
        self.assertEqual(broker.priority_order("local"), ["chat"])
        self.assertEqual(broker.ranked_priority_order("local"), ["render", "chat"])
        self.assertTrue(broker.chat_priority_enabled("local"),
                        "Chat first is still Chat first, even with render read ahead of it")

    def test_a_saved_order_may_name_render_and_nothing_else_may(self) -> None:
        registry.set_priority_order("local", ["render", "chat"])
        self.assertEqual(registry.priority_order("local"), ["render", "chat"])
        with self.assertRaises(ValueError):
            registry.set_priority_order("local", ["chat", "not-a-function"])
        with self.assertRaises(ValueError):
            registry.set_priority_order("local", ["render", "render"])

    def test_a_damaged_order_is_neutral_rather_than_half_read(self) -> None:
        self.assertEqual(registry.normalize_priority_order(["chat", 7]), [])
        self.assertEqual(registry.normalize_priority_order(["chat", "chat"]), [])
        self.assertEqual(registry.normalize_priority_order("chat"), [])


class ReservationTests(Fixture):
    """The lease half of the same rule: a waiting render takes the next free moment where ranked."""

    def test_a_waiting_render_reserves_a_mac_that_ranks_it_first(self) -> None:
        self.executor()
        registry.set_priority_order("local", ["render", "chat"])
        self.assertFalse(broker.acquire_external_machine("local", "chat:b:0"))
        self.assertTrue(broker.acquire_external_machine("local", "render:job-a"))

    def test_a_mac_that_ranks_chat_above_render_gives_chat_the_free_moment(self) -> None:
        self.executor()
        registry.set_priority_order("local", ["chat", "render"])
        self.assertTrue(broker.acquire_external_machine("local", "chat:b:0"),
                        "Chat outranks a waiting render on this Mac")
        # The render cannot take it while Chat holds the lease: nothing preempts.
        self.assertFalse(broker.acquire_external_machine("local", "render:job-a"))

    def test_an_order_omitting_render_keeps_the_old_reservation(self) -> None:
        self.executor()
        registry.set_priority_order("local", ["chat"])
        self.assertFalse(broker.acquire_external_machine("local", "chat:b:0"))
        self.assertFalse(broker.acquire_external_machine("local", "transcription:b:0"))

    def test_an_empty_order_keeps_the_old_reservation(self) -> None:
        """Today's behaviour for a Mac that never saved an order, at the lease, unchanged."""
        self.executor()
        registry.set_priority_order("local", [])
        self.assertFalse(broker.acquire_external_machine("local", "chat:b:0"))

    def test_a_mac_that_ranks_transcription_above_render_gives_it_the_free_moment(self) -> None:
        self.executor()
        registry.set_priority_order("local", ["transcription", "render"])
        self.assertTrue(broker.acquire_external_machine("local", "transcription:b:0"))

    def test_no_waiting_render_means_no_reservation_at_all(self) -> None:
        broker.configure_render_executor(None)
        registry.set_priority_order("local", ["render", "chat"])
        self.assertTrue(broker.acquire_external_machine("local", "chat:b:0"))

    def test_a_lease_owner_is_read_as_its_lane_not_its_first_word(self) -> None:
        """The owner strings the lanes already mint, including the two Chat spellings."""
        self.assertEqual(broker.lease_lane("render:job-a"), "render")
        self.assertEqual(broker.lease_lane("chat:batch:0"), "chat")
        self.assertEqual(broker.lease_lane("direct-chat:abc"), "chat")
        self.assertEqual(broker.lease_lane("relay:chat:abc"), "chat")
        self.assertEqual(broker.lease_lane("transcription:batch:1"), "transcription")
        self.assertEqual(broker.lease_lane("peer:voice:abc"), "voice")
        self.assertEqual(broker.lease_lane("peer:image:abc"), "image")
        self.assertEqual(broker.lease_lane("peer:somethingelse:abc"), "")
        self.assertEqual(broker.lease_lane(""), "")

    def test_an_unplaceable_lease_keeps_the_old_answer(self) -> None:
        """A lease this build cannot place never gains priority over a waiting render."""
        self.executor()
        registry.set_priority_order("local", ["chat"])
        self.assertFalse(broker.acquire_external_machine("local", "mystery-owner"))


class PauseExemptionTests(Fixture):
    def gate(self, **patches):
        """`render_dispatch_allowed` with every gate open except the one under test."""
        defaults = dict(machine_enabled=True, studio_enabled=True, blocked=False,
                        standing_by=False, busy=set(), owner=None)
        defaults.update(patches)
        patches = (
            mock.patch.object(broker, "machine_enabled",
                              return_value=defaults["machine_enabled"]),
            mock.patch.object(broker, "studio_enabled",
                              return_value=defaults["studio_enabled"]),
            mock.patch.object(broker, "_machine_blocked", return_value=defaults["blocked"]),
            mock.patch.object(broker.peers, "standing_by",
                              return_value=defaults["standing_by"]),
            mock.patch.object(broker, "busy_machines", return_value=defaults["busy"]),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return broker.render_dispatch_allowed("local", defaults["owner"])

    def test_a_paused_mac_takes_no_render_by_default(self) -> None:
        self.assertFalse(self.gate(machine_enabled=False))
        self.assertFalse(registry.render_while_paused("local"), "the switch is off unless set")

    def test_the_switch_lets_a_paused_mac_render(self) -> None:
        registry.set_render_while_paused("local", True)
        self.assertTrue(registry.render_while_paused("local"))
        self.assertTrue(self.gate(machine_enabled=False))

    def test_the_switch_exempts_the_pause_only(self) -> None:
        """Lite mode, drain, maintenance and a quarantine still refuse a render."""
        registry.set_render_while_paused("local", True)
        self.assertFalse(self.gate(machine_enabled=False, standing_by=True),
                         "Lite Mode still refuses")
        self.assertFalse(self.gate(machine_enabled=False, blocked=True),
                         "a quarantine still refuses")
        self.assertFalse(self.gate(machine_enabled=False, studio_enabled=False),
                         "the render studio switched off still refuses")
        # A running job is never interrupted: with the Mac leased to another render, this one is
        # refused, exemption or not. (Queried with no owner the comparison reads "no lease" on
        # both sides, which is the pre-existing shape of this gate and not something the exemption
        # changes, so the assertion names the owner it is about.)
        broker._external_machine_leases["local"] = "render:other"
        self.addCleanup(broker._external_machine_leases.pop, "local", None)
        self.assertFalse(self.gate(machine_enabled=False, busy={"local"}, owner="render:job-a"),
                         "a busy Mac is still busy")

    def test_an_unpaused_mac_is_unaffected_either_way(self) -> None:
        self.assertTrue(self.gate())
        registry.set_render_while_paused("local", True)
        self.assertTrue(self.gate())

    def test_a_damaged_or_malformed_setting_reads_as_off(self) -> None:
        path = Path(self.data.name) / "machine_flags.json"
        for value in (1, "true", [True], None, {}):
            path.write_text(json.dumps({"local": {"render_while_paused": value}}))
            registry._flags_cache = None
            self.assertFalse(registry.render_while_paused("local"), f"{value!r} must read as off")
        path.write_text(json.dumps({"local": {"render_while_paused": True}}))
        registry._flags_cache = None
        self.assertTrue(registry.render_while_paused("local"))
        with self.assertRaises(ValueError):
            registry.set_render_while_paused("local", "yes")

    def test_the_exemption_does_not_disturb_the_saved_order(self) -> None:
        registry.set_priority_order("local", ["chat"])
        registry.set_render_while_paused("local", True)
        stored = json.loads((Path(self.data.name) / "machine_flags.json").read_text())
        self.assertEqual(stored["local"]["priority_order"], ["chat"])
        self.assertIs(stored["local"]["render_while_paused"], True)


class WorkPolicyTests(Fixture):
    def test_the_order_is_editable_and_lists_render_in_plain_words(self) -> None:
        from components.hub import operator_controls
        with mock.patch("components.hub.backend.render_executor.capability",
                        return_value={"physical_machine_id": "local"}):
            policy = operator_controls.work_policy(self.monitor)
        ids = [row["id"] for row in policy["priority_functions"]]
        self.assertIn("render", ids)
        titles = {row["id"]: row["title"] for row in policy["priority_functions"]}
        self.assertEqual(titles["render"], "Final render")
        # The saved order it publishes is the saved one, not the arbitration default.
        registry.set_priority_order("local", ["chat"])
        with mock.patch("components.hub.backend.render_executor.capability",
                        return_value={"physical_machine_id": "local"}):
            policy = operator_controls.work_policy(self.monitor)
        self.assertEqual(policy["priority_order"], ["chat"])
        self.assertTrue(policy["chat_priority"],
                        "the legacy Chat-first projection still reads Chat")

    def test_a_hub_with_no_render_lane_does_not_offer_render(self) -> None:
        from components.hub import operator_controls
        with mock.patch("components.hub.backend.render_executor.capability", return_value=None):
            policy = operator_controls.work_policy(self.monitor)
        self.assertNotIn("render", [row["id"] for row in policy["priority_functions"]])

    def test_saving_the_owners_example_order_is_accepted(self) -> None:
        from components.hub import operator_controls
        with mock.patch("components.hub.backend.render_executor.capability",
                        return_value={"physical_machine_id": "local"}), \
                mock.patch.object(operator_controls, "_wake_dispatchers"):
            operator_controls.save_work_policy(self.monitor, {
                "priority_order": ["chat", "render", "voice", "transcription", "image"]})
        self.assertEqual(registry.priority_order("local"),
                         ["chat", "render", "voice", "transcription", "image"])

    def test_a_function_this_mac_does_not_have_is_still_refused(self) -> None:
        from fastapi import HTTPException
        from components.hub import operator_controls
        with mock.patch("components.hub.backend.render_executor.capability",
                        return_value={"physical_machine_id": "local"}):
            with self.assertRaises(HTTPException) as caught:
                operator_controls.save_work_policy(self.monitor, {
                    "priority_order": ["chat", "music"]})
        self.assertEqual(caught.exception.status_code, 400)


class FunctionSwitchTests(Fixture):
    """Per-kind on/off switch in the work order, ON by default, for every function including render.

    Owner, 2026-10-28: "each generation should actually have a cross to switch them on or off.
    but defaults to on."
    """

    def test_every_function_defaults_to_on_with_nothing_stored(self) -> None:
        for kind in vocabulary.PRIORITY_MODALITIES:
            self.assertTrue(registry.function_enabled("local", kind),
                            f"{kind} must default to on")
            self.assertTrue(broker.function_enabled("local", kind))
        self.assertEqual(registry.function_switches("local"), {})

    def test_absent_and_malformed_values_read_as_on(self) -> None:
        """Strict reader: only a real boolean `false` turns a function off."""
        path = Path(self.data.name) / "machine_flags.json"
        for bad in (0, 1, "false", "no", None, [], {}, {"chat": 0, "image": "false", "render": None}):
            path.write_text(json.dumps({"local": {"function_enabled": bad}}))
            registry._flags_cache = None
            for kind in ("chat", "image", "render"):
                self.assertTrue(registry.function_enabled("local", kind),
                                f"{bad!r} must read as on for {kind}")
        path.write_text(json.dumps({"local": {"function_enabled": {"chat": False, "image": 0}}}))
        registry._flags_cache = None
        self.assertFalse(registry.function_enabled("local", "chat"))
        self.assertTrue(registry.function_enabled("local", "image"),
                        "a non-boolean sibling is dropped rather than read as off")

    async def test_a_switched_off_function_is_skipped_by_arbitration_and_lease(self) -> None:
        """Rank decides *when*; the switch decides *whether*."""
        self.executor()
        registry.set_priority_order("local", ["chat", "render", "transcription"])
        registry.set_function_enabled("local", "chat", False)
        # Chat is still ranked first, and has work waiting, and is skipped because it is off:
        # render (ranked second) wins instead.
        self.assertEqual((await self.winner(lanes=("chat", "transcription")))[3], "render")
        self.assertFalse(broker.acquire_external_machine("local", "chat:b:0"),
                         "a switched-off lane cannot take the Mac's lease either")
        self.assertFalse(broker.acquire_external_machine("local", "direct-chat:abc"))
        self.assertFalse(broker.acquire_external_machine("local", "relay:chat:abc"))
        # Turning it back on restores its first-place rank, because the order never moved.
        registry.set_function_enabled("local", "chat", True)
        self.assertEqual((await self.winner(lanes=("chat", "transcription")))[3], "chat")

    async def test_switching_render_off_skips_it_and_stops_reserving_the_mac(self) -> None:
        self.executor()
        registry.set_priority_order("local", ["render", "chat"])
        registry.set_function_enabled("local", "render", False)
        self.assertEqual((await self.winner(lanes=("chat",)))[3], "chat",
                         "a switched-off render does not win even when ranked first")
        self.assertFalse(broker.render_dispatch_allowed("local"),
                         "render's own admission gate refuses it when switched off")
        self.assertFalse(broker.acquire_external_machine("local", "render:job-a"),
                         "render cannot take the lease while switched off")
        self.assertTrue(broker.acquire_external_machine("local", "chat:b:0"),
                        "a waiting switched-off render no longer holds the Mac against Chat")

    def test_switching_a_broker_modality_off_excludes_its_studio_without_touching_studio_enabled(self) -> None:
        """The work-order switch and `studio_enabled` stay independent."""
        self.assertEqual([s["id"] for s in broker._eligible_studios("image", "pool")], ["image"])
        registry.set_function_enabled("local", "image", False)
        trace: list[dict] = []
        self.assertEqual(broker._eligible_studios("image", "pool", trace=trace), [])
        self.assertEqual(trace[0]["gate"], "function_switched_off")
        self.assertTrue(registry.studio_enabled("local", "image"),
                        "the Studio checkbox is untouched")
        self.assertFalse(broker.acquire_external_machine("local", "peer:image:abc"))

    def test_a_joined_peer_publishes_its_switches_to_the_controller(self) -> None:
        from components.hub.backend import fleet_nodes, peers
        self.monitor.registry.append({
            "id": "image@node-b", "title": "Image", "modality": "image",
            "machine": "node-b", "local": False,
        })
        with mock.patch.object(peers, "cached", return_value={
                "function_enabled": {"image": False, "voice": "not-a-bool"}}):
            self.assertFalse(broker.function_enabled("node-b", "image"))
            self.assertTrue(broker.function_enabled("node-b", "voice"))
        self.assertEqual(fleet_nodes._function_enabled_of(
            {"function_enabled": {"render": False, " bogus ": False, "chat": 0}}),
            {"render": False})

    def test_work_policy_exposes_and_validates_the_switches_while_keeping_rows_and_order(self) -> None:
        from fastapi import HTTPException
        from components.hub import operator_controls
        registry.set_priority_order("local", ["chat", "render", "image"])
        with mock.patch("components.hub.backend.render_executor.capability",
                        return_value={"physical_machine_id": "local"}), \
                mock.patch.object(operator_controls, "_wake_dispatchers"):
            initial = operator_controls.work_policy(self.monitor)
            self.assertTrue(all(row["enabled"] is True for row in initial["priority_functions"]))
            saved = operator_controls.save_work_policy(
                self.monitor, {"function_enabled": {"render": False, "image": False}})
            by_id = {row["id"]: row["enabled"] for row in saved["priority_functions"]}
            self.assertEqual(by_id["render"], False)
            self.assertEqual(by_id["image"], False)
            self.assertEqual(by_id["chat"], True)
            # Still in the list, and the saved order and Studio checkboxes are untouched.
            self.assertIn("render", by_id)
            self.assertIn("image", by_id)
            self.assertEqual(saved["priority_order"], ["chat", "render", "image"])
            self.assertTrue(all(row["enabled"] is True for row in saved["studios"]))
            for bad in ({"function_enabled": {}},
                        {"function_enabled": "chat"},
                        {"function_enabled": {"music": False}},
                        {"function_enabled": {"chat": 0}},
                        {"function_enabled": {"chat": "false"}}):
                with self.assertRaises(HTTPException) as caught:
                    operator_controls.save_work_policy(self.monitor, bad)
                self.assertEqual(caught.exception.status_code, 400, f"{bad!r} must be refused")
            # A refused batch leaves the earlier switches standing.
            with self.assertRaises(HTTPException):
                operator_controls.save_work_policy(
                    self.monitor, {"function_enabled": {"chat": False, "music": False}})
            self.assertTrue(registry.function_enabled("local", "chat"))


def sqlite3_error() -> Exception:
    import sqlite3
    return sqlite3.DatabaseError("the history could not be read")


def insert_render(path: Path, job_id: str, *, machine: str, started: float, settled: float,
                  seconds: float = 60.0) -> None:
    """One finished, timed render in a Hub database, as `render_jobs` stores it."""
    import sqlite3
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS render_jobs (id TEXT PRIMARY KEY, state TEXT, machine TEXT, "
        "created_at REAL, updated_at REAL, started_at REAL, packet_json TEXT, "
        "output_receipt_json TEXT, display_json TEXT, purpose TEXT, output_path TEXT, "
        "recovery_note TEXT, stopped_evidence_json TEXT, story_uuid TEXT, "
        "intent_id TEXT, attempt_no INTEGER, gateway_attempt_key TEXT, dispatch_key TEXT, "
        "profile_digest TEXT, tools_digest TEXT, compiler_digest TEXT)")
    connection.execute(
        "INSERT INTO render_jobs (id, state, machine, created_at, updated_at, started_at, "
        "packet_json) VALUES (?, 'output_ready', ?, ?, ?, ?, ?)",
        (job_id, machine, started, settled, started,
         json.dumps({"recipe": {"target_duration_sec": seconds}})))
    connection.commit()
    connection.close()


class MeasuredSpeedTests(unittest.TestCase):
    """The per-Mac measured render speed the Gateway's fastest-first choice reads."""

    def entries(self, *rows) -> list[dict]:
        """Timed renders as `render_history.entry` gives them: length, wall time, machine."""
        return [{"outcome": "succeeded", "machine": machine, "video_seconds": video,
                 "wall_seconds": wall} for machine, video, wall in rows]

    def test_one_macs_ratio_is_never_borrowed_for_another(self) -> None:
        from components.hub.backend import render_history
        speeds = render_history.speed_by_machine(self.entries(
            ("local", 60.0, 30.0),      # 2.0x
            ("node-b", 30.0, 30.0),     # 1.0x
            ("node-b", 30.0, 30.0),     # 1.0x again, by sum not by average of averages
        ))
        self.assertAlmostEqual(speeds["local"]["speed"], 2.0, places=6)
        self.assertAlmostEqual(speeds["node-b"]["speed"], 1.0, places=6)
        self.assertEqual(speeds["node-b"]["renders"], 2)

    def test_an_untimed_or_failed_render_is_not_a_measurement(self) -> None:
        from components.hub.backend import render_history
        speeds = render_history.speed_by_machine([
            {"outcome": "succeeded", "machine": "local", "video_seconds": 10.0,
             "wall_seconds": None},
            {"outcome": "failed", "machine": "local", "video_seconds": 10.0,
             "wall_seconds": 5.0},
            {"outcome": "succeeded", "machine": None, "video_seconds": 10.0,
             "wall_seconds": 5.0},
            {"outcome": "succeeded", "machine": "local", "video_seconds": 0,
             "wall_seconds": 5.0},
        ])
        self.assertEqual(speeds, {}, "nothing measured is an empty answer, never a 0")

    def test_a_mac_with_no_measurement_is_absent_not_zero(self) -> None:
        from components.hub.backend import render_history
        speeds = render_history.speed_by_machine(self.entries(("local", 60.0, 30.0)))
        self.assertIn("local", speeds)
        self.assertNotIn("node-b", speeds)
        self.assertNotIn("node-b", speeds, "a missing Mac is missing, which is the honest answer")

    def test_the_probe_publishes_it_and_omits_it_when_unmeasured(self) -> None:
        """`resources.host_stats` is the host snapshot every peer probe and fleet row carries."""
        from components.hub.backend import ledger, resources
        with tempfile.TemporaryDirectory(prefix="render-speed-") as folder:
            database = Path(folder) / "hub.db"
            with mock.patch.object(ledger, "DB_FILE", database), \
                    mock.patch.object(resources, "_RENDER_SPEED_TTL_S", 0.0):
                self.assertIsNone(resources.measured_render_speed(now=1.0),
                                  "no history is no measurement, never 0")
                resources._render_speed_cache = (0.0, None)
                self.assertNotIn("render_speed", resources.host_stats())
                now = time.time()
                insert_render(database, "j", machine="local",
                              started=now - 60, settled=now - 30)
                resources._render_speed_cache = (0.0, None)
                speed = resources.measured_render_speed(now=1.0)
                self.assertEqual(speed["renders"], 1)
                self.assertAlmostEqual(speed["speed"], 2.0, places=6)
                resources._render_speed_cache = (0.0, None)
                self.assertEqual(resources.host_stats()["render_speed"]["renders"], 1)

    def test_a_measurement_cannot_be_confused_with_an_error(self) -> None:
        from components.hub.backend import resources
        with mock.patch.object(resources, "_RENDER_SPEED_TTL_S", 0.0):
            resources._render_speed_cache = (0.0, None)
            with mock.patch("components.hub.backend.render_history.read",
                            side_effect=sqlite3_error()):
                self.assertIsNone(resources.measured_render_speed(now=1.0),
                                  "an unreadable history is an absent measurement")

    def test_the_fleet_machine_row_carries_it_and_only_when_measured(self) -> None:
        """`read` publishes it per Mac, which is where the fleet machine row takes it from."""
        import sqlite3
        from components.hub.backend import render_history
        with tempfile.TemporaryDirectory(prefix="render-speed-") as folder:
            path = Path(folder) / "hub.db"
            answer = render_history.read(path)
            self.assertEqual(answer["by_machine"], {})
            # A Hub that never created the table answers an empty history rather than raising.
            path.write_bytes(b"")
            self.assertEqual(render_history.read(path)["by_machine"], {})
            insert_render(path, "job-1", machine="node-b", started=100.0, settled=130.0)
            answer = render_history.read(path, now=200.0)
        self.assertEqual(answer["by_machine"]["node-b"]["renders"], 1)
        self.assertAlmostEqual(answer["by_machine"]["node-b"]["speed"], 2.0, places=6)


class LeaseLaneWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_and_transcription_still_ask_before_leasing(self) -> None:
        """The rule reaches the lanes through `acquire_external_machine`, which they already use."""
        import inspect
        from components.hub.backend import chat_jobs as cj, transcription_jobs as tj
        for module in (cj, tj):
            source = inspect.getsource(module)
            self.assertIn("broker.acquire_external_machine", source)
            self.assertIn("broker.higher_priority_work_waiting", source)
        from components.hub.backend import render_executor
        self.assertIn("higher_priority_work_waiting",
                      inspect.getsource(render_executor.RenderExecutor.dispatch_ready))


if __name__ == "__main__":
    unittest.main()
