"""0.6.169: a YuE2 job gets the whole Mac (idle Studios release first) and starts at normal pressure.

Measured 2026-10-12: with the LICENSE fixed, YuE2 on 16 GB fleet Macs (1103, 1201 — M2 16 GB) failed
mid-song with "System memory pressure is not normal (level=2)": the port's guard, with idle Studios
still holding their models. On This Mac, releasing idle Studios first gave zero failures.
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.hub.backend import broker  # noqa: E402

spec = importlib.util.spec_from_file_location("yue2_runner", ROOT / "components/music/app/backend/yue2_runner.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

STUDIO = {"id": "music", "machine": "local"}


class ClearMachineTests(unittest.TestCase):
    def run_gate(self, model: str, handoff: dict):
        release = AsyncMock(return_value=handoff)
        with mock.patch.object(broker, "release_idle_siblings", new=release), \
                mock.patch.object(broker, "_admission_requirements", return_value=None):
            decision = asyncio.run(broker.prepare_machine_memory(object(), STUDIO, model, {}))
        return decision, release.await_count

    def test_yue2_releases_idle_studios_even_when_memory_looks_ample(self):
        self.assertEqual(self.run_gate("vanch007/mlx-Yue2-3B", {"busy": [], "released": 2}), (("run", None), 1))

    def test_yue2_waits_its_turn_behind_busy_work_and_nothing_is_preempted(self):
        (decision, note), calls = self.run_gate("vanch007/mlx-Yue2-3B", {"busy": ["image"], "released": 0})
        self.assertEqual((decision, calls), ("wait", 1))
        self.assertIn("YuE2", note)

    def test_other_models_keep_the_free_memory_gate_only(self):
        self.assertEqual(self.run_gate("stabilityai/stable-audio-3-optimized", {"busy": [], "released": 0}),
                         (("run", None), 0))


class PressureWaitTests(unittest.TestCase):
    def test_waits_for_normal_then_runs_and_never_waits_past_its_limit(self):
        levels = iter([2, 2, 1])
        with mock.patch.object(runner, "pressure_level", side_effect=lambda: next(levels)), \
                mock.patch.object(runner.time, "sleep"):
            self.assertGreaterEqual(runner.wait_for_normal_pressure(60, poll_s=0.0), 0.0)
        with mock.patch.object(runner, "pressure_level", return_value=2):
            self.assertLess(runner.wait_for_normal_pressure(0.05, poll_s=0.01), 1.0)
        with mock.patch.object(runner, "pressure_level", return_value=None):
            self.assertEqual(runner.wait_for_normal_pressure(60), 0.0, "unknown pressure never blocks a run")


if __name__ == "__main__":
    unittest.main()
