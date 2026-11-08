"""Focused adversarial tests for developer gate inventory helper.

Verifies:
- Physical internal drive topology vs APFS container capacity.
- Wire revision 2 physicalDrive exact schema conformance.
- APFS synthesized container missing physical stores refuses physical proof.
- Malformed/partial APFS stores refuse immediately; never skip to claim unambiguous.
- Positive integer capacity only: reject boolean/string coercion.
- Bounded process primitive terminates and bounds memory on exceed.
- Python probe isolated execution flags (-I -B).
- Swift compiler version required (not swift-driver version).
- Unsupported tool IDs rejected without arbitrary command execution.
- No private paths or raw stderr echoed into reasons.
- is_internal_drive_eligible accepts diskutil-apfs-list-plist and enforces strict types.
"""

from __future__ import annotations

import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest import mock

from components.developer_gate_inventory import (
    _default_volume_id_runner,
    collect_developer_gate_inventory,
    is_internal_drive_eligible,
    probe_internal_drive,
    probe_tool_version,
    probe_toolchains,
    probe_work_volume,
    run_bounded_process,
)


def _make_plist_bytes(data: dict) -> bytes:
    return plistlib.dumps(data)


class DeveloperGateInventoryTests(unittest.TestCase):
    def test_internal_512gb_drive_topology_accepted(self) -> None:
        """APFS container size (494GB) is not confused with physical SSD capacity (500GB/512GB)."""
        responses = {
            ("info", "-plist", "/"): _make_plist_bytes({
                "DeviceIdentifier": "disk3s5",
                "ParentWholeDisk": "disk3",
                "TotalSize": 494_384_795_648,  # APFS container size
                "APFSPhysicalStores": [{"APFSPhysicalStore": "disk0s2"}],
            }),
            ("info", "-plist", "disk0s2"): _make_plist_bytes({
                "DeviceIdentifier": "disk0s2",
                "ParentWholeDisk": "disk0",
                "WholeDisk": False,
                "TotalSize": 494_384_795_648,
            }),
            ("info", "-plist", "disk0"): _make_plist_bytes({
                "DeviceIdentifier": "disk0",
                "ParentWholeDisk": "disk0",
                "WholeDisk": True,
                "Internal": True,
                "BusProtocol": "Apple Fabric",
                "Content": "GUID_partition_scheme",
                "TotalSize": 500_277_792_768,  # Apple 512GB internal SSD physical capacity
            }),
        }

        def mock_runner(args: list[str]) -> bytes:
            key = tuple(args)
            if key in responses:
                return responses[key]
            raise RuntimeError(f"Unexpected diskutil call: {args}")

        drive = probe_internal_drive(workspace_path="/", runner=mock_runner)
        self.assertTrue(drive["internal"])
        self.assertTrue(drive["physical"])
        self.assertEqual(drive["device_id"], "disk0")
        self.assertEqual(drive["workspace_backing_device_id"], "disk0")
        self.assertEqual(drive["size_bytes"], 500_277_792_768)
        self.assertEqual(drive["source"], "diskutil-info-plist")
        self.assertEqual(drive["backing_source"], "diskutil-info-plist")
        self.assertTrue(drive["observed_at"].endswith("Z"))
        self.assertIsNone(drive["reason"])

        # Measured 500_277_792_768 fits default (nominal 512GB class: 500_000_000_000 floor)
        self.assertTrue(is_internal_drive_eligible(drive))
        self.assertTrue(is_internal_drive_eligible(drive, min_bytes=500_000_000_000))

        # Explicit lower threshold override works
        self.assertTrue(is_internal_drive_eligible(drive, min_bytes=250_000_000_000))

        # Explicit larger threshold override (e.g. 1TB floor) refuses
        self.assertFalse(is_internal_drive_eligible(drive, min_bytes=1_000_000_000_000))

        # Container size alone would have failed 500GB floor
        self.assertFalse(494_384_795_648 >= 500_000_000_000)

    def test_apfs_synthesized_container_missing_stores_refuses_physical(self) -> None:
        """Synthesized container missing APFSPhysicalStores must not falsely claim physical."""
        responses = {
            ("info", "-plist", "/"): _make_plist_bytes({
                "DeviceIdentifier": "disk3s5",
                "ParentWholeDisk": "disk3",
                "TotalSize": 494_384_795_648,
                # APFSPhysicalStores missing or unresolvable
            }),
            ("info", "-plist", "disk3"): _make_plist_bytes({
                "DeviceIdentifier": "disk3",
                "ParentWholeDisk": "disk3",
                "WholeDisk": True,
                "Internal": True,
                "VirtualOrPhysical": "Virtual",
                "Content": "EF57347C-0000-11AA-AA11-00306543ECAC",  # APFS container GUID
                "APFSContainerSize": 494_384_795_648,
                "TotalSize": 494_384_795_648,
            }),
        }

        drive = probe_internal_drive(workspace_path="/", runner=lambda args: responses[tuple(args)])
        self.assertNotEqual(drive["physical"], True)
        self.assertIsNone(drive["size_bytes"])
        self.assertFalse(is_internal_drive_eligible(drive))

    def test_malformed_or_partial_apfs_stores_refuses_immediately(self) -> None:
        """One valid store + one malformed store must refuse; never claim single unambiguous drive."""
        responses = {
            ("info", "-plist", "/"): _make_plist_bytes({
                "DeviceIdentifier": "disk3s5",
                "APFSPhysicalStores": [
                    {"APFSPhysicalStore": "disk0s2"},
                    {"invalid_key": "corrupted"},  # Malformed store entry
                ],
            }),
            ("info", "-plist", "disk0s2"): _make_plist_bytes({
                "DeviceIdentifier": "disk0s2",
                "ParentWholeDisk": "disk0",
            }),
        }

        drive = probe_internal_drive(workspace_path="/", runner=lambda args: responses[tuple(args)])
        self.assertIsNone(drive["size_bytes"])
        self.assertEqual(drive["source"], "unknown")
        self.assertIsNotNone(drive["reason"])
        self.assertEqual(drive["reason"]["code"], "MALFORMED_STORE_ID")
        self.assertFalse(is_internal_drive_eligible(drive))

    def test_positive_integer_capacity_no_bool_or_string_coercion(self) -> None:
        """TotalSize as boolean True or numeric string must be rejected as invalid capacity."""
        # Case A: boolean True
        responses_bool = {
            ("info", "-plist", "disk0"): _make_plist_bytes({
                "DeviceIdentifier": "disk0",
                "WholeDisk": True,
                "Internal": True,
                "BusProtocol": "Apple Fabric",
                "TotalSize": True,  # Python bool is subclass of int!
            }),
        }
        drive_bool = probe_internal_drive(workspace_path="disk0", runner=lambda args: responses_bool[tuple(args)])
        self.assertIsNone(drive_bool["size_bytes"])
        self.assertFalse(is_internal_drive_eligible(drive_bool))

        # Case B: string number
        responses_str = {
            ("info", "-plist", "disk0"): _make_plist_bytes({
                "DeviceIdentifier": "disk0",
                "WholeDisk": True,
                "Internal": True,
                "BusProtocol": "Apple Fabric",
                "TotalSize": "500277792768",
            }),
        }
        drive_str = probe_internal_drive(workspace_path="disk0", runner=lambda args: responses_str[tuple(args)])
        self.assertIsNone(drive_str["size_bytes"])
        self.assertFalse(is_internal_drive_eligible(drive_str))

    def test_external_drive_no_eligibility_claim(self) -> None:
        """External drive (even 1TB) must return internal=False and size_bytes=None."""
        responses = {
            ("info", "-plist", "/Volumes/External"): _make_plist_bytes({
                "DeviceIdentifier": "disk5s1",
                "ParentWholeDisk": "disk5",
                "APFSPhysicalStores": [{"APFSPhysicalStore": "disk4s2"}],
            }),
            ("info", "-plist", "disk4s2"): _make_plist_bytes({
                "DeviceIdentifier": "disk4s2",
                "ParentWholeDisk": "disk4",
                "WholeDisk": False,
            }),
            ("info", "-plist", "disk4"): _make_plist_bytes({
                "DeviceIdentifier": "disk4",
                "ParentWholeDisk": "disk4",
                "WholeDisk": True,
                "Internal": False,
                "VirtualOrPhysical": "Physical",
                "TotalSize": 1_024_209_543_168,
            }),
        }

        drive = probe_internal_drive(workspace_path="/Volumes/External", runner=lambda args: responses[tuple(args)])
        self.assertFalse(drive["internal"])
        self.assertTrue(drive["physical"])
        self.assertIsNone(drive["size_bytes"])
        self.assertEqual(drive["device_id"], "disk4")
        self.assertEqual(drive["workspace_backing_device_id"], "disk4")
        self.assertIsNotNone(drive["reason"])
        self.assertEqual(drive["reason"]["code"], "EXTERNAL_DRIVE")
        self.assertFalse(is_internal_drive_eligible(drive))

    def test_ambiguous_multiple_backing_drives_returns_unknown(self) -> None:
        """Spanned APFS container across multiple whole physical drives returns unknown."""
        responses = {
            ("info", "-plist", "/Volumes/Spanned"): _make_plist_bytes({
                "DeviceIdentifier": "disk10s1",
                "APFSPhysicalStores": [
                    {"APFSPhysicalStore": "disk0s2"},
                    {"APFSPhysicalStore": "disk1s2"},
                ],
            }),
            ("info", "-plist", "disk0s2"): _make_plist_bytes({
                "ParentWholeDisk": "disk0",
                "WholeDisk": False,
            }),
            ("info", "-plist", "disk1s2"): _make_plist_bytes({
                "ParentWholeDisk": "disk1",
                "WholeDisk": False,
            }),
        }

        drive = probe_internal_drive(workspace_path="/Volumes/Spanned", runner=lambda args: responses[tuple(args)])
        self.assertIsNone(drive["internal"])
        self.assertIsNone(drive["physical"])
        self.assertIsNone(drive["size_bytes"])
        self.assertEqual(drive["source"], "unknown")
        self.assertEqual(drive["backing_source"], "unknown")
        self.assertIsNotNone(drive["reason"])
        self.assertEqual(drive["reason"]["code"], "AMBIGUOUS_BACKING_DRIVES")
        self.assertFalse(is_internal_drive_eligible(drive))

    def test_virtual_drive_image_returns_unknown(self) -> None:
        """Virtual disk images (DMG) return physical=False, size_bytes=None and are ineligible."""
        responses = {
            ("info", "-plist", "disk15s1"): _make_plist_bytes({
                "DeviceIdentifier": "disk15s1",
                "ParentWholeDisk": "disk15",
                "WholeDisk": False,
            }),
            ("info", "-plist", "disk15"): _make_plist_bytes({
                "DeviceIdentifier": "disk15",
                "WholeDisk": True,
                "VirtualOrPhysical": "Virtual",
                "Internal": False,
                "TotalSize": 594_800_000,
            }),
        }

        drive = probe_internal_drive(workspace_path="disk15s1", runner=lambda args: responses[tuple(args)])
        self.assertFalse(drive["physical"])
        self.assertIsNone(drive["size_bytes"])
        self.assertIsNotNone(drive["reason"])
        self.assertEqual(drive["reason"]["code"], "VIRTUAL_DRIVE")
        self.assertFalse(is_internal_drive_eligible(drive))

    def test_diskutil_failure_returns_unknown(self) -> None:
        """Subprocess errors or diskutil errors safely degrade to unknown."""
        drive = probe_internal_drive(
            workspace_path="/",
            runner=lambda args: (_ for _ in ()).throw(RuntimeError("diskutil error")),
        )
        self.assertIsNone(drive["internal"])
        self.assertIsNone(drive["physical"])
        self.assertIsNone(drive["size_bytes"])
        self.assertEqual(drive["source"], "unknown")
        self.assertEqual(drive["backing_source"], "unknown")
        self.assertIsNotNone(drive["reason"])
        self.assertEqual(drive["reason"]["code"], "PROBE_FAILED")
        self.assertFalse(is_internal_drive_eligible(drive))

    def test_bounded_process_primitive_terminates_on_exceed(self) -> None:
        """run_bounded_process terminates child process and raises when output bound is exceeded."""
        # Script producing 50 KB of stdout
        cmd = [sys.executable, "-c", "import sys; sys.stdout.write('A' * 50000); sys.stdout.flush()"]
        with self.assertRaises(RuntimeError) as ctx:
            run_bounded_process(cmd, timeout=2.0, max_bytes=1000)
        self.assertIn("exceeded bound", str(ctx.exception))

    def test_run_bounded_process_closes_owned_pipes(self) -> None:
        """Owned stdout/stderr pipes close on success and on bound-exceeded failure."""
        real_popen = subprocess.Popen
        created = []

        def spy(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            created.append(proc)
            return proc

        with mock.patch("components.developer_gate_inventory.subprocess.Popen", side_effect=spy):
            code, stdout, _ = run_bounded_process([sys.executable, "-c", "print('ok')"], timeout=5.0)
            self.assertEqual(code, 0)
            self.assertIn(b"ok", stdout)
            self.assertTrue(created[-1].stdout.closed)
            self.assertTrue(created[-1].stderr.closed)

            with self.assertRaises(RuntimeError):
                run_bounded_process(
                    [sys.executable, "-c", "import sys; sys.stdout.write('A' * 50000)"],
                    timeout=5.0,
                    max_bytes=1000,
                )
            self.assertTrue(created[-1].stdout.closed)
            self.assertTrue(created[-1].stderr.closed)

    def test_python_probe_uses_isolated_flags(self) -> None:
        """Python probe must execute with -I -B --version for isolated execution."""
        captured_args: list[str] = []

        def recording_runner(args: list[str], timeout: float) -> tuple[int, str, str]:
            captured_args.extend(args)
            return 0, "Python 3.15.0\n", ""

        res = probe_tool_version("python", executable_path="/opt/bin/python3", runner=recording_runner)
        self.assertTrue(res["available"])
        self.assertEqual(res["version"], "3.15.0")
        self.assertEqual(captured_args, ["/opt/bin/python3", "-I", "-B", "--version"])

    def test_swift_probe_requires_apple_swift_version_not_driver(self) -> None:
        """Swift version extraction must reject swift-driver version and require Apple Swift version."""
        # Output with only swift-driver version must fail version parse
        driver_only = (0, "swift-driver version: 1.115 Apple Swift version: unrecognized\n", "")
        res_fail = probe_tool_version(
            "swift",
            executable_path="/usr/bin/swift",
            runner=lambda args, to: driver_only,
        )
        self.assertFalse(res_fail["available"])
        self.assertIsNone(res_fail["version"])

        # Output with Apple Swift version must succeed
        valid_swift = (0, "swift-driver version: 1.115 Apple Swift version 6.0 (swiftlang-6.0.0.9.10)\n", "")
        res_ok = probe_tool_version(
            "swift",
            executable_path="/usr/bin/swift",
            runner=lambda args, to: valid_swift,
        )
        self.assertTrue(res_ok["available"])
        self.assertEqual(res_ok["version"], "6.0")

    def test_unknown_tool_id_rejected_without_command_run(self) -> None:
        """Unknown or arbitrary tool IDs must be rejected without executing commands."""
        run_called = False

        def spy_runner(args: list[str], timeout: float) -> tuple[int, str, str]:
            nonlocal run_called
            run_called = True
            return 0, "1.0.0\n", ""

        res = probe_tool_version("arbitrary_tool", executable_path="/bin/echo", runner=spy_runner)
        self.assertFalse(res["available"])
        self.assertIsNone(res["version"])
        self.assertIn("Unsupported or unknown", res["reason"])
        self.assertFalse(run_called)

    def test_no_private_paths_or_raw_stderr_leak_in_reasons(self) -> None:
        """Error reasons must be sanitized without raw private paths or arbitrary stderr."""
        # Missing executable path
        res1 = probe_tool_version("node", executable_path="/Users/secret_user/private/bin/node")
        self.assertNotIn("/Users/secret_user", res1["reason"])

        # Non-zero exit with sensitive stderr
        def leak_runner(args: list[str], timeout: float) -> tuple[int, str, str]:
            return 1, "", "sensitive trace with private token 123456"

        res2 = probe_tool_version("go", executable_path="/opt/bin/go", runner=leak_runner)
        self.assertNotIn("sensitive trace", res2["reason"])
        self.assertNotIn("123456", res2["reason"])
        self.assertIn("non-zero status 1", res2["reason"])

    def test_is_internal_drive_eligible_provenance_and_strict_types(self) -> None:
        """Eligible check strictly validates diskutil-apfs-list-plist, type safety and IDs."""
        valid_drive = {
            "device_id": "disk0",
            "internal": True,
            "physical": True,
            "size_bytes": 500_277_792_768,
            "source": "diskutil-info-plist",
            "workspace_backing_device_id": "disk0",
            "backing_source": "diskutil-info-plist",
            "observed_at": "2026-11-08T05:53:33Z",
            "reason": None,
        }

        # Valid baseline
        self.assertTrue(is_internal_drive_eligible(valid_drive, min_bytes=500_000_000_000))

        # Accepts diskutil-apfs-list-plist backing_source
        apfs_list_drive = dict(valid_drive, backing_source="diskutil-apfs-list-plist")
        self.assertTrue(is_internal_drive_eligible(apfs_list_drive, min_bytes=500_000_000_000))

        # Rejects unknown or other backing_source
        unknown_source_drive = dict(valid_drive, backing_source="unknown")
        self.assertFalse(is_internal_drive_eligible(unknown_source_drive, min_bytes=500_000_000_000))

        # Rejects bool min_bytes
        self.assertFalse(is_internal_drive_eligible(valid_drive, min_bytes=True))

        # Rejects negative min_bytes
        self.assertFalse(is_internal_drive_eligible(valid_drive, min_bytes=-1))

        # Rejects bool size_bytes
        bool_size_drive = dict(valid_drive, size_bytes=True)
        self.assertFalse(is_internal_drive_eligible(bool_size_drive, min_bytes=1))

        # Rejects device_id mismatch
        mismatched_drive = dict(valid_drive, workspace_backing_device_id="disk1")
        self.assertFalse(is_internal_drive_eligible(mismatched_drive, min_bytes=500_000_000_000))

        # Rejects non-mapping input
        self.assertFalse(is_internal_drive_eligible(None))  # type: ignore[arg-type]
        self.assertFalse(is_internal_drive_eligible("not a dict"))  # type: ignore[arg-type]

    def test_tool_version_probes_success(self) -> None:
        """Bounded tool version probes extract exact semantic version without shell."""
        tools_output = {
            "python": (0, "Python 3.15.0\n", ""),
            "node": (0, "v26.8.2\n", ""),
            "go": (0, "go version go1.27.2 darwin/arm64\n", ""),
            "rust": (0, "rustc 1.82.0 (f6e511eec 2024-10-15)\n", ""),
            "swift": (0, "Apple Swift version 6.0 (swiftlang-6.0.0.9.10)\n", ""),
            "clt": (0, "27.0\n", ""),
        }

        def mock_tool_runner(args: list[str], timeout: float) -> tuple[int, str, str]:
            exe = args[0]
            if "xcrun" in exe:
                return tools_output["clt"]
            for tid, out in tools_output.items():
                if tid in exe:
                    return out
            return 1, "", "unknown tool"

        executable_paths = {
            "python": "/opt/bin/python3",
            "node": "/opt/bin/node",
            "go": "/opt/bin/go",
            "rust": "/opt/bin/rustc",
            "swift": "/opt/bin/swift",
            "clt": "/usr/bin/xcrun",
        }

        probes = probe_toolchains(
            executable_paths=executable_paths,
            runner=mock_tool_runner,
        )

        expected = {
            "python": "3.15.0",
            "node": "26.8.2",
            "go": "1.27.2",
            "rust": "1.82.0",
            "swift": "6.0",
            "clt": "27.0",
        }
        probe_map = {p["id"]: p for p in probes}
        for tid, exp_ver in expected.items():
            self.assertIn(tid, probe_map)
            self.assertTrue(probe_map[tid]["available"])
            self.assertEqual(probe_map[tid]["version"], exp_ver)
            self.assertIsNone(probe_map[tid]["reason"])

    def test_requested_values_never_installed_facts(self) -> None:
        """Requested tool versions bind the request but are NEVER reported as installed facts."""
        requested_tools = [
            {"id": "python", "version": "3.15.0"},
            {"id": "node", "version": "26.8.2"},
            {"id": "go", "version": "1.27.2"},
        ]

        def mock_tool_runner(args: list[str], timeout: float) -> tuple[int, str, str]:
            if "python" in args[0]:
                return 0, "Python 3.12.0\n", ""
            return 1, "", "not found"

        results = probe_toolchains(
            executable_paths={"python": "/bin/python3"},
            requested_tools=requested_tools,
            runner=mock_tool_runner,
        )

        res_map = {r["id"]: r for r in results}

        # python reports actual probed version 3.12.0, NOT requested 3.15.0
        self.assertTrue(res_map["python"]["available"])
        self.assertEqual(res_map["python"]["version"], "3.12.0")

        # node and go were requested with 26.8.2 and 1.27.2, but must NEVER be reported as facts
        self.assertFalse(res_map["node"]["available"])
        self.assertIsNone(res_map["node"]["version"])
        self.assertNotEqual(res_map["node"]["version"], "26.8.2")

        self.assertFalse(res_map["go"]["available"])
        self.assertIsNone(res_map["go"]["version"])
        self.assertNotEqual(res_map["go"]["version"], "1.27.2")

    def test_wire_r2_physical_drive_schema_conformance(self) -> None:
        """physicalDrive fields match developer-gate-v1-r2.schema.json exactly."""
        drive = probe_internal_drive(
            workspace_path="disk0",
            runner=lambda args: _make_plist_bytes({
                "DeviceIdentifier": "disk0",
                "WholeDisk": True,
                "Internal": True,
                "BusProtocol": "Apple Fabric",
                "TotalSize": 500_277_792_768,
            }),
        )

        expected_wire_r2_fields = [
            "device_id",
            "internal",
            "physical",
            "size_bytes",
            "source",
            "workspace_backing_device_id",
            "backing_source",
            "observed_at",
            "reason",
        ]
        self.assertEqual(sorted(drive.keys()), sorted(expected_wire_r2_fields))
        self.assertIn(drive["source"], ["diskutil-info-plist", "unknown"])
        self.assertIn(drive["backing_source"], ["diskutil-info-plist", "diskutil-apfs-list-plist", "unknown"])
        self.assertTrue(drive["observed_at"].endswith("Z"))

        inv = collect_developer_gate_inventory(
            workspace_path="disk0",
            executable_paths={},
            disk_runner=lambda args: _make_plist_bytes({
                "DeviceIdentifier": "disk0",
                "WholeDisk": True,
                "Internal": True,
                "BusProtocol": "Apple Fabric",
                "TotalSize": 500_277_792_768,
            }),
            tool_runner=lambda args, to: (0, "Python 3.15.0\n", ""),
        )
        self.assertIn("internal_physical_drive", inv)
        self.assertIn("toolchains", inv)


class WorkVolumeProbeTests(unittest.TestCase):
    workspace = "/Volumes/Work/developer-gates/workspaces"
    drive = {"device_id": "disk0", "physical": True, "reason": None}

    @staticmethod
    def _runner(args):
        return _make_plist_bytes({
            "DeviceIdentifier": "disk3s5",
            "MountPoint": "/System/Volumes/Data",
        })

    def test_volume_id_runner_reads_the_filesystem_device(self) -> None:
        with mock.patch("components.developer_gate_inventory.run_bounded_process",
                        return_value=(0, b"disk3s5\n", b"")) as run:
            self.assertEqual("disk3s5", _default_volume_id_runner("/work"))
        self.assertEqual(
            ["/usr/bin/stat", "-f", "%Sd", "/work"], list(run.call_args.args[0]))

    def test_exact_shape_uses_statvfs_on_the_resolved_work_volume(self) -> None:
        seen = []

        def statvfs(path):
            seen.append(path)
            return SimpleNamespace(f_frsize=4096, f_bsize=4096, f_blocks=1000, f_bavail=400)

        value = probe_work_volume(
            self.workspace, drive_info=self.drive, statvfs=statvfs,
            volume_id_runner=lambda path: "disk3s5", runner=self._runner)

        self.assertEqual(seen, [self.workspace])
        self.assertEqual(set(value), {
            "path", "mount_point", "volume_id", "device_id", "physical",
            "free_bytes", "total_bytes", "observed_at", "reason"})
        self.assertEqual(value["path"], self.workspace)
        self.assertEqual(value["mount_point"], "/System/Volumes/Data")
        self.assertEqual(value["volume_id"], "disk3s5")
        self.assertEqual(value["device_id"], "disk0")
        self.assertIs(value["physical"], True)
        self.assertEqual(value["free_bytes"], 400 * 4096)
        self.assertEqual(value["total_bytes"], 1000 * 4096)
        self.assertTrue(value["observed_at"].endswith("Z"))
        self.assertIsNone(value["reason"])

    def test_measured_zero_is_zero_not_null(self) -> None:
        value = probe_work_volume(
            self.workspace, drive_info=self.drive, runner=self._runner,
            volume_id_runner=lambda path: "disk3s5",
            statvfs=lambda path: SimpleNamespace(
                f_frsize=4096, f_bsize=4096, f_blocks=0, f_bavail=0))

        self.assertEqual(value["free_bytes"], 0)
        self.assertEqual(value["total_bytes"], 0)
        self.assertIsNone(value["reason"])

    def test_statfs_failure_is_null_with_a_reason_not_zero(self) -> None:
        def statvfs(path):
            raise OSError("statfs unavailable")

        value = probe_work_volume(
            self.workspace, drive_info=self.drive, statvfs=statvfs,
            volume_id_runner=lambda path: "disk3s5", runner=self._runner)

        self.assertIsNone(value["free_bytes"])
        self.assertIsNone(value["total_bytes"])
        self.assertEqual(value["volume_id"], "disk3s5")
        self.assertEqual(value["reason"]["code"], "STATFS_FAILED")
        self.assertIn("OSError", value["reason"]["detail"])

    def test_volume_id_and_backing_unknown_stay_null_with_reasons(self) -> None:
        def volume_id_runner(path):
            raise RuntimeError("stat unavailable")

        def runner(args):
            raise RuntimeError("diskutil unavailable")

        drive = {"device_id": None, "physical": None,
                 "reason": {"code": "PROBE_FAILED", "detail": "backing probe failed"}}
        value = probe_work_volume(
            self.workspace, drive_info=drive, runner=runner,
            volume_id_runner=volume_id_runner,
            statvfs=lambda path: SimpleNamespace(
                f_frsize=4096, f_bsize=4096, f_blocks=10, f_bavail=4))

        self.assertEqual(value["free_bytes"], 4 * 4096)
        self.assertEqual(value["total_bytes"], 10 * 4096)
        self.assertIsNone(value["volume_id"])
        self.assertIsNone(value["mount_point"])
        self.assertIsNone(value["device_id"])
        self.assertIsNone(value["physical"])
        self.assertEqual(value["reason"]["code"], "VOLUME_ID_UNKNOWN")
        self.assertIn("PROBE_FAILED", value["reason"]["detail"])

    def test_mount_point_unknown_keeps_volume_id_and_bytes(self) -> None:
        def runner(args):
            raise RuntimeError("diskutil unavailable")

        value = probe_work_volume(
            self.workspace, drive_info=self.drive, runner=runner,
            volume_id_runner=lambda path: "disk3s5",
            statvfs=lambda path: SimpleNamespace(
                f_frsize=4096, f_bsize=4096, f_blocks=10, f_bavail=4))

        self.assertEqual(value["volume_id"], "disk3s5")
        self.assertIsNone(value["mount_point"])
        self.assertEqual(value["free_bytes"], 4 * 4096)
        self.assertEqual(value["total_bytes"], 10 * 4096)
        self.assertEqual(value["reason"]["code"], "MOUNT_POINT_UNKNOWN")

    def test_known_external_backing_is_false_and_not_a_failure(self) -> None:
        drive = {"device_id": "disk2", "physical": False,
                 "reason": {"code": "EXTERNAL_DRIVE", "detail": "external"}}
        value = probe_work_volume(
            self.workspace, drive_info=drive, runner=self._runner,
            volume_id_runner=lambda path: "disk3s5",
            statvfs=lambda path: SimpleNamespace(
                f_frsize=4096, f_bsize=4096, f_blocks=10, f_bavail=4))

        self.assertIs(value["physical"], False)
        self.assertEqual(value["device_id"], "disk2")
        self.assertIsNone(value["reason"])


class CheapWorkVolumeTests(unittest.TestCase):
    def test_work_volume_measures_bytes_without_the_mount_table(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch(
                    "components.developer_gate_inventory._default_diskutil_runner",
                    side_effect=AssertionError("diskutil must not run")):
                value = probe_work_volume(temporary, probe_backing=False)
        self.assertIsInstance(value["free_bytes"], int)
        self.assertIsInstance(value["total_bytes"], int)
        self.assertIsNone(value["device_id"])
        self.assertIsNone(value["physical"])
        self.assertIsNotNone(value["reason"])
        self.assertIn("BACKING_UNKNOWN", value["reason"]["detail"])

    def test_known_backing_without_mount_probe_reports_the_mount_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            value = probe_work_volume(
                temporary,
                drive_info={"device_id": "disk0", "physical": True},
                probe_backing=False)
        self.assertEqual(value["device_id"], "disk0")
        self.assertIsNone(value["mount_point"])
        self.assertIn("MOUNT_POINT_UNKNOWN", value["reason"]["detail"])


class SharedComponentTests(unittest.TestCase):
    def test_the_inventory_is_discovered_for_the_packaged_tree(self) -> None:
        import importlib.util
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "stage_voice", root / "packaging" / "stage_voice.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        discovered = {path.resolve() for path in
                      module.shared_components_modules(root / "components")}
        self.assertIn((root / "components/developer_gate_inventory.py").resolve(),
                      discovered)


if __name__ == "__main__":
    unittest.main()
