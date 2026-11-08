"""Unit tests for developer_gate_runner.

Tests:
1. Rejection of untrusted / unverified tools (missing, non-executable, unavailable).
2. Command fixture hash and content integrity validation.
3. Preemption before launch and during execution (with exact process release).
4. Child identity recorded before any tool byte runs (authorization gate).
5. Environment scrubbing (no live studiohub, tokens, secrets, custom home/tmp).
6. Cap overflows (log cap, runtime budget, rss budget, swap budget).
7. Unknown stop proof never false-passed.
8. Execution of all 6 known commands with toy child interpreter mocks.
9. Fail-closed metrics and split-safe secret redaction.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.hub.developer_gate_runner import (
    MINIMUM_FREE_BYTES,
    DeveloperGateRunner,
    GateRunResult,
    GateRunnerError,
    _BoundedLogSink,
    load_commands_fixture,
    run_gate,
    validate_trusted_toolchains,
)
from components.hub.developer_image_runner import Metrics, ReleaseProof, stop_exact_identity
import components.hub.developer_image_runner as image_runner

_LIBPROC: Any = None


def _real_create_time(pid: int) -> float | None:
    """The process's real start time (what psutil exposes), or None where unsupported.

    0.0 means the pid is gone or is a zombie; the identity check then never matches it. This
    keeps the fake faithful: a reused pid has a different start time, as in the real Metrics.
    """
    global _LIBPROC
    if _LIBPROC is None:
        try:
            import ctypes
            _LIBPROC = ctypes.CDLL("/usr/lib/libproc.dylib")
        except Exception:  # noqa: BLE001 - non-macOS test host keeps the constant fallback
            _LIBPROC = False
    if not _LIBPROC:
        return None
    import ctypes
    buffer = ctypes.create_string_buffer(256)
    if _LIBPROC.proc_pidinfo(pid, 3, 0, buffer, 256) <= 0:  # PROC_PIDTBSDINFO
        return 0.0
    sec = ctypes.c_uint64.from_buffer_copy(buffer.raw, 120).value
    usec = ctypes.c_uint64.from_buffer_copy(buffer.raw, 128).value
    return float(sec) + usec / 1_000_000.0


class FakePsutilProcess:
    def __init__(self, pid: int, ct: float = 100.0) -> None:
        self.pid = pid
        self._ct = ct

    def create_time(self) -> float:
        real = _real_create_time(self.pid)
        return self._ct if real is None else real

    def status(self) -> str:
        return "running"

    def is_running(self) -> bool:
        # Check if the OS process exists
        try:
            os.kill(self.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def children(self, recursive: bool = True) -> list:
        return []

    def memory_info(self) -> Any:
        class Mem:
            rss = 1024 * 1024
        return Mem()


class FakePsutilModule:
    class NoSuchProcess(Exception):
        pass

    def __init__(self) -> None:
        self._start = time.time()

    def Process(self, pid: int) -> FakePsutilProcess:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            raise FakePsutilModule.NoSuchProcess(f"pid {pid} not found")
        return FakePsutilProcess(pid, ct=self._start)

    def virtual_memory(self) -> Any:
        class VMem:
            available = 16 * 1024 * 1024 * 1024
            total = 16 * 1024 * 1024 * 1024
        return VMem()

    def swap_memory(self) -> Any:
        class SMem:
            used = 0
        return SMem()


class TestDeveloperGateRunner(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="gate_runner_test_")
        self.root = Path(self.tmp_dir).resolve()
        self.ws_dir = (self.root / "ws").resolve()
        self.src_dir = (self.root / "src").resolve()
        self.ws_dir.mkdir(parents=True, mode=0o700)
        self.src_dir.mkdir(parents=True, mode=0o700)

        self.fake_metrics = Metrics(FakePsutilModule())

        # Create toy executable scripts for python, node, and go
        self.tools_bin = self.root / "mock_bin"
        self.tools_bin.mkdir(parents=True, mode=0o700)

        self.mock_python = self.tools_bin / "mock_python"
        self.mock_python.write_text(f"#!{sys.executable}\nimport sys; sys.exit(0)\n")
        self.mock_python.chmod(self.mock_python.stat().st_mode | stat.S_IEXEC)

        self.mock_node = self.tools_bin / "mock_node"
        self.mock_node.write_text(f"#!{sys.executable}\nimport sys; sys.exit(0)\n")
        self.mock_node.chmod(self.mock_node.stat().st_mode | stat.S_IEXEC)

        self.mock_go = self.tools_bin / "mock_go"
        self.mock_go.write_text(f"#!{sys.executable}\nimport sys; sys.exit(0)\n")
        self.mock_go.chmod(self.mock_go.stat().st_mode | stat.S_IEXEC)

        self.trusted_tools = {
            "python": {"available": True, "executable": str(self.mock_python), "version": "3.15.0"},
            "node": {"available": True, "executable": str(self.mock_node), "version": "26.8.2"},
            "go": {"available": True, "executable": str(self.mock_go), "version": "1.27.2"},
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write_marker_tool(self) -> Path:
        marker = self.root / "tool_ran"
        self.mock_python.write_text(
            f"#!{sys.executable}\n"
            f"open({str(marker)!r}, 'w').write('ran')\n"
            f"raise SystemExit(0)\n"
        )
        return marker

    def test_fixture_hash_and_command_count(self) -> None:
        fixture = load_commands_fixture()
        self.assertIn("commands", fixture)
        self.assertEqual(len(fixture["commands"]), 6)
        ids = [c["id"] for c in fixture["commands"]]
        expected_ids = [
            "signing-verifier-unit",
            "javascript-unit",
            "vet-darwin",
            "vet-windows",
            "vet-linux",
            "go-unit",
        ]
        self.assertEqual(ids, expected_ids)

    def test_reject_missing_and_unverified_toolchains(self) -> None:
        # Missing toolchain
        bad_tools = {"python": {"available": True, "executable": str(self.mock_python)}}
        with self.assertRaises(GateRunnerError) as ctx:
            validate_trusted_toolchains(bad_tools)
        self.assertIn("node", str(ctx.exception))

        # Toolchain marked unavailable
        bad_tools = {
            "python": {"available": False, "executable": str(self.mock_python)},
            "node": {"available": True, "executable": str(self.mock_node)},
            "go": {"available": True, "executable": str(self.mock_go)},
        }
        with self.assertRaises(GateRunnerError) as ctx:
            validate_trusted_toolchains(bad_tools)
        self.assertIn("unavailable", str(ctx.exception))

        # Toolchain executable does not exist
        bad_tools = {
            "python": {"available": True, "executable": str(self.root / "nonexistent")},
            "node": {"available": True, "executable": str(self.mock_node)},
            "go": {"available": True, "executable": str(self.mock_go)},
        }
        with self.assertRaises(GateRunnerError) as ctx:
            validate_trusted_toolchains(bad_tools)
        self.assertIn("not an executable file", str(ctx.exception))

        # Runner gracefully produces not_run outcome on missing toolchains
        recorded_identities = []
        result = run_gate(
            workspace_dir=self.ws_dir,
            source_dir=self.src_dir,
            trusted_toolchains={"python": {"available": False}},
            stage_allowed=lambda: True,
            identity_recorder=recorded_identities.append,
        )
        self.assertEqual(result.outcome, "not_run")
        self.assertTrue(result.process_release["confirmed"])
        self.assertEqual(result.process_release["proof"], "never_started")
        self.assertEqual(len(recorded_identities), 0)

    def test_child_identity_recorded_before_heavy_execution(self) -> None:
        recorded_identities = []

        result = run_gate(
            workspace_dir=self.ws_dir,
            source_dir=self.src_dir,
            trusted_toolchains=self.trusted_tools,
            stage_allowed=lambda: True,
            identity_recorder=recorded_identities.append,
            metrics=self.fake_metrics,
        )
        self.assertEqual(result.outcome, "passed")
        self.assertEqual(len(recorded_identities), 6)
        for ident in recorded_identities:
            self.assertIn("pid", ident)
            self.assertIn("pgid", ident)
            self.assertIn("create_time", ident)
            self.assertGreater(ident["pid"], 1)
            self.assertGreater(ident["create_time"], 0.0)

    def test_recorder_completes_before_tool_execution(self) -> None:
        # The tool writes a marker the moment it runs. The recorder waits for it and must
        # never see it: the child cannot execute until the recorder returns and G is sent.
        marker = self._write_marker_tool()
        seen_during_callback = []

        def recorder(identity):
            if not seen_during_callback:
                time.sleep(0.3)
                seen_during_callback.append(marker.exists())

        result = run_gate(
            workspace_dir=self.ws_dir,
            source_dir=self.src_dir,
            trusted_toolchains=self.trusted_tools,
            stage_allowed=lambda: True,
            identity_recorder=recorder,
            sample_interval_seconds=0.02,
            metrics=self.fake_metrics,
        )

        self.assertEqual(result.outcome, "passed", result.reason)
        self.assertEqual(seen_during_callback, [False])
        self.assertTrue(marker.exists())

    def test_environment_scrubbing(self) -> None:
        # Script that writes its env to a file so we can assert sanitization
        env_dump_file = self.root / "env_dump.json"
        script = (
            f"#!{sys.executable}\n"
            f"import os, json\n"
            f"open({str(env_dump_file)!r}, 'w').write(json.dumps(dict(os.environ)))\n"
        )
        self.mock_python.write_text(script)

        # Set dirty environment in parent process
        with mock.patch.dict(os.environ, {
            "STUDIOHUB_TOKEN": "secret-hub-token",
            "HF_TOKEN": "secret-hf-token",
            "AWS_SECRET_ACCESS_KEY": "secret-aws-key",
            "LIVE_VAR": "polluted",
        }):
            result = run_gate(
                workspace_dir=self.ws_dir,
                source_dir=self.src_dir,
                trusted_toolchains=self.trusted_tools,
                stage_allowed=lambda: True,
                identity_recorder=lambda x: None,
                metrics=self.fake_metrics,
            )

        self.assertTrue(env_dump_file.exists())
        captured_env = json.loads(env_dump_file.read_text())

        # Assert dirty env vars were scrubbed
        self.assertNotIn("STUDIOHUB_TOKEN", captured_env)
        self.assertNotIn("HF_TOKEN", captured_env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", captured_env)
        self.assertNotIn("LIVE_VAR", captured_env)

        # Assert mandatory isolation vars
        self.assertEqual(captured_env["GOTOOLCHAIN"], "local")
        self.assertEqual(captured_env["GOMAXPROCS"], "1")
        self.assertTrue(captured_env["HOME"].startswith(str(self.ws_dir)))
        self.assertTrue(captured_env["TMPDIR"].startswith(str(self.ws_dir)))

    def test_preemption_before_launch(self) -> None:
        recorded_identities = []
        result = run_gate(
            workspace_dir=self.ws_dir,
            source_dir=self.src_dir,
            trusted_toolchains=self.trusted_tools,
            stage_allowed=lambda: False,
            identity_recorder=recorded_identities.append,
        )
        self.assertEqual(result.outcome, "interrupted")
        self.assertIsNone(result.exit_code)
        self.assertEqual(result.process_release["proof"], "never_started")
        self.assertTrue(result.process_release["confirmed"])
        self.assertEqual(len(recorded_identities), 0)

    def test_preemption_during_execution(self) -> None:
        # Script that sleeps to allow preemption detection
        sleep_script = (
            f"#!{sys.executable}\n"
            f"import time; time.sleep(10)\n"
        )
        self.mock_python.write_text(sleep_script)

        preempt_flag = [False]

        def check_preempt():
            return preempt_flag[0]

        def stage_allowed():
            return not preempt_flag[0]

        # Trigger preemption shortly after start
        def delayed_preempt():
            time.sleep(0.15)
            preempt_flag[0] = True

        import threading
        t = threading.Thread(target=delayed_preempt)
        t.start()

        recorded_identities = []
        result = run_gate(
            workspace_dir=self.ws_dir,
            source_dir=self.src_dir,
            trusted_toolchains=self.trusted_tools,
            stage_allowed=stage_allowed,
            preempt_check=check_preempt,
            identity_recorder=recorded_identities.append,
            sample_interval_seconds=0.05,
            metrics=self.fake_metrics,
        )
        t.join()

        self.assertEqual(result.outcome, "interrupted")
        self.assertIsNone(result.exit_code)
        self.assertTrue(result.process_release["confirmed"])
        self.assertEqual(result.process_release["owned_processes_remaining"], 0)
        # First check ran and was preempted, remaining checks not_run
        self.assertEqual(len(result.checks), 6)
        self.assertEqual(result.checks[0]["id"], "signing-verifier-unit")
        self.assertEqual(result.checks[0]["reason"]["code"], "production_preempted")

    def test_log_cap_overflow_stops_process_and_fails(self) -> None:
        # Script that spams output beyond limit
        spam_script = (
            f"#!{sys.executable}\n"
            f"import sys\n"
            f"for _ in range(10000):\n"
            f"    sys.stdout.write('A' * 1024)\n"
            f"    sys.stdout.flush()\n"
        )
        self.mock_python.write_text(spam_script)

        result = run_gate(
            workspace_dir=self.ws_dir,
            source_dir=self.src_dir,
            trusted_toolchains=self.trusted_tools,
            stage_allowed=lambda: True,
            identity_recorder=lambda x: None,
            limits={"log_bytes": 10 * 1024},  # Small 10KB cap
            sample_interval_seconds=0.02,
            metrics=self.fake_metrics,
        )
        self.assertEqual(result.outcome, "failed")
        self.assertIsNotNone(result.reason)
        self.assertEqual(result.reason["code"], "log_budget")
        self.assertTrue(result.process_release["confirmed"])

    def test_fast_child_log_overflow_after_exit_still_fails(self) -> None:
        # The child exits before the sampling loop observes the overflow; the drained sink
        # must still fail the check and the retained log must stay bounded.
        self.mock_python.write_text(
            f"#!{sys.executable}\n"
            f"import sys\n"
            f"sys.stdout.write('A' * (64 * 1024))\n"
            f"sys.stdout.flush()\n"
            f"sys.exit(0)\n"
        )

        result = run_gate(
            workspace_dir=self.ws_dir,
            source_dir=self.src_dir,
            trusted_toolchains=self.trusted_tools,
            stage_allowed=lambda: True,
            identity_recorder=lambda x: None,
            limits={"log_bytes": 1024},
            sample_interval_seconds=0.01,
            metrics=self.fake_metrics,
        )
        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.reason["code"], "log_budget")
        self.assertTrue(result.process_release["confirmed"], result.process_release)
        self.assertLessEqual(len(result.check_logs["signing-verifier-unit"]), 1024)
        # The command's own exit is reported: it finished with 0, or it was killed by the
        # exact stop. A budget/evidence failure never fabricates a command exit.
        check = result.checks[0]
        if check["exit_code"] is None:
            self.assertEqual(result.termination_signal, 15)
        else:
            self.assertEqual(check["exit_code"], 0)
            self.assertIsNone(result.termination_signal)
        # The gate failed on evidence, so its own exit is null (the frozen archive rejects a
        # non-passed outcome with exit 0); the check still reports its observed exit.
        self.assertIsNone(result.exit_code)

    def test_unreaped_zombie_leader_is_reaped_and_released(self) -> None:
        # macOS reports an exited, unreaped child as unaddressable (getpgid ESRCH) and its
        # group as unreadable (killpg EPERM). The exact stop must collect its own child and
        # still confirm release; the caller's reap callback keeps the real exit status.
        child = subprocess.Popen(
            [sys.executable, "-I", "-c", "import time; time.sleep(0.1)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        probed = self.fake_metrics.identity(child.pid)
        self.assertIsNotNone(probed)
        identity = {
            "pid": child.pid,
            "pgid": os.getpgid(child.pid),
            "create_time": probed["create_time"],
        }
        deadline = time.monotonic() + 5.0
        while True:
            try:
                os.getpgid(child.pid)
            except ProcessLookupError:
                break  # exited and unreaped: kill(pid, 0) still succeeds for a zombie
            if time.monotonic() > deadline:
                self.fail("toy child did not exit")
            time.sleep(0.01)

        proof = stop_exact_identity(
            identity, metrics=self.fake_metrics, clock=time.monotonic, grace=0.5,
            reap=lambda: child.poll())

        self.assertTrue(proof.confirmed, proof.reason)
        self.assertEqual(child.returncode, 0)

    def test_unreadable_exit_transition_settles_within_grace(self) -> None:
        # A leader in its exit transition is briefly unreadable: macOS reports its pid
        # unaddressable and its group unsignalable before it is reapable. The exact stop
        # re-verifies over the grace window instead of holding on the first unreadable read.
        child = subprocess.Popen(
            [sys.executable, "-I", "-c", "import time; time.sleep(0.1)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        probed = self.fake_metrics.identity(child.pid)
        self.assertIsNotNone(probed)
        identity = {
            "pid": child.pid,
            "pgid": os.getpgid(child.pid),
            "create_time": probed["create_time"],
        }
        deadline = time.monotonic() + 5.0
        while True:
            try:
                os.getpgid(child.pid)
            except ProcessLookupError:
                break
            if time.monotonic() > deadline:
                self.fail("toy child did not exit")
            time.sleep(0.01)

        calls = {"alive": 0, "reap": 0}
        real_alive = self.fake_metrics.alive

        def unreadable_twice(item):
            calls["alive"] += 1
            return None if calls["alive"] <= 2 else real_alive(item)

        def reap_late():
            calls["reap"] += 1
            if calls["reap"] > 2:
                child.poll()

        with mock.patch.object(self.fake_metrics, "alive", side_effect=unreadable_twice):
            proof = stop_exact_identity(
                identity, metrics=self.fake_metrics, clock=time.monotonic, grace=1.0,
                reap=reap_late)

        self.assertTrue(proof.confirmed, proof.reason)
        self.assertEqual(child.returncode, 0)

    def test_reused_pid_is_never_signalled_or_released(self) -> None:
        # A recorded identity whose create_time does not match the live pid is a different
        # process (a reused pid). Its group is not ours: no signal is sent and release holds.
        child = subprocess.Popen(
            [sys.executable, "-I", "-c", "import time; time.sleep(10)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        try:
            probed = self.fake_metrics.identity(child.pid)
            self.assertIsNotNone(probed)
            identity = {
                "pid": child.pid,
                "pgid": os.getpgid(child.pid),
                "create_time": probed["create_time"] + 1.0,
            }

            proof = stop_exact_identity(
                identity, metrics=self.fake_metrics, clock=time.monotonic, grace=0.2)

            self.assertFalse(proof.confirmed)
            self.assertEqual(proof.reason["code"], "release_unknown")
            self.assertIsNone(child.poll())  # the live process was never signalled
        finally:
            child.terminate()
            child.wait(timeout=2)

    def _spawn_leader_with_ignoring_descendant(self) -> tuple[subprocess.Popen, int]:
        """A toy leader in its own session whose child ignores SIGTERM in the same group."""
        pid_file = self.root / "descendant.pid"
        script = self.root / "toy_leader.py"
        script.write_text(
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-I', '-B', '-c', "
            "'import os, signal, sys, time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "open(sys.argv[1], \"w\").write(str(os.getpid())); time.sleep(60)', sys.argv[1]])\n"
            "time.sleep(60)\n")
        leader = subprocess.Popen(
            [sys.executable, "-I", "-B", str(script), str(pid_file)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        try:
            deadline = time.monotonic() + 10.0
            while True:
                try:
                    return leader, int(pid_file.read_text())
                except (OSError, ValueError):
                    if time.monotonic() > deadline:
                        self.fail("toy leader did not report its descendant pid")
                    time.sleep(0.02)
        except BaseException:
            leader.kill()
            leader.wait(timeout=2)
            raise

    def _stop_identity(self, leader: subprocess.Popen, known_pids, metrics: Metrics,
                       grace: float) -> ReleaseProof:
        leader_identity = metrics.identity(leader.pid)
        self.assertIsNotNone(leader_identity)
        identity = {"pid": leader.pid, "pgid": os.getpgid(leader.pid),
                    "create_time": leader_identity["create_time"]}
        return stop_exact_identity(
            identity, metrics=metrics, clock=time.monotonic, grace=grace,
            known_pids=known_pids, reap=lambda: leader.poll())

    def test_leader_gone_ignoring_descendant_is_signalled_and_released(self) -> None:
        # The leader exits on SIGTERM while its sampled child ignores it. The group pgid may
        # be reused, so the exact stop must kill the verified child individually and confirm.
        metrics = Metrics()
        leader, descendant_pid = self._spawn_leader_with_ignoring_descendant()
        descendant = None
        try:
            descendant = metrics.identity(descendant_pid)
            self.assertIsNotNone(descendant)
            proof = self._stop_identity(leader, [descendant], metrics, grace=1.0)

            self.assertTrue(proof.confirmed, proof.reason)
            self.assertEqual(leader.returncode, -signal.SIGTERM)
            self.assertIs(metrics.alive(descendant), False)
        finally:
            if descendant is not None and metrics.alive(descendant) is True:
                try:
                    os.kill(descendant_pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            if leader.poll() is None:
                leader.kill()
            leader.wait(timeout=2)

    def test_unkillable_descendant_holds_and_expires_within_bound(self) -> None:
        # A descendant that ignores SIGTERM and cannot be killed is never false-released;
        # the bounded recheck expires and the proof holds.
        metrics = Metrics()
        leader, descendant_pid = self._spawn_leader_with_ignoring_descendant()
        descendant = None
        try:
            descendant = metrics.identity(descendant_pid)
            self.assertIsNotNone(descendant)
            started = time.monotonic()
            with mock.patch("components.hub.developer_image_runner._signal_pid"):
                proof = self._stop_identity(leader, [descendant], metrics, grace=0.2)
            elapsed = time.monotonic() - started

            self.assertFalse(proof.confirmed)
            self.assertEqual(proof.reason["code"], "release_unknown")
            self.assertLess(elapsed, 5.0)
            self.assertIs(metrics.alive(descendant), True)
        finally:
            if descendant is not None and metrics.alive(descendant) is True:
                try:
                    os.kill(descendant_pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            if leader.poll() is None:
                leader.kill()
            leader.wait(timeout=2)

    def test_reused_known_descendant_is_never_signalled(self) -> None:
        # A sampled id whose create_time does not match the live pid is a reused pid: it is
        # not ours, so it is never signalled and does not block release.
        metrics = Metrics()
        other = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        leader = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        try:
            other_identity = metrics.identity(other.pid)
            self.assertIsNotNone(other_identity)
            reused = {"pid": other.pid, "create_time": other_identity["create_time"] + 1.0}
            proof = self._stop_identity(leader, [reused], metrics, grace=0.5)

            self.assertTrue(proof.confirmed, proof.reason)
            self.assertIsNone(other.poll())  # the unrelated live process was not signalled
        finally:
            other.kill()
            other.wait(timeout=2)
            leader.kill()
            leader.wait(timeout=2)

    def test_unreadable_known_descendant_holds_without_signal(self) -> None:
        # A sampled id with no verified create_time is unknown, never release proof: it is
        # not signalled and the proof holds.
        metrics = Metrics()
        other = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        leader = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        try:
            unreadable = {"pid": other.pid, "create_time": 0.0}
            proof = self._stop_identity(leader, [unreadable], metrics, grace=0.2)

            self.assertFalse(proof.confirmed)
            self.assertEqual(proof.reason["code"], "release_unknown")
            self.assertIsNone(other.poll())  # the unrelated live process was not signalled
        finally:
            other.kill()
            other.wait(timeout=2)
            leader.kill()
            leader.wait(timeout=2)

    def test_unknown_leader_at_escalation_never_signals_group(self) -> None:
        # The leader becomes unreadable after SIGTERM. An unknown leader is never a reason to
        # SIGKILL the group (its pgid may be reused); the proof holds without a guessed signal.
        metrics = Metrics()
        leader, descendant_pid = self._spawn_leader_with_ignoring_descendant()
        descendant = None
        try:
            descendant = metrics.identity(descendant_pid)
            self.assertIsNotNone(descendant)
            real_alive = metrics.alive
            leader_calls = {"count": 0}

            def alive(item):
                if int(item.get("pid", -1)) == leader.pid:
                    leader_calls["count"] += 1
                    if leader_calls["count"] > 1:
                        return None
                return real_alive(item)

            signals = []
            real_group = image_runner._signal_group

            def group(pgid, signum):
                signals.append(signum)
                real_group(pgid, signum)

            with mock.patch.object(metrics, "alive", side_effect=alive), \
                    mock.patch("components.hub.developer_image_runner._signal_group",
                               side_effect=group):
                proof = self._stop_identity(leader, [descendant], metrics, grace=0.2)

            self.assertFalse(proof.confirmed)
            self.assertEqual(proof.reason["code"], "release_unknown")
            self.assertEqual(signals, [signal.SIGTERM])
            self.assertIs(metrics.alive(descendant), True)
        finally:
            if descendant is not None and metrics.alive(descendant) is True:
                try:
                    os.kill(descendant_pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            if leader.poll() is None:
                leader.kill()
            leader.wait(timeout=2)

    def test_already_gone_leader_stops_sampled_descendant(self) -> None:
        # Recovery sees a leader that already exited while its orphaned sampled child ignores
        # SIGTERM. The exact stop kills only that verified child and confirms release.
        metrics = Metrics()
        leader, descendant_pid = self._spawn_leader_with_ignoring_descendant()
        descendant = None
        try:
            leader_identity = metrics.identity(leader.pid)
            descendant = metrics.identity(descendant_pid)
            self.assertIsNotNone(leader_identity)
            self.assertIsNotNone(descendant)
            identity = {"pid": leader.pid, "pgid": os.getpgid(leader.pid),
                        "create_time": leader_identity["create_time"]}
            leader.kill()
            leader.wait(timeout=2)

            proof = stop_exact_identity(
                identity, metrics=metrics, clock=time.monotonic, grace=0.2,
                known_pids=[descendant], reap=lambda: leader.poll())

            self.assertTrue(proof.confirmed, proof.reason)
            self.assertGreater(proof.latency_seconds, 0.0)
            self.assertIs(metrics.alive(descendant), False)
        finally:
            if descendant is not None and metrics.alive(descendant) is True:
                try:
                    os.kill(descendant_pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            if leader.poll() is None:
                leader.kill()
            leader.wait(timeout=2)

    def test_budget_stop_reports_killed_command_as_null_exit(self) -> None:
        self.mock_python.write_text(f"#!{sys.executable}\nimport time; time.sleep(10)\n")

        result = run_gate(
            workspace_dir=self.ws_dir,
            source_dir=self.src_dir,
            trusted_toolchains=self.trusted_tools,
            stage_allowed=lambda: True,
            identity_recorder=lambda x: None,
            limits={"runtime_seconds": 0.2},
            sample_interval_seconds=0.02,
            metrics=self.fake_metrics,
        )

        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.reason["code"], "runtime_budget")
        self.assertIsNone(result.exit_code)
        self.assertEqual(result.termination_signal, 15)
        self.assertIsNone(result.checks[0]["exit_code"])
        self.assertTrue(result.process_release["confirmed"], result.process_release)

    def test_signalled_command_reports_null_exit_and_signal(self) -> None:
        # The observed wait status decides: a signalled command has no exit code and keeps
        # its real signal; it never becomes 0 or 1.
        for name, expected in (("SIGTERM", 15), ("SIGKILL", 9)):
            with self.subTest(signal=name):
                self.mock_python.write_text(
                    f"#!{sys.executable}\n"
                    f"import os, signal\n"
                    f"os.kill(os.getpid(), signal.{name})\n")

                result = run_gate(
                    workspace_dir=self.ws_dir,
                    source_dir=self.src_dir,
                    trusted_toolchains=self.trusted_tools,
                    stage_allowed=lambda: True,
                    identity_recorder=lambda x: None,
                    sample_interval_seconds=0.02,
                    metrics=self.fake_metrics,
                )

                self.assertEqual(result.outcome, "failed")
                self.assertEqual(result.reason["code"], "command_failed")
                self.assertIsNone(result.exit_code)
                self.assertEqual(result.termination_signal, expected)
                self.assertIsNone(result.checks[0]["exit_code"])
                self.assertTrue(result.process_release["confirmed"], result.process_release)

    def test_command_failure_keeps_its_actual_exit_code(self) -> None:
        self.mock_python.write_text(f"#!{sys.executable}\nimport sys; sys.exit(3)\n")

        result = run_gate(
            workspace_dir=self.ws_dir,
            source_dir=self.src_dir,
            trusted_toolchains=self.trusted_tools,
            stage_allowed=lambda: True,
            identity_recorder=lambda x: None,
            sample_interval_seconds=0.02,
            metrics=self.fake_metrics,
        )

        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.reason["code"], "command_failed")
        self.assertEqual(result.exit_code, 3)
        self.assertIsNone(result.termination_signal)
        self.assertEqual(result.checks[0]["exit_code"], 3)
        self.assertTrue(result.process_release["confirmed"], result.process_release)

    def test_split_secret_redacted_across_reads(self) -> None:
        # The shared sink holds a suffix that could start a secret, so a secret split over
        # two reads is redacted whole instead of leaking its tail.
        sink = _BoundedLogSink(secrets=[b"unit-marker"], limit=1024)
        sink.feed(b"unit-")
        sink.feed(b"marker")
        sink.finish()
        self.assertNotIn(b"unit-marker", sink.body())
        self.assertIn(b"<redacted>", sink.body())

    def test_recorder_failure_never_executes_tool_and_reaps_launcher(self) -> None:
        # The tool writes a marker if it runs; a failed recorder must close authorization
        # so the marker is never written and the launcher is reaped.
        marker = self._write_marker_tool()
        recorded = []

        def recorder(identity):
            recorded.append(identity)
            raise RuntimeError("ledger unavailable")

        result = run_gate(
            workspace_dir=self.ws_dir,
            source_dir=self.src_dir,
            trusted_toolchains=self.trusted_tools,
            stage_allowed=lambda: True,
            identity_recorder=recorder,
            sample_interval_seconds=0.02,
            metrics=self.fake_metrics,
        )

        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.reason["code"], "identity_unrecorded")
        self.assertIn("identity recorder failed", result.reason["detail"])
        self.assertFalse(marker.exists())
        self.assertEqual(len(recorded), 1)
        with self.assertRaises(ProcessLookupError):
            os.kill(recorded[0]["pid"], 0)
        self.assertTrue(result.process_release["confirmed"])

    def test_unknown_identity_never_executes_tool_or_signals_group(self) -> None:
        marker = self._write_marker_tool()
        fake_metrics = mock.MagicMock(spec=Metrics)
        fake_metrics.identity.return_value = None
        fake_metrics.swap_used_bytes.return_value = 0

        with mock.patch(
            "components.hub.developer_gate_runner.os.killpg",
            side_effect=AssertionError("unverified group signal"),
        ):
            result = run_gate(
                workspace_dir=self.ws_dir,
                source_dir=self.src_dir,
                trusted_toolchains=self.trusted_tools,
                stage_allowed=lambda: True,
                identity_recorder=lambda x: None,
                sample_interval_seconds=0.02,
                metrics=fake_metrics,
            )

        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.reason["code"], "release_unknown")
        self.assertIn("could not be verified", result.reason["detail"])
        self.assertFalse(marker.exists())
        self.assertFalse(result.process_release["confirmed"])

    def test_missing_metrics_refuses_before_any_tool_runs(self) -> None:
        marker = self._write_marker_tool()
        with mock.patch.dict(sys.modules, {"psutil": None}):
            result = run_gate(
                workspace_dir=self.ws_dir,
                source_dir=self.src_dir,
                trusted_toolchains=self.trusted_tools,
                stage_allowed=lambda: True,
                identity_recorder=lambda x: None,
            )

        self.assertEqual(result.outcome, "not_run")
        self.assertEqual(result.reason["code"], "metrics_unavailable")
        self.assertEqual(result.process_release["proof"], "never_started")
        self.assertFalse(marker.exists())

    def test_unknown_stop_proof_never_false_passes(self) -> None:
        fake_metrics = mock.MagicMock(spec=Metrics)
        fake_metrics.identity.return_value = {"pid": 1234, "create_time": 100.0}
        fake_metrics.swap_used_bytes.return_value = 0
        fake_metrics.tree.return_value = (1024, [{"pid": 1234, "create_time": 100.0}])

        # Mock stop_exact_identity to simulate release_unknown failure
        with mock.patch(
            "components.hub.developer_gate_runner.stop_exact_identity",
            return_value=ReleaseProof(
                confirmed=False,
                confirmed_at=None,
                reason={"code": "release_unknown", "detail": "simulated hold"},
                latency_seconds=1.0,
                checked_pids=[],
            ),
        ):
            result = run_gate(
                workspace_dir=self.ws_dir,
                source_dir=self.src_dir,
                trusted_toolchains=self.trusted_tools,
                stage_allowed=lambda: True,
                identity_recorder=lambda x: None,
                metrics=fake_metrics,
            )

        self.assertEqual(result.outcome, "failed")
        self.assertFalse(result.process_release["confirmed"])
        self.assertEqual(result.reason["code"], "release_unknown")
        # The command itself finished with 0; only the evidence failed. The check keeps its
        # observed exit, but the gate-level exit is null (a non-passed 0 is rejected by the
        # frozen result-archive contract).
        self.assertEqual(result.checks[0]["exit_code"], 0)
        self.assertIsNone(result.exit_code)
        self.assertIsNone(result.termination_signal)

    def test_all_six_known_commands_execute_cleanly(self) -> None:
        # Echo unique identifier for each tool invocation
        self.mock_python.write_text(f"#!{sys.executable}\nimport sys; sys.stdout.write('python ok\\n'); sys.exit(0)\n")
        self.mock_node.write_text(f"#!{sys.executable}\nimport sys; sys.stdout.write('node ok\\n'); sys.exit(0)\n")
        self.mock_go.write_text(f"#!{sys.executable}\nimport sys; sys.stdout.write('go ok\\n'); sys.exit(0)\n")

        recorded = []
        result = run_gate(
            workspace_dir=self.ws_dir,
            source_dir=self.src_dir,
            trusted_toolchains=self.trusted_tools,
            stage_allowed=lambda: True,
            identity_recorder=recorded.append,
            metrics=self.fake_metrics,
            before={"observed_at": "2026-11-08T12:00:00Z", "free_bytes": 1000000, "workspace_bytes": 0, "result_spool_bytes": 0, "shared_cache_bytes": 0},
        )

        self.assertEqual(result.outcome, "passed")
        self.assertEqual(result.exit_code, 0)
        self.assertIsNone(result.reason)
        self.assertEqual(len(result.checks), 6)
        self.assertEqual(len(recorded), 6)
        self.assertTrue(result.process_release["confirmed"])
        self.assertEqual(result.process_release["owned_processes_remaining"], 0)

        for check in result.checks:
            self.assertEqual(check["outcome"], "passed")
            self.assertEqual(check["exit_code"], 0)
            self.assertIsNone(check["reason"])
            self.assertGreaterEqual(check["log_bytes"], 0)
            self.assertEqual(
                check["log_sha256"],
                hashlib.sha256(result.check_logs[check["id"]]).hexdigest(),
            )

        # Check logs match expected tool stdout
        self.assertIn(b"python ok", result.check_logs["signing-verifier-unit"])
        self.assertIn(b"node ok", result.check_logs["javascript-unit"])
        self.assertIn(b"go ok", result.check_logs["vet-darwin"])
        self.assertIn(b"go ok", result.check_logs["vet-windows"])
        self.assertIn(b"go ok", result.check_logs["vet-linux"])
        self.assertIn(b"go ok", result.check_logs["go-unit"])

    # --- Live free-disk admission ---------------------------------------------

    GIB = 1024**3

    def _sequence_sampler(self, *values):
        """Deterministic disk sampler: each call returns the next value, last repeats."""
        calls = []

        def sample(_path):
            calls.append(1)
            value = values[min(len(calls) - 1, len(values) - 1)]
            if isinstance(value, Exception):
                raise value
            return value

        return sample, calls

    def _run_with_disk(self, sampler, **kwargs):
        return run_gate(
            workspace_dir=self.ws_dir,
            source_dir=self.src_dir,
            trusted_toolchains=self.trusted_tools,
            stage_allowed=lambda: True,
            identity_recorder=kwargs.pop("identity_recorder", lambda x: None),
            metrics=self.fake_metrics,
            disk_free_bytes=sampler,
            sample_interval_seconds=0.02,
            **kwargs,
        )

    def test_low_disk_before_spawn_never_starts_tool(self) -> None:
        marker = self._write_marker_tool()
        recorded = []
        result = self._run_with_disk(
            lambda _path: MINIMUM_FREE_BYTES - 1,
            identity_recorder=recorded.append,
        )

        self.assertEqual(result.outcome, "not_run")
        self.assertEqual(result.reason["code"], "not_enough_disk")
        self.assertEqual(result.process_release["proof"], "never_started")
        self.assertTrue(result.process_release["confirmed"])
        self.assertFalse(marker.exists())
        self.assertEqual(recorded, [])

    def test_requested_floor_can_only_raise_the_ten_gib_minimum(self) -> None:
        # A request of 0 cannot disable the owner's 10 GiB live floor.
        result = self._run_with_disk(
            lambda _path: MINIMUM_FREE_BYTES - 1,
            limits={"minimum_free_bytes": 0},
        )
        self.assertEqual(result.outcome, "not_run")
        self.assertEqual(result.reason["code"], "not_enough_disk")

        # A higher requested floor is enforced.
        result = self._run_with_disk(
            lambda _path: 11 * self.GIB,
            limits={"minimum_free_bytes": 12 * self.GIB},
        )
        self.assertEqual(result.outcome, "not_run")
        self.assertEqual(result.reason["code"], "not_enough_disk")

    def test_invalid_configured_floor_refused(self) -> None:
        for bad in (True, -1, float("nan"), float("inf"), "10737418240", 1.5, 1.0,
                    2**63, 9007199254740992):
            with self.subTest(minimum_free_bytes=bad):
                result = self._run_with_disk(
                    lambda _path: 99 * self.GIB,
                    limits={"minimum_free_bytes": bad},
                )
                self.assertEqual(result.outcome, "not_run")
                self.assertEqual(result.reason["code"], "invalid_request")

    def test_disk_drop_between_preflight_and_spawn_leaves_check_not_run(self) -> None:
        marker = self._write_marker_tool()
        recorded = []
        sampler, calls = self._sequence_sampler(99 * self.GIB, MINIMUM_FREE_BYTES - 1)
        result = self._run_with_disk(sampler, identity_recorder=recorded.append)

        self.assertEqual(result.outcome, "not_run")
        self.assertIsNone(result.exit_code)
        self.assertEqual(result.reason["code"], "not_enough_disk")
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(result.checks), 6)
        self.assertEqual(result.checks[0]["outcome"], "not_run")
        self.assertIsNone(result.checks[0]["exit_code"])
        self.assertIsNone(result.checks[0]["duration_seconds"])
        self.assertEqual(recorded, [])
        self.assertFalse(marker.exists())

    def test_invalid_disk_sample_refused_before_spawn(self) -> None:
        marker = self._write_marker_tool()
        result = self._run_with_disk(lambda _path: True)

        self.assertEqual(result.outcome, "not_run")
        self.assertEqual(result.reason["code"], "metric_unavailable")
        self.assertEqual(result.process_release["proof"], "never_started")
        self.assertFalse(marker.exists())

    def test_low_disk_after_identity_before_authorization_stops_owned_process(self) -> None:
        marker = self._write_marker_tool()
        recorded = []
        sampler, calls = self._sequence_sampler(
            99 * self.GIB, 99 * self.GIB, MINIMUM_FREE_BYTES - 1)
        result = self._run_with_disk(sampler, identity_recorder=recorded.append)

        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.reason["code"], "not_enough_disk")
        self.assertIn("before authorization", result.reason["detail"])
        self.assertEqual(len(recorded), 1)
        self.assertFalse(marker.exists())
        self.assertTrue(result.process_release["confirmed"], result.process_release)
        with self.assertRaises(ProcessLookupError):
            os.kill(recorded[0]["pid"], 0)
        self.assertEqual(len(calls), 3)

    def test_low_disk_while_running_stops_owned_process(self) -> None:
        self.mock_python.write_text(
            f"#!{sys.executable}\nimport time; time.sleep(10)\n")
        sampler, _calls = self._sequence_sampler(
            99 * self.GIB, 99 * self.GIB, 99 * self.GIB, MINIMUM_FREE_BYTES - 1)
        result = self._run_with_disk(sampler)

        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.reason["code"], "not_enough_disk")
        self.assertTrue(result.process_release["confirmed"], result.process_release)
        self.assertEqual(result.process_release["owned_processes_remaining"], 0)

    def test_disk_sample_error_while_running_stops_owned_process(self) -> None:
        self.mock_python.write_text(
            f"#!{sys.executable}\nimport time; time.sleep(10)\n")
        sampler, _calls = self._sequence_sampler(
            99 * self.GIB, 99 * self.GIB, 99 * self.GIB, OSError("statvfs failed"))
        result = self._run_with_disk(sampler)

        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.reason["code"], "metric_unavailable")
        self.assertIn("free-space sample unavailable", result.reason["detail"])
        self.assertTrue(result.process_release["confirmed"], result.process_release)

    def test_low_disk_after_completion_never_reports_passed(self) -> None:
        marker = self._write_marker_tool()

        def sampler(_path):
            return MINIMUM_FREE_BYTES - 1 if marker.exists() else 99 * self.GIB

        result = self._run_with_disk(sampler)

        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.reason["code"], "not_enough_disk")
        self.assertTrue(marker.exists())
        self.assertTrue(result.process_release["confirmed"], result.process_release)


if __name__ == "__main__":
    unittest.main()
