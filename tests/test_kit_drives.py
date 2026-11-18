"""Kit-drive detection that cannot freeze the Hub, and a copy that only reads the drive.

**The defect this pins:** a stat inside a mounted volume can raise the macOS permission prompt,
and an unanswered prompt once held Hub routes until the Mac looked frozen (commit 26d086a7). So:

1. Listing the volume root is free; anything inside a volume is read only by an explicit
   drive-check or drive copy, in a child process with a hard timeout.
2. A refusal is `NEEDS_PERMISSION`, never an empty `has` list - unknown is not "does not have".
3. A checked drive is cached (0600) and re-read only when its kit folder changed.
4. A drive copy runs the same run machinery, records `source: "ssd:ATLAS"`, and uses no network.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from components import model_packages
from components.hub import model_downloads
from components.hub.backend import kit_drives

ROOT = Path(__file__).resolve().parents[1]
REV = "0" * 40
LIBRARY_RELATIVE = ("1 Install Apps", "Hub Studio KH", "model-library")


def manifest(package_id: str, *, size: int = 3) -> dict:
    return {"package_id": package_id, "revision": REV,
            "files": [{"path": "model/weights.bin", "size": size}]}


class DriveFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.volumes = self.base / "Volumes"
        self.volumes.mkdir()
        self.data = self.base / "data"
        self.data.mkdir()
        env = mock.patch.dict(os.environ, {"STUDIOHUB_DATA_DIR": str(self.data)})
        env.start()
        self.addCleanup(env.stop)

    def drive(self, name: str = "ATLAS") -> Path:
        drive = self.volumes / f"KH-STUDIO-{name}"
        drive.mkdir(parents=True, exist_ok=True)
        return drive

    def put(self, drive: Path, package_id: str, *, payload: bytes = b"abc") -> Path:
        folder = drive.joinpath(*LIBRARY_RELATIVE) / package_id / REV / "model"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "weights.bin"
        path.write_bytes(payload)
        return path


class ReportTests(DriveFixture):
    def test_only_kit_volumes_are_named_and_unknown_is_unknown(self) -> None:
        self.drive("ATLAS")
        (self.volumes / "Other Drive").mkdir()
        entries, checked_at = kit_drives.report(self.volumes)
        self.assertEqual([row["name"] for row in entries], ["ATLAS"])
        self.assertEqual(entries[0]["path"], str(self.drive("ATLAS")))
        self.assertFalse(entries[0]["checked"])
        self.assertNotIn("has", entries[0])
        self.assertIsNone(checked_at)

    def test_report_never_stats_inside_a_volume(self) -> None:
        drive = self.drive("ATLAS")
        real_stat = os.stat

        def guarded(path, *args, **kwargs):
            if str(path).startswith(str(self.volumes)):
                raise AssertionError(f"stat inside a volume: {path}")
            return real_stat(path, *args, **kwargs)

        with mock.patch("os.stat", side_effect=guarded):
            entries, _ = kit_drives.report(self.volumes)
        self.assertEqual(entries[0]["path"], str(drive))

    def test_volumes_root_is_injectable_by_environment(self) -> None:
        with mock.patch.dict(os.environ, {"STUDIOHUB_VOLUMES_DIR": str(self.volumes)}):
            self.assertEqual(kit_drives.volumes_root(), self.volumes)


class CheckTests(DriveFixture):
    def test_check_reads_a_mounted_drive_and_caches_it_owner_only(self) -> None:
        drive = self.drive("ATLAS")
        self.put(drive, "pkg-a")
        answer = kit_drives.check({"pkg-a": manifest("pkg-a")}, self.volumes)
        self.assertEqual(answer["drives"][0]["checked"], True)
        self.assertEqual(answer["drives"][0]["has"], ["pkg-a"])
        self.assertIsNotNone(answer["drives_checked_at"])
        cache = self.data / "hub" / "kit-drives.json"
        self.assertEqual(cache.stat().st_mode & 0o777, 0o600)
        stored = json.loads(cache.read_text())
        self.assertEqual(stored[str(drive)]["has"], ["pkg-a"])

    def test_a_missing_or_truncated_file_is_not_a_package_the_drive_has(self) -> None:
        drive = self.drive("ATLAS")
        self.put(drive, "pkg-a")
        self.put(drive, "pkg-b", payload=b"toolong")
        answer = kit_drives.check({"pkg-a": manifest("pkg-a"), "pkg-b": manifest("pkg-b")},
                                  self.volumes)
        self.assertEqual(answer["drives"][0]["has"], ["pkg-a"])

    def test_a_changed_kit_folder_is_read_again(self) -> None:
        drive = self.drive("ATLAS")
        self.put(drive, "pkg-a")
        kit_drives.check({"pkg-a": manifest("pkg-a")}, self.volumes)
        cache = self.data / "hub" / "kit-drives.json"
        stored = json.loads(cache.read_text())
        stored[str(drive)]["checked_at"] = 0.0            # older than every mtime: stale
        cache.write_text(json.dumps(stored))
        self.put(drive, "pkg-b")
        answer = kit_drives.check({"pkg-a": manifest("pkg-a"), "pkg-b": manifest("pkg-b")},
                                  self.volumes)
        self.assertEqual(answer["drives"][0]["has"], ["pkg-a", "pkg-b"])

    def test_a_refusal_is_needs_permission_and_caches_nothing(self) -> None:
        self.drive("ATLAS")
        output = io.StringIO()
        with mock.patch.object(kit_drives.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired(["reader"], 10)), \
                contextlib.redirect_stdout(output):
            with self.assertRaises(kit_drives.DriveRefused) as caught:
                kit_drives.check({"pkg-a": manifest("pkg-a")}, self.volumes)
        self.assertEqual(caught.exception.code, "NEEDS_PERMISSION")
        self.assertEqual(caught.exception.detail,
                         "macOS needs your permission to read ATLAS once.")
        self.assertFalse((self.data / "hub" / "kit-drives.json").exists())
        lines = [line for line in output.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, "one log line")
        self.assertIn("ATLAS", lines[0])

    def test_an_unplugged_drive_keeps_its_answer_and_says_so(self) -> None:
        drive = self.drive("ATLAS")
        self.put(drive, "pkg-a")
        kit_drives.check({"pkg-a": manifest("pkg-a")}, self.volumes)
        drive.rename(self.volumes / "Elsewhere")
        entries, _ = kit_drives.report(self.volumes)
        self.assertEqual([row["name"] for row in entries], ["ATLAS"])
        self.assertFalse(entries[0]["mounted"])
        self.assertTrue(entries[0]["checked"])
        self.assertEqual(entries[0]["has"], ["pkg-a"])


class ResolveTests(DriveFixture):
    def test_a_drive_resolves_by_name_and_only_when_plugged_in(self) -> None:
        drive = self.drive("ATLAS")
        self.assertEqual(kit_drives.resolve("ATLAS", self.volumes), drive)
        self.assertEqual(kit_drives.resolve("KH-STUDIO-ATLAS", self.volumes), drive)
        with self.assertRaises(kit_drives.DriveRefused) as caught:
            kit_drives.resolve("NOVA", self.volumes)
        self.assertEqual(caught.exception.code, "DRIVE_LACKS_PACKAGE")

    def test_package_presence_is_read_not_assumed(self) -> None:
        drive = self.drive("ATLAS")
        self.put(drive, "pkg-a")
        self.assertTrue(kit_drives.has_package(drive, manifest("pkg-a")))
        self.assertFalse(kit_drives.has_package(drive, manifest("pkg-b")))

    def test_a_permission_refusal_while_checking_one_package_is_needs_permission(self) -> None:
        drive = self.drive("ATLAS")
        with mock.patch.object(kit_drives.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired(["reader"], 10)), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(kit_drives.DriveRefused) as caught:
                kit_drives.has_package(drive, manifest("pkg-a"))
        self.assertEqual(caught.exception.code, "NEEDS_PERMISSION")


class DriveRunTests(DriveFixture):
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(model_downloads._processes.clear)
        self.manifest = json.loads((ROOT / "model-manifests" / "bge-small-en-v1-5-bf16.json")
                                   .read_text(encoding="utf-8"))

    def test_a_drive_run_records_its_source_and_asks_the_child_for_no_network(self) -> None:
        drive = self.drive("ATLAS")
        library = drive.joinpath(*LIBRARY_RELATIVE)
        package_id = self.manifest["package_id"]
        calls: list[list[str]] = []

        class FakeProcess:
            pid = 4242

            def poll(self) -> int:
                return 0

        def fake_popen(command, **_kwargs):
            calls.append(command)
            return FakeProcess()

        with mock.patch.object(model_downloads, "_free_space_check"), \
                mock.patch.object(model_downloads.subprocess, "Popen", side_effect=fake_popen):
            run = model_downloads.start(package_id=package_id, manifest=self.manifest,
                                        catalog={package_id: self.manifest},
                                        library=self.base / "models", sources=None,
                                        drive=library)
        self.assertEqual(run["source"], "ssd:ATLAS")
        command = calls[0]
        self.assertEqual(command[command.index("--drive") + 1], str(library))
        self.assertFalse(any(word == "--peer" or word == "--sources" for word in command))

    def test_the_copy_verifies_each_file_and_names_the_one_that_is_wrong(self) -> None:
        staging = self.base / "staging"
        staging.mkdir()
        drive = self.drive("ATLAS")
        entry = {"path": "model/weights.bin", "size": 3,
                 "sha256": model_packages.hashlib.sha256(b"abc").hexdigest()}
        folder = drive.joinpath(*LIBRARY_RELATIVE) / "pkg-a" / REV
        (folder / "model").mkdir(parents=True)
        (folder / "model" / "weights.bin").write_bytes(b"abc")
        model_packages._copy_from_drive(entry, staging, staging, folder)
        self.assertEqual((staging / "model" / "weights.bin").read_bytes(), b"abc")

        (folder / "model" / "weights.bin").write_bytes(b"xyz")
        damaged = self.base / "damaged"
        damaged.mkdir()
        with self.assertRaises(model_packages.PackageError) as caught:
            model_packages._copy_from_drive(entry, damaged, damaged, folder)
        self.assertIn("model/weights.bin", str(caught.exception))

        (folder / "model" / "weights.bin").unlink()
        absent = self.base / "absent"
        absent.mkdir()
        with self.assertRaises(model_packages.PackageError) as caught:
            model_packages._copy_from_drive(entry, absent, absent, folder)
        self.assertIn("model/weights.bin", str(caught.exception))

    def test_a_resume_keeps_the_files_it_already_staged(self) -> None:
        staging = self.base / "staging"
        (staging / "model").mkdir(parents=True)
        (staging / "model" / "weights.bin").write_bytes(b"abc")
        entry = {"path": "model/weights.bin", "size": 3,
                 "sha256": model_packages.hashlib.sha256(b"abc").hexdigest()}
        # The drive is gone entirely: a staged good file is still accepted, not copied again.
        model_packages._copy_from_drive(entry, staging, staging, self.base / "missing")
        self.assertEqual((staging / "model" / "weights.bin").read_bytes(), b"abc")


if __name__ == "__main__":
    unittest.main()
