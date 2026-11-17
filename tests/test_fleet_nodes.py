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
import time
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
        joined = fleet_nodes.join({"node_id": "mac-mini-2", "name": "Studio 2",
                                   "address": "http://192.168.1.21:8080"})
        node = joined["node"]
        self.assertEqual(node["id"], "mac-mini-2")
        self.assertEqual(node["address"], "http://192.168.1.21:8080")
        self.assertTrue(node["joined_at"] > 0)
        # **`join` returns a credential, and it is returned nowhere else.** The shared Fleet
        # token enrols a Mac; it does not authorise it for ever, because a token every member
        # holds cannot be withdrawn from one member (A8).
        self.assertTrue(joined["credential"])
        self.assertNotIn(joined["credential"],
                         fleet_nodes.store_path().read_text(encoding="utf-8"),
                         "the roster must store the hash, never the credential")
        # The registry is on disk, so a controller restart keeps its nodes. A re-join proves
        # it is the same machine by presenting the credential it was given.
        second = fleet_nodes.join({"node_id": "mac-mini-2", "name": "Studio 2 renamed",
                                   "address": "http://192.168.1.21:8080",
                                   "credential": joined["credential"]})
        reloaded = second["node"]
        self.assertEqual(reloaded["joined_at"], node["joined_at"])
        self.assertEqual(reloaded["name"], "Studio 2 renamed")
        # A node that restarted reports a new address, and the controller has to take it: the
        # whole point of re-joining on start is that nothing else repairs the stored address, so a
        # controller dispatching to the old one fails with the node sitting there healthy.
        moved = fleet_nodes.join({"node_id": "mac-mini-2", "name": "Studio 2 renamed",
                                  "address": "http://192.168.1.21:47873",
                                  "credential": second["credential"]})["node"]
        self.assertEqual(moved["address"], "http://192.168.1.21:47873")
        self.assertEqual(moved["joined_at"], node["joined_at"], "a re-join must not restamp the join")
        self.assertGreaterEqual(moved["updated_at"], node["updated_at"])
        self.assertTrue(fleet_nodes.leave("mac-mini-2"))
        self.assertFalse(fleet_nodes.leave("mac-mini-2"))

    def test_a_second_mac_cannot_take_over_an_existing_node_id(self) -> None:
        """Two identically named Mac minis must not share one roster row (H12, decision 9).

        `controller_id` defaults to `<hostname>-hub`, so before this a re-imaged Mac — or a
        second machine with the same name — silently inherited the first one's row, credential
        and place in routing. The owner's answer was to expose the id and refuse the takeover.
        """
        first = fleet_nodes.join({"node_id": "mac-mini-2", "name": "Studio 2",
                                  "address": "http://192.168.1.21:8080"})
        with self.assertRaises(ValueError) as caught:
            fleet_nodes.join({"node_id": "mac-mini-2", "name": "A different Mac",
                              "address": "http://192.168.1.99:8080"})
        self.assertIn("already has a machine called 'mac-mini-2'", str(caught.exception))
        # The machine that really is mac-mini-2 gets in, and only it.
        again = fleet_nodes.join({"node_id": "mac-mini-2", "name": "Studio 2",
                                  "address": "http://192.168.1.21:8080"},
                                 actor={"kind": "node", "node_id": "mac-mini-2"})
        self.assertTrue(again["credential"])
        # The controller's own operator may always re-add a machine deliberately.
        fleet_nodes.join({"node_id": "mac-mini-2", "name": "Studio 2",
                          "address": "http://192.168.1.21:8080"},
                         actor={"kind": "hub", "node_id": None})
        self.assertEqual(fleet_nodes._read()["mac-mini-2"]["address"],
                         "http://192.168.1.21:8080")

    def test_a_join_is_refused_rather_than_trusted(self) -> None:
        # An id is normalised to lower case, as this Hub already does for station and
        # controller ids, so the same node cannot join twice under two spellings.
        self.assertEqual(fleet_nodes.join({"node_id": "Mac-Mini-2", "name": "x",
                                           "address": "http://192.168.1.21:8080"})["node"]["id"],
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
                         {"total": 2, "reachable": 1, "unreachable": 1, "unauthorized": 0,
                          "error": 0, "removed": 0, "on_previous_token": 0,
                          # How many of the reachable ones this controller believes because the Mac
                          # called in rather than because it managed to dial it (0.6.193).
                          "check_in": 0,
                          "previous_token_until": None})

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





class RemovalIsAnEvictionTest(unittest.TestCase):
    """A6 the owner's decision B: a removed Mac must never come back on its own.

    Deleting the roster row alone was not enough (B3): the machine still held the group
    credential and re-joined on its next start, and with one shared Fleet token there was
    nothing to revoke (B2). Per-node credentials (A8) are what make the removal real — the
    controller keeps only a hash, so revoking one machine cannot touch another.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-fleet-remove-")
        self.old_data = fleet_nodes.DATA_DIR
        fleet_nodes.DATA_DIR = Path(self.temporary.name)
        self.addCleanup(self.restore)

    def restore(self) -> None:
        fleet_nodes.DATA_DIR = self.old_data
        self.temporary.cleanup()

    def _join(self, node_id: str, address: str = "http://192.168.1.21:8080") -> dict:
        return fleet_nodes.join({"node_id": node_id, "name": node_id, "address": address})

    def test_a_removed_node_is_refused_by_name_and_a_neighbour_still_works(self) -> None:
        sold = self._join("sold-mac")
        kept = self._join("kept-mac", "http://192.168.1.22:8080")
        # The neighbour's credential authenticates ...
        self.assertTrue(fleet_nodes.verify_node_credential("kept-mac", kept["credential"]))
        removed = fleet_nodes.remove("sold-mac")
        self.assertIsNotNone(removed)
        self.assertEqual(removed["id"], "sold-mac")
        self.assertGreater(removed["removed_at"], 0)
        # ... the sold Mac's does not: revocation is the point.
        self.assertFalse(fleet_nodes.verify_node_credential("sold-mac", sold["credential"]),
                         "a removed Mac's credential must stop working")
        self.assertTrue(fleet_nodes.verify_node_credential("kept-mac", kept["credential"]),
                        "removing one Mac must not touch its neighbour")
        # And the machine may not simply re-join, not even holding the group Fleet token.
        with self.assertRaises(fleet_nodes.NodeRemovedError) as caught:
            self._join("sold-mac")
        self.assertIn("removed this Mac on", str(caught.exception))
        self.assertIn("re-admit it from Connections", str(caught.exception))
        self.assertEqual(fleet_nodes.removed()["sold-mac"]["name"], "sold-mac")

    def test_a_removed_mac_stays_removed_across_a_reload(self) -> None:
        # "It does not come back when the node restarts" also means the *controller* keeps the
        # decision across its own restart: the record is in the file, not in memory.
        self._join("sold-mac")
        fleet_nodes.remove("sold-mac")
        self.assertEqual(list(fleet_nodes.removed()), ["sold-mac"])
        with self.assertRaises(fleet_nodes.NodeRemovedError):
            self._join("sold-mac")

    def test_re_admitting_is_a_deliberate_action_and_then_the_mac_may_join(self) -> None:
        self._join("sold-mac")
        fleet_nodes.remove("sold-mac")
        with self.assertRaises(fleet_nodes.NodeRemovedError):
            self._join("sold-mac")
        self.assertIsNotNone(fleet_nodes.readmit("sold-mac"))
        self.assertEqual(fleet_nodes.removed(), {})
        again = self._join("sold-mac")
        self.assertTrue(again["credential"], "a re-admitted Mac is issued a fresh credential")
        self.assertTrue(fleet_nodes.verify_node_credential("sold-mac", again["credential"]))

    def test_a_wiped_mac_holds_nothing_that_works(self) -> None:
        # A wiped Mac has no credential at all. It can only re-join by being given the group
        # Fleet token, and the controller still refuses it by name.
        self._join("wiped-mac")
        fleet_nodes.remove("wiped-mac")
        with self.assertRaises(fleet_nodes.NodeRemovedError):
            fleet_nodes.join({"node_id": "wiped-mac", "name": "Wiped",
                              "address": "http://192.168.1.21:8080",
                              "credential": ""})

    def test_only_a_hash_of_a_credential_is_ever_stored(self) -> None:
        joined = self._join("mac-mini-2")
        stored = fleet_nodes._read()["mac-mini-2"]
        self.assertNotIn("credential", stored)
        self.assertEqual(stored["credential_hash"],
                         fleet_nodes.hash_credential(joined["credential"]))
        self.assertNotIn(joined["credential"], json.dumps(stored))
        self.assertNotEqual(stored["credential_hash"], joined["credential"])

    def test_last_seen_is_stamped_only_for_the_machines_that_answered(self) -> None:
        # M20/B1: without this a Mac that is switched off and a Mac that was scrapped a month
        # ago look the same. Stamped in one batched write, and never for a silent node.
        fleet_nodes.join({"node_id": "here", "name": "Here",
                          "address": "http://192.168.1.21:8080"})
        fleet_nodes.join({"node_id": "gone", "name": "Gone",
                          "address": "http://192.168.1.22:8080"})
        before = {node_id: row["last_seen_at"] for node_id, row in fleet_nodes._read().items()}
        time.sleep(0.02)
        client = FakeClient({"http://192.168.1.21:8080": {"version": "0.4.0"}})
        inventory = asyncio.run(fleet_nodes.inventory(client))
        rows = {row["id"]: row for row in inventory["nodes"]}
        self.assertGreater(rows["here"]["last_seen_at"], before["here"])
        self.assertEqual(rows["gone"]["last_seen_at"], before["gone"],
                         "a node that did not answer must not be stamped as seen")
        self.assertEqual(fleet_nodes._read()["gone"]["last_seen_at"], before["gone"])

    def test_the_roster_publishes_removed_machines_and_never_a_credential(self) -> None:
        joined = self._join("sold-mac")
        fleet_nodes.remove("sold-mac")
        inventory = asyncio.run(fleet_nodes.inventory(FakeClient({})))
        self.assertEqual(inventory["summary"]["removed"], 1)
        self.assertEqual(inventory["removed"][0]["id"], "sold-mac")
        blob = json.dumps(inventory)
        self.assertNotIn(joined["credential"], blob)
        self.assertNotIn("credential_hash", blob)


class TheRegistryMigratesFromItsOldShapeTest(unittest.TestCase):
    """A controller's existing roster must survive the update that adds credentials."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-fleet-migrate-")
        self.old_data = fleet_nodes.DATA_DIR
        fleet_nodes.DATA_DIR = Path(self.temporary.name)
        self.addCleanup(self.restore)

    def restore(self) -> None:
        fleet_nodes.DATA_DIR = self.old_data
        self.temporary.cleanup()

    def test_a_flat_roster_file_is_read_as_the_node_map(self) -> None:
        fleet_nodes.store_path().write_text(json.dumps({
            "mac-mini-2": {"id": "mac-mini-2", "name": "Studio 2",
                           "address": "http://192.168.1.21:8080", "joined_at": 1.0},
        }))
        nodes = fleet_nodes._read()
        self.assertEqual(list(nodes), ["mac-mini-2"])
        self.assertEqual(fleet_nodes.removed(), {})
        # A row from before credentials existed reads as "the next join must prove itself",
        # not as "trusted".
        self.assertFalse(nodes["mac-mini-2"].get("credential_hash"))
        # And writing it back does not lose the node.
        joined = fleet_nodes.join({"node_id": "mac-mini-2", "name": "Studio 2",
                                   "address": "http://192.168.1.21:8080"},
                                  actor={"kind": "hub", "node_id": None})
        self.assertTrue(joined["credential"])
        self.assertEqual(list(fleet_nodes._read()), ["mac-mini-2"])


class NodeStatusAndLabelTest(unittest.TestCase):
    """Symmetric removal: a node can ask whether it is still listed, and a rename reaches the roster."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-fleet-status-")
        self.old_data = fleet_nodes.DATA_DIR
        fleet_nodes.DATA_DIR = Path(self.temporary.name)
        self.addCleanup(self.restore)

    def restore(self) -> None:
        fleet_nodes.DATA_DIR = self.old_data
        self.temporary.cleanup()

    def _join(self, node_id: str, name: str = "") -> dict:
        return fleet_nodes.join({"node_id": node_id, "name": name or node_id,
                                 "address": "http://192.168.1.21:8080"})

    def test_status_says_member_removed_or_unknown(self) -> None:
        self._join("kept")
        self._join("sold")
        fleet_nodes.remove("sold", by="operator")
        self.assertEqual(fleet_nodes.status("kept"), {"id": "kept", "state": "member"})
        gone = fleet_nodes.status("sold")
        self.assertEqual(gone["state"], "removed")
        self.assertGreater(gone["removed_at"], 0)
        self.assertIn("removed this Mac on", gone["detail"])
        self.assertEqual(fleet_nodes.status("never-heard-of-it"),
                         {"id": "never-heard-of-it", "state": "unknown"})

    def test_a_voluntary_leave_is_forgotten_not_evicted(self) -> None:
        self._join("leaver")
        self.assertTrue(fleet_nodes.leave("leaver"))
        self.assertEqual(fleet_nodes.status("leaver")["state"], "unknown")
        self.assertEqual(fleet_nodes.removed(), {})
        self._join("leaver")  # no tombstone, so it may simply join again

    def test_a_revoked_credential_is_told_it_was_removed_and_nothing_publishes_the_hash(self) -> None:
        sold = self._join("sold")
        kept = self._join("kept", "kept")
        self.assertIsNone(fleet_nodes.revoked_detail(sold["credential"]), "still valid until removed")
        fleet_nodes.remove("sold", by="operator")
        told = fleet_nodes.revoked_detail(sold["credential"])
        self.assertIsNotNone(told, "the removed Mac's credential must be recognised as revoked")
        self.assertIn("removed this Mac on", told)
        self.assertIsNone(fleet_nodes.revoked_detail(kept["credential"]))
        self.assertIsNone(fleet_nodes.revoked_detail(""))
        self.assertNotIn("revoked_hash", json.dumps(fleet_nodes.removed()))
        # Re-admitting ends it: the old credential is then just an unknown one again.
        fleet_nodes.readmit("sold")
        self.assertIsNone(fleet_nodes.revoked_detail(sold["credential"]))

    def test_a_revoked_credential_is_refused_by_code_not_by_sentence(self) -> None:
        sold = self._join("sold")
        self.assertIsNone(fleet_nodes.revoked_refusal(sold["credential"]))
        fleet_nodes.remove("sold", by="operator")
        body = fleet_nodes.revoked_refusal(sold["credential"])
        # The Mac classifies on these; the sentence stays for the person reading it.
        self.assertFalse(body["ok"])
        self.assertEqual(body["state"], "revoked")
        self.assertEqual(body["code"], "IDENTITY_REVOKED")
        self.assertEqual(body["reconnect"], "join")
        self.assertEqual(body["node_id"], "sold")
        self.assertRegex(body["removed_at"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn("removed this Mac on", body["detail"])
        self.assertNotIn("revoked_hash", json.dumps(body), "the hash never leaves the roster")
        fleet_nodes.readmit("sold")
        self.assertIsNone(fleet_nodes.revoked_refusal(sold["credential"]))

    def test_a_rename_on_the_mac_updates_the_roster_name(self) -> None:
        self._join("studio-mac-1102-x", "studio-mac-1102")
        self.assertTrue(fleet_nodes.rename("studio-mac-1102-x", "studio-mac-1501"))
        self.assertEqual(fleet_nodes._read()["studio-mac-1102-x"]["name"], "studio-mac-1501")
        self.assertFalse(fleet_nodes.rename("studio-mac-1102-x", "studio-mac-1501"))
        self.assertFalse(fleet_nodes.rename("studio-mac-1102-x", "   "))
        self.assertFalse(fleet_nodes.rename("nobody", "x"))

    def test_never_connected_needs_a_day_and_no_answer_since_joining(self) -> None:
        now = time.time()
        base = {"id": "n", "name": "n", "address": "http://127.0.0.1:1"}
        for label, joined, seen, expected in (
                ("old and silent", now - 3 * 86400, now - 3 * 86400, True),
                ("old but answered since", now - 3 * 86400, now - 60, False),
                ("joined an hour ago", now - 3600, now - 3600, False)):
            with self.subTest(label):
                row = asyncio.run(fleet_nodes.probe(
                    {**base, "joined_at": joined, "last_seen_at": seen}, FakeClient({})))
                self.assertIs(row["never_connected"], expected)


class TheNodeIdentityStopsFollowingTheHostnameTest(unittest.TestCase):
    """C3: a rename in macOS before the first save used to change the id a controller knows a Mac by."""

    def setUp(self) -> None:
        from components.hub.backend import control_plane
        self.control_plane = control_plane
        self.temporary = tempfile.TemporaryDirectory()
        self.old_file = control_plane.SETTINGS_FILE
        self.old_cache = control_plane._settings_cache
        control_plane.SETTINGS_FILE = Path(self.temporary.name) / "controller_settings.json"
        control_plane._settings_cache = None
        self.addCleanup(self.restore)

    def restore(self) -> None:
        self.control_plane.SETTINGS_FILE = self.old_file
        self.control_plane._settings_cache = self.old_cache
        self.temporary.cleanup()

    def test_the_first_id_is_kept_when_the_hostname_changes(self) -> None:
        from unittest import mock
        with mock.patch("socket.gethostname", return_value="studio-mac-1102.local"):
            self.control_plane.ensure_controller_id()
        self.control_plane._settings_cache = None
        with mock.patch("socket.gethostname", return_value="studio-mac-1501.local"):
            self.assertEqual(self.control_plane.load_settings()["controller_id"],
                             "studio-mac-1102-hub")

    def test_a_damaged_settings_file_is_left_alone(self) -> None:
        self.control_plane.SETTINGS_FILE.write_text("{not json")
        self.control_plane.ensure_controller_id()
        self.assertEqual(self.control_plane.SETTINGS_FILE.read_text(), "{not json")


class JoinedRenderWorkerReadTest(unittest.TestCase):
    """A controller republishes a joined leaf's render row from one bounded extra read.

    The row is routing evidence, not authority: a node that refuses the read, runs an older
    Hub, or answers something that is not that closed row simply has no render worker here,
    and the probe's own reachability answer never changes because of it.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-render-cap-")
        self.old_data = fleet_nodes.DATA_DIR
        fleet_nodes.DATA_DIR = Path(self.temporary.name)
        fleet_nodes._render_capability_cache.clear()
        self.addCleanup(self.restore)

    def restore(self) -> None:
        fleet_nodes.DATA_DIR = self.old_data
        fleet_nodes._render_capability_cache.clear()
        self.temporary.cleanup()

    def _capability(self) -> dict:
        bounds = {"max_rss_bytes": 10737418240, "max_wall_seconds": 14400,
                  "min_available_memory_bytes": 2147483648, "output_limit_bytes": 8589934592}
        return {
            "operation": "video.final_render",
            "physical_machine_id": "local",
            "hardware_class": "m4-16gb",
            "profile_digest": "a1" * 32,
            "tools_digest": "b2" * 32,
            "compiler_digest": "c3" * 32,
            "qualification_digest": "d4" * 32,
            "selected_tier": "H.264 · hardware (videotoolbox, q:v 65)",
            "exact_encoder": "h264_videotoolbox",
            "font_support": False,
            "bounds": bounds,
            "profile_execution_bounds": {"max_duration_sec": 10800},
            "production_eligible": False,
            "production_enabled": False,
            "available_capacity": {"slots": 0, "slots_total": 1},
            "qualification_candidates": [{
                "vector_id": "intent-1", "hardware_class": "m4-16gb",
                "profile_digest": "a1" * 32, "tools_digest": "b2" * 32,
                "compiler_digest": "c3" * 32, "qualification_digest": "d4" * 32,
                "selected_tier": "H.264 · hardware (videotoolbox, q:v 65)",
                "exact_encoder": "h264_videotoolbox", "font_support": False,
                "bounds": dict(bounds),
                "profile_execution_bounds": {"max_duration_sec": 10800},
                "production_eligible": False, "qualification_only": True,
                "available_capacity": {"slots": 1, "slots_total": 1},
            }],
        }

    def _client(self, capability_status: int, body: object):
        import httpx

        class Client:
            def __init__(self):
                self.calls: list[str] = []

            async def get(self, url, timeout=None, **kwargs):
                self.calls.append(url)
                if url.endswith(fleet_nodes.PROBE_CAPABILITY_PATH):
                    if capability_status == 200:
                        return httpx.Response(200, json=body,
                                              request=httpx.Request("GET", url))
                    return httpx.Response(capability_status, request=httpx.Request("GET", url))
                return httpx.Response(200, json={"version": "0.6.194", "host": {}},
                                      request=httpx.Request("GET", url))

            async def aclose(self):
                pass

        return Client()

    def _probe(self, client) -> dict:
        return asyncio.run(fleet_nodes.probe(
            {"id": "shop-m4", "name": "shop-m4", "address": "http://127.0.0.1:9"}, client))

    def test_a_live_row_is_remembered_and_expires_as_a_hint(self) -> None:
        client = self._client(200, self._capability())
        row = self._probe(client)
        self.assertEqual(row["state"], "reachable")
        capability = fleet_nodes.render_capabilities()["shop-m4"]
        self.assertEqual(capability["operation"], "video.final_render")
        self.assertEqual(
            len(capability["qualification_candidates"][0]["profile_digest"]), 64)
        self.assertIn(fleet_nodes.PROBE_CAPABILITY_PATH, client.calls[-1])
        self.assertEqual(
            fleet_nodes.render_capabilities(now=time.time() + 46), {})

    def test_an_older_hub_or_a_refusal_leaves_no_worker_and_no_state_change(self) -> None:
        for status in (404, 401, 500):
            with self.subTest(status):
                fleet_nodes._render_capability_cache.clear()
                row = self._probe(self._client(status, None))
                self.assertEqual(row["state"], "reachable")
                self.assertEqual(fleet_nodes.render_capabilities(), {})

    def test_a_body_that_is_not_that_closed_row_is_not_remembered(self) -> None:
        body = {**self._capability(), "unexpected": True}
        self._probe(self._client(200, body))
        self.assertEqual(fleet_nodes.render_capabilities(), {})


if __name__ == "__main__":
    unittest.main()
