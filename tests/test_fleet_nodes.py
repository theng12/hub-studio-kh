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
        # `render` is the unrouted example now: it is a closed modality in the Hub's own
        # broker table (its local ffmpeg step) and deliberately has no worker, while `video`
        # — this list's original example — became a real worker modality on 2026-09-26.
        # Note what is *not* invalid: an id that differs from its modality, because a
        # Subtitle worker legitimately reports `id: subtitle, modality: transcription`.
        for studios in ([{"id": "image", "modality": "render"}],
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

    def test_a_node_may_offer_music(self) -> None:
        """Every current app runs Music, so refusing that modality blocks the whole join."""
        from components.hub.backend import peers
        fleet_nodes.join({"node_id": "mac-mini-2", "name": "Studio 2",
                          "address": "http://192.168.1.21:47873",
                          "studios": [{"id": "image", "modality": "image"},
                                      {"id": "music", "modality": "music"}]})
        entries = {entry["modality"]: entry for entry in fleet_nodes.remote_studios()}
        self.assertIn("music", entries)
        url, _ = peers.studio_request(entries["music"], "/api/generate/txt2music")
        self.assertEqual(url,
                         "http://192.168.1.21:47873/studio/music/api/generate/txt2music")

    def test_every_modality_the_hub_routes_can_join_and_is_dispatchable(self) -> None:
        """The join vocabulary is the Hub's routing vocabulary, derived rather than copied.

        `NODE_MODALITIES` used to be this file's own copy of it, and `chat` was missing: a node
        running the shipped Chat worker was refused outright at join time (`raise ValueError`),
        and an already-joined node's chat studio was silently skipped when the controller built
        its registry. The comment above that set records the identical Music incident, so this
        test asserts the rule — every routed modality is accepted and produced — rather than a
        membership that has to be hand-edited for the next one.
        """
        from components.hub.backend import vocabulary
        modalities = sorted(vocabulary.WORKER_FOR_MODALITY)
        self.assertTrue(modalities, "the Hub routes no modality at all")
        self.assertEqual(set(fleet_nodes.NODE_MODALITIES), set(modalities))
        fleet_nodes.join({
            "node_id": "mac-mini-2", "name": "Studio 2",
            "address": "http://192.168.1.21:47873",
            "studios": [{"id": f"studio-{index}", "modality": modality}
                        for index, modality in enumerate(modalities)],
        })
        entries = fleet_nodes.remote_studios()
        self.assertEqual(
            sorted(entry["modality"] for entry in entries), modalities,
            "a modality the Hub routes could not join a node, or was dropped from the "
            "controller's registry",
        )

    def test_an_unknown_modality_is_refused_by_name_and_says_where_to_add_it(self) -> None:
        with self.assertRaises(ValueError) as caught:
            fleet_nodes.join({"node_id": "mac-mini-2", "name": "Studio 2",
                              "address": "http://192.168.1.21:47873",
                              "studios": [{"id": "hologram", "modality": "hologram"}]})
        message = str(caught.exception)
        self.assertIn("hologram", message)
        self.assertIn("vocabulary.py", message,
                      "the refusal must name the file where a modality is registered")


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
        # A node that restarted reports a new address, and the controller has to take it: the
        # whole point of re-joining on start is that nothing else repairs the stored address, so a
        # controller dispatching to the old one fails with the node sitting there healthy.
        moved = fleet_nodes.join({"node_id": "mac-mini-2", "name": "Studio 2 renamed",
                                  "address": "http://192.168.1.21:47873"})
        self.assertEqual(moved["address"], "http://192.168.1.21:47873")
        self.assertEqual(moved["joined_at"], node["joined_at"], "a re-join must not restamp the join")
        self.assertGreaterEqual(moved["updated_at"], node["updated_at"])
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
        # `unauthorized` is counted separately from `unreachable`: a node that answers and refuses
        # this controller is a credential problem, not a silent Mac (0.6.16).
        self.assertEqual(inventory["summary"],
                         {"total": 2, "reachable": 1, "unreachable": 1, "unauthorized": 0, "error": 0})

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


class FleetRoleTests(unittest.IsolatedAsyncioTestCase):
    """The fleet roster must report the role this Hub is actually in.

    `inventory()` hardcoded `"role": "controller"`, so a standalone Mac advertised itself as able to
    own nodes while `/api/hub/capabilities` reported `standalone` from the same setting - two
    endpoints, two answers, on the field a fleet gateway uses to decide whether a location can
    accept an enrolment. Measured on a standalone Mac, 2026-09-18.

    Isolated in a temporary data directory on purpose. This test writes a role, and an earlier
    version of it wrote `controller_settings.json` into the source tree. `SETTINGS_FILE` is bound
    at import time, so redirecting the environment alone is not enough; the module attribute is
    redirected too, exactly as the roster tests above do it.
    """

    def setUp(self):
        from components.hub.backend import control_plane
        self.temporary = tempfile.TemporaryDirectory()
        self.old_env = os.environ.get("STUDIOHUB_DATA_DIR")
        os.environ["STUDIOHUB_DATA_DIR"] = self.temporary.name
        self.control_plane = control_plane
        self.old_file = control_plane.SETTINGS_FILE
        self.old_cache = control_plane._settings_cache
        control_plane.SETTINGS_FILE = Path(self.temporary.name) / "controller_settings.json"
        control_plane._settings_cache = None

    def tearDown(self):
        self.control_plane.SETTINGS_FILE = self.old_file
        self.control_plane._settings_cache = self.old_cache
        if self.old_env is None:
            os.environ.pop("STUDIOHUB_DATA_DIR", None)
        else:
            os.environ["STUDIOHUB_DATA_DIR"] = self.old_env
        self.temporary.cleanup()

    async def test_a_standalone_hub_does_not_call_itself_a_controller(self):
        from components.hub.backend import fleet_nodes
        self.control_plane.save_settings({"role": "standalone"})
        self.assertEqual((await fleet_nodes.inventory())["role"], "standalone")
        self.control_plane.save_settings({"role": "controller"})
        self.assertEqual((await fleet_nodes.inventory())["role"], "controller")

    async def test_the_roster_and_the_saved_setting_always_agree(self):
        """One role per Hub, whichever endpoint is asked."""
        from components.hub.backend import fleet_nodes
        for role in ("standalone", "controller", "agent"):
            self.control_plane.save_settings({"role": role})
            self.assertEqual((await fleet_nodes.inventory())["role"], role)
            self.assertEqual(self.control_plane.load_settings()["role"], role)

    def test_this_test_never_writes_into_the_source_tree(self):
        """The guard for the mistake this file already made once."""
        settings = self.control_plane.SETTINGS_FILE
        self.assertTrue(str(settings).startswith(self.temporary.name),
                        f"role settings would be written to {settings}")

    async def test_a_node_row_reports_what_the_node_is_and_runs(self):
        """A roster row must answer "what is this Mac and what can it run".

        `probe()` dropped the `role` and `studios` recorded at join time, so a Fleet page could
        show a node's name and reachability and nothing about the work it could take.
        """
        from components.hub.backend import fleet_nodes
        node = {"id": "node-a", "name": "Studio 2", "address": "http://127.0.0.1:1",
                "role": "agent", "studios": [{"id": "voice", "modality": "voice"}],
                "joined_at": 1.0}
        row = await fleet_nodes.probe(node)
        self.assertEqual(row["role"], "agent")
        self.assertEqual(row["studios"], [{"id": "voice", "modality": "voice"}])


if __name__ == "__main__":
    unittest.main()
