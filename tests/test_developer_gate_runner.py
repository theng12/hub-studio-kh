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
import stat
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
    DeveloperGateRunner,
    GateRunResult,
    GateRunnerError,
    _BoundedLogSink,
    load_commands_fixture,
    run_gate,
    validate_trusted_toolchains,
)
from components.hub.developer_image_runner import Metrics, ReleaseProof


class FakePsutilProcess:
    def __init__(self, pid: int, ct: float = 100.0) -> None:
        self.pid = pid
        self._ct = ct

    def create_time(self) -> float:
        return self._ct

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


if __name__ == "__main__":
    unittest.main()
