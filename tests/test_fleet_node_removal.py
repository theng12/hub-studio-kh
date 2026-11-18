"""The Gateway's remove-and-reconnect wire, on a real controller (design 3.1-3.3, 2026-11-18).

Three behaviours the Gateway already speaks to and this Hub did not answer:

* `POST .../nodes/{id}/unregister` — remove one Mac, revoke its credential, drop its queue, and
  say `NODE_NOT_FOUND` **as a code** when it is already gone (a bare 404 is a route this Hub
  does not have, which the Gateway reads as "too old").
* A removed Mac's check-in is refused with `NODE_REMOVED`, and **never re-creates its row**: a
  sold Mac must not be able to enrol itself again by asking again.
* `POST .../nodes/connect` — a one-time join ticket. The Mac that redeems it comes back as
  itself: the tombstone is replaced, no second row appears, and it starts with nothing from its
  last life, because the row it had was deleted with its queue.
"""
import json
import os
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from components.hub.backend import auth, fleet_nodes, fleet_pull

NODE = "shop-mini-hub"
ADDRESS = "http://100.64.0.8:47873"
STUDIOS = [{"id": "voice", "modality": "voice"}]
HARDWARE = "a1b2c3d4-hardware"
OTHER_HARDWARE = "ffff0000-other-mac"


class Routes(unittest.TestCase):
    """The real app, through its middleware — the same harness the rename routes use."""

    OWNER = {"Authorization": "Bearer removal-test-hub-token"}

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="studiohub-removal-routes-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.data = root / "State"
        self.data.mkdir()
        (self.data / ".hub_token").write_text("removal-test-hub-token\n")
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
        # The token the app holds, from the same source it reads it from.
        self.FLEET = {"Authorization": f"Bearer {os.environ['STUDIOHUB_FLEET_TOKEN']}"}

    def client(self, stack: ExitStack):
        from fastapi.testclient import TestClient
        from components.hub.backend import (control_plane, fleet_pull as pull_module, ledger,
                                            monitor as monitor_module, registry)
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
            # Bound at import from `registry.DATA_DIR`, so it is not moved by patching that.
            (pull_module, "DATA_DIR", self.data),
            (control_plane, "SETTINGS_FILE", self.data / "controller_settings.json"),
            (control_plane, "_settings_cache", None),
        ):
            stack.enter_context(mock.patch.object(module, name, value))
        registry._labels_cache = None
        stack.callback(setattr, registry, "_labels_cache", None)
        return stack.enter_context(TestClient(create_app(start_background=False)))

    def join(self, node_id: str = NODE, **extra):
        return fleet_nodes.join({"node_id": node_id, "name": "Shop Mini", "address": ADDRESS,
                                 "studios": STUDIOS, **extra})

    # -- both routes are on the allowlist a controller's Fleet token uses ------------------
    def test_the_gateway_reaches_both_routes_with_the_fleet_token(self) -> None:
        for path in (f"/api/hub/fleet/nodes/{NODE}/unregister", "/api/hub/fleet/nodes/connect"):
            self.assertTrue(auth.fleet_path_allowed("POST", path), path)
            self.assertFalse(auth.node_path_allowed("POST", path), path)
        # Nothing wider came with them: the same word on another route is still refused.
        self.assertFalse(auth.fleet_path_allowed("POST", f"/api/hub/fleet/nodes/{NODE}/unregister/x"))
        self.assertFalse(auth.fleet_path_allowed("GET", "/api/hub/fleet/nodes/connect"))

    def test_an_unknown_or_missing_credential_is_refused(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            self.assertEqual(client.post(f"/api/hub/fleet/nodes/{NODE}/unregister",
                                         json={"requested_by": "gateway-admin"}).status_code, 401)
            wrong = {"X-Hub-Token": "not-the-fleet-token"}
            self.assertEqual(client.post("/api/hub/fleet/nodes/connect", json={},
                                         headers=wrong).status_code, 401)

    # -- 3.1 unregister ------------------------------------------------------------------
    def test_a_removal_takes_the_row_the_credential_and_the_queue(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            joined = self.join()
            fleet_pull.queue_json(NODE, "POST", "/api/hub/machine-label", {"name": "x"})
            self.assertEqual(fleet_pull.summary(NODE)["queued"], 1)
            answer = client.post(f"/api/hub/fleet/nodes/{NODE}/unregister",
                                 json={"requested_by": "gateway-admin", "purge": True},
                                 headers=self.FLEET)
            self.assertEqual(answer.status_code, 200)
            self.assertEqual(answer.json(), {"ok": True, "removed": True, "machine": NODE,
                                             "revoked": True})
            self.assertNotIn(NODE, fleet_nodes._read(), "the row is gone, not hidden")
            self.assertIn(NODE, fleet_nodes._removed(), "and the removal is remembered")
            self.assertEqual(fleet_pull.summary(NODE)["queued"], 0, "nothing is left queued for it")
            # The credential it was issued is revoked, so the Mac is told why, by code.
            refused = client.post(f"/api/hub/fleet/nodes/{NODE}/checkin", json={},
                                  headers={"X-Hub-Token": joined["credential"]})
            self.assertIn(refused.status_code, (401, 403))
            self.assertEqual(refused.json()["code"], "NODE_REMOVED")

    def test_an_already_removed_mac_answers_with_its_code(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            answer = client.post(f"/api/hub/fleet/nodes/{NODE}/unregister",
                                 json={"requested_by": "gateway-admin", "purge": True},
                                 headers=self.FLEET)
            # The code is the load-bearing half: a bare 404 is a route this Hub does not have,
            # which the Gateway reads as "too old to remove anything".
            self.assertEqual(answer.status_code, 404)
            self.assertEqual(answer.json(), {"ok": False, "code": "NODE_NOT_FOUND",
                                             "detail": "No such node."})
            self.join()
            client.post(f"/api/hub/fleet/nodes/{NODE}/unregister", json={}, headers=self.FLEET)
            again = client.post(f"/api/hub/fleet/nodes/{NODE}/unregister", json={},
                                headers=self.FLEET)
            self.assertEqual((again.status_code, again.json()["code"]), (404, "NODE_NOT_FOUND"))

    def test_a_removed_macs_check_in_never_puts_it_back(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            joined = self.join(hardware_id=HARDWARE)
            fleet_nodes.remove(NODE, by="gateway-admin")
            refused = client.post(f"/api/hub/fleet/nodes/{NODE}/checkin",
                                  json={"address": ADDRESS, "studios": STUDIOS},
                                  headers={"X-Hub-Token": joined["credential"]})
            self.assertEqual(refused.status_code, 403)
            self.assertEqual(refused.json()["code"], "NODE_REMOVED")
            self.assertIn("removed this Mac", refused.json()["detail"])
            # **The invariant.** Refusing by name is not enough: nothing it carried was written,
            # and no row came back, so "removed" survives the Mac's next start.
            self.assertNotIn(NODE, fleet_nodes._read())
            self.assertIn(NODE, fleet_nodes._removed())
            status = client.get(f"/api/hub/fleet/nodes/{NODE}/status",
                                headers={"X-Hub-Token": joined["credential"]})
            self.assertNotEqual(status.status_code, 200, "a revoked credential reads nothing either")

    # -- 3.3 connect ---------------------------------------------------------------------
    def test_connect_mints_one_ticket_for_the_named_mac(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            before = time.time()
            answer = client.post("/api/hub/fleet/nodes/connect",
                                 json={"requested_by": "gateway-admin", "name": "Shop Mini 09"},
                                 headers=self.FLEET)
            self.assertEqual(answer.status_code, 200)
            body = answer.json()
            self.assertTrue(body["ok"])
            self.assertTrue(body["token"])
            self.assertEqual(body["name"], "Shop Mini 09")
            self.assertEqual(body["detail"], "Ready to connect.")
            self.assertGreater(body["expires_at"], before)
            # The file keeps the hash only, like every other credential here.
            stored = (self.data / fleet_nodes.JOIN_TICKET_FILE).read_text(encoding="utf-8")
            self.assertNotIn(body["token"], stored)

    def test_a_name_the_operator_could_not_have_meant_is_refused_not_crashed(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            answer = client.post("/api/hub/fleet/nodes/connect", json={"name": "x" * 200},
                                 headers=self.FLEET)
            # The Gateway prints this after "No Mac was connected — ", so it has to be a phrase.
            self.assertEqual(answer.status_code, 400)
            self.assertEqual(answer.json()["detail"],
                             "name must be between 1 and 120 printable characters")
            self.assertEqual(fleet_nodes.join_ticket("anything"), None)
            self.assertFalse((self.data / fleet_nodes.JOIN_TICKET_FILE).exists(),
                             "a refused connect mints nothing")

    def test_a_reconnecting_mac_comes_back_as_itself_once_and_fresh(self) -> None:
        from components.hub.backend import ledger
        with ExitStack() as stack:
            client = self.client(stack)
            joined = self.join(hardware_id=HARDWARE)
            fleet_nodes.record_checkin(NODE, {"address": ADDRESS, "studios": STUDIOS,
                                              "resources": {"ram_gb": 32}})
            self.assertIn("checkin_resources", fleet_nodes._read()[NODE])
            # Its first life is on the books: an open registration epoch that activity and
            # machine state are read against.
            first = ledger.reconcile_machine_registrations({NODE})[NODE]["active_since"]
            removed = client.post(f"/api/hub/fleet/nodes/{NODE}/unregister",
                                  json={"requested_by": "gateway-admin", "purge": True},
                                  headers=self.FLEET)
            self.assertEqual(removed.status_code, 200)
            closed = ledger.machine_registration_epoch(NODE)
            self.assertIsNotNone(closed.get("closed_at"),
                                 "the removal closed its epoch, so none of it can show as the next")
            ticket = client.post("/api/hub/fleet/nodes/connect",
                                 json={"requested_by": "gateway-admin", "name": "Shop Mini 09"},
                                 headers=self.FLEET).json()["token"]

            back = client.post("/api/hub/fleet/nodes", json={
                "node_id": NODE, "name": "Its own hostname", "address": ADDRESS,
                "studios": STUDIOS, "hardware_id": HARDWARE},
                headers={"X-Hub-Token": ticket})
            self.assertEqual(back.status_code, 201, back.text)
            row = fleet_nodes._read()[NODE]
            # It is itself: the tombstone is replaced and there is exactly one row for it.
            self.assertNotIn(NODE, fleet_nodes._removed())
            self.assertEqual(list(fleet_nodes._read()), [NODE])
            # The operator's name wins over the one the Mac reports; the credential is new.
            self.assertEqual(row["name"], "Shop Mini 09")
            self.assertTrue(back.json()["credential"])
            self.assertNotEqual(back.json()["credential"], joined["credential"])
            # Fresh: what the last life measured is not carried over — its row is new, and its
            # registration epoch began at this join.
            self.assertNotIn("checkin_resources", row, "a reconnected Mac starts with no history")
            self.assertEqual(fleet_pull.summary(NODE)["queued"], 0)
            second = ledger.machine_registration_epoch(NODE)
            self.assertIsNotNone(second, "the reconnect opened its own epoch")
            self.assertGreater(second["active_since"], first)
            # And the ticket was spent: it is not a standing credential.
            again = client.post("/api/hub/fleet/nodes", json={
                "node_id": NODE, "name": "again", "address": ADDRESS, "studios": STUDIOS},
                headers={"X-Hub-Token": ticket})
            self.assertEqual(again.status_code, 401)

    def test_a_different_mac_under_a_removed_name_is_still_refused(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            self.join(hardware_id=HARDWARE)
            fleet_nodes.remove(NODE, by="gateway-admin")
            ticket = client.post("/api/hub/fleet/nodes/connect", json={},
                                 headers=self.FLEET).json()["token"]
            answer = client.post("/api/hub/fleet/nodes", json={
                "node_id": NODE, "name": "A different Mac", "address": ADDRESS,
                "studios": STUDIOS, "hardware_id": OTHER_HARDWARE},
                headers={"X-Hub-Token": ticket})
            self.assertEqual(answer.status_code, 403, "the name is not a way in for another Mac")
            self.assertIn(NODE, fleet_nodes._removed(), "and the removal still stands")
            self.assertNotIn(NODE, fleet_nodes._read())


class TicketStoreTests(unittest.TestCase):
    """The ticket itself: hashed, expiring, and spent exactly once."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-tickets-")
        self.addCleanup(self.temporary.cleanup)
        self.old = fleet_nodes.DATA_DIR
        fleet_nodes.DATA_DIR = Path(self.temporary.name)
        self.addCleanup(setattr, fleet_nodes, "DATA_DIR", self.old)

    def test_a_ticket_expires_and_lives_nowhere_in_the_clear(self) -> None:
        minted = fleet_nodes.mint_join_ticket(name="Shop Mini", now=1000.0)
        self.assertEqual(fleet_nodes.join_ticket(minted["token"], now=1000.0)["name"], "Shop Mini")
        self.assertIsNone(fleet_nodes.join_ticket(minted["token"], now=1000.0
                                                  + fleet_nodes.JOIN_TICKET_LIFETIME_S + 1))
        self.assertNotIn(minted["token"],
                         (fleet_nodes.DATA_DIR / fleet_nodes.JOIN_TICKET_FILE)
                         .read_text(encoding="utf-8"))

    def test_reading_a_ticket_does_not_spend_it(self) -> None:
        minted = fleet_nodes.mint_join_ticket()
        self.assertIsNotNone(fleet_nodes.join_ticket(minted["token"]))
        self.assertTrue(fleet_nodes.consume_join_ticket(minted["token"]))
        self.assertIsNone(fleet_nodes.join_ticket(minted["token"]))
        self.assertFalse(fleet_nodes.consume_join_ticket(minted["token"]))


if __name__ == "__main__":
    unittest.main()
