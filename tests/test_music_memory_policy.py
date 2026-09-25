"""The Music worker's model-memory policy: the fleet contract, its refusals, its lifecycle.

Music was the last Studio that keeps a model resident in a long-lived child and published
no way to let go of it, so Settings showed its row as "Needs an update" while Subtitle,
Voice and Chat offered Performance / Balanced / Memory Saver / Immediate.  The worker
enforces its own bounds, so these tests exercise the real ``GenerationManager`` (only the
package cache is a stand-in that reports every model cached) and the real FastAPI app.
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
        from components.voice.app.backend import memory_policy as voice

        self.assertEqual(memory_policy.MODES, voice.MODES)
        self.assertEqual(memory_policy.DEFAULT_MODE, voice.DEFAULT_MODE)
        self.assertEqual(memory_policy.default_mode(), voice.default_mode())
        self.assertEqual(memory_policy.CHECK_INTERVAL_SECONDS,
                         voice.CHECK_INTERVAL_SECONDS)
        self.assertEqual(
            [option["mode"] for option in memory_policy.status()["options"]],
            ["performance", "balanced", "memory_saver", "immediate"])

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
        self.assertEqual(row["mode"], "immediate")
        self.assertEqual(row["default_mode"], "immediate")
        self.assertEqual([option["mode"] for option in row["options"]],
                         ["performance", "balanced", "memory_saver", "immediate"])
        self.assertEqual(row["loaded_models"], [])

        bad = self.client.put("/api/memory-policy", json={"mode": "overnight"},
                              headers=self.headers)
        self.assertEqual(bad.status_code, 422)
        self.assertIn("overnight", bad.json()["detail"])
        self.assertEqual(
            self.client.get("/api/memory-policy", headers=self.headers).json()["mode"],
            "immediate")

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


if __name__ == "__main__":
    unittest.main()
