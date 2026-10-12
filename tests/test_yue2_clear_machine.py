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
        self.assertEqual(self.run_gate("vanch007/mlx-Yue2-3B", {"busy": [], "released": 2, "failed": []}), (("run", None), 1))

    def test_yue2_waits_its_turn_behind_busy_work_and_nothing_is_preempted(self):
        (decision, note), calls = self.run_gate("vanch007/mlx-Yue2-3B", {"busy": ["image"], "released": 0, "failed": []})
        self.assertEqual((decision, calls), ("wait", 1))
        self.assertIn("YuE2", note)

    def test_a_mac_that_cannot_be_cleared_is_skipped_not_started(self):
        (decision, note), _ = self.run_gate("vanch007/mlx-Yue2-3B", {"busy": [], "released": 1, "failed": ["voice"]})
        self.assertEqual(decision, "skip")
        self.assertIn("voice", note)

    def test_other_models_keep_the_free_memory_gate_only(self):
        self.assertEqual(self.run_gate("stabilityai/stable-audio-3-optimized", {"busy": [], "released": 0, "failed": []}),
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


class SongDeadlineTests(unittest.TestCase):
    """0.6.170: a song's own deadline (the lane's flat 900 s cut 3-4 min songs on the fleet's M4s)."""

    def test_the_deadline_is_twice_the_slowest_macs_time_for_the_song(self):
        from components.music.app.backend import yue2
        # 2 x (slowest 7.1 s per song-second x length + 120 s load), the same number on every Mac.
        self.assertEqual(yue2.job_deadline_s(210), 3222.0)
        self.assertGreater(yue2.job_deadline_s(210), 1415.5, "covers the slowest measured 3.5 min song")
        self.assertEqual(yue2.job_deadline_s(None), 240.0)

    def test_a_silent_bridge_is_stopped_at_its_deadline_and_on_cancel(self):
        import tempfile, threading, time
        from components.music.app.backend import yue2
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            fake = tmp / "python3"
            fake.write_text("#!/bin/sh\nexec sleep 60\n")
            fake.chmod(0o755)
            (tmp / "vae").mkdir()
            args = dict(model_dir=tmp, vae_dir=tmp / "vae", output=tmp / "out" / "song.flac", prompt="folk",
                        lyrics="[Verse]\nla", mode="off", precision="8bit", duration_seconds=60, max_tokens=900,
                        min_tokens=200, steps=32, guidance_scale=1.5, seed=1, interpreter=fake)
            started = time.time()
            with self.assertRaisesRegex(yue2.Yue2Error, "deadline"):
                yue2.generate(**args, timeout_seconds=1)
            self.assertLess(time.time() - started, 20, "stopped by the watcher, not by the bridge ending")
            cancel = threading.Event()
            threading.Timer(1.0, cancel.set).start()
            started = time.time()
            with self.assertRaises(yue2.Yue2Cancelled):
                yue2.generate(**args, cancel_event=cancel)
            self.assertLess(time.time() - started, 20, "a cancel is seen while the bridge prints nothing")
