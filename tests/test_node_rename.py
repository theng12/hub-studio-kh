"""A renamed Mac shows its new name on its controller and the Gateway, without leaving and re-joining.

Live 2026-10-02 (Hub 0.6.132): studio-mac-0108-m1-8-256 was renamed studio-mac-1409-m1-8-256. Its own
Connections page said so; its controller's roster, and so the Gateway's Fleet page, kept the old name.
The roster name was written at join and changed only by the node's minute status call - and only when
that call carried the node's own credential, which a Mac whose credential the controller no longer
recognises cannot present (it falls back to the group token, which may not rename).

* the node says what it is called in the snapshot its controller already reads every poll, and the
  roster follows it; silence, errors and another Mac's answer change nothing;
* the Gateway can rename a Mac: the controller pushes the name to that Mac first, so the Mac and its row
  agree, and a Mac that is off or refuses keeps its name;
* the new routes are fleet-token routes a node's own credential cannot use.
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub.backend import auth, fleet_nodes  # noqa: E402

NODE = "studio-mac-0108-m1-8-256-hub"
STUDIOS = [{"id": "voice", "modality": "voice"}]


def snapshot(name: object = "studio-mac-1409-m1-8-256", node: str = NODE, *, reachable: bool = True,
             auth_ok: bool = True) -> dict:
    """What the peer poll stores from `/api/hub/resources?local_only=true`."""
    return {"reachable": reachable, "auth": auth_ok, "status": "connected",
            "studios": {"voice": {"id": "voice", "status": "up"}},
            "machine": {"id": node, "name": name}}


class _Roster(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="studiohub-node-rename-")
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(fleet_nodes, "DATA_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        fleet_nodes.join({"node_id": NODE, "name": "studio-mac-0108-m1-8-256",
                          "address": "http://100.64.0.8:47873", "studios": STUDIOS})

    def name(self) -> str:
        return fleet_nodes._read()[NODE]["name"]


class RosterNameFollowsTheNode(_Roster):
    def test_a_renamed_node_is_shown_under_its_new_name(self) -> None:
        with mock.patch.object(fleet_nodes, "fresh_snapshot", return_value=snapshot()):
            self.assertTrue(fleet_nodes.sync_roster_from_snapshots())
            self.assertFalse(fleet_nodes.sync_roster_from_snapshots(), "a second pass changes nothing")
        self.assertEqual(self.name(), "studio-mac-1409-m1-8-256")
        # The id is the identity and does not move; what the Gateway reads as the roster name does.
        self.assertIn(NODE, fleet_nodes._read())
        self.assertEqual(fleet_nodes._member(fleet_nodes._read()[NODE], role="agent")["label"],
                         "studio-mac-1409-m1-8-256")

    def test_silence_and_strangers_never_rename_a_row(self) -> None:
        for said in (None, snapshot(reachable=False), snapshot(auth_ok=False),
                     {"reachable": True, "auth": True, "studios": {}},          # an older Hub
                     snapshot(node="some-other-mac-hub"),                         # another Mac's answer
                     snapshot(""), snapshot("   "), snapshot("x" * 121), snapshot("bad\nname"),
                     {**snapshot(), "machine": "studio-mac-1409"}):
            with mock.patch.object(fleet_nodes, "fresh_snapshot", return_value=said):
                fleet_nodes.sync_roster_from_snapshots()
            self.assertEqual(self.name(), "studio-mac-0108-m1-8-256", said)

    def test_the_node_credential_rename_path_still_works(self) -> None:
        self.assertTrue(fleet_nodes.rename(NODE, "  renamed by its own call  "))
        self.assertEqual(self.name(), "renamed by its own call")
        self.assertFalse(fleet_nodes.rename(NODE, ""))
        self.assertFalse(fleet_nodes.rename(NODE, "y" * 121))


class _Client:
    """Records what the controller sends a node and answers as told."""

    def __init__(self, answer: httpx.Response | Exception) -> None:
        self.answer, self.calls = answer, []

    async def post(self, url, json=None, timeout=None):  # noqa: A002
        self.calls.append((url, json))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    async def aclose(self) -> None:
        return None


class RenameFromTheGateway(_Roster):
    def relay(self, client: _Client, name: object = "Studio Mac 14") -> dict:
        return asyncio.run(fleet_nodes.relay_name(NODE, name, client=client))

    def test_the_mac_is_renamed_first_then_its_row(self) -> None:
        from components.hub.backend import peers
        client = _Client(httpx.Response(200, json={"settings": {}}))
        peers._cache[NODE] = (time.time(), snapshot("studio-mac-0108-m1-8-256"))
        self.addCleanup(peers._cache.pop, NODE, None)
        answer = self.relay(client, "  Studio Mac 14 ")
        self.assertIsNone(peers.fresh_snapshot(NODE), "the pre-rename snapshot must not rename it back")
        self.assertEqual(client.calls, [("http://100.64.0.8:47873/api/hub/machine-label",
                                         {"name": "Studio Mac 14"})])
        self.assertEqual(answer, {"ok": True, "node": NODE, "name": "Studio Mac 14"})
        self.assertEqual(self.name(), "Studio Mac 14")
        # The Mac's next snapshot says the same name, so nothing flips back.
        with mock.patch.object(fleet_nodes, "fresh_snapshot", return_value=snapshot("Studio Mac 14")):
            self.assertFalse(fleet_nodes.sync_roster_from_snapshots())

    def test_a_mac_that_refuses_or_is_off_keeps_its_name(self) -> None:
        for answer, code in ((httpx.Response(403, json={}), "NODE_REFUSED"),
                             (httpx.Response(401, json={}), "NODE_REFUSED"),
                             (httpx.Response(500, json={}), "NODE_ERROR"),
                             (httpx.ConnectError("refused"), "NODE_UNREACHABLE")):
            with self.assertRaises(fleet_nodes.StartupRelayError) as error:
                self.relay(_Client(answer))
            self.assertEqual(error.exception.status, 502)
            self.assertEqual(error.exception.body["code"], code)
            self.assertEqual(self.name(), "studio-mac-0108-m1-8-256")

    def test_a_bad_name_or_unknown_mac_is_refused_before_anything_is_sent(self) -> None:
        client = _Client(httpx.Response(200, json={}))
        for name in ("", "   ", "z" * 121, "tab\there", None):
            with self.assertRaises(fleet_nodes.StartupRelayError) as error:
                self.relay(client, name)
            self.assertEqual(error.exception.status, 400)
        with self.assertRaises(fleet_nodes.StartupRelayError) as error:
            asyncio.run(fleet_nodes.relay_name("nobody-hub", "Name", client=client))
        self.assertEqual(error.exception.status, 404)
        self.assertEqual(client.calls, [])


class Allowlist(unittest.TestCase):
    def test_the_fleet_token_may_rename_and_a_node_credential_may_not(self) -> None:
        for path in ("/api/hub/fleet/nodes/x-hub/name", "/api/hub/machine-label"):
            self.assertTrue(auth.fleet_path_allowed("POST", path), path)
            self.assertFalse(auth.node_origin_allowed("POST", path), path)
        self.assertFalse(auth.fleet_path_allowed("PUT", "/api/hub/machine-label"))
        self.assertFalse(auth.fleet_path_allowed("POST", "/api/hub/fleet/nodes/x/name/extra"))


class Routes(unittest.TestCase):
    """The real app, through its middleware."""

    OWNER = {"Authorization": "Bearer rename-test-hub-token"}
    FLEET = {"Authorization": "Bearer fleet-token-for-the-rename-tests"}

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="studiohub-rename-routes-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.data = root / "State"
        self.data.mkdir()
        (self.data / ".hub_token").write_text("rename-test-hub-token\n")
        os.chmod(self.data / ".hub_token", 0o600)
        workers = root / "workers.json"
        workers.write_text(json.dumps([{
            "id": "voice", "title": "Voice Studio", "modality": "voice", "host": "127.0.0.1",
            "port": 51102, "machine": "local", "local": True,
            "runtime_identity": {"id": "voice-runtime"}, "hub_token": "rename-test-hub-token",
            "managed_output_roots": []}]))
        os.chmod(workers, 0o600)
        env = mock.patch.dict(os.environ, {"STUDIOHUB_DATA_DIR": str(self.data),
                                           "STUDIOHUB_WORKERS_FILE": str(workers),
                                           "STUDIOHUB_PACKAGED": "1", "STUDIOHUB_REQUIRE_AUTH": "1",
                                           "STUDIOHUB_FLEET_TOKEN": "fleet-token-for-the-rename-tests"})
        env.start()
        self.addCleanup(env.stop)

    def client(self, stack: ExitStack):
        from fastapi.testclient import TestClient
        from components.hub.backend import control_plane, ledger, monitor as monitor_module, registry
        from components.hub.standalone import create_app
        for module, name, value in (
            (registry, "DATA_DIR", self.data), (registry, "REGISTRY_FILE", self.data / "studios.json"),
            (registry, "LABELS_FILE", self.data / "machine_labels.json"),
            (registry, "FLAGS_FILE", self.data / "machine_flags.json"),
            (monitor_module, "DATA_DIR", self.data), (auth, "TOKEN_FILE", self.data / ".hub_token"),
            (auth, "PASSWORD_FILE", self.data / ".hub_password.json"),
            (auth, "SESSIONS_FILE", self.data / ".hub_sessions.json"),
            (ledger, "DB_FILE", self.data / "hub.db"), (ledger, "PINOKIO_HOME", self.data),
            (fleet_nodes, "DATA_DIR", self.data),
            (control_plane, "SETTINGS_FILE", self.data / "controller_settings.json"),
            (control_plane, "_settings_cache", None),
        ):
            stack.enter_context(mock.patch.object(module, name, value))
        registry._labels_cache = None
        stack.callback(setattr, registry, "_labels_cache", None)
        return stack.enter_context(TestClient(create_app(start_background=False)))

    def test_resources_tell_the_controller_what_this_mac_is_called(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            client.post("/api/hub/machine-label", json={"name": "studio-mac-1409-m1-8-256"}, headers=self.OWNER)
            machine = client.get("/api/hub/resources", headers=self.FLEET).json()["machine"]
            self.assertEqual(machine["name"], "studio-mac-1409-m1-8-256")
            from components.hub.backend import control_plane
            self.assertEqual(machine["id"], control_plane.public_settings()["controller_id"])

    def test_the_fleet_token_renames_this_mac_but_cannot_clear_its_name(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            self.assertEqual(client.post("/api/hub/machine-label", json={"name": "x"}).status_code, 401)
            self.assertEqual(client.post("/api/hub/machine-label", json={"name": "From the Gateway"},
                                         headers=self.FLEET).status_code, 200)
            self.assertEqual(client.post("/api/hub/machine-label", json={"name": ""},
                                         headers=self.FLEET).status_code, 400)
            self.assertEqual(client.get("/api/hub/resources", headers=self.FLEET).json()["machine"]["name"],
                             "From the Gateway")

    def test_a_controller_renames_itself_and_relays_for_a_node(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            mine = client.post("/api/hub/fleet/nodes/local/name", json={"name": "Head Mac"}, headers=self.FLEET)
            self.assertEqual(mine.json()["name"], "Head Mac")
            self.assertEqual(client.post("/api/hub/fleet/nodes/local/name", json={"name": ""},
                                         headers=self.FLEET).status_code, 400)
            self.assertEqual(client.post("/api/hub/fleet/nodes/nobody-hub/name", json={"name": "N"},
                                         headers=self.FLEET).status_code, 404)
            fleet_nodes.join({"node_id": NODE, "name": "old", "address": "http://100.64.0.8:47873",
                              "studios": STUDIOS})
            relayed = mock.AsyncMock(return_value={"ok": True, "node": NODE, "name": "New"})
            with mock.patch.object(fleet_nodes, "relay_name", relayed):
                answer = client.post(f"/api/hub/fleet/nodes/{NODE}/name", json={"name": "New"},
                                     headers=self.FLEET)
            self.assertEqual(answer.status_code, 200)
            relayed.assert_awaited_once_with(NODE, "New")


if __name__ == "__main__":
    unittest.main()
