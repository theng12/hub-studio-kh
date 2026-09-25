"""The Music worker's model-memory policy: the fleet contract, its refusals, its lifecycle.

Music was the last Studio that keeps a model resident in a long-lived child and published
no way to let go of it, so Settings showed its row as "Needs an update" while Subtitle,
Voice and Chat offered Performance / Balanced / Memory Saver / Immediate.  The worker
enforces its own bounds, so these tests exercise the real ``GenerationManager`` (only the
package cache is a stand-in that reports every model cached) and the real FastAPI app.

The same fixture carries the worker's *other* per-machine setting — the per-model generation
defaults (``GET/PUT/DELETE /api/model-defaults``) — because both are routes of this app on this
manager's state directory, and the one thing worth pinning about the pair is that neither setting
changes the other.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components import model_defaults
from components.music.app.backend import catalog, generation, memory_policy


class _VerifiedCache:
    """A package view that reports every model cached, so no weights are needed."""

    def __init__(self, *unused, library=None, **also_unused):
        self.root = Path(library) / "installed" / "model"
        self.root.mkdir(parents=True, exist_ok=True)

    def status(self, **unused):
        return {"state": "cached", "checkpoints_root": str(self.root)}


class _ResidentEngine:
    """A stand-in for ``EngineExecutor`` that is holding a model right now.

    ``released=False`` is the child whose exit was not verified: it survives the discard
    and must never be reported as freed.
    """

    def __init__(self, *, released: bool = True, pid: int = 4321):
        self._pid = pid
        self._released = released
        self.discards = 0

    @property
    def child_pid(self):
        return self._pid

    def _discard(self):
        self.discards += 1
        if self._released:
            self._pid = None
        return self._released

    def close(self):
        self._discard()


class MusicMemoryPolicyTests(unittest.TestCase):
    """One manager over a temporary state directory, bound to the real policy module."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "models").mkdir()
        (self.root / "engine").mkdir()
        self.reset_policy()
        self.manager = self.build_manager()
        memory_policy.start_background(self.manager)
        # Leave the module unbound after each test: the idle thread is process-wide and
        # would otherwise read the next test's manager through the previous one.
        self.addCleanup(self.reset_policy)
        # Performance keeps the background timer inert; the tests that exercise it save
        # their own mode and inject their own clock.
        memory_policy.save("performance")

    def build_manager(self, data_dir: Path | None = None):
        with mock.patch.object(generation.cache_module, "MusicCache", _VerifiedCache):
            manager = generation.GenerationManager(
                data_dir or self.root / "data", engine_root=str(self.root / "engine"),
                library_dir=str(self.root / "models"), runtime_sha256="f" * 64)
        self.addCleanup(manager.close)
        manager._engine_contract_reason = lambda: None
        return manager

    @staticmethod
    def reset_policy() -> None:
        """Unbind and zero the module, so one test cannot read the previous one's state."""
        memory_policy._MANAGER = None
        memory_policy._STATE_DIR = None
        memory_policy._LAST_ACTIVITY = None
        memory_policy._LAST_RELEASE_AT = None
        memory_policy._LAST_RELEASE_REASON = None
        memory_policy._LAST_RELEASE_DETAILS = None
        memory_policy._LAST_ERROR = None
        memory_policy._RELEASE_COUNT = 0
        memory_policy._RELEASING = False

    def resident(self, **kwargs) -> _ResidentEngine:
        engine = _ResidentEngine(**kwargs)
        self.manager._executors[catalog.MAIN_REPO] = engine
        return engine

    def running_job(self) -> None:
        self.manager.jobs["job-running"] = {
            "id": "job-running", "state": "running", "created_at": time.time(),
        }

    def release_count(self) -> int:
        return memory_policy.status()["release_count"]

    # -- the table and the default ------------------------------------------
    def test_the_mode_table_and_default_are_the_voice_workers_verbatim(self) -> None:
        """One per-device choice must mean the same thing on every Studio row."""
        from components import memory_modes
        from components.voice.app.backend import memory_policy as voice

        self.assertIs(memory_policy.MODES, memory_modes.MODES)
        self.assertIs(memory_policy.MODES, voice.MODES,
                      "the table is shared, not copied")
        self.assertEqual(memory_policy.DEFAULT_MODE, voice.DEFAULT_MODE)
        self.assertEqual(memory_policy.DEFAULT_MODE, "auto")
        self.assertEqual(memory_policy.default_mode(), "auto")
        self.assertEqual(memory_policy.CHECK_INTERVAL_SECONDS,
                         voice.CHECK_INTERVAL_SECONDS)
        self.assertEqual(
            [option["mode"] for option in memory_policy.status()["options"]],
            ["auto", "performance", "balanced", "memory_saver", "immediate"])

    def test_an_unknown_mode_is_refused_by_name_and_nothing_is_written(self) -> None:
        memory_policy.save("memory_saver")
        with self.assertRaises(ValueError) as caught:
            memory_policy.save("overnight")
        self.assertIn("overnight", str(caught.exception))
        self.assertIn("balanced", str(caught.exception))
        self.assertEqual(
            json.loads(memory_policy.settings_file().read_text(encoding="utf-8")),
            {"mode": "memory_saver"},
            "a refused mode must leave the saved choice untouched")

    # -- persistence ---------------------------------------------------------
    def test_the_choice_is_persisted_beside_the_job_ledger_and_survives_a_reload(self) -> None:
        memory_policy.save("balanced")
        path = memory_policy.settings_file()
        self.assertEqual(path, self.root / "data" / "memory_policy.json")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                         {"mode": "balanced"})

        # A restart is a fresh manager over the same state directory.
        reloaded = self.build_manager(data_dir=self.root / "data")
        memory_policy.start_background(reloaded)
        self.assertEqual(memory_policy.status()["mode"], "balanced")
        self.assertEqual(memory_policy.status()["idle_seconds"], 600)

    def test_a_corrupt_or_unknown_saved_mode_falls_back_to_the_default(self) -> None:
        path = memory_policy.settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        for saved in ('{"mode": "overnight"}', "not json at all", '{"mode": null}'):
            path.write_text(saved, encoding="utf-8")
            self.assertEqual(memory_policy.status()["mode"], memory_policy.DEFAULT_MODE)

    # -- release -------------------------------------------------------------
    def test_release_discards_the_resident_engine_child_and_says_so(self) -> None:
        engine = self.resident()
        status = memory_policy.status()
        self.assertEqual(status["loaded_models"], [[catalog.MAIN_REPO, "ace-step"]])
        self.assertEqual(status["mode"], "performance",
                         "the idle timer is not what released this child")
        before = self.release_count()

        released = memory_policy.release_now()

        self.assertTrue(released["released"])
        self.assertEqual(released["reason"], "manual")
        self.assertEqual(released["models"], [[catalog.MAIN_REPO, "ace-step"]])
        self.assertEqual(released["actions"][0]["engine_child_pid"], 4321)
        self.assertEqual(engine.discards, 1)
        self.assertEqual(released["release_count"], before + 1)
        self.assertEqual(released["last_release_reason"], "manual")
        self.assertEqual(memory_policy.status()["loaded_models"], [])

    def test_a_child_whose_exit_was_not_verified_is_never_reported_as_released(self) -> None:
        engine = self.resident(released=False)
        result = memory_policy.release_now()

        self.assertFalse(result["released"])
        self.assertEqual(engine.discards, 1)
        self.assertEqual(memory_policy.status()["loaded_models"],
                         [[catalog.MAIN_REPO, "ace-step"]],
                         "a model that is still held must still be published as held")
        self.assertIn("not verified", memory_policy.status()["last_error"])

    def test_release_refuses_while_a_job_is_running(self) -> None:
        engine = self.resident()
        self.running_job()
        before = self.release_count()

        with self.assertRaises(RuntimeError) as caught:
            memory_policy.release_now()

        self.assertEqual(str(caught.exception),
                         "music generation is active; memory was not released")
        self.assertEqual(engine.discards, 0, "a running job must not lose its engine")
        self.assertEqual(self.release_count(), before)
        self.assertEqual(memory_policy.status()["active_work"], True)

    # -- the idle timer ------------------------------------------------------
    def test_an_idle_child_is_released_only_after_the_chosen_window(self) -> None:
        engine = self.resident()
        idle_since = time.time() - 10_000
        memory_policy._LAST_ACTIVITY = idle_since

        memory_policy.save("balanced")
        self.assertIsNone(memory_policy.run_due_release(now=idle_since + 599),
                          "599 s of idle is not 600")
        self.assertEqual(engine.discards, 0)
        released = memory_policy.run_due_release(now=idle_since + 600)
        self.assertIsNotNone(released)
        self.assertTrue(released["released"])
        self.assertEqual(released["last_release_reason"], "idle:balanced")
        self.assertEqual(engine.discards, 1)

    def test_performance_never_releases_and_immediate_releases_at_once(self) -> None:
        self.resident()
        idle_since = time.time() - 10_000
        memory_policy._LAST_ACTIVITY = idle_since

        memory_policy.save("performance")
        self.assertIsNone(memory_policy.run_due_release(now=idle_since + 10_000_000))
        self.assertEqual(memory_policy.status()["next_release_at"], None)

        memory_policy.save("immediate")
        released = memory_policy.run_due_release(now=idle_since)
        self.assertIsNotNone(released)
        self.assertEqual(released["last_release_reason"], "idle:immediate")

    def test_auto_releases_on_the_window_this_machine_resolves_to(self) -> None:
        """`auto` is the shipped default, so these are the two real machine behaviours."""
        engine = self.resident()
        idle_since = time.time() - 10_000
        memory_policy._LAST_ACTIVITY = idle_since
        memory_policy.save("auto")

        with mock.patch.object(memory_policy, "machine_bytes", lambda: 32 * 1024**3):
            status = memory_policy.status()
            self.assertEqual(status["mode"], "auto")
            self.assertEqual(status["resolved_mode"], "balanced")
            self.assertEqual(status["idle_seconds"], 600)
            self.assertIsNone(memory_policy.run_due_release(now=idle_since + 599),
                              "599 s of idle is not the balanced window")
            released = memory_policy.run_due_release(now=idle_since + 600)
            self.assertIsNotNone(released)
        self.assertEqual(released["last_release_reason"], "idle:auto")
        self.assertEqual(engine.discards, 1)

    def test_auto_releases_at_once_below_the_balanced_tier(self) -> None:
        """An 8 GB Mac keeps the old immediate behaviour: no model held between jobs."""
        engine = self.resident()
        idle_since = time.time() - 10_000
        memory_policy._LAST_ACTIVITY = idle_since
        memory_policy.save("auto")

        with mock.patch.object(memory_policy, "machine_bytes", lambda: 8 * 1024**3):
            status = memory_policy.status()
            self.assertEqual(status["resolved_mode"], "immediate")
            self.assertEqual(status["idle_seconds"], 0)
            released = memory_policy.run_due_release(now=idle_since)
        self.assertIsNotNone(released)
        self.assertEqual(released["last_release_reason"], "idle:auto")
        self.assertEqual(engine.discards, 1)

    def test_an_unmeasurable_machine_resolves_to_immediate(self) -> None:
        """A Mac that cannot prove it has room must not hold a model."""
        self.resident()
        memory_policy.save("auto")
        with mock.patch.object(memory_policy, "machine_bytes", lambda: 0):
            self.assertEqual(memory_policy.machine_mode(), "immediate")
            self.assertEqual(memory_policy.status()["idle_seconds"], 0)

    def test_the_idle_timer_never_preempts_a_running_job(self) -> None:
        engine = self.resident()
        idle_since = time.time() - 10_000
        memory_policy._LAST_ACTIVITY = idle_since
        memory_policy.save("immediate")
        self.running_job()

        self.assertIsNone(memory_policy.run_due_release(now=idle_since + 10_000))
        self.assertEqual(engine.discards, 0)

    def test_one_release_per_idle_window(self) -> None:
        """A child that survived its discard must not make the timer fire every tick."""
        self.resident(released=False)
        idle_since = time.time() - 10_000
        memory_policy._LAST_ACTIVITY = idle_since
        memory_policy.save("immediate")

        self.assertIsNotNone(memory_policy.run_due_release(now=idle_since + 1))
        self.assertIsNone(memory_policy.run_due_release(now=idle_since + 2))


@unittest.skipIf(importlib.util.find_spec("fastapi") is None,
                 "FastAPI is not installed in the system test interpreter")
class MusicMemoryRouteTests(unittest.TestCase):
    """The three routes the Hub's fleet controller calls, through the real app."""

    def setUp(self) -> None:
        from fastapi.testclient import TestClient
        from components.music.app.backend.main import create_app

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "models").mkdir()
        (self.root / "engine").mkdir()
        with mock.patch.object(generation.cache_module, "MusicCache", _VerifiedCache):
            self.manager = generation.GenerationManager(
                self.root / "data", engine_root=str(self.root / "engine"),
                library_dir=str(self.root / "models"), runtime_sha256="f" * 64)
        self.addCleanup(self.manager.close)
        self.manager._engine_contract_reason = lambda: None
        self.addCleanup(self.unbind)
        self.client = TestClient(create_app(self.manager, "secret"))
        self.headers = {"X-Studio-Token": "secret"}

    @staticmethod
    def unbind() -> None:
        memory_policy._MANAGER = None
        memory_policy._STATE_DIR = None

    def test_get_put_and_release_answer_the_fleet_contract(self) -> None:
        response = self.client.get("/api/memory-policy", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        row = response.json()
        # A fresh Music install selects `auto`, and the row publishes what that resolves to
        # on this Mac — for `auto`, `idle_seconds` is never None.
        self.assertEqual(row["mode"], "auto")
        self.assertEqual(row["default_mode"], "auto")
        self.assertEqual(row["resolved_mode"], row["machine_mode"])
        self.assertIn(row["resolved_mode"], {"balanced", "immediate"})
        self.assertIsNotNone(row["idle_seconds"])
        self.assertEqual([option["mode"] for option in row["options"]],
                         ["auto", "performance", "balanced", "memory_saver", "immediate"])
        self.assertEqual(row["loaded_models"], [])

        bad = self.client.put("/api/memory-policy", json={"mode": "overnight"},
                              headers=self.headers)
        self.assertEqual(bad.status_code, 422)
        self.assertIn("overnight", bad.json()["detail"])
        self.assertEqual(
            self.client.get("/api/memory-policy", headers=self.headers).json()["mode"],
            "auto")

        changed = self.client.put("/api/memory-policy", json={"mode": "memory_saver"},
                                  headers=self.headers)
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["mode"], "memory_saver")
        self.assertEqual(changed.json()["idle_seconds"], 120)
        self.assertEqual(
            self.client.get("/api/memory-policy", headers=self.headers).json()["mode"],
            "memory_saver")

        engine = _ResidentEngine()
        self.manager._executors[catalog.MAIN_REPO] = engine
        self.assertEqual(
            self.client.get("/api/memory-policy", headers=self.headers).json()[
                "loaded_models"],
            [[catalog.MAIN_REPO, "ace-step"]])
        released = self.client.post("/api/memory/release", headers=self.headers)
        self.assertEqual(released.status_code, 200)
        self.assertTrue(released.json()["released"])
        self.assertEqual(engine.discards, 1)
        self.assertEqual(
            self.client.get("/api/memory-policy", headers=self.headers).json()[
                "loaded_models"],
            [])

    def test_release_while_a_job_runs_is_a_named_409(self) -> None:
        engine = _ResidentEngine()
        self.manager._executors[catalog.MAIN_REPO] = engine
        self.manager.jobs["job-running"] = {
            "id": "job-running", "state": "running", "created_at": time.time(),
        }

        refused = self.client.post("/api/memory/release", headers=self.headers)

        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.json()["detail"],
                         "music generation is active; memory was not released")
        self.assertEqual(engine.discards, 0)


@unittest.skipIf(importlib.util.find_spec("fastapi") is None,
                 "FastAPI is not installed in the system test interpreter")
class MusicModelDefaultsRouteTests(unittest.TestCase):
    """Per-model generation defaults as the worker stores, validates, applies and publishes them.

    Every bound here is read from the worker's own published lane contract, so a bound that moves
    in the catalog moves these tests with it. Each group carries its own negative control — the
    same call in the direction that must succeed — so a green suite cannot mean a route that
    simply refuses everything.
    """

    def setUp(self) -> None:
        from fastapi.testclient import TestClient
        from components.music.app.backend.main import create_app

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "models").mkdir()
        (self.root / "engine").mkdir()
        with mock.patch.object(generation.cache_module, "MusicCache", _VerifiedCache):
            self.manager = generation.GenerationManager(
                self.root / "data", engine_root=str(self.root / "engine"),
                library_dir=str(self.root / "models"), runtime_sha256="f" * 64)
        self.addCleanup(self.manager.close)
        self.manager._engine_contract_reason = lambda: None
        self.addCleanup(self.unbind)
        self.client = TestClient(create_app(self.manager, "secret"))
        self.headers = {"X-Studio-Token": "secret"}

    @staticmethod
    def unbind() -> None:
        memory_policy._MANAGER = None
        memory_policy._STATE_DIR = None

    # -- helpers -------------------------------------------------------------
    def rows(self) -> dict:
        response = self.client.get("/api/model-defaults", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return {row["repo"]: row for row in response.json()["models"]}

    def put(self, repo: str, values: dict):
        return self.client.put("/api/model-defaults", headers=self.headers,
                               json={"repo": repo, "values": values})

    def delete(self, repo: str, *, query: bool = False):
        if query:
            return self.client.request("DELETE", f"/api/model-defaults?repo={repo}",
                                       headers=self.headers)
        return self.client.request("DELETE", "/api/model-defaults", headers=self.headers,
                                   json={"repo": repo})

    @staticmethod
    def published(repo: str) -> dict:
        """One model's published parameters, from the lane contract itself."""
        model = catalog.require_model(repo)
        return {parameter["name"]: parameter
                for parameter in generation.lane_parameters(model)}

    @staticmethod
    def lane_stated(repo: str) -> dict:
        return {name: parameter["default"]
                for name, parameter in MusicModelDefaultsRouteTests.published(repo).items()
                if "default" in parameter}

    def catalog_entry(self, repo: str) -> dict:
        """One raw row of `/api/catalog`, as the app and GatewayKH read it."""
        entries = self.client.get("/api/catalog", headers=self.headers).json()["models"]
        return next(item for item in entries if item["repo"] == repo)

    def catalog_contract(self, repo: str) -> dict:
        """What `/api/catalog` publishes for one model's parameters."""
        return {parameter["name"]: parameter
                for parameter in self.catalog_entry(repo)["lane_contract"]["parameters"]}

    def submitted(self, body: dict) -> dict:
        """The job record `submit` stores, without starting the engine thread.

        The record's `params` are exactly what the runner turns into the engine payload, so this
        is the value the engine would receive — checked here without spawning a child.
        """
        class _NoThread:
            def __init__(self, *_args, **_kwargs):
                pass

            def start(self):
                pass

        with mock.patch.object(generation, "threading", mock.Mock(Thread=_NoThread)):
            return self.manager.submit(body)

    # -- the round trip ------------------------------------------------------
    def test_get_before_anything_is_set_is_exactly_what_the_lane_states(self) -> None:
        payload = self.client.get("/api/model-defaults", headers=self.headers).json()
        self.assertEqual(payload["settings_file"], model_defaults.SETTINGS_NAME)
        self.assertEqual({row["repo"] for row in payload["models"]},
                         {model.repo for model in catalog.CATALOG})
        for row in payload["models"]:
            stated = self.lane_stated(row["repo"])
            with self.subTest(repo=row["repo"]):
                self.assertTrue(stated)
                self.assertEqual(row["stated"], stated)
                self.assertEqual(row["defaults"], stated)
                self.assertEqual(row["chosen"], {})
                self.assertEqual(row["not_used"], {})
                # The offered surface is the shared module's own rule over the lane's published
                # parameters (`required` parameters and one-option enums have nothing to choose).
                self.assertEqual(row["parameters"],
                                 model_defaults.editable(
                                     generation.lane_parameters(
                                         catalog.require_model(row["repo"]))))

    def test_put_stores_it_and_get_reflects_it_for_that_model_only(self) -> None:
        stored = self.put(catalog.MAIN_REPO, {"steps": 5, "duration_seconds": 60.0})
        self.assertEqual(stored.status_code, 200, stored.text)
        rows = {row["repo"]: row for row in stored.json()["models"]}
        self.assertEqual(rows[catalog.MAIN_REPO]["chosen"],
                         {"duration_seconds": 60.0, "steps": 5})
        self.assertEqual(rows[catalog.MAIN_REPO]["stated"]["steps"],
                         self.lane_stated(catalog.MAIN_REPO)["steps"])
        self.assertEqual(rows[catalog.STABLE_AUDIO_REPO]["chosen"], {},
                         "a default belongs to one model, never to the lane")
        # A fresh GET and the file itself agree with the PUT's own answer.
        self.assertEqual(self.rows()[catalog.MAIN_REPO]["defaults"]["steps"], 5)
        self.assertEqual(json.loads(model_defaults.settings_file(self.manager.data)
                                    .read_text(encoding="utf-8")),
                         {catalog.MAIN_REPO: {"duration_seconds": 60.0, "steps": 5}})

    def test_the_catalog_publishes_the_same_numbers_the_route_holds(self) -> None:
        self.assertEqual(self.put(catalog.MAIN_REPO, {"steps": 5}).status_code, 200)
        published = self.catalog_contract(catalog.MAIN_REPO)
        self.assertEqual(published["steps"]["default"],
                         self.rows()[catalog.MAIN_REPO]["defaults"]["steps"])
        # The bounds stay the model's own, whatever the operator chose.
        self.assertEqual(published["steps"]["minimum"],
                         self.published(catalog.MAIN_REPO)["steps"]["minimum"])
        self.assertEqual(published["steps"]["maximum"],
                         self.published(catalog.MAIN_REPO)["steps"]["maximum"])
        # And a model nobody chose for publishes what it always did.
        self.assertEqual(self.catalog_contract(catalog.STABLE_AUDIO_REPO)["steps"]["default"],
                         self.lane_stated(catalog.STABLE_AUDIO_REPO)["steps"])

    def test_delete_with_a_body_or_a_query_returns_the_model_to_the_lanes_own(self) -> None:
        for use_query in (False, True):
            with self.subTest(query=use_query):
                self.assertEqual(self.put(catalog.MAIN_REPO, {"steps": 5}).status_code, 200)
                cleared = self.delete(catalog.MAIN_REPO, query=use_query)
                self.assertEqual(cleared.status_code, 200, cleared.text)
                row = {item["repo"]: item for item in cleared.json()["models"]}[catalog.MAIN_REPO]
                self.assertEqual(row["defaults"], self.lane_stated(catalog.MAIN_REPO))
                self.assertEqual(self.rows()[catalog.MAIN_REPO]["chosen"], {})

    def test_every_offered_control_default_is_a_value_the_worker_accepts(self) -> None:
        """The catalog cannot advertise a number ``model_defaults`` would refuse.

        Both come from one ``resolved()`` call, so this is that invariant made checkable: every
        control the editor would render, at its published default, is fed straight back through
        the route and must be accepted.
        """
        self.assertEqual(self.put(catalog.MAIN_REPO, {"steps": 5, "shift": 4.0}).status_code, 200)
        for repo, row in self.rows().items():
            settable = {parameter["name"]: row["defaults"][parameter["name"]]
                        for parameter in row["parameters"]}
            with self.subTest(repo=repo):
                accepted = self.put(repo, settable)
                self.assertEqual(accepted.status_code, 200, accepted.text)

    def test_the_routes_require_the_owner_credential(self) -> None:
        for call in (lambda: self.client.get("/api/model-defaults"),
                     lambda: self.client.put("/api/model-defaults", json={"repo": "x"}),
                     lambda: self.client.request("DELETE", "/api/model-defaults")):
            self.assertEqual(call().status_code, 401)

    # -- the refusals --------------------------------------------------------
    def test_a_bound_crossing_is_refused_by_name_while_the_bound_itself_is_accepted(self) -> None:
        for model in catalog.CATALOG:
            parameter = self.published(model.repo)["steps"]
            minimum, maximum = parameter["minimum"], parameter["maximum"]
            with self.subTest(repo=model.repo):
                # Control: the edge of the published range is what this lane accepts, so a
                # refusal below is the bound's doing, not a route that refuses everything.
                self.assertEqual(self.put(model.repo, {"steps": maximum}).status_code, 200)
                self.assertEqual(self.rows()[model.repo]["defaults"]["steps"], maximum)
                self.assertEqual(self.delete(model.repo).status_code, 200)
                for value, reason, bound in ((maximum + 1, "above the lane", maximum),
                                             (minimum - 1, "below the lane", minimum)):
                    refused = self.put(model.repo, {"steps": value})
                    self.assertEqual(refused.status_code, 422, refused.text)
                    detail = refused.json()["detail"]
                    self.assertIn("steps", detail)
                    self.assertIn(reason, detail["steps"])
                    self.assertIn(str(bound), detail["steps"],
                                  "the refusal names the bound the worker enforces")
                    self.assertEqual(self.rows()[model.repo]["defaults"]["steps"],
                                     self.lane_stated(model.repo)["steps"],
                                     "a refused value must not be stored")

    def test_every_bad_value_is_reported_at_once_and_none_of_them_is_stored(self) -> None:
        published = self.published(catalog.MAIN_REPO)
        refused = self.put(catalog.MAIN_REPO, {
            "steps": published["steps"]["maximum"] + 1,
            "duration_seconds": published["duration_seconds"]["maximum"] + 1,
            "shift": published["shift"]["maximum"] + 1,
        })
        self.assertEqual(refused.status_code, 422)
        self.assertEqual(sorted(refused.json()["detail"]),
                         ["duration_seconds", "shift", "steps"],
                         "an operator who typed three bad values should not have to press Save "
                         "three times to find out")
        self.assertFalse(model_defaults.settings_file(self.manager.data).exists())

    def test_an_unknown_repo_is_a_named_refusal_not_a_silent_no_op(self) -> None:
        for response in (self.put("someone/not-registered", {"steps": 4}),
                         self.delete("someone/not-registered")):
            with self.subTest(status=response.status_code):
                self.assertEqual(response.status_code, 422)
                self.assertIn("not-registered", response.json()["detail"])
                self.assertIn(catalog.MAIN_REPO, response.json()["detail"],
                              "the refusal names the models this worker does serve")
        self.assertFalse(model_defaults.settings_file(self.manager.data).exists())

    def test_a_control_one_runtime_discards_has_nothing_to_default_for_it(self) -> None:
        """Stable Audio 3 forces instrumental output, so it publishes no such control."""
        stable = self.published(catalog.STABLE_AUDIO_REPO)
        for name, value in (("shift", 3.0), ("instrumental", False), ("lyrics", "la"),
                            ("vocal_language", "en")):
            with self.subTest(parameter=name):
                self.assertNotIn(name, stable)
                refused = self.put(catalog.STABLE_AUDIO_REPO, {name: value})
                self.assertEqual(refused.status_code, 422)
                self.assertIn(name, refused.json()["detail"])
                self.assertIn("publishes no such settable parameter",
                              refused.json()["detail"][name])
        # Control: the same names are settable for the engine that does accept them.
        self.assertEqual(self.put(catalog.MAIN_REPO, {"shift": 3.5, "instrumental": True,
                                                      "lyrics": "la", "vocal_language": "en"})
                         .status_code, 200)

    # -- what is applied -----------------------------------------------------
    def test_a_parameter_the_request_supplies_is_untouched_by_a_stored_default(self) -> None:
        self.assertEqual(self.put(catalog.MAIN_REPO, {"steps": 5, "duration_seconds": 60.0})
                         .status_code, 200)
        applied = generation.validate_request(
            {"repo": catalog.MAIN_REPO, "prompt": "rain", "steps": 3, "duration_seconds": 12.0},
            data_dir=self.manager.data)
        self.assertEqual((applied["steps"], applied["duration_seconds"]), (3, 12.0))
        # The control in the other direction: the same request with those two omitted.
        omitted = generation.validate_request(
            {"repo": catalog.MAIN_REPO, "prompt": "rain"}, data_dir=self.manager.data)
        self.assertEqual((omitted["steps"], omitted["duration_seconds"]), (5, 60.0))

    def test_a_parameter_the_request_omits_gets_the_stored_default(self) -> None:
        self.assertEqual(self.put(catalog.STABLE_AUDIO_REPO, {"steps": 4}).status_code, 200)
        applied = generation.validate_request(
            {"repo": catalog.STABLE_AUDIO_REPO, "prompt": "rain"}, data_dir=self.manager.data)
        self.assertEqual(applied["steps"], 4)
        self.assertEqual(applied["duration_seconds"],
                         self.lane_stated(catalog.STABLE_AUDIO_REPO)["duration_seconds"],
                         "everything the operator did not choose stays the model's own")

    def test_what_is_applied_is_what_the_catalog_publishes_as_that_default(self) -> None:
        """Read both and compare: one number in the setting, the catalog and the validator."""
        self.assertEqual(self.put(catalog.MAIN_REPO, {"steps": 5, "duration_seconds": 60.0})
                         .status_code, 200)
        published = self.catalog_contract(catalog.MAIN_REPO)
        applied = generation.validate_request(
            {"repo": catalog.MAIN_REPO, "prompt": "rain"}, data_dir=self.manager.data)
        for name in ("steps", "duration_seconds"):
            with self.subTest(parameter=name):
                self.assertEqual(published[name]["default"], applied[name])
                self.assertEqual(published[name]["default"],
                                 self.rows()[catalog.MAIN_REPO]["defaults"][name])

    def test_generation_defaults_and_the_lane_contract_are_one_answer(self) -> None:
        """The catalog says the same number twice, and the job that omits it gets that number.

        ``generation_defaults`` is what the app pre-fills its form from and sends with every
        request; the lane contract's per-parameter ``default`` is what a scheduler reads. Two
        answers to one question is how a stored default reaches one of them and not the other.
        """
        stored = {"steps": 5, "duration_seconds": 60.0, "guidance_scale": 2.5, "seed": 7}
        self.assertEqual(self.put(catalog.MAIN_REPO, stored).status_code, 200)
        entry = self.catalog_entry(catalog.MAIN_REPO)
        defaults = entry["generation_defaults"]
        published = self.catalog_contract(catalog.MAIN_REPO)
        for name, value in stored.items():
            with self.subTest(parameter=name):
                self.assertEqual(defaults[name], value)
                self.assertEqual(published[name]["default"], value,
                                 "the two published halves must agree with each other")
        # The row's other spelling of the same two numbers follows them rather than disagreeing
        # with the field beside it.
        self.assertEqual(entry["inference_steps"], defaults["steps"])
        self.assertEqual(entry["guidance_scale"], defaults["guidance_scale"])
        # ...and what a job that omits them carries is exactly those numbers.
        job = self.submitted({"repo": catalog.MAIN_REPO, "prompt": "rain"})
        for name, value in stored.items():
            with self.subTest(parameter=name, where="job"):
                self.assertEqual(job["params"][name], value)
        self.assertEqual(job["params"]["steps"], published["steps"]["default"])
        self.assertEqual(job["params"]["duration_seconds"],
                         published["duration_seconds"]["default"])

    def test_a_model_nobody_has_chosen_for_still_uses_its_own_defaults(self) -> None:
        """The control for the tests above: with nothing stored, nothing moves."""
        entry = self.catalog_entry(catalog.MAIN_REPO)
        for name, value in self.lane_stated(catalog.MAIN_REPO).items():
            with self.subTest(parameter=name):
                self.assertEqual(entry["generation_defaults"][name], value)
        job = self.submitted({"repo": catalog.MAIN_REPO, "prompt": "rain"})
        self.assertEqual(job["params"]["steps"],
                         self.lane_stated(catalog.MAIN_REPO)["steps"])
        applied = generation.validate_request(
            {"repo": catalog.MAIN_REPO, "prompt": "rain"}, data_dir=self.manager.data)
        self.assertEqual(applied["steps"], catalog.CATALOG[0].defaults["steps"])
        self.assertEqual(applied["duration_seconds"],
                         catalog.CATALOG[0].defaults["duration_seconds"])

    def test_a_stored_default_does_not_make_an_out_of_bound_request_acceptable(self) -> None:
        published = self.published(catalog.MAIN_REPO)["steps"]
        self.assertEqual(self.put(catalog.MAIN_REPO, {"steps": 5}).status_code, 200)
        with self.assertRaises(ValueError):
            generation.validate_request(
                {"repo": catalog.MAIN_REPO, "prompt": "rain",
                 "steps": published["maximum"] + 1}, data_dir=self.manager.data)

    # -- the two axes --------------------------------------------------------
    def test_neither_setting_changes_the_other(self) -> None:
        self.assertEqual(self.put(catalog.MAIN_REPO, {"steps": 5}).status_code, 200)
        self.assertFalse(memory_policy.settings_file().exists(),
                         "choosing a generation default must not write the memory policy")
        self.assertEqual(memory_policy.status()["mode"], "auto")

        changed = self.client.put("/api/memory-policy", json={"mode": "memory_saver"},
                                  headers=self.headers)
        self.assertEqual(changed.status_code, 200)
        row = self.rows()[catalog.MAIN_REPO]
        self.assertEqual(row["defaults"]["steps"], 5,
                         "changing when the engine gives memory back must not change what the "
                         "request asks it for")
        self.assertEqual(row["chosen"], {"steps": 5})
        self.assertEqual(json.loads(model_defaults.settings_file(self.manager.data)
                                    .read_text(encoding="utf-8")),
                         {catalog.MAIN_REPO: {"steps": 5}})


if __name__ == "__main__":
    unittest.main()
