"""Workstation mode: a shop's cashier Mac lends the fleet its idle time, never its user's time.

Owner, 2026-10-03 ("Shops"), with two corrections the same night: step-aside FINISHES the job it has
(nothing wasted), then unloads its model and takes no new work until the resume window passes; there
is no fixed memory reserve - a job starts on the model's own free-before-start floor (or the Mac's own
`start_free_gb`), and the only interruption is macOS critical memory pressure for 20 seconds.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import test_node_rename as base
from components import workstation_models
from components.hub import workstation
from components.hub.backend import auth, broker, fleet_nodes, peers

NOON = dt.datetime(2026, 10, 3, 12, 0)


class _Settings(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="studiohub-workstation-")
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(workstation, "SETTINGS_FILE", Path(tmp.name) / "workstation.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        workstation.reset_for_tests()
        self.addCleanup(workstation.reset_for_tests)


class Settings(_Settings):
    def test_defaults_are_off_with_the_owners_numbers(self) -> None:
        value = workstation.load()
        self.assertFalse(value["enabled"])
        self.assertFalse(value["idle_only"])
        self.assertFalse(value["step_aside"])
        self.assertEqual((value["idle_minutes"], value["resume_after_minutes"]), (3, 2))
        self.assertIsNone(value["start_free_gb"], "no fixed reserve: each model's own floor")

    def test_save_keeps_what_it_was_given_and_refuses_junk_by_name(self) -> None:
        workstation.save({"enabled": True, "idle_minutes": 10, "quiet_hours": ["22:00-07:00"],
                          "start_free_gb": 3})
        value = workstation.load()
        self.assertEqual((value["enabled"], value["idle_minutes"], value["start_free_gb"]), (True, 10, 3.0))
        workstation.save({"start_free_gb": None})
        self.assertIsNone(workstation.load()["start_free_gb"])
        for junk, word in (({"idle_minutes": 0}, "idle_minutes"), ({"idle_minutes": 61}, "idle_minutes"),
                           ({"idle_minutes": True}, "idle_minutes"), ({"enabled": "yes"}, "enabled"),
                           ({"quiet_hours": ["25:00-07:00"]}, "quiet_hours"),
                           ({"start_free_gb": 20}, "start_free_gb"), ({"memory_floor_gb": 2.5}, "memory_floor_gb")):
            with self.assertRaises(ValueError) as caught:
                workstation.save(junk)
            self.assertIn(word, str(caught.exception))
        self.assertEqual(workstation.load()["idle_minutes"], 10, "a refused change changed nothing")

    def test_a_damaged_file_keeps_the_mode_on(self) -> None:
        workstation.SETTINGS_FILE.write_text("{not json")
        self.assertTrue(workstation.load()["enabled"])


class Decide(unittest.TestCase):
    def settings(self, **changes):
        return workstation.validate({"enabled": True, **changes})

    def test_off_and_working(self) -> None:
        self.assertEqual(workstation.decide(workstation.validate({}), idle_s=0, locked=False, now=NOON)[0], "off")
        self.assertEqual(workstation.decide(self.settings(), idle_s=0, locked=False, now=NOON),
                         ("working", None), "both toggles default off: input does not matter")

    def test_quiet_hours_wrap_midnight(self) -> None:
        spans = self.settings(quiet_hours=["22:00-07:00"])
        for hour, expected in ((23, "quiet_hours"), (3, "quiet_hours"), (7, "working"), (12, "working")):
            state = workstation.decide(spans, idle_s=999, locked=False, now=NOON.replace(hour=hour))[0]
            self.assertEqual(state, expected, hour)

    def test_only_when_idle_waits_for_the_threshold_or_a_locked_screen(self) -> None:
        s = self.settings(idle_only=True, idle_minutes=3)
        self.assertEqual(workstation.decide(s, idle_s=170, locked=False, now=NOON)[0], "standing_by")
        self.assertEqual(workstation.decide(s, idle_s=180, locked=False, now=NOON)[0], "working")
        self.assertEqual(workstation.decide(s, idle_s=1, locked=True, now=NOON)[0], "working")
        self.assertEqual(workstation.decide(s, idle_s=None, locked=False, now=NOON)[0], "standing_by",
                         "an unreadable idle time counts as someone at the keyboard")

    def test_step_aside_stands_by_until_the_resume_window_passes(self) -> None:
        s = self.settings(step_aside=True, resume_after_minutes=2)
        state, why = workstation.decide(s, idle_s=5, locked=False, now=NOON)
        self.assertEqual((state, why), ("standing_by", "someone is using this Mac"))
        self.assertEqual(workstation.decide(s, idle_s=120, locked=False, now=NOON)[0], "working")

    def test_a_yield_is_reported_for_a_minute(self) -> None:
        s = self.settings()
        self.assertEqual(workstation.decide(s, idle_s=999, locked=False, now=NOON, since_yield=10,
                                            yield_reason="pressure")[0], "yielded")
        self.assertEqual(workstation.decide(s, idle_s=999, locked=False, now=NOON, since_yield=61)[0], "working")


class MacReads(unittest.TestCase):
    """The two reads Lite Mode decides on, taken from this Mac for real (nothing mocked)."""

    def test_idle_time_comes_from_the_hid_system(self) -> None:
        for _ in range(2):
            value = workstation.hid_idle_seconds()
            self.assertIsInstance(value, float, "the HID idle read must answer on a Mac")
            self.assertTrue(math.isfinite(value) and value >= 0.0, value)

    def test_a_locked_screen_reads_as_a_bool(self) -> None:
        self.assertIsInstance(workstation.screen_locked(), bool)


class Admission(_Settings):
    def test_off_lets_everything_in(self) -> None:
        self.assertIsNone(workstation.admission_refusal("anything/else"))

    def test_only_workstation_models_and_only_when_working(self) -> None:
        workstation.save({"enabled": True, "step_aside": True})
        with mock.patch.object(workstation, "hid_idle_seconds", return_value=999.0), \
                mock.patch.object(workstation, "screen_locked", return_value=False):
            self.assertIsNone(workstation.admission_refusal("mlx-community/VoxCPM2-4bit"))
            self.assertEqual(workstation.admission_refusal("mlx-community/VoxCPM2-8bit")["code"],
                             workstation.NOT_ALLOWED_CODE)
        with mock.patch.object(workstation, "hid_idle_seconds", return_value=0.3), \
                mock.patch.object(workstation, "screen_locked", return_value=False):
            refusal = workstation.admission_refusal("mlx-community/VoxCPM2-4bit")
        self.assertEqual(refusal["code"], workstation.STANDING_BY_CODE)
        self.assertIn("someone is using this Mac", refusal["detail"])

    def test_the_allowlist_names_the_owners_six_models_by_package_and_repo(self) -> None:
        manifests = Path(__file__).resolve().parents[1] / "model-manifests"
        allowed = [path.stem for path in manifests.glob("*.json")
                   if workstation_models.manifest_allowed(json.loads(path.read_text()), path.stem)]
        self.assertEqual(sorted(allowed), sorted([
            "flux2-klein-4b-mlx-4bit", "z-image-turbo-mflux-4bit", "qwen3-tts-12hz-0-6b-base-4bit",
            "voxcpm2-4bit", "whisper-turbo", "stable-audio-3-medium"]))
        ids = {json.loads(path.read_text()).get(key) for path in manifests.glob("*.json")
               for key in ("package_id", "model_id")}
        missing = [name for name in workstation_models.WORKSTATION_MODELS if name not in ids]
        self.assertEqual(missing, [], "a workstation model no manifest ships")


class HandBack(_Settings):
    RECEIPT = {"job": {"id": "j1", "state": "running", "worker_id": "image", "model_revision": "r1"}}

    def test_a_handed_back_job_reads_as_an_error_with_its_receipt_kept(self) -> None:
        self.assertIsNone(workstation.rewrite_status("image", "j1", 200, self.RECEIPT), "not yielded")
        workstation._yielded[("image", "j1")] = {"reason": "pressure", "at": 0, "payload": self.RECEIPT}
        status, body = workstation.rewrite_status(
            "image", "j1", 200, {"job": {**self.RECEIPT["job"], "state": "cancelled"}})
        self.assertEqual((status, body["job"]["state"], body["job"]["error_code"]),
                         (200, "error", "WORKSTATION_YIELDED"))
        self.assertEqual(body["job"]["model_revision"], "r1", "identity fields the controller checks stay")
        status, body = workstation.rewrite_status("image", "j1", 502, None)
        self.assertEqual((status, body["job"]["worker_id"], body["job"]["state"]), (200, "image", "error"),
                         "a stopped worker answers with the receipt saved at the yield")

    def test_a_job_that_finished_first_is_kept(self) -> None:
        workstation._yielded[("image", "j1")] = {"reason": "pressure", "at": 0, "payload": None}
        done = {"job": {"id": "j1", "state": "done"}}
        self.assertIsNone(workstation.rewrite_status("image", "j1", 200, done))


class _Monitor:
    registry = [{"id": "image", "local": True, "pid": 999999, "base_url": "http://127.0.0.1:1",
                 "hub_token": "t"}]
    _client = None


class Tick(_Settings):
    """The loop: step-aside never interrupts; only sustained critical pressure does."""

    def run_tick(self, held, memo, *, idle=0.2, pressure=1):
        calls = {"yield": [], "release": 0}

        async def fake_yield(monitor, jobs, reason):
            calls["yield"].append((jobs, reason))

        async def fake_release(monitor):
            calls["release"] += 1
        with mock.patch.object(workstation, "yield_jobs", fake_yield), \
                mock.patch.object(workstation, "release_models", fake_release), \
                mock.patch.object(workstation, "apply_priority"), \
                mock.patch.object(workstation, "hid_idle_seconds", return_value=idle), \
                mock.patch.object(workstation, "screen_locked", return_value=False), \
                mock.patch.object(workstation, "pressure_level", return_value=pressure):
            asyncio.run(workstation.tick(_Monitor(), lambda: list(held), memo))
        return calls

    def test_step_aside_finishes_the_job_then_unloads_once(self) -> None:
        workstation.save({"enabled": True, "step_aside": True})
        memo: dict = {}
        calls = self.run_tick([("image", "j1")], memo, idle=0.2)
        self.assertEqual(calls, {"yield": [], "release": 0}, "input during a job: the job runs on")
        calls = self.run_tick([], memo, idle=0.5)
        self.assertEqual(calls["release"], 1, "the job ended while someone is using it: unload")
        self.assertEqual(self.run_tick([], memo, idle=1.0)["release"], 0, "once")
        self.run_tick([], memo, idle=500)   # back to working after the window
        self.assertEqual(self.run_tick([], memo, idle=0.1)["release"], 1, "again after work resumed")

    def test_only_sustained_critical_pressure_yields(self) -> None:
        workstation.save({"enabled": True})
        memo: dict = {}
        with mock.patch.object(workstation.time, "monotonic", return_value=1000.0):
            self.assertEqual(self.run_tick([("image", "j1")], memo, pressure=4)["yield"], [])
        with mock.patch.object(workstation.time, "monotonic", return_value=1019.0):
            self.assertEqual(self.run_tick([("image", "j1")], memo, pressure=4)["yield"], [])
        with mock.patch.object(workstation.time, "monotonic", return_value=1020.0):
            calls = self.run_tick([("image", "j1")], memo, pressure=4)
        self.assertEqual(calls["yield"][0][0], [("image", "j1")])
        self.assertIn("critical memory pressure", calls["yield"][0][1])
        memo.clear()
        with mock.patch.object(workstation.time, "monotonic", return_value=2000.0):
            self.run_tick([("image", "j2")], memo, pressure=4)
        with mock.patch.object(workstation.time, "monotonic", return_value=2030.0):
            self.assertEqual(self.run_tick([("image", "j2")], memo, pressure=2)["yield"], [],
                             "pressure that eased resets the clock")

    def test_tick_backgrounds_this_macs_workers_only_while_on(self) -> None:
        with mock.patch.object(workstation, "apply_priority") as priority:
            asyncio.run(workstation.tick(_Monitor(), lambda: [], {}))
            priority.assert_not_called()
            workstation.save({"enabled": True})
            with mock.patch.object(workstation, "pressure_level", return_value=1):
                asyncio.run(workstation.tick(_Monitor(), lambda: [], {}))
            priority.assert_called_once_with([999999], background=True)

    def test_background_priority_is_put_on_and_taken_off(self) -> None:
        import subprocess
        child = subprocess.Popen(["/bin/sleep", "5"])
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        workstation.apply_priority([child.pid], background=True)
        self.assertIn(child.pid, workstation._backgrounded)
        ps = subprocess.run(["ps", "-o", "pri=", "-p", str(child.pid)], capture_output=True, text=True)
        self.assertLess(int(ps.stdout.strip()), 10, "PRIO_DARWIN_BG drops it to the background band")
        workstation.apply_priority([], background=False)
        ps = subprocess.run(["ps", "-o", "pri=", "-p", str(child.pid)], capture_output=True, text=True)
        self.assertGreater(int(ps.stdout.strip()), 10)
        self.assertEqual(workstation._backgrounded, set())


class Controller(unittest.TestCase):
    """The controller treats a standing-by workstation as a busy Mac, never a failed one."""

    def tearDown(self) -> None:
        peers._cache.pop("shop-1", None)

    def snapshot(self, state: str, **extra) -> None:
        import time
        peers._cache["shop-1"] = (time.time(), {
            "reachable": True, "auth": True, "status": "connected", "host": {"available_gb": 3.3},
            "workstation": {"state": state, **extra}})

    def test_dispatch_skips_a_standing_by_mac_and_capacity_counts_it_busy(self) -> None:
        studio = {"id": "image@shop-1", "machine": "shop-1", "local": False}
        self.snapshot("working")
        self.assertTrue(peers.dispatch_ready(studio))
        for state in ("standing_by", "yielded", "quiet_hours"):
            self.snapshot(state)
            self.assertFalse(peers.dispatch_ready(studio), state)
            self.assertTrue(peers.standing_by("shop-1"))
        self.snapshot("off")
        self.assertTrue(peers.dispatch_ready(studio))

    def test_the_macs_own_start_floor_replaces_the_models(self) -> None:
        studio = {"machine": "shop-1"}
        self.snapshot("working")
        self.assertEqual(broker._with_machine_floor({"min_free": 2.8}, studio), {"min_free": 2.8})
        self.snapshot("working", enabled=True, start_free_gb=3.5)
        self.assertEqual(broker._with_machine_floor({"min_free": 2.8}, studio), {"min_free": 3.5})
        self.snapshot("off", enabled=False, start_free_gb=3.5)
        self.assertEqual(broker._with_machine_floor({"min_free": 2.8}, studio), {"min_free": 2.8},
                         "Lite Mode off: its floor does not apply")
        self.assertIsNone(broker._with_machine_floor(None, studio))

    def test_workstation_refusals_are_waits(self) -> None:
        for code in ("WORKSTATION_STANDING_BY", "WORKSTATION_MODEL_NOT_ALLOWED", "MACHINE_BUSY"):
            error = RuntimeError("x")
            error.status_code, error.error_code = 409, code
            self.assertTrue(broker._is_machine_reservation_refusal(error), code)

    def test_a_node_report_is_cut_to_the_contract(self) -> None:
        row = fleet_nodes._workstation_of({"state": "working", "enabled": True, "secret": "no"})
        self.assertNotIn("secret", row)
        self.assertIsNone(fleet_nodes._workstation_of({"enabled": True}))


class Routes(unittest.TestCase):
    setUp = base.Routes.setUp
    client = base.Routes.client
    OWNER, FLEET = base.Routes.OWNER, base.Routes.FLEET

    def test_the_fleet_token_reads_and_changes_this_macs_settings(self) -> None:
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(workstation, "SETTINGS_FILE", self.data / "workstation.json"))
            client = self.client(stack)
            self.assertEqual(client.post("/api/hub/workstation", json={"enabled": True}).status_code, 401)
            answer = client.post("/api/hub/workstation", json={"enabled": True, "step_aside": True},
                                 headers=self.FLEET).json()
            self.assertTrue(answer["ok"])
            self.assertTrue(answer["workstation"]["step_aside"])
            self.assertIn(answer["workstation"]["state"], workstation.STATES)
            bad = client.post("/api/hub/workstation", json={"idle_minutes": 0}, headers=self.FLEET)
            self.assertEqual((bad.status_code, bad.json()["code"]), (400, "BAD_SETTING"))
            local = client.post("/api/hub/fleet/nodes/local/workstation", json={"idle_only": True},
                                headers=self.FLEET).json()
            self.assertTrue(local["workstation"]["idle_only"])
            unknown = client.post("/api/hub/fleet/nodes/nobody-hub/workstation", json={}, headers=self.FLEET)
            self.assertEqual(unknown.json()["code"], "UNKNOWN_NODE")
            resources = client.get("/api/hub/resources", headers=self.FLEET).json()
            self.assertTrue(resources["workstation"]["enabled"], "the controller reads it every poll")

    def test_a_nodes_own_credential_cannot_change_another_mac(self) -> None:
        for method, path in (("POST", "/api/hub/fleet/nodes/x-hub/workstation"),
                             ("POST", "/api/hub/workstation")):
            self.assertTrue(auth.fleet_path_allowed(method, path))
            self.assertFalse(auth.node_origin_allowed(method, path))



import test_studio_proxy as proxy_base  # noqa: E402


class PeerSurface(unittest.TestCase):
    """The real proxy: admission at the door, and a handed-back job's status rewritten."""

    setUpClass = classmethod(proxy_base.StudioProxyTests.setUpClass.__func__)
    tearDownClass = classmethod(proxy_base.StudioProxyTests.tearDownClass.__func__)
    client = proxy_base.StudioProxyTests.client
    release_peer_leases = staticmethod(proxy_base.StudioProxyTests.release_peer_leases)
    HEADERS = {"X-Hub-Token": proxy_base.TOKEN}
    KLEIN = "AITRADER/FLUX2-klein-4B-mlx-4bit"

    def setUp(self) -> None:
        self.addCleanup(self.release_peer_leases)
        tmp = tempfile.TemporaryDirectory(prefix="studiohub-workstation-proxy-")
        self.addCleanup(tmp.cleanup)
        for target, name, value in ((workstation, "SETTINGS_FILE", Path(tmp.name) / "workstation.json"),
                                    (workstation, "hid_idle_seconds", lambda: 999.0),
                                    (workstation, "screen_locked", lambda: False)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        workstation.reset_for_tests()
        self.addCleanup(workstation.reset_for_tests)
        workstation.save({"enabled": True, "step_aside": True})

    def submit(self, client, repo: str):
        return client.post("/studio/image/api/generate/txt2img", json={"repo": repo, "prompt": "p"},
                           headers=self.HEADERS)

    def test_admission_takes_a_workstation_model_and_counts_it(self) -> None:
        client = self.client()
        self.assertEqual(self.submit(client, self.KLEIN).status_code, 200)
        self.assertEqual(workstation.status()["jobs_today"], 1)
        self.release_peer_leases()
        refused = self.submit(client, "mlx-community/Qwen3.5-9B-4bit")
        self.assertEqual((refused.status_code, refused.json()["code"]), (409, "WORKSTATION_MODEL_NOT_ALLOWED"))
        with mock.patch.object(workstation, "hid_idle_seconds", lambda: 0.2):
            busy = self.submit(client, self.KLEIN)
        self.assertEqual((busy.status_code, busy.json()["code"]), (409, "WORKSTATION_STANDING_BY"))

    def test_a_handed_back_job_reads_as_handed_back_and_artifacts_are_untouched(self) -> None:
        client = self.client()
        self.worker.poll_job = {"id": "job-1", "state": "running", "worker_id": "image"}
        workstation._yielded[("image", "job-1")] = {"reason": "pressure", "at": 0, "payload": None}
        job = client.get("/studio/image/api/generate/jobs/job-1", headers=self.HEADERS).json()["job"]
        self.assertEqual((job["state"], job["error_code"], job["worker_id"]),
                         ("error", "WORKSTATION_YIELDED", "image"))
        art = client.get("/studio/image/api/generate/jobs/job-1/image", headers=self.HEADERS)
        self.assertEqual(art.content, b"\x89PNG\r\n\x1a\nfake")
        self.worker.poll_job = {"id": "job-1", "state": "done"}


if __name__ == "__main__":
    unittest.main()
