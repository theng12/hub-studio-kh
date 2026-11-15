"""The Hub readiness record names the process the app launched (0.6.235 hotfix).

The app owns the helper process; the helper spawns the Python Hub as its child. The app
accepts readiness only when the record's pid is the helper it launched, so the helper
passes `--ready-pid` and the worker writes that value instead of its own pid. Direct
launches keep the old behaviour (own pid). Shutdown also tolerates a child group that is
no longer ours to signal instead of raising out of cleanup.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "packaging"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import hub_worker  # noqa: E402
import voice_worker  # noqa: E402


class ReadyPidTests(unittest.TestCase):
    def test_ready_pid_override_names_the_owned_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hub-ready.json"
            voice_worker.write_ready_file(
                path, "http://127.0.0.1:47873", "0.6.235",
                token="t" * 32, pid=4242)
            value = json.loads(path.read_text())
            mode = path.stat().st_mode & 0o777
        self.assertEqual(value["pid"], 4242)
        self.assertEqual(value["url"], "http://127.0.0.1:47873")
        self.assertEqual(mode, 0o600)

    def test_ready_pid_defaults_to_this_process(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hub-ready.json"
            voice_worker.write_ready_file(
                path, "http://127.0.0.1:47873", "0.6.235", token="t" * 32)
            value = json.loads(path.read_text())
        self.assertEqual(value["pid"], os.getpid())


class _FakeProcess:
    pid = 4242

    def __init__(self):
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True
        return 0


class StopChildPermissionTests(unittest.TestCase):
    def test_a_group_no_longer_ours_falls_back_to_the_process_handle(self):
        process = _FakeProcess()
        with mock.patch.object(hub_worker.os, "killpg",
                               side_effect=PermissionError), \
                mock.patch.object(hub_worker, "_group_exists", return_value=False):
            hub_worker.stop_child(process, process.pid)
        self.assertTrue(process.terminated)
        self.assertTrue(process.waited)
        self.assertFalse(process.killed)


if __name__ == "__main__":
    unittest.main()
