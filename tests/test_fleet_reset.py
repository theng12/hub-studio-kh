"""Disassembling a group on purpose — and being able to start again afterwards.

The owner's clean-slate requirement (2026-10-25): *"if it's too hard to retain the old records, just
make sure we can disassemble the nodes and controllers so I can restart anew. It's just one focus
day of resetting and enrolling."*

Two halves, and they are deliberately different actions:

* **A node leaves** (`POST /api/hub/fleet/leave`) — it forgets the controller address, the per-node
  credential and the group token, and runs on its own again. It must work while the old controller
  is unreachable or has already forgotten it, because that is the normal case after a reset: the
  controller went first.
* **A controller resets its fleet** (`POST /api/hub/fleet/reset`) — every node row and every
  per-node credential goes, a fresh group token is minted, and the roster is **moved into the data
  directory's `_trash`, never deleted**.

What must survive both: this Mac's Machine ID, its model library, its owner settings, its local Hub
token, its jobs and its output. What must *not* survive: any bar to the same Macs joining again.
`fleet.remove` writes a tombstone that refuses an id for ever, which is right for a Mac being sold
and wrong here — so these tests pin that a reset leaves none.

The end-to-end test at the bottom is the owner's actual day: a node joins a controller, that
controller resets, the node leaves (against a controller that no longer knows it), it is given a new
Machine ID, and it joins a **different** controller and is reachable from there.
"""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from components.hub.backend import (  # noqa: E402
    control_plane, fleet_nodes, fleet_pull, peers, state_trash)
from test_standalone_connections import HubProcess  # noqa: E402

NODE = "office-imac-blue-m1-8-256-hub"
OTHER = "grace-2fs-imac-hub"
ADDRESS = "http://100.64.0.1:47873"
STUDIOS = [{"id": "image", "modality": "image"}]

# The reset route, the leave route and the credential scope of both, against a real Hub app in a
# child process whose private roots — data directory, models directory, workers manifest — are all
# under one temporary directory. No request leaves the process.
PROOF = r'''
import asyncio
import json
import os
from pathlib import Path
import httpx

state = Path(os.environ['STUDIOHUB_DATA_DIR'])
(state / '.hub_token').write_text('hub-operator-token\n')
os.chmod(state / '.hub_token', 0o600)
workers = Path(os.environ['STUDIOHUB_WORKERS_FILE'])
workers.write_text(json.dumps([{
    'id': 'image', 'modality': 'image', 'host': '127.0.0.1', 'port': 51101,
    'machine': 'local', 'local': True, 'runtime_identity': {'id': 'image-runtime'},
    'hub_token': 'hub-operator-token'}]))
os.chmod(workers, 0o600)

from components.hub import standalone
from components.hub.backend import app_release, control_plane, fleet_nodes, fleet_pull, peers

NODE = 'office-imac-blue-m1-8-256-hub'
OTHER = 'grace-2fs-imac-hub'
ADDRESS = 'http://100.64.0.1:47873'
STUDIOS = [{'id': 'image', 'modality': 'image'}]
HUB = {'X-Hub-Token': 'hub-operator-token'}


async def proof():
    app = standalone.create_app(start_background=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url='http://fixture-hub') as client:
        group = peers.fleet_token()
        joined = fleet_nodes.join({'node_id': NODE, 'name': 'Office iMac',
                                   'address': ADDRESS, 'studios': STUDIOS})
        node_credential = joined['credential']
        fleet_nodes.join({'node_id': OTHER, 'name': 'Grace iMac',
                          'address': ADDRESS, 'studios': STUDIOS})
        # Work the controller could not dial, held for a node through its own check-in.
        fleet_pull.queue_json(OTHER, 'POST', '/api/hub/workstation', {'enabled': True})
        # A node that missed the last rotation, so the previous-token file is not empty.
        peers.note_nodes_on_previous_token({OTHER: 'an-older-group-token'})
        assert peers.nodes_on_previous_token(), 'fixture: nothing to forget'
        # A gateway key pinned while this Mac was in the group.
        app_release.set_trusted_publisher({'public_key': '11' * 32, 'publisher_id': 'fixture-gw'})
        assert app_release.trusted_publisher() is not None

        # 1. A Fleet token may not reset the roster it belongs to: one leaked group credential would
        #    otherwise be a fleet-wide wipe. A node's own credential is narrower still.
        refused = await client.post('/api/hub/fleet/reset', headers={'X-Hub-Token': group})
        assert refused.status_code == 403, refused.text
        refused = await client.post('/api/hub/fleet/reset',
                                    headers={'X-Hub-Token': node_credential})
        assert refused.status_code == 403, refused.text
        refused = await client.post('/api/hub/fleet/reset')
        assert refused.status_code == 401, refused.text
        assert len(fleet_nodes._read()) == 2, 'a refused reset changed the roster'

        answer = await client.post('/api/hub/fleet/reset', headers=HUB)
        assert answer.status_code == 200, answer.text
        body = answer.json()
        assert body['reset'] is True and body['total'] == 2, body
        assert sorted(row['id'] for row in body['forgotten']) == sorted([NODE, OTHER]), body
        assert body['token'] and body['token'] != group, 'the group token was not replaced'
        assert peers.current_fleet_token() == body['token'], 'the reported token is not the live one'

        # 2. The roster is forgotten, and **not** evicted: no tombstone may refuse these ids later.
        assert fleet_nodes._read() == {}, fleet_nodes._read()
        assert fleet_nodes.removed() == {}, 'a reset recorded an eviction'
        assert peers.nodes_on_previous_token() == {}, 'the previous-token file survived the reset'
        assert fleet_nodes.verify_node_credential(NODE, node_credential) is False

        # 3. The old roster is kept, and still readable: this is the only record of the group.
        kept = Path(body['archived']['roster'])
        assert kept.is_file(), body['archived']
        assert str(state / '_trash') in str(kept), kept
        archived = json.loads(kept.read_text())
        assert set(archived['nodes']) == {NODE, OTHER}, archived
        assert archived['nodes'][NODE]['credential_hash'], 'the hash was not kept with the row'
        assert Path(body['archived']['queue']).is_file(), body['archived']
        assert json.loads(Path(body['archived']['queue']).read_text())['nodes'][OTHER]['commands'], \
            'the held work was not kept with the roster'

        # 4. The same Mac may join again at once, with the new group token, and is issued a fresh
        #    credential — the reset is not a one-way door.
        again = await client.post('/api/hub/fleet/nodes', headers={'X-Hub-Token': body['token']},
                                  json={'node_id': NODE, 'name': 'Office iMac',
                                        'address': ADDRESS, 'studios': STUDIOS})
        assert again.status_code == 201, again.text
        fresh = again.json()['credential']
        assert fresh and fresh != node_credential
        assert fleet_nodes.verify_node_credential(NODE, fresh) is True
        assert fleet_nodes.verify_node_credential(NODE, node_credential) is False, \
            'a credential revoked by the reset still works'

        # 5. The gateway pin is **not** touched by a reset: it belongs to the gateway, not to this
        #    group, and dropping it here would make every node lazily re-pin whatever answers first.
        assert app_release.trusted_publisher() is not None, 'the reset dropped the gateway pin'
        # And this controller keeps its own identity: a group reset is not an identity reset.
        assert control_plane.load_settings()['site_id'] == 'local-site', \
            'the reset moved the controller identity'

        # 6. **A replayed command may not change this Mac's membership** even though it arrives under
        #    the Hub token. The app marks every replay (`runFleetCommand`), and these four routes are
        #    refused for one: one leaked group token could otherwise make every node leave or adopt
        #    at once, which is the direction the fleet allowlists already forbid for a direct call.
        for path in ('/api/hub/fleet/leave', '/api/hub/fleet/adopt',
                     '/api/hub/fleet/reset', '/api/hub/fleet'):
            replayed = await client.post(
                path, headers={**HUB, 'X-StudioHub-Pull': 'c_fixture'},
                json={'token': 'a-group-token-value-123456789'})
            assert replayed.status_code == 403, (path, replayed.text)
            assert 'replayed' in replayed.text, (path, replayed.text)
        # A replayed *work* request is untouched: the channel is for work, and still works.
        still_reads = await client.get('/api/hub/resources?local_only=true',
                                       headers={**HUB, 'X-StudioHub-Pull': 'c_fixture'})
        assert still_reads.status_code == 200, still_reads.text
        # And the operator, with no marker, can still leave — the refusal is about who asked.
        left = await client.post('/api/hub/fleet/leave', headers=HUB)
        assert left.status_code == 200, left.text


asyncio.run(proof())
print('fleet reset: roster archived, credentials revoked, no tombstones, re-join works')
'''


class FleetStateMixin:
    """Every module that binds a path at import time, redirected at one temporary root."""

    def redirect_state(self, prefix: str) -> Path:
        self.temporary = tempfile.TemporaryDirectory(prefix=prefix)
        self.addCleanup(self.temporary.cleanup)
        data = Path(self.temporary.name)
        for module in (fleet_nodes, fleet_pull, peers):
            patcher = mock.patch.object(module, "DATA_DIR", data)
            patcher.start()
            self.addCleanup(patcher.stop)
        settings = mock.patch.object(control_plane, "SETTINGS_FILE",
                                     data / "controller_settings.json")
        settings.start()
        self.addCleanup(settings.stop)
        # `DATA_DIR` alone is not enough: these modules bound their paths when they were imported, so
        # a test that only patched `DATA_DIR` would still write the per-node credential into
        # `components/` and the group token into the repo root — and then read those files back to
        # "prove" a leave had cleared them.
        for module, attribute, name in (
            (fleet_nodes, "NODE_CREDENTIAL_FILE", ".node_credential"),
            (peers, "FLEET_TOKEN_FILE", ".fleet_token"),
            (peers, "SHARED_STUDIO_TOKEN_FILE", ".kh_studio_token"),
            (peers, "PREVIOUS_TOKENS_FILE", ".fleet_token_previous"),
            (control_plane, "DATABASE_URL_FILE", ".controller_database_url"),
        ):
            redirected = mock.patch.object(module, attribute, data / name)
            redirected.start()
            self.addCleanup(redirected.stop)
        # The writer lock lives beside the real data directory; a test does not need the file it
        # locks, and must not create one there.
        lock = mock.patch.object(control_plane, "settings_writer_lock",
                                 lambda **kwargs: contextlib.nullcontext())
        lock.start()
        self.addCleanup(lock.stop)
        previous = control_plane._settings_cache
        control_plane._settings_cache = None
        self.addCleanup(setattr, control_plane, "_settings_cache", previous)
        return data


class ResetFleetUnitTests(FleetStateMixin, unittest.TestCase):
    """`fleet_nodes.reset_fleet` on its own: what it forgets and what it keeps."""

    def setUp(self) -> None:
        self.data = self.redirect_state("studiohub-fleet-reset-")
        fleet_nodes.join({"node_id": NODE, "name": "Office iMac",
                          "address": ADDRESS, "studios": STUDIOS})
        self.row = fleet_nodes._read()[NODE]

    def test_the_roster_and_the_queue_are_moved_to_trash_not_deleted(self) -> None:
        fleet_pull.queue_json(NODE, "POST", "/api/hub/workstation", {"enabled": True})

        report = fleet_nodes.reset_fleet(by="operator")

        self.assertTrue(report["reset"])
        self.assertEqual([NODE], [row["id"] for row in report["forgotten"]])
        self.assertEqual({}, fleet_nodes._read())
        self.assertEqual({}, fleet_nodes.removed())
        roster = Path(report["archived"]["roster"])
        queue = Path(report["archived"]["queue"])
        self.assertTrue(roster.is_file())
        self.assertTrue(queue.is_file())
        # Under the data directory's own `_trash`, the folder name the owner's rule uses.
        self.assertEqual(self.data / "_trash", roster.parents[2])
        self.assertEqual("reset-fleet", roster.parents[1].name)
        kept = json.loads(roster.read_text())
        self.assertEqual(self.row["credential_hash"], kept["nodes"][NODE]["credential_hash"])
        self.assertEqual(1, len(json.loads(queue.read_text())["nodes"][NODE]["commands"]))

    def test_a_reset_leaves_no_eviction_so_the_same_id_may_join_again(self) -> None:
        fleet_nodes.reset_fleet()
        # `remove` would have recorded a tombstone here, and `join` would refuse by name for ever.
        again = fleet_nodes.join({"node_id": NODE, "name": "Office iMac",
                                  "address": ADDRESS, "studios": STUDIOS})
        self.assertIn("credential", again)
        self.assertIn(NODE, fleet_nodes._read())

    def test_a_second_reset_keeps_the_first_record(self) -> None:
        first = fleet_nodes.reset_fleet()["archived"]["roster"]
        fleet_nodes.join({"node_id": NODE, "name": "Office iMac",
                          "address": ADDRESS, "studios": STUDIOS})
        second = fleet_nodes.reset_fleet()["archived"]["roster"]
        self.assertNotEqual(first, second)
        self.assertTrue(Path(first).is_file(), "the earlier record was overwritten")
        self.assertTrue(Path(second).is_file())

    def test_an_archive_that_cannot_be_made_stops_the_reset(self) -> None:
        """A reason to fail loudly: a caller must never be told the roster was kept when it was not."""
        with mock.patch.object(state_trash, "move_to_trash", side_effect=OSError("read-only volume")):
            with self.assertRaises(OSError):
                fleet_nodes.reset_fleet()
        # Nothing was forgotten by the attempt that failed.
        self.assertEqual(self.row, fleet_nodes._read()[NODE])

    def test_a_file_that_is_not_there_is_not_an_archive(self) -> None:
        self.assertIsNone(state_trash.move_to_trash(self.data / "absent.json", reason="reset-fleet"))


class FleetTokenSweepTests(FleetStateMixin, unittest.TestCase):
    """The previous-token file is part of the group, so a reset takes it too."""

    def setUp(self) -> None:
        self.data = self.redirect_state("studiohub-fleet-token-sweep-")

    def test_a_node_that_missed_a_rotation_is_forgotten(self) -> None:
        peers.note_nodes_on_previous_token({NODE: "an-older-group-token"})
        self.assertEqual(1, len(peers.nodes_on_previous_token()))
        self.assertEqual(1, peers.clear_previous_tokens())
        self.assertEqual({}, peers.nodes_on_previous_token())
        self.assertFalse(peers.accepts_previous_token("an-older-group-token"),
                         "a retired group token still opens this Hub")
        self.assertFalse((self.data / ".fleet_token_previous").exists())


class MembershipBoundaryTests(unittest.TestCase):
    """Two channels, one rule: a controller does not decide which group this Mac belongs to.

    A direct call is already covered by the allowlists. The **pull channel** was the route around
    them — the app replays a queued request under this Mac's own Hub token, so on this side it looks
    exactly like the operator. These pin that every membership route is closed on both channels,
    and that ordinary relayed work is not caught by the same net.
    """

    MEMBERSHIP = (("POST", "/api/hub/fleet/leave"),
                  ("POST", "/api/hub/fleet/adopt"),
                  ("POST", "/api/hub/fleet/reset"),
                  ("POST", "/api/hub/fleet"))

    def test_no_membership_route_is_reachable_on_a_node_credential(self) -> None:
        from components.hub.backend import auth
        for method, path in self.MEMBERSHIP:
            with self.subTest(path=path):
                self.assertFalse(auth.node_origin_allowed(method, path))

    def test_the_owner_only_membership_routes_are_not_fleet_reachable(self) -> None:
        from components.hub.backend import auth
        # `POST /api/hub/fleet` is fleet-reachable on purpose: a controller hands its nodes a new
        # credential and head through it. It is the pull channel that must not carry it.
        for method, path in self.MEMBERSHIP[:3]:
            with self.subTest(path=path):
                self.assertFalse(auth.fleet_path_allowed(method, path))

    def test_every_membership_route_is_refused_when_replayed_from_a_queue(self) -> None:
        from components.hub.backend import auth
        for method, path in self.MEMBERSHIP:
            with self.subTest(path=path):
                self.assertTrue(auth.pull_membership_refused(method, path))
        # And nothing else is caught: a replayed Lite Mode change or resource read must still work,
        # or the channel that exists for Macs the controller cannot dial would stop working.
        for method, path in (("POST", "/api/hub/workstation"),
                             ("POST", "/api/hub/work-policy"),
                             ("POST", "/api/hub/startup/request"),
                             ("GET", "/api/hub/resources")):
            with self.subTest(path=path):
                self.assertFalse(auth.pull_membership_refused(method, path))


class RouteScopeTests(unittest.TestCase):
    """The reset route's credential scope and its archives, against a real Hub app."""

    def test_reset_is_owner_only_and_keeps_the_record(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="hub-fleet-reset-route-")
        self.addCleanup(temporary.cleanup)
        state = temporary.name
        models = Path(state) / "models"
        models.mkdir()
        env = {key: value for key, value in os.environ.items() if not key.startswith("STUDIOHUB_")}
        env.update(STUDIOHUB_DATA_DIR=state, STUDIOHUB_MODELS_DIR=str(models),
                   STUDIOHUB_WORKERS_FILE=state + "/workers.json",
                   STUDIOHUB_PACKAGED="1", STUDIOHUB_REQUIRE_AUTH="1",
                   PYTHONPATH=str(PROJECT_ROOT))
        result = subprocess.run(
            [os.environ.get("STUDIOHUB_TEST_RUNTIME", sys.executable), "-c", PROOF],
            env=env, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class NodeLeaveTests(FleetStateMixin, unittest.TestCase):
    """A node leaving: what it drops, and what it keeps."""

    def setUp(self) -> None:
        self.data = self.redirect_state("studiohub-node-leave-")

    def _adopted(self) -> None:
        peers.set_fleet_token("the-group-token-value-123456")
        fleet_nodes.set_node_credential("the-credential-the-controller-issued")
        control_plane.save_settings({"fleet_credential_origin": "adopted",
                                     "parent_controller_url": ADDRESS,
                                     "controller_id": "office-imac-hub",
                                     "role": "agent"})

    def _leave(self) -> None:
        """The local half of `POST /api/hub/fleet/leave`, in the order the route runs it."""
        peers.set_fleet_token("")
        peers.clear_previous_tokens()
        fleet_nodes.set_node_credential(None)
        control_plane.save_settings({"fleet_credential_origin": "minted", "role": "standalone",
                                     "parent_controller_url": None})

    def test_leaving_ends_the_membership_and_keeps_this_macs_own_identity(self) -> None:
        self._adopted()
        self.assertEqual(ADDRESS, fleet_nodes.membership()["controller_url"])
        peers.note_nodes_on_previous_token({"some-other-mac": "older-token"})

        self._leave()

        member = fleet_nodes.membership()
        self.assertFalse(member["member"], "this Mac is still in a group after leaving")
        self.assertIsNone(member["controller_url"])
        self.assertEqual("standalone", member["role"])
        self.assertIsNone(fleet_nodes.node_credential(), "the borrowed credential stayed on disk")
        self.assertEqual({}, peers.nodes_on_previous_token())
        settings = control_plane.load_settings()
        self.assertEqual("minted", settings["fleet_credential_origin"])
        # **Kept.** The Machine ID, the settings file, the library, the jobs: a leave is about
        # membership and nothing else, and a reset flow that lost them would be a reinstall.
        self.assertEqual("office-imac-hub", settings["controller_id"])
        self.assertFalse((self.data / ".node_credential").exists())
        self.assertTrue((self.data / "controller_settings.json").is_file())
        self.assertEqual("minted", control_plane.load_settings()["fleet_credential_origin"],
                         "the process cache still says this Mac is joined")

    def test_leaving_works_when_the_old_controller_is_gone(self) -> None:
        """The normal case after a reset: the controller went first and knows nothing of this Mac.

        "Unreachable" here is made impossible rather than simulated: any outbound HTTP client raises
        for the length of the leave, so a leave that dialled anything would fail. That is the real
        requirement — the controller's address is the one fact after a reset that may be dead, and
        leaving must not depend on it.
        """
        self._adopted()
        import httpx

        def refuse(*args, **kwargs):
            raise AssertionError("leaving a fleet must not contact the controller")

        with mock.patch.object(httpx, "AsyncClient", refuse), \
                mock.patch.object(httpx, "Client", refuse):
            self._leave()

        self.assertFalse(fleet_nodes.membership()["member"])
        self.assertIsNone(fleet_nodes.node_credential())
        self.assertEqual("minted", control_plane.load_settings()["fleet_credential_origin"])


class ResetFlowEndToEndTests(unittest.TestCase):
    """The owner's day, over real Hub processes and real sockets.

    A node joins a controller; that controller resets its fleet; the node leaves — against a
    controller that no longer knows it — is given a new Machine ID, and joins a **different**
    controller, where it answers that controller's credential and reads as reachable.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="studiohub-reset-flow-")
        cls.root = Path(cls.temporary.name)
        cls.hubs: list[HubProcess] = []
        for name, token in (("controller-one", "controller-one-hub-token"),
                            ("controller-two", "controller-two-hub-token"),
                            ("node-one", "node-one-hub-token")):
            models = cls.root / name / "models"
            models.mkdir(parents=True, exist_ok=True)
            hub = HubProcess(cls.root, name, token,
                             env={"STUDIOHUB_MODELS_DIR": str(models)})
            hub.start()
            cls.hubs.append(hub)
        cls.first, cls.second, cls.node = cls.hubs

    @classmethod
    def tearDownClass(cls) -> None:
        for hub in cls.hubs:
            hub.stop()
        cls.temporary.cleanup()

    def group_token(self, controller: HubProcess) -> str:
        """The controller's current Fleet token, read from its own state file.

        Read rather than injected: `STUDIOHUB_FLEET_TOKEN` outranks the file for *both*
        `fleet_token()` and `current_fleet_token()`, so a fixture that set it would make a reset's
        new token unreadable — and would be exercising something the app never does.
        """
        controller.request("/api/hub/controller", token=controller.token)
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                value = (controller.data / ".fleet_token").read_text().strip()
            except OSError:
                value = ""
            if value:
                return value
            time.sleep(0.2)
        raise AssertionError("the controller minted no Fleet token")

    def heartbeat(self, hub: HubProcess, token: str) -> dict:
        status, body, _ = hub.request("/api/hub/controller", token=token)
        self.assertEqual(200, status, body)
        return body

    def test_a_group_can_be_disassembled_and_its_macs_enrolled_somewhere_else(self) -> None:
        first_group = self.group_token(self.first)
        node_address = self.node.base
        # What this Mac calls itself before it belongs to anyone. Everything about it is supposed to
        # survive the whole cycle below, and only the Machine ID is deliberately changed.
        before = self.heartbeat(self.node, self.node.token)["settings"]
        self.assertIsNone(before["parent_controller_url"])
        self.assertEqual("minted", before["fleet_credential_origin"])

        # 1. The node joins controller one, the way the app does: register, then adopt.
        status, joined, _ = self.first.post_json(
            "/api/hub/fleet/nodes",
            {"node_id": NODE, "name": "Office iMac", "address": node_address, "studios": STUDIOS},
            token=first_group)
        self.assertEqual(201, status, joined)
        credential = joined["credential"]
        status, adopted, _ = self.node.post_json(
            "/api/hub/fleet/adopt",
            {"token": first_group, "controller_url": self.first.base, "node_credential": credential},
            token=self.node.token)
        self.assertEqual(200, status, adopted)
        self.assertEqual(self.first.base, adopted["controller_url"])
        joined_settings = self.heartbeat(self.node, self.node.token)["settings"]
        self.assertEqual("adopted", joined_settings["fleet_credential_origin"])
        self.assertEqual(before["controller_id"], joined_settings["controller_id"],
                         "the node's Machine ID changed just by joining")
        self.assertEqual(before["site_id"], joined_settings["site_id"])

        # Controller one can reach it, with the credential the join issued.
        status, roster, _ = self.first.request("/api/hub/fleet/nodes", token=self.first.token)
        self.assertEqual(200, status)
        row = next(entry for entry in roster["nodes"] if entry["id"] == NODE)
        self.assertEqual("reachable", row["state"], row)

        # 2. Controller one disassembles the group.
        status, reset, _ = self.first.post_json("/api/hub/fleet/reset", {}, token=self.first.token)
        self.assertEqual(200, status, reset)
        self.assertEqual(1, reset["total"], reset)
        self.assertNotEqual(first_group, reset["token"], "the group token was not replaced")
        status, roster, _ = self.first.request("/api/hub/fleet/nodes", token=self.first.token)
        self.assertEqual([], roster["nodes"], "the roster survived a reset")
        self.assertEqual([], roster["removed"], "a reset must not bar these ids from joining again")
        self.assertTrue(Path(reset["archived"]["roster"]).is_file(), "the old roster was not kept")

        # 3. The node leaves, against a controller that no longer recognises its credential. The
        #    refusal is expected, and the local half still completes: this is the whole point.
        status, refusal, _ = self.node.request(f"/api/hub/fleet/nodes/{NODE}", token=credential,
                                               method="DELETE")
        self.assertIn(status, (401, 403), refusal)
        status, left, _ = self.node.post_json("/api/hub/fleet/leave", {}, token=self.node.token)
        self.assertEqual(200, status, left)
        heartbeat = self.heartbeat(self.node, self.node.token)["settings"]
        self.assertIsNone(heartbeat["parent_controller_url"])
        self.assertEqual("minted", heartbeat["fleet_credential_origin"])
        self.assertFalse((self.node.data / ".node_credential").exists(),
                         "the borrowed credential stayed on disk after leaving")

        # 4. A clean slate, then enrolment somewhere else: a new Machine ID, set while standalone.
        status, _, _ = self.node.request(
            "/api/hub/controller", token=self.node.token, method="PUT",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"controller_id": "office-imac-renamed-01",
                             "site_id": "office-site", "site_name": "Office"}).encode())
        self.assertEqual(200, status)
        settings = self.heartbeat(self.node, self.node.token)["settings"]
        self.assertEqual("office-imac-renamed-01", settings["controller_id"])
        self.assertEqual("office-site", settings["site_id"])

        renamed = "office-imac-renamed-01"
        second_group = self.group_token(self.second)
        status, joined, _ = self.second.post_json(
            "/api/hub/fleet/nodes",
            {"node_id": renamed, "name": "Office iMac", "address": node_address,
             "studios": STUDIOS},
            token=second_group)
        self.assertEqual(201, status, joined)
        status, adopted, _ = self.node.post_json(
            "/api/hub/fleet/adopt",
            {"token": second_group, "controller_url": self.second.base,
             "node_credential": joined["credential"]},
            token=self.node.token)
        self.assertEqual(200, status, adopted)

        # 5. It works there. Controller two reaches the Mac and gets its own resources back — the
        #    evidence dispatch uses — and the Mac answers as the id it was re-enrolled under.
        status, roster, _ = self.second.request("/api/hub/fleet/nodes", token=self.second.token)
        self.assertEqual(200, status)
        row = next(entry for entry in roster["nodes"] if entry["id"] == renamed)
        self.assertEqual("reachable", row["state"], row)
        status, resources, _ = self.node.request("/api/hub/resources?local_only=true",
                                                token=second_group)
        self.assertEqual(200, status, resources)
        self.assertEqual("office-imac-renamed-01", resources["machine"]["id"],
                         "the Mac answers another controller's credential as itself")


if __name__ == "__main__":
    unittest.main()
