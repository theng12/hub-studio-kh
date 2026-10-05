"""Software-only startup and recovery fixtures for the packaged Hub boundary.

These tests exercise ownership decisions without registering a login item or
touching a real model library.  Physical login, logout and reboot remain ST-11.
"""
from __future__ import annotations

import os
from pathlib import Path
import json
import socket
import subprocess
import tempfile
import textwrap
import time
import unittest

try:
    from test_hub_process import HubProcessTests
except ImportError:  # pragma: no cover - supports direct invocation from the repo root
    from tests.test_hub_process import HubProcessTests

WORKTREE = Path(__file__).resolve().parents[1]


class StartupRecoveryTests(unittest.TestCase):
    def _fixture(self, root: Path) -> dict[str, Path]:
        return HubProcessTests()._fixture(root)

    @staticmethod
    def _command(fixture: dict[str, Path], *, port: int = 0) -> list[str]:
        return [
            str(fixture["runtime_python"]), "-I", "-B",
            str(fixture["resources"] / "hub_worker.py"),
            "--data-dir", str(fixture["data"]),
            "--models-dir", str(fixture["models"]),
            "--ready-file", str(fixture["ready"]),
            "--parent-pid", str(os.getpid()), "--port", str(port), "--offline",
        ]

    @staticmethod
    def _wait(process: subprocess.Popen[str], timeout: float = 8) -> None:
        deadline = time.monotonic() + timeout
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)

    def test_missing_selected_volume_fails_before_state_replacement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="studiohub-startup-") as temp:
            fixture = self._fixture(Path(temp))
            fixture["models"].rmdir()
            process = subprocess.run(self._command(fixture), capture_output=True, text=True)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("Selected model library is unavailable", process.stderr)
            self.assertFalse(fixture["models"].exists())
            self.assertFalse(fixture["ready"].exists())

    def test_occupied_hub_port_fails_without_adopting_the_port(self) -> None:
        with tempfile.TemporaryDirectory(prefix="studiohub-startup-") as temp:
            fixture = self._fixture(Path(temp))
            occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            try:
                port = occupied.getsockname()[1]
                process = subprocess.Popen(self._command(fixture, port=port),
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            text=True)
                self._wait(process)
                self.assertIsNotNone(process.poll(), "occupied port left Hub running")
                self.assertFalse(fixture["ready"].exists())
                self.assertFalse(fixture["workers"].exists())
            finally:
                occupied.close()
                if process.poll() is None:
                    HubProcessTests._stop_pid(process.pid)
                process.communicate(timeout=4)

    def test_stale_readiness_is_replaced_by_current_owner(self) -> None:
        with tempfile.TemporaryDirectory(prefix="studiohub-startup-") as temp:
            fixture = self._fixture(Path(temp))
            fixture["ready"].write_text(
                '{"url":"http://127.0.0.1:1","pid":1,"version":"stale",'
                '"token":"stale-stale-stale-stale-stale-stale"}'
            )
            process = None
            process = subprocess.Popen(self._command(fixture),
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True)
            try:
                self._wait_for(lambda: (fixture["ready"].exists()
                                        and '"pid":1' not in fixture["ready"].read_text())
                               or process.poll() is not None,
                               8, "Hub did not publish fresh readiness")
                self.assertIsNone(process.poll(), self._process_error(process))
                self.assertNotEqual(fixture["ready"].read_text(),
                                    '{"url":"http://127.0.0.1:1","pid":1,"version":"stale",'
                                    '"token":"stale-stale-stale-stale-stale-stale"}')
            finally:
                HubProcessTests._stop_pid(process.pid)
                process.communicate(timeout=4)

    def test_relaunch_after_owned_crash_reuses_one_state_and_worker_descriptor(self) -> None:
        with tempfile.TemporaryDirectory(prefix="studiohub-startup-") as temp:
            fixture = self._fixture(Path(temp))
            first = subprocess.Popen(self._command(fixture), stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True)
            try:
                self._wait_for(lambda: fixture["ready"].exists() or first.poll() is not None,
                               8, "first Hub did not publish readiness")
                self.assertIsNone(first.poll(), self._process_error(first))
                first_pid = json.loads(fixture["ready"].read_text())["pid"]
                HubProcessTests._stop_pid(first.pid)
                first.communicate(timeout=4)
                self.assertFalse(fixture["ready"].exists())
                self.assertFalse(fixture["workers"].exists())

                second = subprocess.Popen(self._command(fixture), stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, text=True)
                try:
                    self._wait_for(lambda: fixture["ready"].exists() or second.poll() is not None,
                                   8, "second Hub did not publish readiness")
                    self.assertIsNone(second.poll(), self._process_error(second))
                    second_ready = json.loads(fixture["ready"].read_text())
                    self.assertNotEqual(second_ready["pid"], first_pid)
                    workers = json.loads(fixture["workers"].read_text())
                    self.assertEqual(len(workers), 1)
                    self.assertEqual(workers[0]["machine"], "local")
                finally:
                    HubProcessTests._stop_pid(second.pid)
                    second.communicate(timeout=4)
            finally:
                if first.poll() is None:
                    HubProcessTests._stop_pid(first.pid)
                first.communicate(timeout=4)

    def test_operator_pause_persists_and_restart_preserves_active_voice_identity(self) -> None:
        """The existing Hub pause flag stops admission but never drops active work."""
        with tempfile.TemporaryDirectory(prefix="studiohub-pause-") as temp:
            fixture = self._fixture(Path(temp))
            proof = r'''
import json
import time
from types import SimpleNamespace

from components.hub.backend import broker, ledger, registry

workers = [{"id": "voice", "modality": "voice", "machine": "node-a",
            "host": "127.0.0.1", "port": 51001, "local": True}]
monitor = SimpleNamespace(registry=workers, status={"voice": {"status": "up"}})
broker.configure_monitor(monitor)
broker.batches.clear()
broker._busy.clear()
registry._flags_cache = None
registry.set_machine_enabled("node-a", False)
assert json.loads(registry.FLAGS_FILE.read_text())["node-a"]["enabled"] is False

batch = {
    "id": "pause-proof", "created_at": time.time(), "modality": "voice",
    "items": [{"index": 0, "state": "running", "studio": "voice",
                "machine": "node-a", "studio_job_id": "voice-job-42",
                "retry_at": None, "recovery": None}],
}
ledger.save_batch(batch)
broker.batches.clear()
registry._flags_cache = None  # simulate a fresh Hub process reading disk
assert registry.machine_enabled("node-a") is False
assert broker.restore_batches() == 1
item = broker.batches["pause-proof"]["items"][0]
# The active work is re-run, not dropped (owner, 2026-10-05), and its original is kept for adoption.
assert item["state"] == "queued"
assert [(row[2], row[3]) for row in broker._restored_attempts] == [("voice", "voice-job-42")]
assert broker._eligible_studios("voice", "pool") == []
print("operator pause/restart proof: flag persisted, active work kept and re-queued, admission paused")
'''
            env = os.environ.copy()
            env["STUDIOHUB_DATA_DIR"] = str(fixture["data"])
            env["STUDIOHUB_PACKAGED"] = "1"
            env["PYTHONPATH"] = os.pathsep.join(
                [str(WORKTREE), env.get("PYTHONPATH", "")]
            ).rstrip(os.pathsep)
            completed = subprocess.run(
                [str(fixture["runtime_python"]), "-c", textwrap.dedent(proof)],
                cwd=WORKTREE,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(
            completed.returncode,
            0,
            "operator pause/restart fixture failed:\nstdout:\n%s\nstderr:\n%s"
            % (completed.stdout, completed.stderr),
        )
        self.assertIn("active work kept and re-queued", completed.stdout)

    @staticmethod
    def _wait_for(predicate, timeout: float, message: str):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        raise AssertionError(message)

    @staticmethod
    def _process_error(process: subprocess.Popen[str]) -> str:
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            return "Hub did not exit cleanly"
        return f"stdout={stdout}\nstderr={stderr}"


if __name__ == "__main__":
    unittest.main()
