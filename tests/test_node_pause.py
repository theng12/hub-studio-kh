"""The Gateway can pause a Mac by hand, and resume it: it gets no new work, and rejoins when resumed.

Owner request 2026-10-03 ("isn't there an enable/disable button ... in the future it will rejoin"). The Hub
already had the switch the scheduler reads for every modality (`registry.machine_enabled`); this adds the
fleet-token route a controller needs to flip it for a joined Mac, and publishes it on the roster row.
"""
from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest import mock

import test_node_rename as base
from components.hub.backend import auth, fleet_nodes, registry


class PauseRoutes(unittest.TestCase):
    setUp = base.Routes.setUp
    client = base.Routes.client
    OWNER, FLEET = base.Routes.OWNER, base.Routes.FLEET

    def join(self) -> None:
        fleet_nodes.join({"node_id": base.NODE, "name": "n", "address": "http://100.64.0.8:47873",
                          "studios": base.STUDIOS})

    def pause(self, client, node: str, body: dict, headers=FLEET):
        return client.post(f"/api/hub/fleet/nodes/{node}/pause", json=body, headers=headers)

    def test_the_fleet_token_pauses_and_resumes_a_joined_mac(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            self.join()
            self.assertTrue(registry.machine_enabled(base.NODE))
            answer = self.pause(client, base.NODE, {"paused": True})
            self.assertEqual(answer.json(), {"ok": True, "node": base.NODE, "paused": True})
            self.assertFalse(registry.machine_enabled(base.NODE))
            self.assertEqual(self.pause(client, base.NODE, {"paused": False}).json()["paused"], False)
            self.assertTrue(registry.machine_enabled(base.NODE))

    def test_this_controller_can_pause_itself_and_junk_is_refused(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            self.assertEqual(self.pause(client, "local", {"paused": True}, headers={}).status_code, 401)
            self.assertTrue(registry.machine_enabled("local"))  # the refused call changed nothing
            self.assertEqual(self.pause(client, "local", {"paused": True}).status_code, 200)
            self.assertFalse(registry.machine_enabled("local"))
            self.pause(client, "local", {"paused": False})
            self.assertEqual(self.pause(client, "nobody-hub", {"paused": True}).json()["code"], "NODE_UNKNOWN")
            self.assertEqual(self.pause(client, "local", {"paused": "yes"}).json()["code"], "VALUE_REQUIRED")

    def test_a_nodes_own_credential_cannot_pause_another_mac(self) -> None:
        path = "/api/hub/fleet/nodes/x-hub/pause"
        self.assertTrue(auth.fleet_path_allowed("POST", path))
        self.assertFalse(auth.node_origin_allowed("POST", path))

    def test_the_roster_row_says_paused(self) -> None:
        import asyncio
        with ExitStack() as stack:
            self.client(stack)
            self.join()
            registry.set_machine_enabled(base.NODE, False)

            async def probe(node, client=None):
                return {"id": node["id"], "state": "reachable"}

            with mock.patch.object(fleet_nodes, "probe", probe):
                rows = asyncio.run(fleet_nodes.inventory())["nodes"]
            self.assertIs(rows[0]["paused"], True)
            registry.set_machine_enabled(base.NODE, True)
            with mock.patch.object(fleet_nodes, "probe", probe):
                self.assertIs(asyncio.run(fleet_nodes.inventory())["nodes"][0]["paused"], False)


if __name__ == "__main__":
    unittest.main()
