"""The service log is bounded WHILE the workers run: 16 MB live + one rotated copy.

The app opens `hub-worker.log` once and gives the descriptor to the Hub and every worker, so it is
only ever rotated when the app spawns the Hub; a Mac that stays up for weeks wrote 4-14 GB a month.
`rotate_log_by_size` copy-truncates it from inside the workers.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for extra in (PROJECT_ROOT, PROJECT_ROOT / "packaging"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import voice_worker  # noqa: E402


class LogRotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / "hub-worker.log"

    def test_the_limit_is_16_mb_and_two_files(self):
        self.assertEqual(voice_worker.LOG_ROTATE_BYTES, 16 * 1024 * 1024)

    def test_a_small_log_is_left_alone(self):
        self.log.write_bytes(b"a" * 100)
        self.assertFalse(voice_worker.rotate_log_by_size(self.log, limit=1000))
        self.assertEqual(self.log.read_bytes(), b"a" * 100)
        self.assertFalse(self.log.with_name("hub-worker.log.1").exists())

    def test_a_big_log_is_copied_aside_and_emptied_in_place(self):
        self.log.write_bytes(b"a" * 1500)
        self.assertTrue(voice_worker.rotate_log_by_size(self.log, limit=1000))
        self.assertEqual(self.log.stat().st_size, 0)
        self.assertEqual(self.log.with_name("hub-worker.log.1").read_bytes(), b"a" * 1500)

    def test_only_one_rotated_copy_is_ever_kept(self):
        rotated = self.log.with_name("hub-worker.log.1")
        for fill in (b"a", b"b", b"c"):
            self.log.write_bytes(fill * 1500)
            voice_worker.rotate_log_by_size(self.log, limit=1000)
        self.assertEqual(rotated.read_bytes(), b"c" * 1500)
        self.assertEqual(sorted(p.name for p in self.log.parent.iterdir()), ["hub-worker.log", "hub-worker.log.1"])
        self.assertEqual(oct(rotated.stat().st_mode & 0o777), "0o600")

    def test_a_process_that_keeps_appending_through_its_own_descriptor_carries_on(self):
        """The Hub and workers hold the file open (O_APPEND) for the life of the process."""
        writer = os.open(self.log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        self.addCleanup(os.close, writer)
        os.write(writer, b"x" * 1500)
        self.assertTrue(voice_worker.rotate_log_by_size(self.log, limit=1000))
        os.write(writer, b"after")
        self.assertEqual(self.log.read_bytes(), b"after", "the writer did not carry on at the new end")

    def test_a_symlinked_log_is_never_followed(self):
        target = Path(self.temp.name) / "elsewhere"
        target.write_bytes(b"z" * 5000)
        self.log.symlink_to(target)
        self.assertFalse(voice_worker.rotate_log_by_size(self.log, limit=1000))
        self.assertEqual(target.stat().st_size, 5000)

    def test_the_rotation_thread_checks_on_a_timer_while_the_process_runs(self):
        self.log.write_bytes(b"a" * 1500)
        stop = threading.Event()
        thread = voice_worker.start_log_rotation(stop, path=self.log, limit=1000, every=0.01)
        self.addCleanup(stop.set)
        for _ in range(200):
            if self.log.stat().st_size == 0:
                break
            stop.wait(0.01)
        self.assertEqual(self.log.stat().st_size, 0, "the log kept growing while the process ran")
        self.assertIsNotNone(thread)

    def test_run_server_starts_the_rotation(self):
        import inspect
        self.assertIn("start_log_rotation(", inspect.getsource(voice_worker.run_server))


if __name__ == "__main__":
    unittest.main()
