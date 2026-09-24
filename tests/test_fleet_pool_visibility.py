"""The pool a Mac belongs to has to be visible from both ends of the join.

Two live Hub processes on loopback ports, doing exactly what the app does when one Mac joins
another: the node adopts the controller's Fleet Token, records the controller it answers to,
and posts itself to the controller's roster.

Then both ends are read back the way the Connections screen reads them. A Mac that just joined
must be able to say which pool it is in, and the controller must be able to say which Macs are
in its pool - including a node that has been switched off, which is a node that is absent rather
than a node that was never in the group.
"""
from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from test_standalone_connections import HubProcess, free_port  # noqa: E402

STUDIOS = [{"id": "image", "modality": "image"}]


class PoolCase(unittest.TestCase):
    """A controller and the nodes that joined it, joined the way the app joins them."""

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.root = Path(self.scratch.name)
        self.addCleanup(self.scratch.cleanup)
        self.nodes: list[HubProcess] = []
        self.controller = HubProcess(self.root, "controller", "controller-hub-token",
                                     workers=[{"id": "voice", "modality": "voice",
                                               "port": free_port()}])
        self.controller.start()
        self.addCleanup(self.controller.stop)
        # Every Mac has its own identity. `controller_id` defaults to the hostname, so two Hubs on
        # one development Mac would otherwise join a group under the same name - which is exactly
        # the collision a pool listing has to be able to tell apart.
        self.identify(self.controller, "controller-hub-token", "controller-mac", "Control Mac",
                      role="controller")
        self.controller_fleet = (self.controller.data / ".fleet_token").read_text().strip()

    def identify(self, hub: HubProcess, token: str, machine_id: str, name: str,
                 role: str = "standalone") -> None:
        # The PUT answers the settings themselves; the GET wraps them under "settings".
        status, body = self.request(hub, "/api/hub/controller", token=token, method="PUT",
                                    payload={"site_id": machine_id, "site_name": name,
                                             "controller_id": machine_id, "role": role})
        self.assertEqual(200, status, body)
        self.assertEqual(machine_id, body["controller_id"])

    def add_node(self, name: str) -> HubProcess:
        node = HubProcess(self.root, name, f"{name}-hub-token",
                          workers=[{"id": "image", "modality": "image", "port": free_port()}])
        node.start()
        self.addCleanup(node.stop)
        self.identify(node, f"{name}-hub-token", name, name.replace("-", " ").title())
        self.nodes.append(node)
        return node

    # ── helpers ────────────────────────────────────────────────────────────

    def request(self, hub: HubProcess, path: str, *, token: str, method: str = "GET",
                payload: dict | None = None):
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        status, parsed, _ = hub.request(path, token=token, method=method,
                                        headers=headers, body=body)
        return status, parsed

    def identity(self, hub: HubProcess, token: str) -> dict:
        status, body = self.request(hub, "/api/hub/controller", token=token)
        self.assertEqual(200, status, body)
        return body["settings"]

    def join(self, node: HubProcess) -> str:
        """Drive the documented join: adopt the credential, record the controller, post to it.

        The node registers under the identity its own Hub holds - the same value the app sends -
        so a Mac cannot appear twice in its own pool under two different names.
        """
        token = f"{node.script.parent.name}-hub-token"
        node_id = self.identity(node, token)["controller_id"]
        status, body = self.request(
            node, "/api/hub/fleet/adopt", token=token, method="POST",
            payload={"token": self.controller_fleet, "controller_url": self.controller.base})
        self.assertEqual(200, status, "the node refused to adopt the controller's Fleet Token")
        self.assertEqual(self.controller.base, body.get("controller_url"),
                         "adopting did not record which controller this Mac joined")
        status, _ = self.request(node, "/api/hub/controller", token=token, method="PUT",
                                 payload={"role": "agent"})
        self.assertEqual(200, status, "the node refused its role")
        status, body = self.request(
            self.controller, "/api/hub/fleet/nodes", token=self.controller_fleet, method="POST",
            payload={"node_id": node_id, "name": node_id, "address": node.base,
                     "role": "agent", "studios": STUDIOS})
        self.assertEqual(201, status, f"the controller refused the join: {body}")
        return node_id

    def overview(self, hub: HubProcess, token: str) -> dict:
        status, body = self.request(hub, "/api/hub/fleet/overview", token=token)
        self.assertEqual(200, status, body)
        return body

    @staticmethod
    def row(overview: dict, machine: str) -> dict:
        return next(row for row in overview["machines"] if row["machine"] == machine)


class FleetPoolVisibility(PoolCase):
    """One controller, one node."""

    # ── the controller's pool ──────────────────────────────────────────────

    def test_controller_lists_the_node_it_just_took_in(self):
        node_id = self.join(self.add_node("node"))
        status, body = self.request(self.controller, "/api/hub/fleet/nodes",
                                    token=self.controller_fleet)
        self.assertEqual(200, status)
        self.assertEqual(1, body["summary"]["total"], "the roster lost the node it just accepted")
        self.assertEqual(node_id, body["nodes"][0]["id"])
        self.assertEqual("reachable", body["nodes"][0]["state"],
                         "a node that is up and just joined must probe as reachable")
        self.assertEqual(STUDIOS, body["nodes"][0]["studios"],
                         "the roster dropped what the node said it runs")

    def test_the_roster_names_the_controller_it_belongs_to(self):
        """A node reads this route to find its group, so the group's head has to be on it."""
        self.join(self.add_node("node"))
        status, body = self.request(self.controller, "/api/hub/fleet/nodes",
                                    token=self.controller_fleet)
        self.assertEqual(200, status)
        self.assertEqual(self.identity(self.controller, "controller-hub-token")["controller_id"],
                         body["machine"]["id"])
        self.assertTrue(body["machine"]["name"], "the controller has no name in its own roster")

    def test_controller_overview_shows_the_joined_node_under_its_own_name(self):
        node_id = self.join(self.add_node("node"))
        body = self.overview(self.controller, "controller-hub-token")
        machines = {row["machine"] for row in body["machines"]}
        self.assertIn(node_id, machines,
                      f"the controller's fleet overview does not show its own pool: {machines}")
        row = self.row(body, node_id)
        self.assertNotEqual("This Mac", row["label"],
                            "the joined node is labelled as the controller's own Mac")
        self.assertTrue(row["reachable"],
                        f"a live joined node reads as unreachable: {row.get('detail')}")
        self.assertEqual("agent", row["role"])
        self.assertEqual(2, body["summary"]["total"],
                         "the summary counts the controller but not the node it took in")

    def test_a_node_that_is_switched_off_still_belongs_to_the_pool(self):
        """Belonging is a fact about the roster; being up is a fact about now."""
        node_id = self.join(self.add_node("node"))
        self.nodes[0].stop()
        body = self.overview(self.controller, "controller-hub-token")
        row = self.row(body, node_id)
        self.assertFalse(row["reachable"])
        self.assertTrue(row["detail"], "an absent node must say why it reads as absent")
        self.assertEqual("agent", row["role"],
                         "a node that is off is still the node that joined")
        self.assertEqual(2, body["summary"]["total"],
                         "a node that is off dropped out of its own pool")

    # ── the node's pool ────────────────────────────────────────────────────

    def test_node_answers_which_controller_it_joined(self):
        node = self.add_node("node")
        self.join(node)
        status, body = self.request(node, "/api/hub/fleet/nodes", token="node-hub-token")
        self.assertEqual(200, status)
        self.assertEqual("agent", body["role"])

    def test_a_controller_is_recorded_at_adoption_and_cleared_when_it_is_not_named(self):
        """A stale controller is worse than an unknown one, so adoption always writes the field."""
        node = self.add_node("node")
        self.join(node)
        self.assertEqual(self.controller.base,
                         self.identity(node, "node-hub-token")["parent_controller_url"])
        # Re-adopting a credential without naming its controller must not leave the previous
        # group's address in place: the pool page would then show the wrong Mac as the head.
        other_fleet = (self.add_node("other").data / ".fleet_token").read_text().strip()
        status, body = self.request(node, "/api/hub/fleet/adopt", token="node-hub-token",
                                    method="POST", payload={"token": other_fleet})
        self.assertEqual(200, status, body)
        self.assertIsNone(body.get("controller_url"),
                          "an unnamed adoption must clear the controller, not keep the old one")
        self.assertIsNone(self.identity(node, "node-hub-token")["parent_controller_url"])
        pool = self.overview(node, "node-hub-token")
        self.assertNotIn(self.controller.base,
                         [row.get("address") for row in pool["machines"]],
                         "the node still points at the group it left")

    def test_a_joined_node_is_a_member_of_the_group_it_adopted(self):
        node = self.add_node("node")
        self.join(node)
        for token, value in (("role", "agent"),):
            self.assertEqual(value, self.identity(node, "node-hub-token")[token])
        self.assertEqual("adopted", self.identity(node, "node-hub-token")["fleet_credential_origin"])
        self.assertEqual(self.controller.base,
                         self.identity(node, "node-hub-token")["parent_controller_url"],
                         "the Hub does not know which controller this Mac joined")

    def test_node_can_see_the_pool_it_belongs_to(self):
        node = self.add_node("node")
        node_id = self.join(node)
        body = self.overview(node, "node-hub-token")
        machines = {row["machine"] for row in body["machines"]}
        self.assertIn("local", machines, "the node does not list itself")
        self.assertNotIn(node_id, machines,
                         "the node lists itself twice, once as This Mac and once by its id")
        controller_row = next((row for row in body["machines"] if row["machine"] != "local"), None)
        self.assertIsNotNone(controller_row,
                             f"a joined node shows no pool beyond itself: {machines}")
        self.assertEqual("controller", controller_row["role"])
        self.assertTrue(controller_row["reachable"],
                        f"the node cannot see its controller: {controller_row.get('detail')}")
        self.assertTrue(controller_row["label"], "the controller has no name in the node's pool")
        self.assertEqual(self.controller.base, controller_row["address"])
        self.assertEqual(2, body["summary"]["total"],
                         "the node counted itself but not the group it belongs to")


class FleetPoolWithTwoNodes(PoolCase):
    """Two nodes under one controller - the case where a pool is more than one other Mac."""

    def test_each_node_sees_the_other_nodes_beside_it(self):
        first, second = self.add_node("node-a"), self.add_node("node-b")
        first_id, second_id = self.join(first), self.join(second)

        body = self.overview(first, "node-a-hub-token")
        machines = {row["machine"] for row in body["machines"]}
        # This Mac is one row under its local key, then the controller and the Mac beside it.
        self.assertEqual({"local", "controller-mac", second_id}, machines,
                         f"a node does not see the whole pool: {machines}")
        self.assertNotIn(first_id, machines,
                         "the node lists itself twice, once as This Mac and once by its id")
        self.assertEqual(3, body["summary"]["total"])
        self.assertEqual(3, body["summary"]["reachable"],
                         "every Mac in this pool is up, so all three must read as reachable")
        self.assertEqual("This Mac", self.row(body, "local")["label"])
        sibling = self.row(body, second_id)
        self.assertEqual("agent", sibling["role"])
        self.assertEqual(second.base, sibling["address"])

        # And the other node's view is the same pool, seen from the other seat.
        other = {row["machine"] for row in self.overview(second, "node-b-hub-token")["machines"]}
        self.assertEqual({"local", "controller-mac", first_id}, other)

    def test_the_controller_sees_both_nodes_in_one_pool(self):
        first_id = self.join(self.add_node("node-a"))
        second_id = self.join(self.add_node("node-b"))
        body = self.overview(self.controller, "controller-hub-token")
        self.assertEqual({"local", first_id, second_id},
                         {row["machine"] for row in body["machines"]})
        self.assertEqual(3, body["summary"]["total"])
        self.assertEqual("controller", self.row(body, "local")["role"])

    def test_a_node_losing_its_controller_says_so_rather_than_showing_an_empty_pool(self):
        """The group is known even when its head is not answering, and the page must say which."""
        node = self.add_node("node")
        self.join(node)
        self.controller.stop()
        body = self.overview(node, "node-hub-token")
        self.assertFalse(body["roster_readable"],
                         "an unreachable controller must not read as a group with nothing in it")
        self.assertEqual("local", body["machines"][0]["machine"])
        controller_row = next(row for row in body["machines"] if row["machine"] != "local")
        self.assertEqual("controller", controller_row["role"])
        self.assertFalse(controller_row["reachable"],
                         "a controller that was switched off reads as reachable")


if __name__ == "__main__":
    unittest.main()
