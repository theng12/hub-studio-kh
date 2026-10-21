"""A Mac the controller cannot dial is a Mac that is here.

These tests model a node behind one-way Tailscale **node sharing**: the shared Mac can dial out and
the sharer cannot dial back. The three Shops Macs that prompted the channel were in that position,
joined, and then read `never_connected` for ever — 51 hours after joining at the 2026-10-20
measurement — because the only evidence of life the controller accepted was a dial it could not
make. Whether they were running and calling home was never proven: the controller recorded no call
from them. These tests pin the four things that fix the channel for a Mac in that position:

* the Mac's own authenticated call home **is** evidence, and the reason is kept distinct;
* it carries the Mac's current address, so an address is no longer frozen at join time;
* the requests the controller could not dial are **held for the Mac** and replayed on its own Hub,
  with a command id the Mac acks so nothing runs twice;
* the peer poll and the scheduler see the same Mac, so it is not merely listed as up — it is given
  work.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub.backend import fleet_nodes, fleet_pull, peers  # noqa: E402

NODE = "office-imac-blue-m1-8-256-hub"
ADDRESS = "http://100.120.246.9:47873"
STUDIOS = [{"id": "image", "modality": "image"}]
RESOURCES = {"host": {"chip": "Apple M1", "total_gb": 8.0, "available_gb": 5.0},
             "studios": {"image": {"id": "image", "status": "up"}},
             "version": "0.6.193", "source": "hub",
             "workstation": {"enabled": True, "state": "idle"},
             "current_job": None,
             "machine": {"id": NODE, "name": "Office iMac"}}


class DeadDial:
    """A controller that cannot reach the Mac at all — the Shops case, exactly."""

    def __init__(self, *, error: Exception | None = None):
        self.calls: list[str] = []
        self.error = error or httpx.ConnectTimeout("timed out")

    async def get(self, url: str, timeout=None, **kwargs):
        self.calls.append(url)
        raise self.error

    async def post(self, url: str, **kwargs):
        self.calls.append(url)
        raise self.error

    async def request(self, method: str, url: str, **kwargs):
        self.calls.append(url)
        raise self.error

    async def handle_async_request(self, request):
        self.calls.append(str(request.url))
        raise self.error

    async def aclose(self):
        return None


class PullCase(unittest.TestCase):
    """A controller with one joined node it cannot dial."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-pull-")
        self.data = Path(self.temporary.name)
        self.old_env = os.environ.copy()
        self.old_data = (fleet_nodes.DATA_DIR, fleet_pull.DATA_DIR)
        os.environ["STUDIOHUB_DATA_DIR"] = self.temporary.name
        os.environ.pop("STUDIOHUB_FLEET_TOKEN", None)
        fleet_nodes.DATA_DIR = self.data
        fleet_pull.DATA_DIR = self.data
        peers._cache.clear()
        self.addCleanup(self.restore)
        fleet_nodes.join({"node_id": NODE, "name": "Office iMac", "address": ADDRESS,
                          "studios": STUDIOS})

    def restore(self) -> None:
        fleet_nodes.DATA_DIR, fleet_pull.DATA_DIR = self.old_data
        peers._cache.clear()
        os.environ.clear()
        os.environ.update(self.old_env)
        self.temporary.cleanup()

    def checkin(self, **payload) -> dict:
        body = {"address": ADDRESS, "name": "Office iMac", "version": "0.6.193",
                "studios": STUDIOS, "resources": RESOURCES}
        body.update(payload)
        row = fleet_nodes.record_checkin(NODE, body)
        self.assertIsNotNone(row)
        return row

    def probe(self, **kwargs):
        return asyncio.run(fleet_nodes.probe(fleet_nodes._read()[NODE],
                                             kwargs.pop("client", DeadDial())))


class CheckinIsEvidenceOfLife(PullCase):
    def test_a_mac_that_calls_home_reads_as_reachable_with_the_reason_named(self) -> None:
        self.checkin()
        row = self.probe()
        self.assertEqual("reachable", row["state"])
        # The state is the same one a dialled Mac reports; the reason is not, and an operator
        # looking at a Shops Mac has to be able to tell the two apart.
        self.assertEqual("check-in", row["via"])
        self.assertIsNone(row["detail"])

    def test_the_call_home_stamps_last_seen_and_clears_never_connected(self) -> None:
        # Joined long ago, never once dialled successfully: the state that read "never connected"
        # for 51 hours on a Mac whose liveness was never proven.
        nodes = fleet_nodes._read()
        nodes[NODE]["joined_at"] = time.time() - 51 * 3600
        nodes[NODE]["last_seen_at"] = nodes[NODE]["joined_at"]
        fleet_nodes._write(nodes)
        before = self.probe()
        self.assertTrue(before["never_connected"])
        self.assertEqual("unreachable", before["state"])

        self.checkin()
        after = self.probe()
        self.assertFalse(after["never_connected"])
        self.assertAlmostEqual(time.time(), after["last_seen_at"], delta=5)
        self.assertEqual("check-in", fleet_nodes._read()[NODE]["last_seen_via"])

    def test_a_mac_that_stops_calling_home_stops_reading_as_reachable(self) -> None:
        self.checkin()
        nodes = fleet_nodes._read()
        nodes[NODE]["last_checkin_at"] = time.time() - fleet_nodes.CHECKIN_FRESH_S - 1
        fleet_nodes._write(nodes)
        row = self.probe()
        self.assertEqual("unreachable", row["state"])
        self.assertIsNone(row.get("via"))

    def test_the_roster_counts_a_check_in_mac_as_reachable(self) -> None:
        self.checkin()
        # `inventory` probes every node itself; drive it with a client that cannot dial.
        rows = asyncio.run(fleet_nodes.inventory(DeadDial()))
        self.assertEqual(1, rows["summary"]["reachable"])
        self.assertEqual(1, rows["summary"]["check_in"])
        self.assertEqual(0, rows["summary"]["unreachable"])

    def test_the_call_home_carries_what_the_dial_would_have_returned(self) -> None:
        self.checkin()
        row = self.probe()
        self.assertEqual("0.6.193", row["version"])
        self.assertEqual({"enabled": True, "state": "idle"}, row["workstation"])
        self.assertEqual("Apple M1", (row["host"] or {}).get("chip"))


class CheckinCarriesTheAddress(PullCase):
    def test_a_changed_address_is_adopted(self) -> None:
        moved = "http://100.64.0.9:47873"
        self.checkin(address=moved)
        self.assertEqual(moved, fleet_nodes._read()[NODE]["address"])
        self.assertEqual(moved, self.probe()["address"])

    def test_an_address_this_controller_will_not_use_is_refused(self) -> None:
        # The Mac's word is validated exactly as a join is: a check-in must never be able to point
        # this controller at an arbitrary host.
        self.checkin(address="http://93.184.216.34:47873")
        self.assertEqual(ADDRESS, fleet_nodes._read()[NODE]["address"])

    def test_a_mac_that_says_nothing_about_its_address_keeps_it(self) -> None:
        fleet_nodes.record_checkin(NODE, {"version": "0.6.193"})
        self.assertEqual(ADDRESS, fleet_nodes._read()[NODE]["address"])


class TheQueueDeliversOnce(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-pull-queue-")
        self.data = Path(self.temporary.name)
        self.old = fleet_pull.DATA_DIR
        fleet_pull.DATA_DIR = self.data
        self.addCleanup(self.restore)

    def restore(self) -> None:
        fleet_pull.DATA_DIR = self.old
        self.temporary.cleanup()

    def test_a_queued_request_is_handed_over_once(self) -> None:
        command = fleet_pull.queue_json(NODE, "POST", "/api/hub/workstation",
                                        {"enabled": True, "idle_minutes": 10})
        first = fleet_pull.take_commands(NODE)
        self.assertEqual([command["id"]], [row["id"] for row in first])
        self.assertEqual("POST", first[0]["method"])
        self.assertEqual("/api/hub/workstation", first[0]["path"])
        self.assertEqual({"enabled": True, "idle_minutes": 10}, json.loads(
            __import__("base64").b64decode(first[0]["content_b64"]).decode()))

    def test_an_acked_command_is_never_handed_back(self) -> None:
        command = fleet_pull.queue_json(NODE, "POST", "/api/hub/workstation", {"enabled": True})
        fleet_pull.take_commands(NODE)
        self.assertEqual(1, fleet_pull.ack(NODE, [command["id"]]))
        self.assertEqual([], fleet_pull.take_commands(NODE))

    def test_the_nodes_own_seen_list_retires_a_command_it_already_ran(self) -> None:
        # The delivery that the node acked can be lost in flight; the node's own word is what
        # stops the same Lite Mode change being applied a second time.
        command = fleet_pull.queue_json(NODE, "POST", "/api/hub/workstation", {"enabled": True})
        self.assertEqual([], fleet_pull.take_commands(NODE, [command["id"]]))
        self.assertEqual([], fleet_pull.take_commands(NODE))

    def test_a_command_past_its_expiry_is_dropped(self) -> None:
        fleet_pull.queue_json(NODE, "POST", "/api/hub/workstation", {"enabled": True}, ttl_s=0.01)
        time.sleep(0.05)
        self.assertEqual([], fleet_pull.take_commands(NODE))

    def test_the_controller_never_hands_the_node_its_own_credential(self) -> None:
        command = fleet_pull.queue(NODE, "POST", "/api/hub/workstation",
                                   headers={"X-Hub-Token": "the-controllers-fleet-token",
                                            "Content-Length": "12", "Accept": "application/json"},
                                   content=b'{"enabled":1}')
        self.assertNotIn("X-Hub-Token", command["headers"])
        self.assertNotIn("Content-Length", command["headers"])
        self.assertEqual("application/json", command["headers"]["Accept"])

    def test_a_result_wakes_the_caller_that_is_still_waiting(self) -> None:
        async def scenario() -> dict:
            command = fleet_pull.queue_json(NODE, "POST", "/api/hub/workstation",
                                            {"enabled": True})
            waiter = asyncio.create_task(
                fleet_pull.await_result(NODE, command["id"], timeout=5))
            await asyncio.sleep(0.02)
            fleet_pull.record_result(NODE, command["id"], 200, {"ok": True})
            return await waiter

        result = asyncio.run(scenario())
        self.assertEqual(200, result["status"])
        self.assertEqual({"ok": True}, result["json"])

    def test_recording_a_result_retires_its_command(self) -> None:
        command = fleet_pull.queue_json(NODE, "POST", "/api/hub/workstation", {"enabled": True})
        fleet_pull.record_result(NODE, command["id"], 200, {"ok": True})
        self.assertEqual([], fleet_pull.take_commands(NODE))

    def test_forgetting_a_node_drops_everything_held_for_it(self) -> None:
        fleet_pull.queue_json(NODE, "POST", "/api/hub/workstation", {"enabled": True})
        fleet_pull.forget(NODE)
        self.assertEqual({"queued": 0, "results": 0}, fleet_pull.summary(NODE))


class RelaysAreHeldForAMacTheControllerCannotDial(PullCase):
    def test_a_lite_mode_change_is_held_and_reported_as_queued(self) -> None:
        self.checkin()
        with self.assertRaises(fleet_nodes.StartupRelayError) as raised:
            asyncio.run(fleet_nodes.relay_workstation(NODE, {"enabled": True}, client=DeadDial()))
        self.assertEqual(202, raised.exception.status)
        self.assertTrue(raised.exception.body["queued"])
        held = fleet_pull.take_commands(NODE)
        self.assertEqual(["/api/hub/workstation"], [row["path"] for row in held])
        self.assertEqual({"enabled": True},
                         json.loads(__import__("base64").b64decode(held[0]["content_b64"])))

    def test_a_mac_that_is_not_calling_home_is_still_unreachable(self) -> None:
        # No check-in: nothing changed for a Mac that really is switched off.
        with self.assertRaises(fleet_nodes.StartupRelayError) as raised:
            asyncio.run(fleet_nodes.relay_workstation(NODE, {"enabled": True}, client=DeadDial()))
        self.assertEqual(502, raised.exception.status)
        self.assertEqual("NODE_UNREACHABLE", raised.exception.body["code"])
        self.assertEqual([], fleet_pull.take_commands(NODE))

    def test_every_relay_holds_the_request_it_could_not_send(self) -> None:
        self.checkin()
        cases = [
            (fleet_nodes.relay_startup, ({"action": "set_open_at_login", "value": True},),
             "/api/hub/startup/request"),
            (fleet_nodes.relay_name, ("Renamed Mac",), "/api/hub/machine-label"),
            (fleet_nodes.relay_workstation, ({"enabled": False},), "/api/hub/workstation"),
        ]
        for function, arguments, path in cases:
            with self.subTest(path=path):
                fleet_pull.forget(NODE)
                with self.assertRaises(fleet_nodes.StartupRelayError) as raised:
                    asyncio.run(function(NODE, *arguments, client=DeadDial()))
                self.assertEqual(202, raised.exception.status)
                self.assertEqual([path], [row["path"] for row in fleet_pull.take_commands(NODE)])

    def test_a_model_relay_holds_the_path_the_node_owns(self) -> None:
        self.checkin()
        with self.assertRaises(fleet_nodes.StartupRelayError) as raised:
            asyncio.run(fleet_nodes.relay_model(NODE, "POST", "/model-packages/klein/download",
                                                payload={"package_id": "klein"},
                                                timeout=httpx.Timeout(0.2, connect=0.2),
                                                client=DeadDial()))
        self.assertEqual(202, raised.exception.status)
        held = fleet_pull.take_commands(NODE)
        self.assertEqual(["/api/hub/fleet/nodes/local/model-packages/klein/download"],
                         [row["path"] for row in held])


class TheTransportDialsFirstAndFallsBack(PullCase):
    def test_a_request_the_dial_cannot_make_travels_on_the_check_in(self) -> None:
        self.checkin()

        async def scenario() -> httpx.Response:
            client = fleet_pull.client(transport=fleet_pull.PullTransport(dial=DeadDial()))
            task = asyncio.create_task(client.post(f"{ADDRESS}/studio/image/api/generate/txt2img",
                                                   json={"prompt": "a cat"}))
            # The Mac picks its work up on its next call home.
            for _ in range(200):
                await asyncio.sleep(0.01)
                held = fleet_pull.take_commands(NODE)
                if held:
                    break
            else:
                raise AssertionError("nothing was held for the Mac")
            command = held[0]
            fleet_pull.record_result(NODE, command["id"], 200, {"job_id": "j-1", "state": "done"})
            return await task

        response = asyncio.run(scenario())
        self.assertEqual(200, response.status_code)
        self.assertEqual({"job_id": "j-1", "state": "done"}, response.json())
        # Named, so the roster can say the Mac answered through its own call home.
        self.assertTrue(response.extensions.get("studiohub_pull"))

    def test_a_host_that_is_not_a_joined_node_is_never_queued_for(self) -> None:
        async def scenario() -> None:
            client = fleet_pull.client(transport=fleet_pull.PullTransport(dial=DeadDial()))
            with self.assertRaises(httpx.TransportError):
                await client.get("http://192.168.0.9:47873/api/hub/resources")

        asyncio.run(scenario())
        self.assertEqual(0, fleet_pull.summary(NODE)["queued"])

    def test_a_dial_that_works_is_used_and_nothing_is_queued(self) -> None:
        # A Mac this controller *can* reach: its most recent evidence was a successful probe, so
        # the dial is attempted — the pull channel is a fallback, never the first choice.
        self.checkin()
        nodes = fleet_nodes._read()
        nodes[NODE]["last_seen_via"] = "probe"
        fleet_nodes._write(nodes)
        seen: list[str] = []

        class Working:
            async def handle_async_request(self, request):
                seen.append(str(request.url))
                return httpx.Response(200, json={"ok": "dialled"})

        async def scenario() -> httpx.Response:
            client = fleet_pull.client(transport=fleet_pull.PullTransport(dial=Working()))
            return await client.get(f"{ADDRESS}/api/hub/resources?local_only=true")

        response = asyncio.run(scenario())
        self.assertEqual({"ok": "dialled"}, response.json())
        self.assertEqual([f"{ADDRESS}/api/hub/resources?local_only=true"], seen)
        self.assertEqual(0, fleet_pull.summary(NODE)["queued"])


class TheSchedulerSeesTheSameMac(PullCase):
    def test_the_peer_poll_uses_the_call_home_when_the_dial_fails(self) -> None:
        self.checkin()
        studio = {"id": f"{NODE}:image", "modality": "image", "machine": NODE,
                  "host": "100.120.246.9", "port": 47873, "hub_port": 47873,
                  "local": False, "fleet_node": NODE}
        snapshot = peers._checkin_fallback(studio, {"reachable": False, "status": "unreachable"})
        self.assertTrue(snapshot["reachable"])
        self.assertTrue(snapshot["pulled"])
        peers._cache[NODE] = (time.time(), snapshot)
        # The point of the whole exercise: a Mac the controller cannot dial is still given work.
        self.assertTrue(peers.dispatch_ready(studio))
        peers._cache.clear()

    def test_the_peer_poll_keeps_a_real_failure_for_a_mac_that_is_not_calling_home(self) -> None:
        studio = {"id": f"{NODE}:image", "modality": "image", "machine": NODE,
                  "local": False, "fleet_node": NODE}
        failure = {"reachable": False, "status": "unreachable"}
        self.assertEqual(failure, peers._checkin_fallback(studio, failure))


if __name__ == "__main__":
    unittest.main()
