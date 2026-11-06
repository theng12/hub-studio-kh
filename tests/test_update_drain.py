"""Drain for update: a Mac with a verified update stops taking NEW work, with a code the gateway re-routes.

The code is `MACHINE_BUSY` - the one the gateway already files as a location refusal
(`LOCATION_NOT_READY`, another Mac is tried) and the broker treats as "not available right now", never
as a failure. The drain is a lease, so a Mac that restarts, or an app that dies, resumes taking work.

Every Mac installs on its own (owner 2026-11-06: fan out): a drain is never serialised against another
Mac's, and a node never asks its controller, so no Mac waits for another one's install.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub import standalone  # noqa: E402
from components.hub.backend import auth, broker, update_drain  # noqa: E402

CATALOG = {"voice-mlx:qwen": {"engine": "voice-mlx", "model_id": "m"}}
ENVELOPE = {"modality": "voice", "model": "m"}


class _Monitor:
    registry = [{"id": "voice-1", "modality": "voice", "local": True, "machine": "local"}]
    status = {"voice-1": {"status": "up"}}


class _GroupMonitor:
    """A controller (local voice studio) with one joined node."""
    registry = [{"id": "voice-1", "modality": "voice", "local": True, "machine": "local"},
                {"id": "node-1:voice", "modality": "voice", "local": False, "machine": "node-1"}]
    status = {"voice-1": {"status": "up"}, "node-1:voice": {"status": "up"}}


def _reset():
    update_drain._drain_until = 0.0


def _submission(monitor):
    with mock.patch.object(standalone, "_model_catalog", lambda: CATALOG), \
         mock.patch.object(standalone, "_model_state",
                           lambda manifest, catalog, mon, lane_contracts=None: {"dispatch_state": "eligible"}):
        return standalone._submission_readiness(ENVELOPE, monitor)


class DrainRefusesNewWork(unittest.TestCase):
    def setUp(self):
        _reset()
        self.addCleanup(_reset)

    def test_a_draining_mac_refuses_a_submission_with_the_rerouteable_code(self):
        self.assertIsNone(_submission(_Monitor()))
        update_drain._set_draining(True)
        refusal = _submission(_Monitor())
        self.assertIsNotNone(refusal, "a draining Mac must refuse new work")
        self.assertEqual(refusal["code"], "MACHINE_BUSY")

    def test_a_draining_mac_takes_its_own_machine_out_of_the_scheduler_and_peer_admission(self):
        with mock.patch.object(broker, "_monitor", lambda: _Monitor()):
            self.assertNotIn("local", broker.busy_machines())
            update_drain._set_draining(True)
            self.assertIn("local", broker.busy_machines())
            refusal = standalone._peer_admission_refusal(_Monitor.registry[0], _Monitor(), "")
            self.assertEqual(refusal["code"], "MACHINE_BUSY")
            self.assertFalse(broker.acquire_external_machine("local", "chat"))

    def test_the_drain_is_a_lease_that_lapses(self):
        update_drain._set_draining(True)
        self.assertTrue(update_drain.draining())
        with mock.patch.object(update_drain.time, "monotonic", lambda: update_drain._drain_until + 1):
            self.assertFalse(update_drain.draining(), "a Mac whose app died must take work again")

    def test_a_drain_is_renewed_while_it_waits_and_resumed_when_it_is_over(self):
        first = asyncio.run(update_drain.request(True))
        self.assertEqual(first["state"], "draining")
        self.assertTrue(update_drain.draining())
        expiry = update_drain._drain_until
        with mock.patch.object(update_drain.time, "monotonic", lambda: expiry - 1):
            self.assertEqual(asyncio.run(update_drain.request(True))["state"], "draining",
                             "the app renews the same drain")
        self.assertGreater(update_drain._drain_until, expiry)
        self.assertEqual(asyncio.run(update_drain.request(False))["state"], "resumed")
        self.assertFalse(update_drain.draining())


class DrainingControllerKeepsServingItsNodes(unittest.TestCase):
    """While the controller's own work runs, its nodes keep taking work."""

    def setUp(self):
        _reset()
        self.addCleanup(_reset)
        own_work = mock.patch.object(broker, "_busy", {"voice-1"})
        own_work.start()
        self.addCleanup(own_work.stop)

    def test_only_the_controllers_own_machine_is_taken(self):
        with mock.patch.object(broker, "_monitor", lambda: _GroupMonitor()):
            update_drain._set_draining(True)
            busy = broker.busy_machines()
            self.assertIn("local", busy)
            self.assertNotIn("node-1", busy, "a draining controller stopped dispatching to its idle node")
            self.assertFalse(broker.acquire_external_machine("local", "chat"))

    def test_the_scheduler_still_offers_work_to_a_node_and_not_to_the_draining_mac(self):
        with mock.patch.object(broker, "_monitor", lambda: _GroupMonitor()), \
             mock.patch.object(broker.peers, "dispatch_ready", lambda studio, generation=None: True):
            update_drain._set_draining(True)
            ids = [s["id"] for s in broker._eligible_studios("voice", "pool", None)]
        self.assertEqual(ids, ["node-1:voice"])

    def test_a_submission_is_accepted_while_a_node_can_serve_it(self):
        with mock.patch.object(broker, "_monitor", lambda: _GroupMonitor()):
            update_drain._set_draining(True)
            self.assertIsNone(_submission(_GroupMonitor()))
            refusal = _submission(_Monitor())
        self.assertEqual(refusal["code"], "MACHINE_BUSY", "a Mac with no node still refuses while it drains")


class EveryMacDrainsIndependently(unittest.TestCase):
    def setUp(self):
        _reset()
        self.addCleanup(_reset)

    def test_two_holders_are_granted_where_the_old_group_slot_allowed_one(self):
        self.assertTrue(update_drain.slot("controller", True))
        self.assertTrue(update_drain.slot("node-1", True), "a second Mac is not made to wait")
        self.assertTrue(update_drain.slot("node-2", True))
        self.assertTrue(update_drain.slot("node-1", False))
        self.assertTrue(update_drain.slot("node-2", True), "releasing never hands the slot elsewhere")

    def test_a_node_drains_without_asking_its_controller(self):
        target = ("http://controller.invalid:9", {"X-Hub-Token": "t"})
        with mock.patch("socket.create_connection", side_effect=AssertionError("network used")):
            answer = asyncio.run(update_drain.request(True, target))
        self.assertEqual(answer["state"], "draining", "a node's update must not depend on its controller")
        self.assertTrue(update_drain.draining())

    def test_a_draining_controller_no_longer_holds_its_idle_nodes(self):
        with mock.patch.object(broker, "_monitor", lambda: _GroupMonitor()), \
             mock.patch.object(broker, "_busy", set()), \
             mock.patch.object(broker.peers, "dispatch_ready", lambda studio, generation=None: True):
            update_drain._set_draining(True)
            busy = broker.busy_machines()
            ids = [s["id"] for s in broker._eligible_studios("voice", "pool", None)]
            refusal = _submission(_GroupMonitor())
        self.assertIn("local", busy)
        self.assertNotIn("node-1", busy, "each node installs when its own work is done, not when this Mac's is")
        self.assertEqual(ids, ["node-1:voice"])
        self.assertIsNone(refusal, "the controller keeps routing its nodes' work while it drains")

    def test_the_slot_route_is_open_to_the_fleet_and_to_a_node_credential(self):
        self.assertTrue(auth.fleet_path_allowed("POST", "/api/hub/fleet/update-slot"))
        self.assertTrue(auth.node_path_allowed("POST", "/api/hub/fleet/update-slot"))
        self.assertFalse(auth.fleet_path_allowed("POST", "/api/hub/update/drain"),
                         "asking a Mac to stop taking work is the owner's app alone")


if __name__ == "__main__":
    unittest.main()
