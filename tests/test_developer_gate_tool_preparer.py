"""Focused tests for the trusted official-tool preparation adapter.

No real download, Hub, network or inference is used.  The official manifest and a
synthetic tar.gz are local; real probes use the installed psutil-backed Metrics, and
synthetic metrics appear only in controlled fault injection.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import signal
import sys
import tarfile
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.hub import developer_gate_contract as contract  # noqa: E402
from components.hub import developer_gate_runner as gate_runner  # noqa: E402
from components.hub import developer_gate_tool_preparer as preparer_mod  # noqa: E402
from components.hub import developer_gate_tools as tools_mod  # noqa: E402

FREE = types.SimpleNamespace(free=50 * 1024**3, total=100 * 1024**3, used=50 * 1024**3)
LIMITS = {"runtime_seconds": 30, "workspace_bytes": 10_000_000,
          "max_process_tree_rss_bytes": 1_000_000_000,
          "max_system_swap_growth_bytes": 64 * 1024**2}


class SwapGrowthMetrics(gate_runner.Metrics):
    """Real identities, but swap grows after the baseline read."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def swap_used_bytes(self) -> int:
        self.calls += 1
        return 100 if self.calls == 1 else 200


class SwapUnreadableMetrics(gate_runner.Metrics):
    def swap_used_bytes(self) -> int:
        raise RuntimeError("injected swap failure")


class TreeFailureMetrics(gate_runner.Metrics):
    """Real identities, but an unreadable tree is the injected fault."""

    def tree(self, pid: int):
        raise RuntimeError("injected tree failure")


def _archive(script: str = '#!/bin/sh\necho "Python 3.15.0"\n') -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data, mode in (
            ("python", None, 0o755),
            ("python/bin", None, 0o755),
            ("python/bin/python3.15", script.encode(), 0o755),
        ):
            info = tarfile.TarInfo(name)
            info.mtime = 1_700_000_000
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if data is None:
                info.type = tarfile.DIRTYPE
                info.mode = mode
                tar.addfile(info)
            else:
                info.type = tarfile.REGTYPE
                info.mode = mode
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class OfficialToolPreparerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="studiohub-gate-preparer-")
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.tools_root = base / "tools"
        self.tools_root.mkdir(mode=0o700)
        self.tools_root = self.tools_root.resolve()
        self.attempt_root = base / "attempt"
        self.attempt_root.mkdir(mode=0o700)
        self.manifest_path = base / "official-tools.json"
        self.archive = _archive()
        self.sha256 = hashlib.sha256(self.archive).hexdigest()
        self.metrics = gate_runner.Metrics()
        try:
            self.metrics.available_bytes()
        except Exception as exc:  # noqa: BLE001 - the installed runtime must have psutil
            self.skipTest(f"psutil is unavailable: {exc}")
        self.disk = mock.patch.object(preparer_mod, "_disk_usage", return_value=FREE)
        self.disk.start()
        self.addCleanup(self.disk.stop)
        self.tool_disk = mock.patch.object(tools_mod, "_disk_usage", return_value=FREE)
        self.tool_disk.start()
        self.addCleanup(self.tool_disk.stop)
        self.stream = mock.patch.object(
            preparer_mod, "_open_trusted_stream", return_value=io.BytesIO(self.archive))
        self.real_open = self.stream.get_original()[0]
        self.stream.start()
        self.addCleanup(self.stream.stop)
        self.write_manifest()

    def write_manifest(self, *, version: str = "3.15.0", sha256: str | None = None) -> None:
        platform = tools_mod._runtime_platform()
        manifest = {
            "format": tools_mod.MANIFEST_FORMAT,
            "scope": "archive-preparation-only",
            "production_installer_qualified": False,
            "platform": platform,
            "limits": {
                "max_archive_bytes": 1_000_000,
                "max_expanded_bytes": 1_000_000,
                "max_member_bytes": 1_000_000,
                "max_members": 100,
                "max_path_length": 200,
                "max_total_seconds": 60,
                "min_free_bytes": 10 * 1024**3,
                "download_cache_bytes": 1_000_000,
            },
            "artifacts": [{
                "id": "python", "version": version, "platform": platform,
                "filename": "python.tar.gz",
                "url": "https://go.dev/dl/python.tar.gz",
                "size_bytes": len(self.archive),
                "sha256": sha256 or self.sha256,
                "executable": "python/bin/python3.15",
            }],
        }
        self.manifest_path.write_bytes(json.dumps(manifest, sort_keys=True).encode())
        self.manifest_sha256 = hashlib.sha256(self.manifest_path.read_bytes()).hexdigest()

    def tool_manifest(self, *, pin_version: str = "3.15.0",
                      pin_sha256: str | None = None) -> dict:
        return {
            "format": "studio.tool-manifest.v1", "id": "tm-pilot", "revision": "r1",
            "sha256": self.manifest_sha256,
            "tools": [{
                "id": "python", "version": pin_version,
                "manifest_sha256": pin_sha256 or self.manifest_sha256,
            }],
            "provenance": "hub-packaged-official-hash-pinned",
        }

    def preparer(self, *, metrics=None, **kwargs) -> preparer_mod.OfficialToolPreparer:
        return preparer_mod.OfficialToolPreparer(
            self.tool_manifest(**kwargs), metrics=metrics or self.metrics,
            manifest_path=self.manifest_path)

    def use_archive(self, script: str) -> None:
        self.archive = _archive(script)
        self.sha256 = hashlib.sha256(self.archive).hexdigest()
        self.write_manifest()
        self.stream.stop()
        self.stream = mock.patch.object(
            preparer_mod, "_open_trusted_stream", return_value=io.BytesIO(self.archive))
        self.stream.start()
        self.addCleanup(self.stream.stop)

    def call(self, preparer, **overrides):
        values = {
            "attempt_root": self.attempt_root,
            "tools_root": self.tools_root,
            "identity_recorder": lambda identity: None,
            "stage_allowed": lambda: True,
            "preempt_check": lambda: False,
            "resource_safe": lambda: True,
            "deadline_monotonic": time.monotonic() + 60,
            "limits": dict(LIMITS),
        }
        values.update(overrides)
        return preparer("python", **values)

    def test_prepares_one_tool_and_binds_the_accepted_pin(self) -> None:
        recorded = []
        result = self.call(self.preparer(), identity_recorder=recorded.append)
        pin = self.tool_manifest()["tools"][0]
        self.assertEqual(result["actual_toolfacts"]["pin"], pin)
        self.assertTrue(result["actual_toolfacts"]["available"])
        self.assertEqual(result["actual_toolfacts"]["provenance"],
                         "owned-toolchain-readback")
        executable = Path(result["executable"])
        self.assertTrue(executable.is_file())
        self.assertTrue(os.access(executable, os.X_OK))
        self.assertTrue(str(executable).startswith(str(self.tools_root)))
        final = self.tools_root / "tools" / f"python-3.15.0-{self.sha256}"
        allocated = sum(
            os.lstat(os.path.join(root, name)).st_blocks * 512
            for root, _dirs, files in os.walk(final) for name in files)
        self.assertEqual(result["actual_toolfacts"]["installed_bytes"], allocated)
        contract.validate_schema(result["actual_toolfacts"], "toolFact")
        contract.validate_schema(result["process_release"], "processRelease")
        self.assertEqual(result["process_release"]["owned_processes_remaining"], 0)
        self.assertEqual(len(recorded), 1)
        self.assertGreater(recorded[0]["create_time"], 0)
        self.assertEqual(recorded[0]["pgid"], recorded[0]["pid"])

    def test_refuses_a_pin_version_that_does_not_match_the_packaged_manifest(self) -> None:
        with self.assertRaises(tools_mod.OfficialToolError):
            self.preparer(pin_version="9.9.9")

    def test_refuses_a_pin_hash_that_does_not_bind_the_raw_manifest(self) -> None:
        with self.assertRaises(tools_mod.OfficialToolError):
            self.preparer(pin_sha256="0" * 64)

    def test_checkpoint_refuses_before_download_when_preempted(self) -> None:
        recorded = []
        with self.assertRaises(tools_mod.OfficialToolError):
            self.call(self.preparer(), identity_recorder=recorded.append,
                      preempt_check=lambda: True)
        self.assertEqual(recorded, [])
        self.assertFalse(any(path.name.startswith("python-")
                             for path in self.tools_root.rglob("python-*")))

    def test_auth_guard_refuses_before_any_probe_process(self) -> None:
        preparer = self.preparer()
        preparer._checkpoint = lambda *args, **kwargs: (lambda stage: None)
        recorded = []
        with self.assertRaises(tools_mod.OfficialToolError):
            self.call(preparer, identity_recorder=recorded.append,
                      stage_allowed=lambda: False)
        self.assertEqual(recorded, [])

    def test_wrong_version_probe_raises_and_leaves_no_process(self) -> None:
        self.use_archive('#!/bin/sh\necho "Python 9.9.9"\n')
        recorded = []
        with self.assertRaises(tools_mod.OfficialToolError):
            self.call(self.preparer(), identity_recorder=recorded.append)
        self.assertEqual(len(recorded), 1)
        with self.assertRaises(ProcessLookupError):
            os.kill(recorded[0]["pid"], 0)

    def test_quick_overflow_after_exit_is_refused(self) -> None:
        self.use_archive(
            '#!/bin/sh\ndd if=/dev/zero bs=1024 count=70 2>/dev/null | tr "\\0" x\n'
            'echo\necho "Python 3.15.0"\n')
        with self.assertRaises(tools_mod.OfficialToolError):
            self.call(self.preparer())

    def test_unreadable_metrics_fail_closed(self) -> None:
        self.use_archive('#!/bin/sh\nsleep 0.4\necho "Python 3.15.0"\n')
        with self.assertRaises(tools_mod.OfficialToolError):
            self.call(self.preparer(metrics=TreeFailureMetrics()))

    def test_sampled_descendant_outliving_the_leader_is_never_claimed(self) -> None:
        self.use_archive('#!/bin/sh\nsleep 1 &\nsleep 0.5\necho "Python 3.15.0"\n')
        recorded = []
        try:
            with self.assertRaises(tools_mod.OfficialToolError):
                self.call(self.preparer(), identity_recorder=recorded.append)
            self.assertEqual(len(recorded), 1)
        finally:
            if recorded:
                try:
                    os.killpg(recorded[0]["pgid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_accepted_runtime_bound_stops_the_probe(self) -> None:
        self.use_archive('#!/bin/sh\nsleep 2\necho "Python 3.15.0"\n')
        with self.assertRaises(tools_mod.OfficialToolError):
            self.call(self.preparer(),
                      limits={**LIMITS, "runtime_seconds": 1})

    def test_accepted_workspace_bound_stops_the_probe(self) -> None:
        self.use_archive(
            '#!/bin/sh\ndd if=/dev/zero of=scratch.bin bs=1024 count=4 2>/dev/null\n'
            'sleep 1\necho "Python 3.15.0"\n')
        with self.assertRaises(tools_mod.OfficialToolError):
            self.call(self.preparer(),
                      limits={**LIMITS, "workspace_bytes": 1024})

    def test_quick_exit_overbudget_scratch_is_refused_after_release(self) -> None:
        self.use_archive(
            '#!/bin/sh\ndd if=/dev/zero of=scratch.bin bs=1024 count=4 2>/dev/null\n'
            'echo "Python 3.15.0"\n')
        with mock.patch.object(preparer_mod, "CHECKPOINT_RESOURCE_SECONDS", 60.0):
            with self.assertRaises(tools_mod.OfficialToolError):
                self.call(self.preparer(),
                          limits={**LIMITS, "workspace_bytes": 1024})

    def test_system_swap_growth_beyond_the_accepted_bound_is_refused(self) -> None:
        self.use_archive('#!/bin/sh\necho "Python 3.15.0"\n')
        with self.assertRaises(tools_mod.OfficialToolError):
            self.call(self.preparer(metrics=SwapGrowthMetrics()),
                      limits={**LIMITS, "max_system_swap_growth_bytes": 0})

    def test_unreadable_system_swap_fails_closed_before_the_probe(self) -> None:
        recorded = []
        with self.assertRaises(tools_mod.OfficialToolError):
            self.call(self.preparer(metrics=SwapUnreadableMetrics()),
                      identity_recorder=recorded.append)
        self.assertEqual(recorded, [])

    def test_read_manifest_seam_returns_only_raw_hash_verified_bytes(self) -> None:
        preparer = self.preparer()
        manifest, digest = preparer.read_manifest()
        self.assertEqual(digest, self.manifest_sha256)
        self.assertEqual(manifest["platform"], tools_mod._runtime_platform())
        self.manifest_path.write_bytes(self.manifest_path.read_bytes() + b"\n")
        with self.assertRaises(tools_mod.OfficialToolError):
            preparer.read_manifest()

    def test_changed_manifest_is_refused_before_helper(self) -> None:
        preparer = self.preparer()
        self.manifest_path.write_bytes(self.manifest_path.read_bytes() + b"\n")
        with self.assertRaises(tools_mod.OfficialToolError):
            self.call(preparer)

    def test_redirect_and_stream_hosts_are_narrow(self) -> None:
        preparer_mod._require_trusted_url(
            "https://release-assets.githubusercontent.com/asset.tar.gz")
        preparer_mod._require_trusted_url("https://objects.githubusercontent.com/x")
        for url in ("http://go.dev/dl/x", "https://evil.example/x",
                    "https://go.dev:8443/dl/x", "https://user@go.dev/dl/x"):
            with self.assertRaises(tools_mod.OfficialToolError):
                preparer_mod._require_trusted_url(url)
        with self.assertRaises(tools_mod.OfficialToolError):
            self.real_open("https://evil.example/x", 5.0)


if __name__ == "__main__":
    unittest.main()
