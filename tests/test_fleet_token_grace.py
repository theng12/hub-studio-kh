"""Rotating the Fleet token must not strand a Mac that was off at that moment.

The controller used to switch to the new token even when some nodes never received it, so a node
that was switched off answered only to a token nobody presented any more, and had to be re-joined
by hand. Now the controller keeps presenting such a node the token it holds, pushes the new one when
the node answers (authenticated with the old one), and forgets the old token once every node
confirmed or the 30-day window ended. Nodes here are simulated with an in-process transport, so the
real peer code (`sync_fleet_token`, the poll's `refresh`, the auth middleware) runs unchanged.
"""
import asyncio
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

peers = importlib.import_module("components.hub.backend.peers")
auth = importlib.import_module("components.hub.backend.auth")
fleet_nodes = importlib.import_module("components.hub.backend.fleet_nodes")

OLD = "old-fleet-token-aaaaaaaaaaaaaaaa"
NEW = "new-fleet-token-bbbbbbbbbbbbbbbb"


class FakeNode:
    """A Mac's Hub as far as the controller can tell: it holds one token and may be off."""

    def __init__(self, token: str, online: bool = True) -> None:
        self.token = token
        self.online = online
        self.seen: list[tuple[str, str, str]] = []   # (method, path, token presented)


def _registry(*names: str) -> list[dict]:
    return [{"id": f"{name}:voice", "modality": "voice", "machine": name, "fleet_node": name,
             "host": f"{name}.test", "port": 47873, "hub_port": 47873,
             "hub_token": "baked-in-at-load-and-stale"} for name in names]


def _client(nodes: dict[str, FakeNode]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        node = nodes[request.url.host.split(".")[0]]
        presented = request.headers.get("x-hub-token", "")
        node.seen.append((request.method, request.url.path, presented))
        if not node.online:
            raise httpx.ConnectError("switched off", request=request)
        if presented != node.token:
            return httpx.Response(401, json={"detail": "refused"})
        if request.method == "POST" and request.url.path == "/api/hub/fleet":
            node.token = json.loads(request.content)["token"]
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"host": {"memory_gb": 8}, "studios": {}, "version": "x"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class FleetTokenGraceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        patches = [
            mock.patch.object(peers, "FLEET_TOKEN_FILE", self.root / ".fleet_token"),
            mock.patch.object(peers, "SHARED_STUDIO_TOKEN_FILE", self.root / ".kh_studio_token"),
            mock.patch.object(peers, "PREVIOUS_TOKENS_FILE", self.root / ".fleet_token_previous"),
            mock.patch.object(peers, "_cache", {}),
            mock.patch.dict(os.environ, {}, clear=False),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("STUDIOHUB_FLEET_TOKEN", None)
        peers.set_fleet_token(OLD)

    def rotate(self, nodes: dict[str, FakeNode]) -> dict:
        async def run():
            async with _client(nodes) as client:
                return await peers.sync_fleet_token(_registry(*nodes), client, NEW)
        return asyncio.run(run())

    def poll(self, nodes: dict[str, FakeNode]) -> None:
        async def run():
            async with _client(nodes) as client:
                await peers.refresh(_registry(*nodes), client)
        peers._cache.clear()
        asyncio.run(run())

    # -- the rotation itself ------------------------------------------------
    def test_a_node_that_was_off_keeps_being_asked_with_the_token_it_holds(self) -> None:
        nodes = {"on": FakeNode(OLD), "off": FakeNode(OLD, online=False)}
        answer = self.rotate(nodes)
        self.assertEqual(answer["verified"], 1)
        self.assertEqual(peers.fleet_token(), NEW)
        self.assertEqual(nodes["on"].token, NEW)
        self.assertEqual(set(peers.nodes_on_previous_token()), {"off"})
        on, off = _registry("on", "off")
        self.assertEqual(peers._peer_token(on), NEW)
        self.assertEqual(peers._peer_token(off), OLD,
                         "the stranded node is asked with the token it still holds")
        self.assertEqual(peers.held_token("on"), None)

    def test_an_offline_node_reconnects_and_gets_the_new_token(self) -> None:
        nodes = {"on": FakeNode(OLD), "off": FakeNode(OLD, online=False)}
        self.rotate(nodes)
        nodes["off"].seen.clear()
        nodes["off"].online = True
        self.poll(nodes)
        self.assertEqual(nodes["off"].token, NEW, "the new token was pushed when it came back")
        # The push was authenticated with the token the node held, not the new one it lacked.
        pushes = [token for method, path, token in nodes["off"].seen
                  if method == "POST" and path == "/api/hub/fleet"]
        self.assertEqual(pushes, [OLD])
        # Everyone confirmed: the old token is retired and nothing is left waiting.
        self.assertEqual(peers.nodes_on_previous_token(), {})
        self.assertFalse(peers.accepts_previous_token(OLD))
        self.assertFalse(peers.PREVIOUS_TOKENS_FILE.exists())
        self.assertEqual(peers._peer_token(_registry("off")[0]), NEW)

    def test_the_old_token_stays_good_until_every_node_confirmed(self) -> None:
        nodes = {"a": FakeNode(OLD, online=False), "b": FakeNode(OLD, online=False)}
        self.rotate(nodes)
        nodes["a"].online = True
        self.poll(nodes)
        self.assertEqual(set(peers.nodes_on_previous_token()), {"b"})
        self.assertTrue(peers.accepts_previous_token(OLD), "b is still on it")
        nodes["b"].online = True
        self.poll(nodes)
        self.assertFalse(peers.accepts_previous_token(OLD))

    def test_the_old_token_is_refused_once_the_window_ends(self) -> None:
        nodes = {"off": FakeNode(OLD, online=False)}
        self.rotate(nodes)
        self.assertTrue(peers.accepts_previous_token(OLD))
        later = mock.patch.object(peers.time, "time",
                                  return_value=peers.time.time() + peers.PREVIOUS_TOKEN_GRACE_S + 5)
        with later:
            self.assertFalse(peers.accepts_previous_token(OLD))
            self.assertEqual(peers.nodes_on_previous_token(), {})
            self.assertEqual(peers._peer_token(_registry("off")[0]), NEW)

    def test_the_window_is_thirty_days_and_a_second_rotation_does_not_extend_it(self) -> None:
        self.assertEqual(peers.PREVIOUS_TOKEN_GRACE_S, 30 * 24 * 3600)
        nodes = {"off": FakeNode(OLD, online=False)}
        self.rotate(nodes)
        first = peers.nodes_on_previous_token()["off"]
        with mock.patch.object(peers.time, "time", return_value=peers.time.time() + 3600):
            async def again():
                async with _client(nodes) as client:
                    return await peers.sync_fleet_token(_registry("off"), client, "third-" + NEW)
            asyncio.run(again())
        self.assertEqual(peers.nodes_on_previous_token()["off"], first)
        self.assertEqual(peers.held_token("off"), OLD, "it still holds the first token, not the second")

    def test_a_node_that_already_has_the_new_token_is_not_held_back(self) -> None:
        # The push reached it but the answer was lost: asked with the old token it says 401, and
        # the current token works - so it is confirmed rather than reported as refusing us.
        nodes = {"off": FakeNode(OLD, online=False)}
        self.rotate(nodes)
        nodes["off"].online, nodes["off"].token = True, NEW
        self.poll(nodes)
        self.assertEqual(peers.nodes_on_previous_token(), {})

    def test_a_removed_node_is_not_waited_for(self) -> None:
        self.rotate({"off": FakeNode(OLD, online=False)})
        with mock.patch.object(fleet_nodes, "_document",
                               return_value={"nodes": {"off": {"name": "off"}}, "removed": {}}), \
             mock.patch.object(fleet_nodes, "_write"):
            fleet_nodes.remove("off", by="operator")
        self.assertEqual(peers.nodes_on_previous_token(), {})

    # -- normal requests are unaffected -------------------------------------
    def test_with_every_node_confirmed_nothing_changes(self) -> None:
        nodes = {"a": FakeNode(OLD), "b": FakeNode(OLD)}
        answer = self.rotate(nodes)
        self.assertEqual((answer["verified"], answer["pending"]), (2, 0))
        self.assertFalse(peers.PREVIOUS_TOKENS_FILE.exists())
        self.assertFalse(peers.accepts_previous_token(OLD))
        for studio in _registry("a", "b"):
            self.assertEqual(peers._peer_token(studio), NEW)
        self.assertFalse(peers.accepts_previous_token(NEW), "the current token is not 'previous'")
        self.assertFalse(peers.accepts_previous_token(None))
        self.assertFalse(peers.accepts_previous_token(""))

    def test_a_node_that_refuses_the_old_token_is_not_held(self) -> None:
        nodes = {"odd": FakeNode("something-else-entirely-xxxxxxxx")}
        answer = self.rotate(nodes)
        self.assertEqual(answer["manual"], 1)
        self.assertEqual(peers.nodes_on_previous_token(), {}, "nothing we hold would open it")

    def test_a_handover_leaves_no_old_token_behind(self) -> None:
        nodes = {"off": FakeNode(OLD, online=False)}

        async def run():
            async with _client(nodes) as client:
                return await peers.sync_fleet_token(
                    _registry("off"), client, NEW, local_commit=lambda _t: None,
                    controller_url="http://10.0.0.9:47873")
        asyncio.run(run())
        self.assertEqual(peers.nodes_on_previous_token(), {})

    def test_the_file_is_private(self) -> None:
        self.rotate({"off": FakeNode(OLD, online=False)})
        self.assertEqual(oct(peers.PREVIOUS_TOKENS_FILE.stat().st_mode & 0o777), "0o600")


class PreviousTokenAuthTests(unittest.TestCase):
    """The controller's door: the old token is a node-scope key for a bounded time, not more."""

    def setUp(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for patch in (
            mock.patch.object(peers, "FLEET_TOKEN_FILE", root / ".fleet_token"),
            mock.patch.object(peers, "SHARED_STUDIO_TOKEN_FILE", root / ".kh_studio_token"),
            mock.patch.object(peers, "PREVIOUS_TOKENS_FILE", root / ".fleet_token_previous"),
            mock.patch.object(fleet_nodes, "store_path", lambda: root / "fleet_nodes.json"),
            mock.patch.dict(os.environ, {"STUDIOHUB_REQUIRE_AUTH": "1"}),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("STUDIOHUB_FLEET_TOKEN", None)
        peers.set_fleet_token(NEW)
        app = FastAPI()
        app.middleware("http")(auth.make_middleware("hub-owner-token"))

        @app.get("/api/hub/release")          # a node may read this
        def release():
            return {"ok": True}

        @app.get("/api/hub/startup")          # fleet-only: a node's own credential may not
        def startup():
            return {"ok": True}
        self.client = TestClient(app)

    def get(self, path: str, token: str) -> int:
        return self.client.get(path, headers={"X-Hub-Token": token}).status_code

    def test_the_old_token_is_refused_when_no_node_is_waiting(self) -> None:
        self.assertEqual(self.get("/api/hub/release", OLD), 401)
        self.assertEqual(self.get("/api/hub/release", NEW), 200)

    def test_a_waiting_node_can_still_reach_its_controller_with_the_old_token(self) -> None:
        peers.note_nodes_on_previous_token({"off": OLD})
        self.assertEqual(self.get("/api/hub/release", OLD), 200)

    def test_the_old_token_does_not_grow_into_the_fleet_credential(self) -> None:
        peers.note_nodes_on_previous_token({"off": OLD})
        self.assertEqual(self.get("/api/hub/startup", NEW), 200)
        self.assertEqual(self.get("/api/hub/startup", OLD), 403)

    def test_the_old_token_stops_working_when_the_node_confirms(self) -> None:
        peers.note_nodes_on_previous_token({"off": OLD})
        peers.confirm_node("off")
        self.assertEqual(self.get("/api/hub/release", OLD), 401)

    def test_the_old_token_stops_working_when_the_window_ends(self) -> None:
        peers.note_nodes_on_previous_token({"off": OLD}, window_s=-1)
        self.assertEqual(self.get("/api/hub/release", OLD), 401)

    def test_a_random_token_is_still_refused(self) -> None:
        peers.note_nodes_on_previous_token({"off": OLD})
        self.assertEqual(self.get("/api/hub/release", "not-a-token-at-all-xxxxxxxxxxxxx"), 401)


class InventoryNamesTheNodesTests(unittest.TestCase):
    def test_the_roster_says_how_many_nodes_are_still_on_the_previous_token(self) -> None:
        node = {"id": "off", "name": "Off", "address": "http://off.test:47873", "joined_at": 1.0}

        async def probe(row, client=None):
            return {**row, "state": "unreachable", "detail": "off"}

        async def run():
            with mock.patch.object(fleet_nodes, "_read", lambda: {"off": node}), \
                 mock.patch.object(fleet_nodes, "probe", probe), \
                 mock.patch.object(fleet_nodes, "nodes_on_previous_token", lambda: {"off": 1234.0}), \
                 mock.patch.object(fleet_nodes, "note_seen", lambda *a, **k: None), \
                 mock.patch.object(fleet_nodes, "this_machine", lambda: {"id": "c", "name": "c"}):
                return await fleet_nodes.inventory()
        inventory = asyncio.run(run())
        self.assertEqual(inventory["summary"]["on_previous_token"], 1)
        self.assertEqual(inventory["summary"]["previous_token_until"], 1234.0)
        self.assertTrue(inventory["nodes"][0]["on_previous_token"])


if __name__ == "__main__":
    unittest.main()
