"""The 0.6.210 freeze: no route walks /Volumes, and a blocked probe cannot stop the Hub.

**The incident.** 0.6.206 added a walk of ``/Volumes`` inside ``GET /api/hub/model-presets``, and
the app read that route on every connect and poll. An ad-hoc signed build loses its macOS
removable-volume permission on every update, so the `open()` waited on an unanswered permission
prompt and held a threadpool slot; enough polls filled the pool and ``/api/health`` and
``/api/hub/resources`` stopped answering. Clicking **Allow** revived the Mac.

What is pinned here, in the order the fix is shaped:

1. The probe runs in a **child process with a hard timeout**, at most **once per process**, and a
   blocked child yields the honest ``needs_permission`` state instead of a hung slot.
2. ``status()``/``kits()`` never probe and never block; ``snapshot()`` takes the kits as an
   argument, so no request can reintroduce a walk.
3. Every route keeps answering while the child is blocked, and ``host_stats()`` carries the state.
4. The periodic removed-model handoff reads the library only when a removal actually happened.
5. The Gateway's per-node preset route does its work off the event loop.

The blocked child is a stand-in for the unanswered prompt. `tccutil` is deliberately not used:
that would change this Mac's privacy settings, which the owner forbade.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from components.hub.backend import model_install_plan
from components.hub.backend import removable_volumes

PREFIX = "studiohub-volume-probe:"


def _blocking_child() -> list[str]:
    return [sys.executable, "-c", "import time; time.sleep(30)"]


def _answering_child(marker: Path) -> list[str]:
    source = (
        "import json, sys\n"
        f"open(sys.argv[1], 'a').write('x')\n"
        f"print({PREFIX!r} + json.dumps({{'ok': True, 'kits': ['/Volumes/KIT/model-library']}}))\n"
    )
    return [sys.executable, "-c", source, str(marker)]


class ProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        removable_volumes._reset_for_testing()
        removable_volumes._set_probe_command_for_testing(None)

    def tearDown(self) -> None:
        removable_volumes._reset_for_testing()
        removable_volumes._set_probe_command_for_testing(None)

    def test_status_before_the_probe_is_not_checked_and_does_not_block(self) -> None:
        # "I have not looked" must not read as "there is nothing there" - and asking must not
        # start a walk either.
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("probed from status")):
            state = removable_volumes.status()
        self.assertEqual(state["state"], "not_checked")
        self.assertEqual(state["kits"], [])

    def test_a_blocked_child_times_out_into_needs_permission(self) -> None:
        started = time.monotonic()
        state = removable_volumes.probe(timeout=0.3, command=_blocking_child())
        elapsed = time.monotonic() - started
        self.assertEqual(state["state"], "needs_permission")
        self.assertIn("permission", (state["reason"] or "").lower())
        self.assertEqual(state["path"], "/Volumes")
        self.assertLess(elapsed, 2.0, "the parent must give up on its own, not wait for the child")

    def test_a_child_that_answers_ready_is_cached_and_probed_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "count"
            removable_volumes._set_probe_command_for_testing(_answering_child(marker))
            removable_volumes.start()
            removable_volumes.start()
            deadline = time.monotonic() + 5
            while removable_volumes.status()["state"] == "checking" and time.monotonic() < deadline:
                time.sleep(0.01)
            state = removable_volumes.status()
            runs = marker.read_text()
        self.assertEqual(state["state"], "ready")
        self.assertEqual(removable_volumes.kits(), [Path("/Volumes/KIT/model-library")])
        self.assertEqual(runs, "x", "the child ran more than once in one process")

    def test_the_kit_layout_three_levels_down_is_found(self) -> None:
        """The 2026-10-28 drives keep the library at <drive>/Video Generation Kit/Studio Hub KH/.

        The old two-level walk never found these, so the screen could not offer the SSD as a
        source on the very drives the kit ships on. Both old layouts stay supported.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Volumes"
            deep = root / "KH-STUDIO-ATLAS" / "Video Generation Kit" / "Studio Hub KH" / "model-library"
            deep.mkdir(parents=True)
            flat = root / "OLD-DRIVE" / "model-library"
            flat.mkdir(parents=True)
            state = removable_volumes.probe(timeout=5, command=removable_volumes._child_command(root))
        self.assertEqual(state["state"], "ready", state)
        self.assertEqual(state["kits"], [str(deep), str(flat)])

    def test_a_refused_read_is_needs_permission_not_an_empty_kit_list(self) -> None:
        source = ("import json\n"
                  f"print({PREFIX!r} + json.dumps({{'ok': False, 'reason': 'Operation not permitted'}}))")
        state = removable_volumes.probe(command=[sys.executable, "-c", source])
        self.assertEqual(state["state"], "needs_permission")
        self.assertIn("Operation not permitted", state["reason"])
        self.assertEqual(state["kits"], [])


class NoWalkOnARequestTests(unittest.TestCase):
    def setUp(self) -> None:
        removable_volumes._reset_for_testing()
        removable_volumes._set_probe_command_for_testing(None)

    def tearDown(self) -> None:
        removable_volumes._reset_for_testing()
        removable_volumes._set_probe_command_for_testing(None)

    def test_a_timed_out_probe_does_not_stop_the_routes_and_host_stats_says_so(self) -> None:
        """The gate's headline case: a removable volume is mounted and an access blocks.

        A child that never returns stands in for the unanswered macOS prompt. `/api/health`,
        `/api/hub/resources` and `/api/hub/model-presets` must all answer while it is blocked, and
        once it has timed out the host stats must say `needs_permission` - distinct from busy and
        from unreachable.
        """
        from fastapi.testclient import TestClient
        from components.hub import standalone
        from components.hub.backend import auth, resources

        removable_volumes._set_probe_command_for_testing(_blocking_child())
        old_timeout = removable_volumes.PROBE_TIMEOUT_SECONDS
        removable_volumes.PROBE_TIMEOUT_SECONDS = 1.0
        try:
            removable_volumes.start()
            with tempfile.TemporaryDirectory() as tmp:
                models = Path(tmp) / "models"
                models.mkdir()
                os.environ["STUDIOHUB_MODELS_DIR"] = str(models)
                app = standalone.create_app(start_background=False)
                token = auth.load_token()
                headers = {"X-Hub-Token": token}
                with TestClient(app) as client:
                    # While the child is still blocked, health must already answer.
                    started = time.monotonic()
                    health = client.get("/api/health")
                    self.assertEqual(health.status_code, 200, health.text)
                    self.assertLess(time.monotonic() - started, 1.0,
                                    "health waited on the blocked volume check")
                    self.assertIn("removable_volumes", health.json())

                    # Let the one probe time out, then read the honest state and every route.
                    deadline = time.monotonic() + 5
                    while (removable_volumes.status()["state"] == "checking"
                           and time.monotonic() < deadline):
                        time.sleep(0.02)
                    state = removable_volumes.status()
                    self.assertEqual(state["state"], "needs_permission", state)
                    self.assertEqual(resources.host_stats()["removable_volumes"]["state"],
                                     "needs_permission")

                    started = time.monotonic()
                    host = client.get("/api/hub/resources", headers=headers)
                    self.assertEqual(host.status_code, 200, host.text)
                    self.assertEqual(host.json()["host"]["removable_volumes"]["state"],
                                     "needs_permission")
                    presets = client.get("/api/hub/model-presets", headers=headers)
                    self.assertEqual(presets.status_code, 200, presets.text)
                    self.assertEqual(presets.json()["kits_state"]["state"], "needs_permission")
                    self.assertLess(time.monotonic() - started, 10.0,
                                    "a route blocked on the volume check")
        finally:
            removable_volumes.PROBE_TIMEOUT_SECONDS = old_timeout

    def test_no_route_calls_the_probe(self) -> None:
        from fastapi.testclient import TestClient
        from components.hub import standalone
        from components.hub.backend import auth

        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp) / "models"
            models.mkdir()
            os.environ["STUDIOHUB_MODELS_DIR"] = str(models)
            app = standalone.create_app(start_background=False)
            token = auth.load_token()
            headers = {"X-Hub-Token": token}
            with mock.patch.object(removable_volumes, "probe",
                                   side_effect=AssertionError("a route walked /Volumes")):
                with TestClient(app) as client:
                    for path in ("/api/health", "/api/hub/resources", "/api/hub/model-presets"):
                        answer = client.get(path, headers=headers)
                        self.assertEqual(answer.status_code, 200, f"{path}: {answer.text}")

    def test_snapshot_takes_kits_and_never_derives_them(self) -> None:
        manifests = {path.stem: json.loads(path.read_text(encoding="utf-8"))
                     for path in (Path(__file__).resolve().parents[1] / "model-manifests").glob("*.json")}
        with self.assertRaises(TypeError):
            model_install_plan.snapshot(          # type: ignore[call-arg]
                manifests=manifests, receipts={}, library=Path("/tmp"),
                memory_gb=16, stop_bytes=5_000_000_000, modality_of=lambda engine: "image")
        with mock.patch.object(model_install_plan, "disk",
                               return_value={"path": "/tmp", "free_bytes": 10 ** 12,
                                             "total_bytes": 2 * 10 ** 12, "measured": True}):
            screen = model_install_plan.snapshot(
                kits=[Path("/Volumes/KIT/model-library")], kits_state={"state": "ready"},
                manifests=manifests, receipts={}, library=Path("/tmp"),
                memory_gb=16, stop_bytes=5_000_000_000, modality_of=lambda engine: "image")
        self.assertEqual(screen["kits"], ["/Volumes/KIT/model-library"])


class IdleCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        from components.hub.backend import model_removal
        model_removal._REMOVAL_HAPPENED = False
        self.removal = model_removal

    def tearDown(self) -> None:
        self.removal._REMOVAL_HAPPENED = False

    def test_an_idle_hub_reads_no_library(self) -> None:
        from components.hub import output_storage
        with mock.patch.object(self.removal, "_pending_path",
                               return_value=Path("/nonexistent/model-removal-pending.json")), \
                mock.patch.object(self.removal, "listing",
                                  side_effect=AssertionError("the idle cleanup read the library")), \
                mock.patch.object(self.removal, "handoff",
                                  side_effect=AssertionError("the idle cleanup ran a handoff")):
            self.assertFalse(self.removal.removal_happened())
            output_storage.removed_model_pass()

    def test_a_removal_makes_the_next_pass_read_the_library(self) -> None:
        from components.hub import output_storage
        self.removal._REMOVAL_HAPPENED = True
        with mock.patch.object(self.removal, "handoff") as handoff:
            output_storage.removed_model_pass()
        handoff.assert_called_once()


class FleetRouteOffTheEventLoopTests(unittest.TestCase):
    def test_a_slow_preset_screen_does_not_hold_the_loop(self) -> None:
        """The Gateway route used to run `model_presets_screen()` on the event loop.

        A slow read there stalls every other coroutine - the same shape as the freeze. The route
        must hand the work to a threadpool, so a concurrent task keeps running.
        """
        from components.hub import standalone

        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp) / "models"
            models.mkdir()
            os.environ["STUDIOHUB_MODELS_DIR"] = str(models)
            app = standalone.create_app(start_background=False)
            endpoint = next(route.endpoint for route in app.routes
                            if getattr(route, "path", None) == "/api/hub/fleet/nodes/{node_id}/model-presets")

            def slow_snapshot(**_kwargs):
                time.sleep(0.4)
                return {"presets": [], "groups": [], "models": [], "problems": []}

            ticks: list[float] = []

            async def scenario() -> None:
                started = time.monotonic()

                async def ticker() -> None:
                    await asyncio.sleep(0.1)
                    ticks.append(time.monotonic() - started)

                with mock.patch.object(model_install_plan, "snapshot", side_effect=slow_snapshot):
                    await asyncio.gather(endpoint("local"), ticker())

            asyncio.run(scenario())
        self.assertTrue(ticks, "the concurrent task never ran")
        self.assertLess(ticks[0], 0.35,
                        "the preset route held the event loop instead of using a threadpool")


if __name__ == "__main__":
    unittest.main()
