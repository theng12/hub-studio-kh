"""Drain for update: a Mac with a verified update stops taking NEW work, with a code the gateway re-routes.

The code is `MACHINE_BUSY` - the one the gateway already files as a location refusal
(`LOCATION_NOT_READY`, another Mac is tried) and the broker treats as "not available right now", never
as a failure. The drain is a lease, so a Mac that restarts, or an app that dies, resumes taking work.
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


def _reset():
    update_drain._drain_until = 0.0
    update_drain._slot = None


class DrainRefusesNewWork(unittest.TestCase):
    def setUp(self):
        _reset()
        self.addCleanup(_reset)

    def _submission(self):
        with mock.patch.object(standalone, "_model_catalog", lambda: CATALOG), \
             mock.patch.object(standalone, "_model_state",
                               lambda manifest, catalog, mon, lane_contracts=None: {"dispatch_state": "eligible"}):
            return standalone._submission_readiness(ENVELOPE, _Monitor())

    def test_a_draining_mac_refuses_a_submission_with_the_rerouteable_code(self):
        self.assertIsNone(self._submission())
        update_drain._set_draining(True)
        refusal = self._submission()
        self.assertIsNotNone(refusal, "a draining Mac must refuse new work")
        self.assertEqual(refusal["code"], "MACHINE_BUSY")

    def test_a_draining_mac_takes_every_machine_out_of_the_scheduler_and_peer_admission(self):
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


class _GroupMonitor:
    """A controller (local voice studio) with one joined node."""
    registry = [{"id": "voice-1", "modality": "voice", "local": True, "machine": "local"},
                {"id": "node-1:voice", "modality": "voice", "local": False, "machine": "node-1"}]
    status = {"voice-1": {"status": "up"}, "node-1:voice": {"status": "up"}}


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
        with mock.patch.object(standalone, "_model_catalog", lambda: CATALOG), \
             mock.patch.object(broker, "_monitor", lambda: _GroupMonitor()), \
             mock.patch.object(standalone, "_model_state",
                               lambda manifest, catalog, mon, lane_contracts=None: {"dispatch_state": "eligible"}):
            update_drain._set_draining(True)
            self.assertIsNone(standalone._submission_readiness(ENVELOPE, _GroupMonitor()))
            refusal = standalone._submission_readiness(ENVELOPE, _Monitor())
        self.assertEqual(refusal["code"], "MACHINE_BUSY", "a Mac with no node still refuses while it drains")


class DrainNeverStarves(unittest.TestCase):
    """Once the controller's own work is done, nothing new starts in its group: what the nodes run for
    it finishes and it installs. A long queue must not keep it (and the group's slot) busy for ever."""

    def setUp(self):
        _reset()
        self.addCleanup(_reset)

    def test_the_group_is_held_once_this_macs_own_work_is_done(self):
        with mock.patch.object(broker, "_monitor", lambda: _GroupMonitor()), \
             mock.patch.object(broker, "_busy", {"voice-1", "node-1:voice"}), \
             mock.patch.object(broker.peers, "dispatch_ready", lambda studio, generation=None: True):
            self.assertFalse(broker.drain_holds_group(), "not draining: nothing is held")
            update_drain._set_draining(True)
            self.assertFalse(broker.drain_holds_group(), "the controller's own job still runs")
            self.assertNotIn("node-1", broker.busy_machines())
            broker._busy.discard("voice-1")
            self.assertTrue(broker.drain_holds_group())
            self.assertIn("node-1", broker.busy_machines(),
                          "a node kept taking new work, so the controller could never install")
            self.assertEqual(broker._eligible_studios("voice", "pool", None), [])

    def test_a_held_group_refuses_a_submission_so_the_gateway_reroutes_it(self):
        with mock.patch.object(standalone, "_model_catalog", lambda: CATALOG), \
             mock.patch.object(standalone, "_model_state",
                               lambda manifest, catalog, mon, lane_contracts=None: {"dispatch_state": "eligible"}), \
             mock.patch.object(broker, "_monitor", lambda: _GroupMonitor()), \
             mock.patch.object(broker, "_busy", set()):
            update_drain._set_draining(True)
            refusal = standalone._submission_readiness(ENVELOPE, _GroupMonitor())
        self.assertEqual(refusal["code"], "MACHINE_BUSY")

    def test_a_render_lease_on_this_mac_is_its_own_work(self):
        with mock.patch.object(broker, "_monitor", lambda: _GroupMonitor()), \
             mock.patch.object(broker, "_busy", set()), \
             mock.patch.dict(broker._external_machine_leases, {"local": "render:1"}, clear=True):
            update_drain._set_draining(True)
            self.assertFalse(broker.drain_holds_group())


class OneMacPerGroup(unittest.TestCase):
    def setUp(self):
        _reset()
        self.addCleanup(_reset)

    def test_the_slot_has_one_holder_and_is_released_by_it(self):
        self.assertTrue(update_drain.slot("controller", True))
        self.assertTrue(update_drain.slot("controller", True), "the holder renews")
        self.assertFalse(update_drain.slot("node-1", True), "a node waits while the controller drains")
        self.assertTrue(update_drain.slot("node-1", False))
        self.assertFalse(update_drain.slot("node-1", True), "a stranger cannot release someone else's slot")
        self.assertTrue(update_drain.slot("controller", False))
        self.assertTrue(update_drain.slot("node-1", True))
        self.assertFalse(update_drain.slot("controller", True), "and the controller waits for its node")

    def test_a_lapsed_slot_is_free(self):
        update_drain.slot("node-1", True)
        with mock.patch.object(update_drain.time, "monotonic", lambda: update_drain._slot[1] + 1):
            self.assertTrue(update_drain.slot("node-2", True), "a node that died must not hold the group")

    def test_a_controller_drains_only_when_it_holds_the_slot(self):
        update_drain.slot("node-1", True)
        answer = asyncio.run(update_drain.request(True, None))
        self.assertEqual(answer["state"], "waiting")
        self.assertFalse(update_drain.draining())
        update_drain.slot("node-1", False)
        self.assertEqual(asyncio.run(update_drain.request(True, None))["state"], "draining")
        self.assertTrue(update_drain.draining())
        self.assertEqual(asyncio.run(update_drain.request(False, None))["state"], "resumed")
        self.assertFalse(update_drain.draining())
        self.assertTrue(update_drain.slot("node-1", True), "resuming gives the slot back")

    def test_a_node_drains_only_when_its_controller_grants_the_slot(self):
        class _Client:
            granted = False
            status = 200

            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, json=None, headers=None):
                self.sent = (url, json, headers)
                return mock.Mock(status_code=_Client.status, json=lambda: {"granted": _Client.granted})

        target = ("http://controller:8080", {"X-Hub-Token": "t"})
        with mock.patch.object(update_drain.httpx, "AsyncClient", _Client):
            self.assertEqual(asyncio.run(update_drain.request(True, target))["state"], "waiting")
            self.assertFalse(update_drain.draining())
            _Client.granted = True
            self.assertEqual(asyncio.run(update_drain.request(True, target))["state"], "draining")
            self.assertTrue(update_drain.draining())
            # An older controller (no route) or an unreachable one must not hold an update back.
            _reset()
            _Client.status = 404
            self.assertEqual(asyncio.run(update_drain.request(True, target))["state"], "draining")

    def test_the_slot_route_is_open_to_the_fleet_and_to_a_node_credential(self):
        self.assertTrue(auth.fleet_path_allowed("POST", "/api/hub/fleet/update-slot"))
        self.assertTrue(auth.node_path_allowed("POST", "/api/hub/fleet/update-slot"))
        self.assertFalse(auth.fleet_path_allowed("POST", "/api/hub/update/drain"),
                         "asking a Mac to stop taking work is the owner's app alone")


if __name__ == "__main__":
    unittest.main()
