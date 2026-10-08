"""One Image job's time limit scales with its steps and this Mac's speed (owner, 2026-10-08)."""
from __future__ import annotations

import unittest

from components.image.app.backend import generation as g

QWEN, LOW = g.QWEN_IMAGE_21, g.QWEN_IMAGE_21_LOW_RAM


class JobDeadlineTests(unittest.TestCase):
    def test_rises_with_steps(self) -> None:
        limits = [g.job_deadline_s(LOW, steps, "Apple M4") for steps in (16, 30, 40, 50)]
        self.assertEqual(limits, sorted(limits))
        self.assertLess(limits[0], limits[-1])

    def test_rises_for_a_slower_mac(self) -> None:
        m2_max = g.job_deadline_s(LOW, 50, "Apple M2 Max")
        m4 = g.job_deadline_s(LOW, 50, "Apple M4")
        m1 = g.job_deadline_s(LOW, 50, "Apple M1")  # unmeasured
        self.assertLess(m2_max, m4)
        self.assertLess(m4, m1)

    def test_is_clamped(self) -> None:
        self.assertEqual(g.job_deadline_s(LOW, 1, "Apple M2 Max"), g.IMAGE_DEADLINE_FLOOR_S)
        self.assertEqual(g.job_deadline_s(g.FLUX2_KLEIN, 100, "Apple M1"), g.IMAGE_DEADLINE_CEILING_S)
        self.assertEqual(g.job_deadline_s(QWEN, 50, "Apple M4", [999.0] * 5),
                         g.IMAGE_DEADLINE_CEILING_S)

    def test_no_history_falls_back_to_the_chip_reading_then_a_safe_default(self) -> None:
        # Measured chip: 120 + 1.5 x 50 x 33.6.
        self.assertEqual(g.job_deadline_s(LOW, 50, "Apple M4"), 2640.0)
        # Unmeasured chip (or no chip): twice the slowest reading, so above every measured Mac.
        slowest = max(g.job_deadline_s(LOW, 40, chip) for chip in LOW.seconds_per_step)
        self.assertGreater(g.job_deadline_s(LOW, 40, None), slowest)
        self.assertGreater(g.job_deadline_s(LOW, 40, "Apple M1"), slowest)
        # Fewer than three own jobs is not history yet.
        self.assertEqual(g.job_deadline_s(LOW, 50, "Apple M4", [5.0, 5.0]), 2640.0)

    def test_this_macs_own_history_wins(self) -> None:
        # Median of the last 20: 40 s/step, though the chip reading is 33.6.
        history = [1.0] * 30 + [40.0] * 11 + [10.0] * 9
        self.assertEqual(g.job_deadline_s(LOW, 50, "Apple M4", history), 120 + 1.5 * 50 * 40)

    def test_default_jobs_keep_at_least_what_0_6_158_gave(self) -> None:
        # 0.6.158: the native leaf stopped at max(900, 34.4 x steps); the low-RAM path had no leaf.
        for model in (g.FLUX2_KLEIN, g.Z_IMAGE_TURBO, QWEN, LOW):
            for chip in (None, "Apple M1", "Apple M2 Max", "Apple M4"):
                with self.subTest(model=model.package_id, chip=chip):
                    self.assertGreaterEqual(
                        g.job_deadline_s(model, model.defaults["steps"], chip), 900.0)
        for steps in (16, 26, 40, 50):
            self.assertGreaterEqual(g.job_deadline_s(QWEN, steps, "Apple M4"),
                                    max(900.0, 34.4 * steps))

    def test_every_model_has_a_reading(self) -> None:
        for model in g.MODELS.values():
            self.assertTrue(model.seconds_per_step, model.package_id)

    def test_the_worker_states_the_limit_from_its_own_finished_jobs(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = g.GenerationManager(root / "data", root / "models", {}, "a" * 64,
                                          total_memory_bytes=16_000_000_000)
            manager.preflight = lambda _repo: {"state": "files_verified", "folder": temporary}
            manager._enqueue = lambda *args: None
            for index in range(3):
                manager.jobs[f"old{index}"] = {
                    "id": f"old{index}", "state": "done", "params": {"repo": LOW.repo},
                    "steps": 16, "duration_seconds": 16 * 60.0, "finished_at": index}
            with patch.object(g, "host_chip", return_value="Apple M4"):
                job = manager.submit({"repo": LOW.repo, "prompt": "a temple", "steps": 20})
            self.assertEqual(job["deadline_s"], 120 + 1.5 * 20 * 60.0)


if __name__ == "__main__":
    unittest.main()
