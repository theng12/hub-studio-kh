"""The Gateway's image pool assigns one image model per Mac; the Hub keeps it and honours it.

Owner, 2026-10-24: each Mac's pool image model is Auto (by RAM) or the owner's pick, decided in the
Gateway and sent here. Only work submitted with `routing: assigned` reads it - a job that names its
model directly still runs on any Mac that has it, so thumbnails asking for plain Qwen are untouched.
"""
from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest import mock

import test_node_rename as base
from components.hub.backend import auth, broker, fleet_nodes, registry
from components.hub.backend import routing as routing_rule


class AssignedImageModelRoutes(unittest.TestCase):
    setUp = base.Routes.setUp
    client = base.Routes.client
    OWNER, FLEET = base.Routes.OWNER, base.Routes.FLEET

    def join(self) -> None:
        fleet_nodes.join({"node_id": base.NODE, "name": "n", "address": "http://100.64.0.8:47873",
                          "studios": base.STUDIOS})

    def assign(self, client, node: str, body: object, headers=FLEET):
        return client.post(f"/api/hub/fleet/nodes/{node}/assigned-image-model", json=body, headers=headers)

    def test_the_fleet_token_sets_and_clears_a_joined_macs_model(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            self.join()
            self.assertIsNone(registry.assigned_image_model(base.NODE))
            answer = self.assign(client, base.NODE, {"model": "z-image"})
            self.assertEqual(answer.json(), {"ok": True, "node": base.NODE, "model": "z-image"})
            self.assertEqual(registry.assigned_image_model(base.NODE), "z-image")
            self.assertIsNone(self.assign(client, base.NODE, {"model": None}).json()["model"])
            self.assertIsNone(registry.assigned_image_model(base.NODE))

    def test_this_controller_itself_and_refusals(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            self.assertEqual(self.assign(client, "local", {"model": "q"}, headers={}).status_code, 401)
            self.assertIsNone(registry.assigned_image_model("local"), "a refused call changes nothing")
            self.assertEqual(self.assign(client, "local", {"model": "q"}).json()["model"], "q")
            self.assertEqual(self.assign(client, "nobody-hub", {"model": "q"}).json()["code"], "NODE_UNKNOWN")
            self.assertEqual(self.assign(client, "local", {}).json()["code"], "VALUE_REQUIRED")
            self.assertEqual(self.assign(client, "local", {"model": 3}).json()["code"], "VALUE_REQUIRED")
            self.assertEqual(self.assign(client, "local", {"model": "  "}).json()["code"], "BAD_SETTING")
            self.assertEqual(registry.assigned_image_model("local"), "q", "a refused change leaves the last one")

    def test_a_nodes_own_credential_cannot_assign_another_mac(self) -> None:
        path = "/api/hub/fleet/nodes/x-hub/assigned-image-model"
        self.assertTrue(auth.fleet_path_allowed("POST", path))
        self.assertFalse(auth.node_origin_allowed("POST", path))


class AssignedRouting(unittest.TestCase):
    def test_assigned_is_a_routing_form(self) -> None:
        self.assertEqual(routing_rule.parse("assigned"), ("assigned", None))
        self.assertIsNotNone(routing_rule.parse("assignd")[1], "a typo is still refused")

    def can_run(self, routing: str, assigned: str | None, model: str = "z-image") -> bool:
        studio = {"id": "s1", "machine": "m1"}
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(broker, "assigned_image_model", lambda machine: assigned))
            stack.enter_context(mock.patch.object(broker.peers, "is_local_studio", lambda studio: False))
            stack.enter_context(mock.patch.object(broker.peers, "lite_mode_allows", lambda studio, model: True))
            stack.enter_context(mock.patch.object(broker, "_model_block_note", lambda *a: None))
            stack.enter_context(mock.patch.object(broker, "_known_memory_gb", lambda studio: None))
            return broker._can_run(studio, {"cached": True}, model, routing)

    def test_assigned_work_runs_only_on_a_mac_assigned_that_model(self) -> None:
        self.assertTrue(self.can_run("assigned", "z-image"))
        self.assertFalse(self.can_run("assigned", "qwen-turbo"), "a Mac assigned another model sits out")
        self.assertFalse(self.can_run("assigned", None), "a Mac with no assignment sits out")

    def test_work_that_names_its_model_ignores_the_assignment(self) -> None:
        self.assertTrue(self.can_run("pool", "qwen-turbo"))
        self.assertTrue(self.can_run("pool", None))


if __name__ == "__main__":
    unittest.main()
