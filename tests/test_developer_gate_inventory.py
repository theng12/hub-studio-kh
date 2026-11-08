"""Focused tests for developer gate inventory helper.

Verifies:
- Physical internal drive topology vs APFS container capacity.
- Wire revision 2 physicalDrive exact schema conformance.
- External / ambiguous / virtual drive eligibility refusal.
- Bounded tool version probes without shell.
- Requested tool versions are never adopted as installed facts.
"""

from __future__ import annotations

import plistlib
import subprocess
import unittest

from components.hub.developer_gate_inventory import (
    collect_developer_gate_inventory,
    is_internal_drive_eligible,
    probe_internal_drive,
    probe_tool_version,
    probe_toolchains,
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

        # Capacity accepted against 500GB / 512GB threshold
        self.assertTrue(is_internal_drive_eligible(drive, min_bytes=500_000_000_000))
        # Container size alone would have failed 500GB floor
        self.assertFalse(494_384_795_648 >= 500_000_000_000)

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
                "TotalSize": 1_024_209_543_168,
            }),
        }

        def mock_runner(args: list[str]) -> bytes:
            return responses[tuple(args)]

        drive = probe_internal_drive(workspace_path="/Volumes/External", runner=mock_runner)
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

        def mock_runner(args: list[str]) -> bytes:
            return responses[tuple(args)]

        drive = probe_internal_drive(workspace_path="/Volumes/Spanned", runner=mock_runner)
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

        def mock_runner(args: list[str]) -> bytes:
            return responses[tuple(args)]

        drive = probe_internal_drive(workspace_path="disk15s1", runner=mock_runner)
        self.assertFalse(drive["physical"])
        self.assertIsNone(drive["size_bytes"])
        self.assertIsNotNone(drive["reason"])
        self.assertEqual(drive["reason"]["code"], "VIRTUAL_DRIVE")
        self.assertFalse(is_internal_drive_eligible(drive))

    def test_diskutil_failure_returns_unknown(self) -> None:
        """Subprocess errors or diskutil errors safely degrade to unknown."""
        def mock_runner(args: list[str]) -> bytes:
            raise RuntimeError("diskutil failure simulated")

        drive = probe_internal_drive(workspace_path="/", runner=mock_runner)
        self.assertIsNone(drive["internal"])
        self.assertIsNone(drive["physical"])
        self.assertIsNone(drive["size_bytes"])
        self.assertEqual(drive["source"], "unknown")
        self.assertEqual(drive["backing_source"], "unknown")
        self.assertIsNotNone(drive["reason"])
        self.assertEqual(drive["reason"]["code"], "PROBE_FAILED")
        self.assertFalse(is_internal_drive_eligible(drive))

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

    def test_unavailable_executable_and_version_refusal(self) -> None:
        """Missing or failing executables produce available=False and reason."""
        # Case 1: executable path omitted
        omitted = probe_tool_version("python", executable_path=None)
        self.assertFalse(omitted["available"])
        self.assertIsNone(omitted["version"])
        self.assertIn("not supplied", omitted["reason"])

        # Case 2: non-existent file with real runner check
        missing = probe_tool_version("node", executable_path="/no/such/executable/node_xyz")
        self.assertFalse(missing["available"])
        self.assertIsNone(missing["version"])
        self.assertIn("not found", missing["reason"])

        # Case 3: execution non-zero exit
        def failing_runner(args: list[str], timeout: float) -> tuple[int, str, str]:
            return 127, "", "command not found"

        failed = probe_tool_version("rust", executable_path="/bin/rustc", runner=failing_runner)
        self.assertFalse(failed["available"])
        self.assertIsNone(failed["version"])
        self.assertIn("code 127", failed["reason"])

        # Case 4: timeout
        def timeout_runner(args: list[str], timeout: float) -> tuple[int, str, str]:
            raise subprocess.TimeoutExpired(cmd=args, timeout=timeout)

        timed_out = probe_tool_version("go", executable_path="/bin/go", runner=timeout_runner)
        self.assertFalse(timed_out["available"])
        self.assertIsNone(timed_out["version"])
        self.assertIn("timed out", timed_out["reason"])

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
                "TotalSize": 500_277_792_768,
            }),
            tool_runner=lambda args, to: (0, "Python 3.15.0\n", ""),
        )
        self.assertIn("internal_physical_drive", inv)
        self.assertIn("toolchains", inv)


if __name__ == "__main__":
    unittest.main()
