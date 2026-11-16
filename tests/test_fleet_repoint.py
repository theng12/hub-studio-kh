"""Moving a Mac to another controller **without re-joining it** — the address-only repoint.

The owner's decision (2026-10-25): *"Nodes must be re-pointable to a new controller WITHOUT a full
re-join; full re-join stays as the fallback."* A full re-join is not a neutral repair: it mints a new
per-node credential and re-registers the machine, so doing it to forty Macs to hand two of them to
another controller reissues forty credentials and forty roster rows. A repoint writes two keys — the
parent address and a receipt — and nothing else about the Mac changes.

What must survive a repoint, and is asserted here: this Mac's Machine ID, its per-node credential,
the group Fleet token, its own Hub token, the pinned gateway publisher key and its model library.
What must *not* survive: the address it left.

The trust boundary is the one the design names: the **local owner or the current group Fleet
credential**, never a node's own credential, and the destination must already hold the transferred
roster — because the node proves continuity by presenting the very credential its old controller
issued. A destination that cannot recognise it is refused, and the Mac keeps the controller it has.

The end-to-end test at the bottom uses two real controllers and a real node: the destination's roster
is seeded with the node's credential hash (what a roster transfer produces), the node is repointed,
and it answers the new controller afterwards without a single join call.
"""
from __future__ import annotations

import asyncio
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

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from components.hub.backend import (  # noqa: E402
    app_release, control_plane, fleet_nodes, fleet_pull, peers)
from test_standalone_connections import HubProcess  # noqa: E402

NODE = "office-imac-blue-m1-8-256-hub"
OTHER = "grace-2fs-imac-hub"
STUDIOS = [{"id": "image", "modality": "image"}]
OLD = "http://100.64.0.10:47873"
NEW = "http://100.64.0.20:47873"
THIRD = "http://100.64.0.30:47873"
OPERATION = "repoint-2026-10-25-office"


def run(coro):
    return asyncio.run(coro)


class _Answer:
    def __init__(self, status: int, body: object) -> None:
        self.status_code = status
        self._body = body
        self.is_success = 200 <= status < 300

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeDestination:
    """A controller answering the one route a repoint asks it: "do you know this Mac?"."""

    def __init__(self, status: int = 200, body: object | None = None) -> None:
        self.status = status
        self.body = {"id": NODE, "state": "member"} if body is None else body
        self.calls: list[tuple[str, dict]] = []
        self.error: Exception | None = None

    async def get(self, url, headers=None, timeout=None, **kwargs):
        self.calls.append((url, dict(headers or {})))
        if self.error is not None:
            raise self.error
        return _Answer(self.status, self.body)

    async def aclose(self) -> None:
        return None


class RepointStateMixin:
    """A Mac that is a node of `OLD`, with only the modules' own paths redirected."""

    def membership(self, prefix: str = "studiohub-repoint-") -> Path:
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
        trust = mock.patch.object(app_release, "TRUST_FILE",
                                  data / ".release_publisher_trust.json")
        trust.start()
        self.addCleanup(trust.stop)
        # These modules bind their paths at **import**, so patching `DATA_DIR` alone leaves every one
        # of them pointing at the checkout: without this the suite writes a real `.node_credential`
        # and a real `.kh_studio_token` into `components/` and the repo root (`SHARED_STUDIO_TOKEN_FILE`
        # resolves to `LAUNCHER_ROOT.parent` when `STUDIOHUB_DATA_DIR` is unset), and each assertion
        # about "the credential file" reads a file the test never wrote.
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
        lock = mock.patch.object(control_plane, "settings_writer_lock",
                                 lambda **kwargs: contextlib.nullcontext())
        lock.start()
        self.addCleanup(lock.stop)
        previous = control_plane._settings_cache
        control_plane._settings_cache = None
        self.addCleanup(setattr, control_plane, "_settings_cache", previous)
        self.data = data
        # What joining a controller left on this Mac: its own Hub token, the group's credential, the
        # per-node credential that controller issued, and the address it answers to.
        (data / ".hub_token").write_text("this-macs-own-hub-token\n")
        app_release.set_trusted_publisher({"public_key": "22" * 32, "publisher_id": "fixture-gw"})
        peers.set_fleet_token("the-group-token-value-123456")
        fleet_nodes.set_node_credential("the-credential-the-old-controller-issued")
        control_plane.save_settings({"controller_id": NODE, "fleet_credential_origin": "adopted",
                                     "parent_controller_url": OLD, "role": "agent"})
        return data

    # -- the Mac's state, read the way a test can prove it did not move -------------
    def settings(self) -> dict:
        return json.loads((self.data / "controller_settings.json").read_text())

    def node_credential(self) -> str:
        return (self.data / ".node_credential").read_text().strip()

    def own_hub_token(self) -> str:
        return (self.data / ".hub_token").read_text().strip()

    def group_token(self) -> str:
        return (self.data / ".fleet_token").read_text().strip()

    def publisher_pin(self) -> dict | None:
        return app_release.trusted_publisher()

    def identity(self) -> dict:
        """Everything a repoint promises to carry across, in one comparable dict."""
        settings = self.settings()
        return {
            "controller_id": settings["controller_id"],
            "site_id": settings["site_id"],
            "site_name": settings["site_name"],
            "role": settings["role"],
            "fleet_credential_origin": settings["fleet_credential_origin"],
            "node_credential": self.node_credential(),
            "group_token": self.group_token(),
            "own_hub_token": self.own_hub_token(),
            "publisher_pin": self.publisher_pin(),
        }


class RepointOperationTests(RepointStateMixin, unittest.TestCase):
    """The node-side operation on its own, with a scripted destination."""

    def setUp(self) -> None:
        self.membership()

    def repoint(self, destination: FakeDestination, **body):
        payload = {"operation_id": OPERATION, "expected_controller_url": OLD,
                   "controller_url": NEW}
        payload.update(body)
        return run(fleet_nodes.repoint(payload, client=destination))

    def test_a_valid_repoint_moves_the_address_and_carries_every_identity_across(self) -> None:
        before = self.identity()
        destination = FakeDestination()

        answer = self.repoint(destination)

        self.assertTrue(answer["repointed"])
        self.assertFalse(answer["already_applied"])
        self.assertEqual(NEW, answer["controller_url"])
        self.assertEqual(OLD, answer["previous_controller_url"])
        # The destination was asked, once, with **this Mac's own credential** — the value its old
        # controller issued it, and the only thing that proves continuity to a controller holding a
        # transferred roster. Not the group token: that is shared by every Mac in the group, so it
        # cannot tell controller one from controller two, and handing it to a candidate controller
        # would leak the group's credential for no evidence at all.
        self.assertEqual(1, len(destination.calls))
        url, headers = destination.calls[0]
        self.assertEqual(f"{NEW}/api/hub/fleet/nodes/{NODE}/status", url)
        self.assertEqual(before["node_credential"], headers.get("X-Hub-Token"),
                         "the destination was not shown the credential this Mac already holds")
        self.assertNotIn(before["group_token"], headers.values(),
                         "the shared group token was sent to the destination")
        # The address moved; nothing else did.
        settings = self.settings()
        self.assertEqual(NEW, settings["parent_controller_url"])
        self.assertEqual(before, self.identity())
        # And the receipt travelled in the same write, so a reader sees both or neither.
        receipt = settings["repoint_receipt"]
        self.assertEqual(OPERATION, receipt["operation_id"])
        self.assertEqual(OLD, receipt["from_url"])
        self.assertEqual(NEW, receipt["to_url"])
        self.assertEqual("applied", receipt["state"])

    def test_the_membership_follows_the_receipt_so_a_node_stays_a_node(self) -> None:
        self.repoint(FakeDestination())
        member = fleet_nodes.membership()
        self.assertTrue(member["member"], "a repointed Mac stopped being a node")
        self.assertEqual(NEW, member["controller_url"])

    def test_an_exact_replay_reports_what_happened_without_writing_again(self) -> None:
        first = self.repoint(FakeDestination())
        written = self.settings()
        destination = FakeDestination()

        again = self.repoint(destination)

        self.assertTrue(again["repointed"])
        self.assertTrue(again["already_applied"], "a lost reply must read as already applied")
        self.assertEqual(first["applied_at"], again["applied_at"])
        self.assertEqual(written, self.settings(), "a replay rewrote the settings file")
        self.assertEqual([], destination.calls,
                         "a replay asked the destination again instead of using the receipt")

    def test_an_operation_id_reused_for_other_addresses_is_refused(self) -> None:
        self.repoint(FakeDestination())
        written = self.settings()

        with self.assertRaises(fleet_nodes.RepointRefused) as refused:
            self.repoint(FakeDestination(), expected_controller_url=NEW, controller_url=THIRD)

        self.assertEqual(409, refused.exception.status)
        self.assertEqual("OPERATION_ID_REUSED", refused.exception.code)
        self.assertEqual(written, self.settings(), "a refused operation wrote settings anyway")

    def test_a_stale_expected_url_is_refused_and_cannot_undo_a_newer_move(self) -> None:
        self.repoint(FakeDestination())
        written = self.settings()

        # The same Mac is told to move *from* the controller it has already left, which is what a
        # queued command that arrived late looks like.
        with self.assertRaises(fleet_nodes.RepointRefused) as refused:
            self.repoint(FakeDestination(), operation_id="repoint-late-arrival-one")

        self.assertEqual("STALE_EXPECTED_URL", refused.exception.code)
        self.assertEqual(409, refused.exception.status)
        self.assertIn(OLD, str(refused.exception))
        self.assertIn(NEW, str(refused.exception))
        self.assertEqual(written, self.settings())
        self.assertEqual(NEW, self.settings()["parent_controller_url"],
                         "a stale operation moved the Mac back")

    def test_a_destination_that_cannot_recognise_this_mac_is_refused(self) -> None:
        for status, body, code in (
                (401, {"id": NODE, "state": "unknown"}, "DESTINATION_DOES_NOT_KNOW_THIS_MAC"),
                (403, {"id": NODE, "state": "unknown"}, "DESTINATION_DOES_NOT_KNOW_THIS_MAC"),
                (200, {"id": NODE, "state": "unknown"}, "DESTINATION_UNKNOWN_ANSWER"),
                (200, "not json at all", "DESTINATION_UNKNOWN_ANSWER"),
                (500, {"id": NODE, "state": "member"}, "DESTINATION_UNKNOWN_ANSWER")):
            with self.subTest(status=status):
                # A fresh Mac for each answer, so one refusal cannot hide behind the last one.
                self.membership(prefix="studiohub-repoint-destination-")
                with self.assertRaises(fleet_nodes.RepointRefused) as refused:
                    self.repoint(FakeDestination(status, body))
                self.assertEqual(409, refused.exception.status)
                self.assertEqual(code, refused.exception.code)
                self.assertEqual(OLD, self.settings()["parent_controller_url"],
                                 "a Mac was moved to a controller that could not recognise it")
                self.assertIsNone(self.settings().get("repoint_receipt"))

    def test_a_destination_that_has_removed_this_mac_says_so(self) -> None:
        with self.assertRaises(fleet_nodes.RepointRefused) as refused:
            self.repoint(FakeDestination(403, {"id": NODE, "state": "removed",
                                               "detail": "removed on 2026-10-01"}))
        self.assertEqual("DESTINATION_REMOVED_THIS_MAC", refused.exception.code)
        self.assertIn("re-admit", str(refused.exception))

    def test_a_destination_that_answers_nothing_is_refused_without_moving(self) -> None:
        destination = FakeDestination()
        destination.error = httpx.ConnectTimeout("no answer")
        with self.assertRaises(fleet_nodes.RepointRefused) as refused:
            self.repoint(destination)
        self.assertEqual("DESTINATION_UNREACHABLE", refused.exception.code)
        self.assertEqual(OLD, self.settings()["parent_controller_url"])
        # **No receipt either**, so the same operation can be tried again when the destination is up
        # rather than being answered "already applied" from a record of something that never happened.
        self.assertIsNone(self.settings().get("repoint_receipt"))

    def test_a_destination_already_in_place_is_reported_and_writes_nothing(self) -> None:
        answer = self.repoint(FakeDestination(), controller_url=OLD)
        self.assertEqual({"repointed": False, "already_there": True},
                         {"repointed": answer["repointed"], "already_there": answer["already_there"]})
        self.assertEqual(OLD, self.settings()["parent_controller_url"])
        self.assertIsNone(self.settings().get("repoint_receipt"))

    def test_a_public_or_malformed_address_is_refused(self) -> None:
        for value in ("http://8.8.8.8:47873", "http://example.com:47873", "https://100.64.0.20:47873",
                      "http://100.64.0.20", "http://100.64.0.20:47873/api/hub", "not an address"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as refused:
                    self.repoint(FakeDestination(), controller_url=value)
                self.assertNotIsInstance(refused.exception, fleet_nodes.RepointRefused)
        self.assertEqual(OLD, self.settings()["parent_controller_url"])
        self.assertIsNone(self.settings().get("repoint_receipt"))

    def test_a_malformed_operation_id_is_refused(self) -> None:
        for value in ("", "short", "has spaces here", "x" * 121, "repoint/2026", "repoint;drop"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.repoint(FakeDestination(), operation_id=value)
        self.assertEqual(OLD, self.settings()["parent_controller_url"])

    def test_a_mac_that_is_not_in_a_group_has_nothing_to_continue(self) -> None:
        control_plane.save_settings({"fleet_credential_origin": "minted",
                                     "parent_controller_url": None, "role": "standalone"})
        with self.assertRaises(fleet_nodes.RepointRefused) as refused:
            self.repoint(FakeDestination())
        self.assertEqual("NOT_A_MEMBER", refused.exception.code)
        self.assertIn("not in a group", str(refused.exception))

    def test_a_mac_with_no_node_credential_is_refused(self) -> None:
        fleet_nodes.set_node_credential(None)
        with self.assertRaises(fleet_nodes.RepointRefused) as refused:
            self.repoint(FakeDestination())
        self.assertEqual("NO_NODE_CREDENTIAL", refused.exception.code)
        self.assertIn("Re-join instead", str(refused.exception))


class RepointRouteScopeTests(unittest.TestCase):
    """Who may call the route, against a real Hub app in a throwaway process."""

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
from components.hub.backend import control_plane, fleet_nodes, peers

NODE = 'office-imac-blue-m1-8-256-hub'
OLD = 'http://100.64.0.10:47873'
NEW = 'http://100.64.0.20:47873'
HUB = {'X-Hub-Token': 'hub-operator-token'}
BODY = {'operation_id': 'repoint-route-scope-1', 'expected_controller_url': OLD,
        'controller_url': NEW}


class FakeDestination:
    """The destination's one answer, injected so no request leaves this process."""

    def __init__(self):
        self.calls = []

    async def get(self, url, headers=None, timeout=None, **kwargs):
        self.calls.append((url, dict(headers or {})))
        class Answer:
            status_code = 200
            is_success = True
            def json(self):
                return {'id': NODE, 'state': 'member'}
        return Answer()

    async def aclose(self):
        return None


def state_of_the_mac():
    return control_plane.load_settings()


async def proof():
    app = standalone.create_app(start_background=False)
    group = peers.fleet_token()
    fleet_nodes.set_node_credential('the-credential-the-old-controller-issued')
    control_plane.save_settings({'controller_id': NODE, 'fleet_credential_origin': 'adopted',
                                 'parent_controller_url': OLD, 'role': 'agent'})
    destination = FakeDestination()
    real_repoint = fleet_nodes.repoint
    async def scripted(payload, *, client=None):
        return await real_repoint(payload, client=destination)
    fleet_nodes.repoint = scripted

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url='http://fixture-hub') as client:
        # A node's own credential may not repoint this Mac: the boundary is the same one
        # `POST /api/hub/fleet` has, and it is the whole reason a sold Mac cannot move itself.
        # 401 and not 403 is this Mac's existing answer for a credential that matches neither its
        # Hub token nor a Fleet token it holds (`auth.credential_refusal_detail`); a 403 here is
        # reserved for a credential this controller has already revoked by removing the Mac.
        refused = await client.post('/api/hub/fleet/repoint',
                                    headers={'X-Hub-Token': 'the-credential-the-old-controller-issued'},
                                    json=BODY)
        assert refused.status_code == 401, refused.text
        assert state_of_the_mac()['parent_controller_url'] == OLD, 'a refused call moved the Mac'

        # No credential at all is refused too.
        anonymous = await client.post('/api/hub/fleet/repoint', json=BODY)
        assert anonymous.status_code == 401, anonymous.text
        assert state_of_the_mac()['parent_controller_url'] == OLD

        # The group Fleet credential may: a controller tells its nodes where it went.
        allowed = await client.post('/api/hub/fleet/repoint', headers={'X-Hub-Token': group},
                                    json=BODY)
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()['repointed'] is True, allowed.text
        assert state_of_the_mac()['parent_controller_url'] == NEW, state_of_the_mac()

        # And so may this Mac's own operator.
        control_plane.save_settings({'parent_controller_url': OLD})
        operator = await client.post('/api/hub/fleet/repoint', headers=HUB,
                                     json={'operation_id': 'repoint-route-scope-2',
                                           'expected_controller_url': OLD,
                                           'controller_url': NEW})
        assert operator.status_code == 200, operator.text
        # A rejection carries the node's own reason and the status that says "not applicable".
        stale = await client.post('/api/hub/fleet/repoint', headers=HUB,
                                  json={'operation_id': 'repoint-route-scope-3',
                                        'expected_controller_url': OLD,
                                        'controller_url': NEW})
        assert stale.status_code == 409, stale.text
        assert 'already left' in stale.text, stale.text
        # A malformed address is a 400: the caller's request is wrong, not this Mac's state.
        bad = await client.post('/api/hub/fleet/repoint', headers=HUB,
                                json={'operation_id': 'repoint-route-scope-4',
                                      'expected_controller_url': NEW,
                                      'controller_url': 'http://8.8.8.8:47873'})
        assert bad.status_code == 400, bad.text

        # The controller's push must state where its Macs are: without the fence there is nothing
        # for a node to compare its own membership against, so the route refuses rather than
        # guessing an address (this side cannot read one - see `peers.sync_repoint`).
        missing = await client.post('/api/hub/fleet/repoint-nodes', headers=HUB,
                                    json={'controller_url': NEW,
                                          'operation_id': 'repoint-route-scope-5'})
        assert missing.status_code == 400, missing.text
        assert 'expected_controller_url' in missing.text, missing.text


asyncio.run(proof())
print('repoint route: owner and group token may; a node credential may not')
'''

    def test_the_route_is_owner_or_group_only(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="hub-repoint-route-")
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
            [os.environ.get("STUDIOHUB_TEST_RUNTIME", sys.executable), "-c", self.PROOF],
            env=env, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_node_credential_is_refused_by_the_allowlists_themselves(self) -> None:
        from components.hub.backend import auth
        self.assertTrue(auth.fleet_path_allowed("POST", "/api/hub/fleet/repoint"))
        self.assertFalse(auth.node_origin_allowed("POST", "/api/hub/fleet/repoint"),
                         "a leaf Mac's own credential must not re-point it")
        # Deliverable through the pull queue, unlike the adoption push: a repoint continues the group
        # the Mac is already in, and each node checks the operation's expected URL before it writes.
        self.assertFalse(auth.pull_membership_refused("POST", "/api/hub/fleet/repoint"))


class ScriptedController:
    """Answers each node's two calls from a script, keyed by the node's address."""

    def __init__(self, script: dict[str, list[object]]) -> None:
        self.script = script
        self.calls: list[tuple[str, str, dict]] = []
        self.bodies: list[dict] = []

    def _next(self, method: str, url: str, headers):
        self.calls.append((method, url, dict(headers or {})))
        for key, answers in self.script.items():
            if key in url and answers:
                return answers.pop(0)
        return httpx.ConnectError("nothing scripted", request=httpx.Request(method, url))

    async def get(self, url, headers=None, timeout=None, **kwargs):
        answer = self._next("GET", url, headers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def post(self, url, headers=None, json=None, timeout=None, **kwargs):
        self.bodies.append(dict(json or {}))
        answer = self._next("POST", url, headers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def aclose(self) -> None:
        return None


class RepointPushTests(RepointStateMixin, unittest.TestCase):
    """The controller's push: what it reports per node, and what it leaves alone."""

    def setUp(self) -> None:
        # The push reads the previous-token file and the peer cache, so the paths are redirected
        # here too: this Mac is the *controller* in these tests, not a node.
        self.membership(prefix="studiohub-repoint-push-")
        peers._cache.clear()

    def registry(self, *nodes: str) -> list[dict]:
        return [{"machine": node, "host": f"100.64.0.{10 + index}", "hub_port": 47873,
                 "fleet_node": node, "id": "image", "modality": "image"}
                for index, node in enumerate(nodes)]

    def push(self, script, *nodes: str, destination: str = NEW, migration: str = "repoint-push-1",
             expected: str = OLD):
        client = ScriptedController(script)
        with mock.patch.object(peers, "fleet_token", lambda: "the-group-token"):
            answer = run(peers.sync_repoint(self.registry(*nodes), client, destination, migration,
                                            expected))
        return answer, client

    def test_a_node_that_moves_is_reported_repointed_from_its_own_answer(self) -> None:
        answer, client = self.push({
            "100.64.0.10": [_Answer(200, {"ok": True, "repointed": True, "controller_url": NEW})],
        }, "node-1")

        self.assertEqual(1, answer["repointed"])
        self.assertEqual(0, answer["failed"])
        row = answer["machines"]["node-1"]
        self.assertEqual("repointed", row["status"])
        self.assertTrue(row["ok"])
        self.assertEqual(OLD, row["from_url"])
        self.assertEqual(NEW, row["to_url"])
        # The operation id is per node, so one push is one operation on each Mac and a retry of the
        # same push is a replay rather than a second instruction.
        self.assertEqual("repoint-push-1:node-1", row["operation_id"])
        self.assertEqual({"operation_id": "repoint-push-1:node-1",
                          "expected_controller_url": OLD, "controller_url": NEW},
                         client.bodies[0],
                         "the push did not fence the operation with the caller's address")
        # **One call per Mac, and it is the repoint itself.** The first version of this push read
        # `GET /api/hub/controller` to learn each Mac's current address and got a 403 on every node:
        # that route carries this Mac's settings and is deliberately outside the Fleet credential's
        # scope, so the whole push failed against a real Hub while these scripted tests were green.
        # The fence is the caller's to state; nothing here may need a route a fleet credential cannot
        # use.
        self.assertEqual(["POST"], [call[0] for call in client.calls])
        self.assertEqual("the-group-token", client.calls[0][2].get("X-Hub-Token"))

    def test_a_node_already_on_the_destination_is_reported_already_there(self) -> None:
        answer, client = self.push({
            "100.64.0.10": [_Answer(200, {"ok": True, "repointed": False, "already_there": True,
                                          "controller_url": NEW})],
        }, "node-1")

        self.assertEqual(1, answer["already_there"])
        self.assertEqual(0, answer["repointed"])
        row = answer["machines"]["node-1"]
        self.assertEqual("already_there", row["status"])
        self.assertTrue(row["ok"])
        # The Mac is asked, and it is the Mac that answers "already there" — the only side that can
        # know, now that this controller no longer reads an address it is not allowed to read.
        self.assertEqual(1, len(client.calls))

    def test_a_node_that_refuses_is_reported_failed_with_its_own_reason(self) -> None:
        answer, _ = self.push({
            "100.64.0.10": [_Answer(409, {"ok": False, "code": "DESTINATION_DOES_NOT_KNOW_THIS_MAC",
                                          "detail": "that controller refused this Mac's credential"})],
        }, "node-1")

        self.assertEqual(1, answer["failed"])
        self.assertEqual(0, answer["repointed"])
        row = answer["machines"]["node-1"]
        self.assertFalse(row["ok"])
        self.assertEqual("DESTINATION_DOES_NOT_KNOW_THIS_MAC", row["code"])
        self.assertIn("refused this Mac's credential", row["detail"])
        self.assertEqual(OLD, row["from_url"], "a failed Mac is not reported as moved")

    def test_a_node_that_does_not_answer_is_pending_never_repointed(self) -> None:
        answer, _ = self.push({"100.64.0.10": [httpx.ConnectTimeout("off")]}, "node-1")
        self.assertEqual(1, answer["pending"])
        self.assertEqual(0, answer["repointed"])
        self.assertFalse(answer["machines"]["node-1"]["ok"])
        self.assertIn("keeps the controller it has", answer["machines"]["node-1"]["detail"])

    def test_a_queued_delivery_is_pending_never_verified(self) -> None:
        """A 202 is the pull channel holding the request: nothing has happened on the Mac yet."""
        answer, _ = self.push({
            "100.64.0.10": [_Answer(202, {"ok": True, "queued": True, "node": "node-1"})],
        }, "node-1")
        self.assertEqual(1, answer["pending"])
        self.assertEqual(0, answer["repointed"])

    def test_a_node_that_accepted_but_did_not_move_is_pending(self) -> None:
        answer, _ = self.push({"100.64.0.10": [_Answer(200, {"ok": True})]}, "node-1")
        self.assertEqual("pending", answer["machines"]["node-1"]["status"])
        self.assertIn("does not report the new controller", answer["machines"]["node-1"]["detail"])

    def test_a_mac_that_is_not_in_a_group_is_reported_failed_from_its_own_answer(self) -> None:
        """This controller no longer decides that — the Mac is the only side that knows."""
        answer, _ = self.push({
            "100.64.0.10": [_Answer(409, {"ok": False, "code": "NOT_A_MEMBER",
                                          "detail": "this Mac is not in a group"})],
        }, "node-1")
        row = answer["machines"]["node-1"]
        self.assertEqual("failed", row["status"])
        self.assertEqual("NOT_A_MEMBER", row["code"])
        self.assertIn("not in a group", row["detail"])

    def test_a_node_that_holds_a_different_fleet_token_is_failed_not_pending(self) -> None:
        answer, _ = self.push({"100.64.0.10": [_Answer(401, {"detail": "no"})]}, "node-1")
        row = answer["machines"]["node-1"]
        self.assertEqual("failed", row["status"])
        self.assertIn("different Fleet token", row["detail"])

    def test_a_mixed_push_reports_every_node_separately(self) -> None:
        answer, _ = self.push({
            "100.64.0.10": [_Answer(200, {"ok": True, "repointed": True, "controller_url": NEW})],
            "100.64.0.11": [_Answer(200, {"ok": True, "already_there": True})],
            "100.64.0.12": [_Answer(409, {"ok": False, "detail": "that Mac refused: no roster row"})],
        }, "node-1", "node-2", "node-3")

        self.assertEqual(3, answer["total"], "total counts every Mac the push was aimed at")
        self.assertEqual({"repointed": 1, "already_there": 1, "pending": 0, "failed": 1},
                         {key: answer[key] for key in ("repointed", "already_there", "pending",
                                                       "failed")})
        self.assertEqual("repointed", answer["machines"]["node-1"]["status"])
        self.assertEqual("already_there", answer["machines"]["node-2"]["status"])
        self.assertEqual("failed", answer["machines"]["node-3"]["status"])


class RepointEndToEndTests(unittest.TestCase):
    """Two real controllers, a real node, and a seeded destination roster.

    This is the whole operation over real sockets: the node joins controller one and holds the
    credential it was issued; controller two is given that credential's **hash** in its own roster,
    which is what a transferred group roster carries; the node is then repointed to controller two
    and answers it afterwards without a single join call.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="studiohub-repoint-flow-")
        cls.root = Path(cls.temporary.name)
        cls.hubs: list[HubProcess] = []
        # Each Hub is seated with its own identity **before** it starts, the way the repo's other
        # two-Hub tests do it. The node's Machine ID is `NODE` on purpose: a Mac joins a controller
        # under its own Machine ID, and `repoint` proves continuity by asking the destination about
        # exactly that id — so a fixture whose node is hostnamed but joined as `NODE` would be
        # testing a state the app cannot reach (and the destination would refuse, correctly, with
        # "a node credential may ask only about the Mac that holds it").
        for name, token, machine in (("controller-one", "controller-one-hub-token",
                                      "repoint-controller-one"),
                                     ("controller-two", "controller-two-hub-token",
                                      "repoint-controller-two"),
                                     ("node-one", "node-one-hub-token", NODE)):
            models = cls.root / name / "models"
            models.mkdir(parents=True, exist_ok=True)
            hub = HubProcess(cls.root, name, token, env={"STUDIOHUB_MODELS_DIR": str(models)})
            hub.data.mkdir(parents=True, exist_ok=True)
            settings = hub.data / "controller_settings.json"
            settings.write_text(json.dumps({
                "version": 2, "role": "standalone", "site_id": f"site-{name}", "site_name": name,
                "controller_id": machine, "database_mode": "off",
                "parent_controller_url": None, "fleet_credential_origin": "minted",
            }))
            os.chmod(settings, 0o600)
            hub.start()
            cls.hubs.append(hub)
        cls.first, cls.second, cls.node = cls.hubs

    @classmethod
    def tearDownClass(cls) -> None:
        for hub in cls.hubs:
            hub.stop()
        cls.temporary.cleanup()

    def group_token(self, controller: HubProcess) -> str:
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

    def settings_of(self, hub: HubProcess, token: str) -> dict:
        status, body, _ = hub.request("/api/hub/controller", token=token)
        self.assertEqual(200, status, body)
        return body["settings"]

    def seed_roster(self, controller: HubProcess, node_id: str, credential: str,
                    address: str, name: str) -> None:
        """Give a controller the transferred roster row: the node's id and its credential's hash.

        Written directly because that is what the precondition is — controller two holds the group's
        roster as controller one had it. No endpoint can mint this: a join would issue a *new*
        credential, which is the thing a repoint exists to avoid.
        """
        roster = controller.data / "fleet_nodes.json"
        roster.write_text(json.dumps({
            "version": 2,
            "nodes": {node_id: {
                "id": node_id, "name": name, "address": address, "role": "agent",
                "studios": STUDIOS, "joined_at": time.time(), "updated_at": time.time(),
                "last_seen_at": time.time(),
                "credential_hash": fleet_nodes.hash_credential(credential),
                "credential_issued_at": time.time()}},
            "removed": {},
        }))
        os.chmod(roster, 0o600)
        # The continuing group's credential travels with the roster: without it the node would be
        # answering a head that cannot dispatch to it.
        (controller.data / ".fleet_token").write_text(self.first_group_token + "\n")
        os.chmod(controller.data / ".fleet_token", 0o600)

    def test_a_node_follows_a_new_controller_and_keeps_everything_it_is(self) -> None:
        first_group = self.group_token(self.first)
        self.first_group_token = first_group
        node_address = self.node.base

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
        before = self.settings_of(self.node, self.node.token)
        self.assertEqual(self.first.base, before["parent_controller_url"])
        self.assertEqual("adopted", before["fleet_credential_origin"])
        node_credential_file = self.node.data / ".node_credential"
        credential_bytes = node_credential_file.read_bytes()
        own_hub_token = (self.node.data / ".hub_token").read_bytes()
        node_group_token = (self.node.data / ".fleet_token").read_bytes()
        machine_id = before["controller_id"]
        # The invariant the destination check rests on: the id this Mac asks the destination about
        # is the id its controller knows it by. The identity guard holds it in a group — Machine ID
        # is read-only while adopted — so it cannot drift after the join that wrote the roster row.
        self.assertEqual(NODE, machine_id,
                         "the node joined under one id and would probe the destination under another")

        # 2. Controller two is given the transferred roster: the node's credential hash, unchanged.
        self.seed_roster(self.second, NODE, credential, node_address, "Office iMac")
        # It really does recognise the credential now, which is the precondition a repoint needs.
        status, status_body, _ = self.second.request(f"/api/hub/fleet/nodes/{NODE}/status",
                                                     token=credential)
        self.assertEqual(200, status, status_body)
        self.assertEqual("member", status_body["state"])

        # 3. The repoint, delivered with the **group Fleet credential** — the controller's own
        #    authority over its nodes, and the reason a Mac that cannot be dialled can still follow.
        status, answer, _ = self.node.post_json(
            "/api/hub/fleet/repoint",
            {"operation_id": OPERATION, "expected_controller_url": self.first.base,
             "controller_url": self.second.base},
            token=first_group)
        self.assertEqual(200, status, answer)
        self.assertTrue(answer["repointed"], answer)
        self.assertFalse(answer["already_applied"])

        # 4. The address moved; nothing else about the Mac did.
        after = self.settings_of(self.node, self.node.token)
        self.assertEqual(self.second.base, after["parent_controller_url"])
        self.assertEqual(OPERATION, after["repoint_receipt"]["operation_id"])
        self.assertEqual(self.first.base, after["repoint_receipt"]["from_url"])
        self.assertEqual(machine_id, after["controller_id"], "the Machine ID changed")
        self.assertEqual("adopted", after["fleet_credential_origin"], "the Mac left its group")
        self.assertEqual(credential_bytes, node_credential_file.read_bytes(),
                         "a repoint reissued this Mac's own credential")
        self.assertEqual(own_hub_token, (self.node.data / ".hub_token").read_bytes())
        self.assertEqual(node_group_token, (self.node.data / ".fleet_token").read_bytes(),
                         "a repoint rotated the group credential")

        # 5. It is a working node of the new controller: the destination authenticates the credential
        #    it already had, and the Mac answers with its own identity — no join call anywhere in
        #    this test after step 1.
        status, roster, _ = self.second.request("/api/hub/fleet/nodes", token=self.second.token)
        self.assertEqual(200, status)
        self.assertEqual([NODE], [row["id"] for row in roster["nodes"]],
                         "the controller the Mac moved to does not list it")
        self.assertEqual(credential, (self.node.data / ".node_credential").read_text().strip())
        status, resources, _ = self.node.request("/api/hub/resources?local_only=true",
                                                token=first_group)
        self.assertEqual(200, status, resources)
        self.assertEqual(machine_id, resources["machine"]["id"])

        # 6. A replay is answered from the receipt: the lost-reply case, and it must not move again.
        status, replay, _ = self.node.post_json(
            "/api/hub/fleet/repoint",
            {"operation_id": OPERATION, "expected_controller_url": self.first.base,
             "controller_url": self.second.base},
            token=first_group)
        self.assertEqual(200, status, replay)
        self.assertTrue(replay["already_applied"], replay)
        self.assertEqual(after["repoint_receipt"]["applied_at"],
                         self.settings_of(self.node, self.node.token)["repoint_receipt"]["applied_at"],
                         "a replay rewrote the receipt")

        # 7. The old controller pushes a *new* repoint at a controller that holds no roster for the
        #    Mac. It is refused by the Mac itself, reported as failed with the Mac's own sentence,
        #    and the Mac stays exactly where it is.
        status, pushed, _ = self.first.post_json(
            "/api/hub/fleet/repoint-nodes",
            {"controller_url": "http://100.64.0.99:47873",
             "expected_controller_url": self.second.base,
             "operation_id": "repoint-push-failure-1"},
            token=self.first.token)
        self.assertEqual(200, status, pushed)
        row = pushed["machines"][NODE]
        self.assertEqual("failed", row.get("status"), pushed)
        self.assertEqual("DESTINATION_UNREACHABLE", row.get("code"), pushed)
        self.assertIn("left alone", row["detail"])
        self.assertEqual(self.second.base,
                         self.settings_of(self.node, self.node.token)["parent_controller_url"],
                         "a failed push moved the Mac anyway")

        # 8. A push whose fence is wrong is refused too, and by the Mac: the caller states where the
        #    nodes are, and a caller that names the wrong controller cannot move anybody.
        status, stale, _ = self.first.post_json(
            "/api/hub/fleet/repoint-nodes",
            {"controller_url": self.first.base,
             "expected_controller_url": self.first.base,
             "operation_id": "repoint-push-stale-1"},
            token=self.first.token)
        self.assertEqual(200, status, stale)
        self.assertEqual("failed", stale["machines"][NODE]["status"], stale)
        self.assertEqual("STALE_EXPECTED_URL", stale["machines"][NODE]["code"], stale)
        self.assertEqual(self.second.base,
                         self.settings_of(self.node, self.node.token)["parent_controller_url"],
                         "a stale push moved the Mac")

        # 9. And the push with the *right* fence moves it back: the operation is a continuation, so
        #    controller one can take its Mac back without a re-join, and the Machine ID is still the
        #    one the Mac has always had.
        status, back, _ = self.first.post_json(
            "/api/hub/fleet/repoint-nodes",
            {"controller_url": self.first.base,
             "expected_controller_url": self.second.base,
             "operation_id": "repoint-push-return-1"},
            token=self.first.token)
        self.assertEqual(200, status, back)
        self.assertEqual("repointed", back["machines"][NODE]["status"], back)
        returned = self.settings_of(self.node, self.node.token)
        self.assertEqual(self.first.base, returned["parent_controller_url"])
        self.assertEqual(machine_id, returned["controller_id"])
        self.assertEqual(credential, (self.node.data / ".node_credential").read_text().strip(),
                         "the push back reissued the credential")


class NativeRepointWiringTests(unittest.TestCase):
    """The app has to follow the Hub, and the source is where that can be pinned."""

    API = (PROJECT_ROOT / "native" / "StudioAPI.swift").read_text()

    def test_the_app_reads_the_hubs_own_parent_address(self) -> None:
        self.assertIn("let parentControllerUrl: String?", self.API,
                      "the app cannot see the address its Hub followed")

    def test_publishing_the_identity_reconciles_the_saved_membership(self) -> None:
        block = self.API[self.API.index("@Published private(set) var controllerIdentity"):
                         self.API.index("@Published private(set) var controllerIdentity") + 900]
        self.assertIn("didSet", block,
                      "the identity can be published without reconciling the membership")
        self.assertIn("followHubMembershipIfMoved()", block)

    def test_reconciliation_moves_only_the_address(self) -> None:
        body = self.API[self.API.index("func followHubMembershipIfMoved()"):
                        self.API.index("static func membershipAddressesDiffer")]
        self.assertIn("fleetCredentialOrigin == \"adopted\"", body)
        # The fields of the membership that must be carried across untouched. `releasePublisherKey`
        # left with the in-app self-update in 0.6.233 (test_native_operator_surfaces checks it is gone).
        for field in ("token:", "boundHosts:", "nodeCredential:"):
            self.assertIn(field, body, f"the membership rebuild drops {field}")
        # The preferences write, so the move survives a restart, and the fences, so an attempt
        # already in flight cannot write the old address back.
        self.assertIn("onControllerJoined?(hubAddress)", body)
        self.assertIn("cancelMembershipRejoin()", body)
        self.assertIn("invalidateStartupMembershipAttempt()", body)
        # It must not re-join: that mints a new credential, which is what a repoint avoids.
        self.assertNotIn("joinController(", body)


if __name__ == "__main__":
    unittest.main()
