"""One app as a controller, another as a node: joining by address, and honest status.

The controller stores who joined and where they answer, and then asks each node directly
whether it is there. A stored "joined" flag is never shown as availability, because a Mac that
was joined last week and has been switched off must read as unreachable.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub.backend import fleet_nodes


class FakeResponse:
    def __init__(self, status_code: int, payload: object):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        if self._payload is None:
            raise ValueError("no body")
        return self._payload


class FakeClient:
    """Answers for the addresses it knows and refuses the rest, like a real network."""

    def __init__(self, answers: dict[str, object], *, fail: set[str] | None = None):
        self.answers = answers
        self.fail = fail or set()
        self.calls: list[str] = []

    async def get(self, url: str, timeout=None):
        self.calls.append(url)
        for address, payload in self.answers.items():
            if url.startswith(address):
                if address in self.fail:
                    import httpx
                    raise httpx.ConnectError("connection refused")
                return FakeResponse(200, payload)
        import httpx
        raise httpx.ConnectError("connection refused")

    async def aclose(self):
        return None


class RemoteStudioTests(unittest.TestCase):
    """A joined node must become something the Hub can actually dispatch to."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-remote-")
        self.old = os.environ.copy()
        self.old_data = fleet_nodes.DATA_DIR
        os.environ["STUDIOHUB_DATA_DIR"] = self.temporary.name
        fleet_nodes.DATA_DIR = Path(self.temporary.name)
        os.environ.pop("STUDIOHUB_FLEET_TOKEN", None)
        self.addCleanup(self.restore)

    def restore(self) -> None:
        fleet_nodes.DATA_DIR = self.old_data
        os.environ.clear()
        os.environ.update(self.old)
        self.temporary.cleanup()

    def test_a_joined_node_becomes_one_entry_per_studio(self) -> None:
        fleet_nodes.join({"node_id": "mac-mini-2", "name": "Studio 2",
                          "address": "http://192.168.1.21:47873",
                          "studios": [{"id": "image", "modality": "image"},
                                      {"id": "voice", "modality": "voice"},
                                      {"id": "subtitle", "modality": "transcription"}]})
        entries = {row["id"]: row for row in fleet_nodes.remote_studios()}
        self.assertEqual(sorted(entries),
                         ["mac-mini-2:image", "mac-mini-2:subtitle", "mac-mini-2:voice"])
        image = entries["mac-mini-2:image"]
        self.assertEqual(image["modality"], "image")
        self.assertEqual(image["host"], "192.168.1.21")
        self.assertEqual(image["port"], 47873)
        self.assertEqual(image["hub_port"], 47873)
        # These three are what decide how the existing routing treats the entry.
        self.assertEqual(image["machine"], "mac-mini-2")
        self.assertIs(image["local"], False)
        self.assertTrue(image["hub_token"])

    def test_a_node_with_no_studios_contributes_nothing(self) -> None:
        # A Mac whose workers all failed to start must not be handed work.
        fleet_nodes.join({"node_id": "empty", "name": "Empty",
                          "address": "http://192.168.1.21:47873", "studios": []})
        self.assertEqual(fleet_nodes.remote_studios(), [])

    def test_a_node_cannot_claim_a_studio_it_may_not_run(self) -> None:
        for studios in ([{"id": "image", "modality": "video"}],
                        [{"id": "image", "modality": ""}],
                        [{"id": "BAD ID", "modality": "image"}],
                        [{"id": "image"}],
                        "not a list",
                        [{"id": "image", "modality": "image"}] * 9):
            with self.subTest(studios=studios):
                with self.assertRaises(ValueError):
                    fleet_nodes.join({"node_id": "n", "name": "N",
                                      "address": "http://192.168.1.21:47873",
                                      "studios": studios})

    def test_a_remote_entry_routes_over_the_peer_path(self) -> None:
        """The point of the entry shape: the request must go to the node, not a local worker."""
        from components.hub.backend import peers
        fleet_nodes.join({"node_id": "mac-mini-2", "name": "Studio 2",
                          "address": "http://192.168.1.21:47873",
                          "studios": [{"id": "image", "modality": "image"}]})
        entry = fleet_nodes.remote_studios()[0]
        self.assertFalse(peers.is_local_studio(entry))
        url, headers = peers.studio_request(entry, "/api/generate/txt2img")
        self.assertEqual(url, "http://192.168.1.21:47873/studio/image/api/generate/txt2img")
        self.assertIn("X-Hub-Token", headers)


class FleetNodeStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-fleet-")
        self.old = os.environ.copy()
        self.old_data = fleet_nodes.DATA_DIR
        os.environ["STUDIOHUB_DATA_DIR"] = self.temporary.name
        fleet_nodes.DATA_DIR = Path(self.temporary.name)
        self.addCleanup(self.restore)

    def restore(self) -> None:
        fleet_nodes.DATA_DIR = self.old_data
        os.environ.clear()
        os.environ.update(self.old)
        self.temporary.cleanup()

    def test_a_node_joins_with_an_address_and_can_leave(self) -> None:
        node = fleet_nodes.join({"node_id": "mac-mini-2", "name": "Studio 2",
                                 "address": "http://192.168.1.21:8080"})
        self.assertEqual(node["id"], "mac-mini-2")
        self.assertEqual(node["address"], "http://192.168.1.21:8080")
        self.assertTrue(node["joined_at"] > 0)
        # The registry is on disk, so a controller restart keeps its nodes.
        reloaded = fleet_nodes.join({"node_id": "mac-mini-2", "name": "Studio 2 renamed",
                                     "address": "http://192.168.1.21:8080"})
        self.assertEqual(reloaded["joined_at"], node["joined_at"])
        self.assertEqual(reloaded["name"], "Studio 2 renamed")
        self.assertTrue(fleet_nodes.leave("mac-mini-2"))
        self.assertFalse(fleet_nodes.leave("mac-mini-2"))

    def test_a_join_is_refused_rather_than_trusted(self) -> None:
        # An id is normalised to lower case, as this Hub already does for station and
        # controller ids, so the same node cannot join twice under two spellings.
        self.assertEqual(fleet_nodes.join({"node_id": "Mac-Mini-2", "name": "x",
                                           "address": "http://192.168.1.21:8080"})["id"],
                         "mac-mini-2")
        fleet_nodes.leave("mac-mini-2")
        for payload in (
            {"node_id": "has space", "name": "x", "address": "http://192.168.1.21:8080"},
            {"node_id": "-leading", "name": "x", "address": "http://192.168.1.21:8080"},
            {"node_id": "", "name": "x", "address": "http://192.168.1.21:8080"},
            {"node_id": "ok", "name": "", "address": "http://192.168.1.21:8080"},
            {"node_id": "ok", "name": "x" * 121, "address": "http://192.168.1.21:8080"},
            # Not http, no port, a name instead of an address, credentials, a path, a query.
            {"node_id": "ok", "name": "x", "address": "https://192.168.1.21:8080"},
            {"node_id": "ok", "name": "x", "address": "http://192.168.1.21"},
            {"node_id": "ok", "name": "x", "address": "http://node.local:8080"},
            {"node_id": "ok", "name": "x", "address": "http://user:pass@192.168.1.21:8080"},
            {"node_id": "ok", "name": "x", "address": "http://192.168.1.21:8080/jobs"},
            {"node_id": "ok", "name": "x", "address": "http://192.168.1.21:8080?x=1"},
            # A public address would turn a join into a request forgery against the internet.
            {"node_id": "ok", "name": "x", "address": "http://93.184.216.34:80"},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    fleet_nodes.join(payload)

    def test_private_loopback_and_tailscale_addresses_are_accepted(self) -> None:
        for address in ("http://127.0.0.1:8080", "http://192.168.1.21:8080",
                        "http://10.0.0.4:9000", "http://100.101.102.103:8080"):
            with self.subTest(address=address):
                fleet_nodes.join({"node_id": "node", "name": "Studio", "address": address})
                fleet_nodes.leave("node")

    def test_status_comes_from_asking_the_node(self) -> None:
        fleet_nodes.join({"node_id": "here", "name": "Here",
                          "address": "http://192.168.1.21:8080"})
        fleet_nodes.join({"node_id": "gone", "name": "Gone",
                          "address": "http://192.168.1.22:8080"})
        client = FakeClient({"http://192.168.1.21:8080": {"version": "0.4.0",
                                                          "source": "studiohub-mac@abc"}})
        inventory = asyncio.run(fleet_nodes.inventory(client))
        rows = {row["id"]: row for row in inventory["nodes"]}
        self.assertEqual(rows["here"]["state"], "reachable")
        self.assertEqual(rows["here"]["version"], "0.4.0")
        # Joined but switched off must read as unreachable, never as available.
        self.assertEqual(rows["gone"]["state"], "unreachable")
        self.assertIn("connection refused", rows["gone"]["detail"])
        self.assertEqual(inventory["summary"],
                         {"total": 2, "reachable": 1, "unreachable": 1, "error": 0})

    def test_a_node_that_answers_nonsense_is_an_error_not_a_success(self) -> None:
        fleet_nodes.join({"node_id": "odd", "name": "Odd",
                          "address": "http://192.168.1.23:8080"})
        client = FakeClient({"http://192.168.1.23:8080": "not a dict"})
        inventory = asyncio.run(fleet_nodes.inventory(client))
        self.assertEqual(inventory["nodes"][0]["state"], "error")
        self.assertEqual(inventory["summary"]["reachable"], 0)

    def test_the_registry_never_grows_past_its_bound(self) -> None:
        for index in range(fleet_nodes.MAX_NODES):
            fleet_nodes.join({"node_id": f"node-{index}", "name": "Studio",
                              "address": "http://192.168.1.21:8080"})
        with self.assertRaises(ValueError):
            fleet_nodes.join({"node_id": "one-too-many", "name": "Studio",
                              "address": "http://192.168.1.21:8080"})

    def test_a_corrupt_registry_reads_as_empty_rather_than_crashing(self) -> None:
        fleet_nodes.store_path().write_text("{not json")
        self.assertEqual(fleet_nodes._read(), {})
        inventory = asyncio.run(fleet_nodes.inventory(FakeClient({})))
        self.assertEqual(inventory["summary"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
