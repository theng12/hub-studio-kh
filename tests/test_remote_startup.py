"""Seeing and changing, from the fleet, what a Mac starts when it starts.

Owner, 2026-10-02: "I don't know if each Mac has 'start services on startup' enabled ... can we see,
enable, and restart the services remotely from the Gateway?" The settings belong to the Swift app; this
process reports what the app says and hands the app what the fleet asks. What these tests pin:

* the report travels - resources, a node snapshot, the controller's roster and overview rows;
* nothing is reachable without a credential, a node's own credential may not command another Mac, and
  the app's own exchange route is never open to a fleet credential;
* a restart is refused while work runs unless forced, and every request leaves an audit line;
* the controller relays with the Fleet token it already holds, and says plainly when the node has no
  Hub running (nothing there can receive it).
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub.backend import auth, fleet_nodes, startup_control  # noqa: E402

REPORT = {"open_at_login": True, "start_services_on_launch": False, "open_at_login_saved": True,
          "app_version": "0.6.127"}
IDLE = {"running": 0, "queued": 0}


class _Isolated(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="studiohub-startup-")
        self.data = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(startup_control, "DATA_DIR", self.data))
        stack.enter_context(patch.object(fleet_nodes, "DATA_DIR", self.data))
        stack.enter_context(patch.object(startup_control, "active_work", lambda: dict(self.work)))
        self.work = dict(IDLE)
        startup_control.reset_for_tests()
        self.addCleanup(startup_control.reset_for_tests)

    def audit_events(self) -> list[str]:
        return [row["event"] for row in startup_control.recent_audit(100)]


class FakeApp:
    """The Swift app: polls sync, applies what it is handed, reports the outcome next time."""

    def __init__(self, report: dict | None = None, *, fail: bool = False) -> None:
        self.report = dict(report or REPORT)
        self.received: list[dict] = []
        self.fail = fail
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        applied: list[dict] = []
        while not self._stop.is_set():
            answer = startup_control.sync({**self.report, "applied": applied})
            applied = []
            for item in answer["requests"]:
                self.received.append(item)
                if item["action"] != "restart_services":
                    applied.append({"id": item["id"], "ok": not self.fail,
                                    "detail": "could not write the agent" if self.fail else None})
                    if not self.fail and item["action"] == "set_start_services_on_launch":
                        self.report["start_services_on_launch"] = item["value"]
                    if not self.fail and item["action"] == "set_open_at_login":
                        self.report["open_at_login"] = item["value"]
            time.sleep(0.02)

    def __enter__(self) -> "FakeApp":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=2)


class ReportTests(_Isolated):
    def test_nothing_is_claimed_before_the_app_reports(self) -> None:
        startup = startup_control.snapshot({"voice": "up"}, app_version="0.6.126")
        self.assertIsNone(startup["open_at_login"])
        self.assertIsNone(startup["start_services_on_launch"])
        self.assertFalse(startup["app_reporting"])
        self.assertEqual(startup["services_running"], {"up": 1, "total": 1, "studios": {"voice": "up"}})
        self.assertEqual(startup["app_version"], "0.6.126")

    def test_the_apps_report_is_published_with_the_studios_and_a_start_time(self) -> None:
        startup_control.sync(REPORT)
        startup = startup_control.snapshot({"voice": "up", "image": "down"})
        self.assertIs(startup["open_at_login"], True)
        self.assertIs(startup["start_services_on_launch"], False, "off is reported as off, not as unknown")
        self.assertEqual(startup["services_running"]["up"], 1)
        self.assertEqual(startup["services_running"]["total"], 2)
        self.assertEqual(startup["app_version"], "0.6.127")
        self.assertIsInstance(startup["last_start_at"], float)
        self.assertTrue(startup["app_reporting"])

    def test_a_report_that_goes_quiet_reads_as_unknown_again(self) -> None:
        startup_control.sync(REPORT, now=1000.0)
        stale = startup_control.snapshot({}, now=1000.0 + startup_control.REPORT_TTL_S + 1)
        self.assertIsNone(stale["start_services_on_launch"])
        self.assertFalse(stale["app_reporting"])

    def test_only_booleans_and_short_text_are_kept(self) -> None:
        startup_control.sync({"open_at_login": "yes", "start_services_on_launch": 1,
                              "app_version": "x" * 500, "evil": {"a": 1}})
        startup = startup_control.snapshot({})
        self.assertIsNone(startup["open_at_login"])
        self.assertIsNone(startup["start_services_on_launch"])
        self.assertEqual(len(startup["app_version"]), 40)
        self.assertNotIn("evil", startup)

    def test_the_apps_last_update_error_is_published_cleaned(self) -> None:
        startup_control.sync(REPORT)
        self.assertIsNone(startup_control.snapshot({})["last_update_error"], "no failure, nothing claimed")
        startup_control.sync({**REPORT, "last_update_error": {
            "phase": "downloading", "reason": "Install the update failed.\nHTTP 404 " + "x" * 400,
            "build": "abc1234", "at": 1790000000.5, "extra": "dropped"}})
        error = startup_control.snapshot({})["last_update_error"]
        self.assertEqual(set(error), {"phase", "reason", "build", "at"})
        self.assertEqual((error["phase"], error["build"], error["at"]), ("downloading", "abc1234", 1790000000.5))
        self.assertEqual(len(error["reason"]), 300)
        self.assertNotIn("\n", error["reason"])
        startup_control.sync({**REPORT, "last_update_error": "not an object"})
        self.assertIsNone(startup_control.snapshot({})["last_update_error"])


class RequestTests(_Isolated):
    def run_request(self, *args, **kwargs):
        kwargs.setdefault("wait", 2.0)
        return asyncio.run(startup_control.request(*args, **kwargs))

    def test_turning_services_on_is_handed_to_the_app_and_its_answer_comes_back(self) -> None:
        with FakeApp() as app:
            time.sleep(0.1)
            answer = self.run_request("set_start_services_on_launch", True, actor="fleet",
                                      requested_by="gateway-admin")
        self.assertEqual(answer["state"], "applied")
        self.assertEqual(app.received[-1]["action"], "set_start_services_on_launch")
        self.assertIs(app.received[-1]["value"], True)
        self.assertEqual(self.audit_events(), ["requested", "applied"])
        first = startup_control.recent_audit()[0]
        self.assertEqual((first["by"], first["actor"]), ("gateway-admin", "fleet"))

    def test_a_failed_change_is_reported_as_failed_with_the_apps_reason(self) -> None:
        with FakeApp(fail=True):
            time.sleep(0.1)
            answer = self.run_request("set_open_at_login", False)
        self.assertEqual(answer["state"], "failed")
        self.assertFalse(answer["ok"])
        self.assertIn("agent", answer["detail"])
        self.assertIn("failed", self.audit_events())

    def test_a_request_the_app_has_not_collected_is_queued_not_lost(self) -> None:
        startup_control.sync(REPORT)  # reporting, but nobody is polling now
        answer = self.run_request("set_start_services_on_launch", True, wait=0.1)
        self.assertEqual(answer["state"], "queued")
        taken = startup_control.sync(REPORT)["requests"]
        self.assertEqual([item["action"] for item in taken], ["set_start_services_on_launch"])
        self.assertEqual(startup_control.sync(REPORT)["requests"], [], "a request is handed over once")

    def test_a_setting_change_needs_a_value_and_an_app_that_is_reporting(self) -> None:
        startup_control.sync(REPORT)
        with self.assertRaises(startup_control.StartupRefused) as missing:
            self.run_request("set_start_services_on_launch", None)
        self.assertEqual(missing.exception.code, "VALUE_REQUIRED")
        with self.assertRaises(startup_control.StartupRefused) as typo:
            self.run_request("set_start_services_on_launch", "yes")
        self.assertEqual(typo.exception.status, 400)
        with self.assertRaises(startup_control.StartupRefused) as unknown:
            self.run_request("reboot", True)
        self.assertEqual(unknown.exception.code, "UNKNOWN_ACTION")
        startup_control.reset_for_tests()
        with self.assertRaises(startup_control.StartupRefused) as silent:
            self.run_request("set_open_at_login", True)
        self.assertEqual(silent.exception.code, "APP_NOT_REPORTING")

    def test_a_restart_is_refused_while_a_job_runs_unless_it_is_forced(self) -> None:
        self.work = {"running": 1, "queued": 2}
        with FakeApp() as app:
            time.sleep(0.1)
            with self.assertRaises(startup_control.StartupRefused) as refused:
                self.run_request("restart_services", actor="fleet")
            self.assertEqual(refused.exception.status, 409)
            self.assertEqual(refused.exception.code, "WORK_IN_PROGRESS")
            self.assertEqual(refused.exception.extra["work"], {"running": 1, "queued": 2})
            self.assertEqual(app.received, [], "nothing reached the app")
            forced = self.run_request("restart_services", force=True, actor="fleet")
        self.assertEqual(forced["state"], "handed_to_app")
        self.assertEqual(app.received[-1]["action"], "restart_services")
        self.assertIn("refused", self.audit_events())
        self.assertIn("handed_to_app", self.audit_events())

    def test_an_idle_mac_restarts_without_force(self) -> None:
        with FakeApp() as app:
            time.sleep(0.1)
            answer = self.run_request("restart_services")
        self.assertEqual(answer["state"], "handed_to_app")
        self.assertEqual([item["action"] for item in app.received], ["restart_services"])

    def test_force_must_be_a_boolean(self) -> None:
        startup_control.sync(REPORT)
        with self.assertRaises(startup_control.StartupRefused):
            self.run_request("restart_services", force="true")

    def test_start_services_does_nothing_while_every_studio_is_up(self) -> None:
        startup_control.sync(REPORT)
        answer = self.run_request("start_services", studios={"voice": "up", "image": "up"})
        self.assertEqual(answer["state"], "already_running")
        self.assertEqual(startup_control.sync(REPORT)["requests"], [])

    def test_start_services_restarts_when_a_studio_is_down_and_still_respects_work(self) -> None:
        self.work = {"running": 1, "queued": 0}
        startup_control.sync(REPORT)
        with self.assertRaises(startup_control.StartupRefused) as refused:
            self.run_request("start_services", studios={"voice": "up", "image": "down"})
        self.assertEqual(refused.exception.code, "WORK_IN_PROGRESS")
        self.work = dict(IDLE)
        with FakeApp() as app:
            time.sleep(0.1)
            answer = self.run_request("start_services", studios={"voice": "up", "image": "down"})
        self.assertEqual(answer["state"], "handed_to_app")
        self.assertEqual(app.received[-1]["action"], "restart_services")

    def test_a_request_nobody_collected_expires(self) -> None:
        startup_control.sync(REPORT, now=time.time())
        self.run_request("set_start_services_on_launch", True, wait=0.05)
        later = time.time() + startup_control.REQUEST_TTL_S + 5
        self.assertEqual(startup_control.sync(REPORT, now=later)["requests"], [])
        self.assertIn("expired", self.audit_events())

    def test_the_audit_log_is_private_and_bounded(self) -> None:
        for number in range(startup_control._AUDIT_KEEP + 20):
            startup_control.audit("noop", {"n": number})
        path = startup_control.audit_path()
        self.assertEqual(oct(path.stat().st_mode & 0o777), oct(0o600))
        self.assertEqual(len(path.read_text().splitlines()), startup_control._AUDIT_KEEP)
        self.assertEqual(startup_control.recent_audit(1)[0]["n"], startup_control._AUDIT_KEEP + 19)


class ActiveWorkTests(unittest.TestCase):
    def test_running_and_waiting_items_count_and_finished_or_cancelled_ones_do_not(self) -> None:
        from components.hub.backend import broker, chat_jobs, transcription_jobs
        batches = {
            "a": {"items": [{"state": "running"}, {"state": "queued"}, {"state": "done"},
                            {"state": "error"}]},
            "b": {"cancelled": True, "items": [{"state": "queued"}]},
        }
        with patch.object(broker, "batches", batches), patch.object(broker, "_busy", set()), \
                patch.object(transcription_jobs, "batches",
                             {"t": {"items": [{"state": "uncertain"}]}}), \
                patch.object(chat_jobs, "batches", {"c": {"packs": [{"state": "running"}]}}):
            self.assertEqual(startup_control.active_work(), {"running": 3, "queued": 1})


class AllowlistTests(unittest.TestCase):
    def test_a_controller_may_relay_and_a_node_may_receive(self) -> None:
        for method, path in (("POST", "/api/hub/startup/request"), ("GET", "/api/hub/startup"),
                             ("POST", "/api/hub/fleet/nodes/mac-1201-hub/startup")):
            self.assertTrue(auth.fleet_path_allowed(method, path), path)

    def test_the_apps_own_exchange_is_never_a_fleet_route(self) -> None:
        self.assertFalse(auth.fleet_path_allowed("POST", "/api/hub/startup/sync"))
        self.assertFalse(auth.node_origin_allowed("POST", "/api/hub/startup/sync"))

    def test_a_nodes_own_credential_cannot_command_a_mac(self) -> None:
        for path in ("/api/hub/startup/request", "/api/hub/fleet/nodes/x/startup"):
            self.assertFalse(auth.node_origin_allowed("POST", path), path)
            self.assertFalse(auth.node_path_allowed("POST", path), path)

    def test_no_other_verb_is_opened(self) -> None:
        self.assertFalse(auth.fleet_path_allowed("PUT", "/api/hub/startup/request"))
        self.assertFalse(auth.fleet_path_allowed("DELETE", "/api/hub/fleet/nodes/x/startup"))
        self.assertFalse(auth.fleet_path_allowed("POST", "/api/hub/startup/request/extra"))


class _FakeResponse:
    def __init__(self, status: int, body: object) -> None:
        self.status_code, self._body = status, body

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class _FakeClient:
    def __init__(self, response: _FakeResponse | Exception) -> None:
        self.response, self.calls = response, []

    async def post(self, url, json=None, timeout=None):  # noqa: A002
        self.calls.append((url, json))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    async def aclose(self) -> None:
        return None


class RelayTests(_Isolated):
    def setUp(self) -> None:
        super().setUp()
        fleet_nodes._write({"mac-1201-hub": {
            "id": "mac-1201-hub", "name": "mac-1201", "address": "http://100.96.171.120:47873",
            "role": "agent", "studios": [], "joined_at": 1.0, "last_seen_at": 1.0}})

    def relay(self, client, payload=None):
        return asyncio.run(fleet_nodes.relay_startup(
            "mac-1201-hub",
            payload or {"action": "set_start_services_on_launch", "value": True, "requested_by": "gateway"},
            actor="fleet", client=client))

    def test_the_request_goes_to_the_nodes_own_route_and_only_the_named_fields_travel(self) -> None:
        client = _FakeClient(_FakeResponse(200, {"ok": True, "state": "applied"}))
        answer = self.relay(client, {"action": "restart_services", "force": True, "requested_by": "g",
                                     "token": "must-not-travel", "extra": 1})
        url, sent = client.calls[0]
        self.assertEqual(url, "http://100.96.171.120:47873/api/hub/startup/request")
        self.assertEqual(sent, {"action": "restart_services", "force": True, "requested_by": "g"})
        self.assertEqual(answer["state"], "applied")
        self.assertEqual(answer["node"], "mac-1201-hub")
        self.assertIn("relay", self.audit_events())

    def test_the_controller_presents_the_fleet_token_like_every_other_call_to_a_node(self) -> None:
        with patch.object(fleet_nodes, "fleet_token", lambda: "fleet-secret-value-123456"):
            client = fleet_nodes._probe_client()
        self.assertEqual(client.headers["X-Hub-Token"], "fleet-secret-value-123456")
        asyncio.run(client.aclose())

    def test_an_unknown_node_is_a_404_not_a_request_to_somewhere_else(self) -> None:
        with self.assertRaises(fleet_nodes.StartupRelayError) as error:
            asyncio.run(fleet_nodes.relay_startup("not-joined", {"action": "restart_services"},
                                                  client=_FakeClient(_FakeResponse(200, {}))))
        self.assertEqual(error.exception.status, 404)

    def test_a_node_with_no_hub_running_is_named_as_such(self) -> None:
        import httpx
        with self.assertRaises(fleet_nodes.StartupRelayError) as error:
            self.relay(_FakeClient(httpx.ConnectError("refused")))
        self.assertEqual(error.exception.status, 502)
        self.assertEqual(error.exception.body["reason"], "no_hub")
        self.assertIn("services are off", error.exception.body["detail"])

    def test_a_node_that_does_not_answer_is_not_called_services_off(self) -> None:
        import httpx
        with self.assertRaises(fleet_nodes.StartupRelayError) as error:
            self.relay(_FakeClient(httpx.ConnectTimeout("slow")))
        self.assertEqual(error.exception.body["reason"], "unreachable")
        self.assertIn("asleep", error.exception.body["detail"])

    def test_the_nodes_own_refusal_is_passed_through_with_its_status_and_work_counts(self) -> None:
        refusal = {"ok": False, "code": "WORK_IN_PROGRESS", "detail": "1 job(s) running",
                   "work": {"running": 1, "queued": 0}}
        with self.assertRaises(fleet_nodes.StartupRelayError) as error:
            self.relay(_FakeClient(_FakeResponse(409, refusal)))
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(error.exception.body["work"], {"running": 1, "queued": 0})

    def test_a_node_that_rejects_the_credential_is_not_reported_as_a_job_problem(self) -> None:
        with self.assertRaises(fleet_nodes.StartupRelayError) as error:
            self.relay(_FakeClient(_FakeResponse(403, {"detail": "refused"})))
        self.assertEqual(error.exception.body["code"], "NODE_REFUSED")


class RosterTests(_Isolated):
    NODE = {"id": "n1", "address": "http://100.1.1.1:47873", "name": "n1", "role": "agent",
            "studios": [], "joined_at": 1.0, "last_seen_at": 1.0}

    def test_a_snapshot_the_controller_already_holds_carries_the_nodes_startup(self) -> None:
        startup = {"open_at_login": True, "start_services_on_launch": False}
        snapshot = {"host": {}, "version": "0.6.127", "reachable": True, "auth": True,
                    "status": "connected", "startup": startup}
        row = fleet_nodes._from_snapshot({"id": "n1"}, snapshot)
        self.assertEqual(row["state"], "reachable")
        self.assertEqual(row["startup"], startup)

    def test_a_nodes_last_update_error_reaches_the_controllers_row(self) -> None:
        error = {"phase": "staging", "reason": "the controller has not supplied a file yet", "build": "b", "at": 1.0}
        snapshot = {"host": {}, "version": "0.6.152", "reachable": True, "auth": True,
                    "status": "connected", "startup": {"open_at_login": True, "last_update_error": error}}
        row = fleet_nodes._from_snapshot({"id": "n1"}, snapshot)
        self.assertEqual(row["startup"]["last_update_error"], error)

    def test_a_node_that_goes_quiet_keeps_what_it_last_said_and_is_marked_stale(self) -> None:
        fleet_nodes._write({"n1": dict(self.NODE)})
        fleet_nodes.note_seen(["n1"], {"n1": {"open_at_login": True, "start_services_on_launch": False}})
        node = fleet_nodes._read()["n1"]
        gone = {"host": None, "reachable": False, "auth": True, "status": "unreachable"}
        row = fleet_nodes._from_snapshot({"id": "n1", "last_startup": node["last_startup"]}, gone)
        self.assertEqual(row["state"], "unreachable")
        self.assertIs(row["startup"]["start_services_on_launch"], False)
        self.assertTrue(row["startup"]["stale"])
        self.assertFalse(row["startup"]["app_reporting"])

    def test_a_node_that_never_reported_is_unknown_not_off(self) -> None:
        gone = {"host": None, "reachable": False, "auth": True, "status": "no_hub"}
        row = fleet_nodes._from_snapshot({"id": "n1"}, gone)
        self.assertIsNone(row["startup"])

    def test_the_roster_probe_reads_startup_from_a_direct_answer(self) -> None:
        class Client:
            async def get(self, url, timeout=None):
                return _FakeResponse(200, {"host": {}, "version": "0.6.127",
                                           "startup": {"open_at_login": False,
                                                       "start_services_on_launch": True}})
        with patch.object(fleet_nodes, "fresh_snapshot", lambda node_id: None):
            row = asyncio.run(fleet_nodes.probe(dict(self.NODE), Client()))
        self.assertIs(row["startup"]["start_services_on_launch"], True)

    def test_an_older_node_that_says_nothing_about_startup_reads_as_unknown(self) -> None:
        class Client:
            async def get(self, url, timeout=None):
                return _FakeResponse(200, {"host": {}, "version": "0.6.126"})
        with patch.object(fleet_nodes, "fresh_snapshot", lambda node_id: None):
            row = asyncio.run(fleet_nodes.probe(dict(self.NODE), Client()))
        self.assertIsNone(row["startup"])

    def test_the_pool_row_keeps_startup(self) -> None:
        member = fleet_nodes._member({"id": "n1", "name": "N", "startup": {"open_at_login": True}},
                                     role="agent")
        self.assertEqual(member["startup"], {"open_at_login": True})


class RouteTests(unittest.TestCase):
    """The real app, through its middleware: who may call what."""

    OWNER = {"Authorization": "Bearer same-existing-hub-token"}

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="studiohub-startup-routes-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "State"
        self.data.mkdir()
        (self.data / ".hub_token").write_text("same-existing-hub-token\n")
        os.chmod(self.data / ".hub_token", 0o600)
        workers = self.root / "workers.json"
        workers.write_text(json.dumps([{
            "id": "voice", "title": "Voice Studio", "modality": "voice", "host": "127.0.0.1",
            "port": 51102, "machine": "node-a", "local": True,
            "runtime_identity": {"id": "voice-runtime"}, "hub_token": "same-existing-hub-token",
            "managed_output_roots": []}]))
        os.chmod(workers, 0o600)
        env = patch.dict(os.environ, {"STUDIOHUB_DATA_DIR": str(self.data),
                                      "STUDIOHUB_WORKERS_FILE": str(workers),
                                      "STUDIOHUB_PACKAGED": "1", "STUDIOHUB_REQUIRE_AUTH": "1",
                                      "STUDIOHUB_FLEET_TOKEN": "fleet-token-for-the-startup-tests"})
        env.start()
        self.addCleanup(env.stop)
        startup_control.reset_for_tests()
        self.addCleanup(startup_control.reset_for_tests)

    def client(self, stack: ExitStack):
        from fastapi.testclient import TestClient
        from components.hub.backend import ledger, monitor as monitor_module, registry
        from components.hub.standalone import create_app
        for module, name, value in (
            (registry, "DATA_DIR", self.data), (registry, "REGISTRY_FILE", self.data / "studios.json"),
            (registry, "LABELS_FILE", self.data / "machine_labels.json"),
            (registry, "FLAGS_FILE", self.data / "machine_flags.json"),
            (monitor_module, "DATA_DIR", self.data), (auth, "TOKEN_FILE", self.data / ".hub_token"),
            (auth, "PASSWORD_FILE", self.data / ".hub_password.json"),
            (auth, "SESSIONS_FILE", self.data / ".hub_sessions.json"),
            (ledger, "DB_FILE", self.data / "hub.db"), (ledger, "PINOKIO_HOME", self.data),
            (startup_control, "DATA_DIR", self.data), (fleet_nodes, "DATA_DIR", self.data),
            (startup_control, "DELIVERY_WAIT_S", 0.2),
        ):
            stack.enter_context(patch.object(module, name, value))
        registry._labels_cache = None
        registry._flags_cache = None
        return stack.enter_context(TestClient(create_app(start_background=False)))

    FLEET = {"Authorization": "Bearer fleet-token-for-the-startup-tests"}

    def applying_app(self, action: str, value: bool):
        startup_control.sync(REPORT)
        wait_for = startup_control._wait_for

        async def apply_before_wait(request_id, seconds):
            # Route checks exercise delivery/ack without racing the fixture's 0.2s deadline.
            requests = startup_control.sync(REPORT)["requests"]
            self.assertEqual([item["id"] for item in requests], [request_id])
            self.assertEqual(requests[0]["action"], action)
            self.assertIs(requests[0]["value"], value)
            startup_control.sync({**REPORT, action.removeprefix("set_"): value,
                                  "applied": [{"id": request_id, "ok": True}]})
            return await wait_for(request_id, 0)

        return patch.object(startup_control, "_wait_for", apply_before_wait)

    def test_no_credential_no_answer(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            for path in ("/api/hub/startup/request", "/api/hub/startup/sync",
                         "/api/hub/fleet/nodes/local/startup"):
                self.assertEqual(client.post(path, json={"action": "restart_services"}).status_code, 401, path)
            self.assertEqual(client.get("/api/hub/startup").status_code, 401)

    def test_the_apps_exchange_is_the_owners_and_a_fleet_credential_is_refused(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            self.assertEqual(client.post("/api/hub/startup/sync", json=REPORT,
                                         headers=self.FLEET).status_code, 403)
            answer = client.post("/api/hub/startup/sync", json=REPORT, headers=self.OWNER)
            self.assertEqual(answer.status_code, 200)
            self.assertEqual(answer.json()["requests"], [])

    def test_resources_publish_the_startup_object_to_a_controller(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            client.post("/api/hub/startup/sync", json=REPORT, headers=self.OWNER)
            body = client.get("/api/hub/resources", headers=self.FLEET).json()
            self.assertIs(body["startup"]["start_services_on_launch"], False)
            self.assertIs(body["startup"]["open_at_login"], True)
            self.assertEqual(body["startup"]["services_running"]["total"], 1)
            health = client.get("/api/health").json()
            self.assertNotIn("startup", health, "the public health route must not carry settings")

    def test_a_fleet_credential_can_ask_and_the_refusal_for_running_work_is_a_409(self) -> None:
        with ExitStack() as stack:
            stack.enter_context(patch.object(startup_control, "active_work",
                                             lambda: {"running": 2, "queued": 0}))
            client = self.client(stack)
            client.post("/api/hub/startup/sync", json=REPORT, headers=self.OWNER)
            refused = client.post("/api/hub/startup/request", json={"action": "restart_services"},
                                  headers=self.FLEET)
            self.assertEqual(refused.status_code, 409)
            self.assertEqual(refused.json()["code"], "WORK_IN_PROGRESS")
            self.assertEqual(refused.json()["work"]["running"], 2)
            bad = client.post("/api/hub/startup/request", json={"action": "format_disk"}, headers=self.FLEET)
            self.assertEqual(bad.status_code, 400)

    def test_a_setting_change_round_trips_through_the_app(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            with self.applying_app("set_start_services_on_launch", True):
                answer = client.post("/api/hub/startup/request", headers=self.FLEET,
                                     json={"action": "set_start_services_on_launch", "value": True,
                                           "requested_by": "gateway"})
            self.assertEqual(answer.status_code, 200)
            self.assertEqual(answer.json()["state"], "applied")
            audit = client.get("/api/hub/startup", headers=self.FLEET).json()["audit"]
            self.assertEqual([row["event"] for row in audit][:2], ["requested", "applied"])

    def test_a_controller_applies_a_request_for_itself_and_refuses_unknown_nodes(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            from components.hub.backend import control_plane
            control_plane.save_settings({"role": "controller"})
            with self.applying_app("set_open_at_login", False):
                mine = client.post("/api/hub/fleet/nodes/local/startup", headers=self.FLEET,
                                   json={"action": "set_open_at_login", "value": False})
            self.assertEqual(mine.json()["state"], "applied")
            missing = client.post("/api/hub/fleet/nodes/nobody/startup", headers=self.FLEET,
                                  json={"action": "restart_services"})
            self.assertEqual(missing.status_code, 404)

    def test_a_mac_that_is_not_a_controller_will_not_relay(self) -> None:
        with ExitStack() as stack:
            client = self.client(stack)
            from components.hub.backend import control_plane
            control_plane.save_settings({"role": "agent"})
            refused = client.post("/api/hub/fleet/nodes/someone/startup", headers=self.FLEET,
                                  json={"action": "restart_services"})
            self.assertEqual(refused.status_code, 404)


if __name__ == "__main__":
    unittest.main()
