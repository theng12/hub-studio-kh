"""The setup ladder on the install screen: SSD, then LAN, then internet.

Pinned here because each is a rule the owner set, not a preference:

1. **The order is fixed.** A package's `sources` starts with `"ssd"` when a mounted kit drive's
   cached check says it holds it, then `"lan"`, then `"internet"`. `source_detail` names the drive
   or the Mac so the screen says `ATLAS`, not `SSD`.
2. **The plan route never stats inside a volume.** `snapshot` lists the volume root and reads the
   cache; an unchecked drive is `checked: false` (unknown), never an empty `has` list.
3. **A cached answer for an unplugged drive is kept and reported `mounted: false`**, and it does
   not claim to be a source: it cannot serve the package right now.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from components.hub.backend import kit_drives
from components.hub.backend import model_install_plan as plan

REV = "0" * 40


def manifest(package_id: str, *, size: int = 10, urls: bool = True) -> dict:
    entry = {"path": "model/weights.bin", "size": size, "sha256": "a" * 64}
    if urls:
        entry["urls"] = [f"https://example.invalid/{package_id}/weights.bin"]
    return {"package_id": package_id, "model_id": package_id, "display_name": package_id,
            "engine": "image-mflux", "revision": REV, "files": [entry]}


def modality_of(_engine: str) -> str:
    return "image"


class LadderTests(unittest.TestCase):
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
        self.manifests = {"pkg-a": manifest("pkg-a"), "pkg-b": manifest("pkg-b")}

    def drive(self, name: str) -> Path:
        drive = self.volumes / f"KH-STUDIO-{name}"
        (drive / "1 Install Apps" / "Hub Studio KH" / "model-library").mkdir(parents=True)
        return drive

    def test_sources_are_ssd_then_lan_then_internet(self) -> None:
        rows = plan.model_rows(manifests=self.manifests, receipts={}, memory_gb=32,
                               modality_of=modality_of, lan_source={"pkg-a": "Mac mini"},
                               ssd_source={"pkg-a": "ATLAS", "pkg-b": "ATLAS"})
        by_id = {row["package_id"]: row for row in rows}
        self.assertEqual(by_id["pkg-a"]["sources"], ["ssd", "lan", "internet"])
        self.assertEqual(by_id["pkg-a"]["source_detail"], {"ssd": "ATLAS", "lan": "Mac mini"})
        self.assertEqual(by_id["pkg-b"]["sources"], ["ssd", "internet"])
        self.assertEqual(by_id["pkg-b"]["source_detail"], {"ssd": "ATLAS"})

    def test_a_mounted_drive_with_no_check_is_unknown_not_empty(self) -> None:
        drive = self.drive("ATLAS")
        screen = plan.snapshot(manifests=self.manifests, receipts={}, library=self.base / "models",
                               memory_gb=32, stop_bytes=1, modality_of=modality_of,
                               volumes_root=self.volumes)
        self.assertEqual(screen["drives"],
                         [{"name": "ATLAS", "path": str(drive), "mounted": True, "checked": False}])
        self.assertNotIn("has", screen["drives"][0])
        self.assertIsNone(screen["drives_checked_at"])
        # Unknown is never a claim: the SSD does not appear as a source until it has been checked.
        self.assertTrue(all(row["sources"][0] != "ssd" for row in screen["models"]))

    def test_a_checked_drive_comes_first_and_is_named(self) -> None:
        drive = self.drive("ATLAS")
        kit_drives._write_cache({str(drive): {"checked_at": 1763500000.0, "has": ["pkg-a"]}})
        screen = plan.snapshot(manifests=self.manifests, receipts={}, library=self.base / "models",
                               memory_gb=32, stop_bytes=1, modality_of=modality_of,
                               volumes_root=self.volumes)
        self.assertEqual(screen["drives_checked_at"], 1763500000.0)
        self.assertTrue(screen["drives"][0]["checked"])
        self.assertEqual(screen["drives"][0]["has"], ["pkg-a"])
        by_id = {row["package_id"]: row for row in screen["models"]}
        self.assertEqual(by_id["pkg-a"]["sources"], ["ssd", "internet"])
        self.assertEqual(by_id["pkg-a"]["source_detail"]["ssd"], "ATLAS")

    def test_an_unplugged_drives_cached_answer_is_kept_but_claims_no_source(self) -> None:
        gone = self.volumes / "KH-STUDIO-ATLAS"
        kit_drives._write_cache({str(gone): {"checked_at": 1763500000.0, "has": ["pkg-a"]}})
        screen = plan.snapshot(manifests=self.manifests, receipts={}, library=self.base / "models",
                               memory_gb=32, stop_bytes=1, modality_of=modality_of,
                               volumes_root=self.volumes)
        self.assertEqual(screen["drives"][0]["mounted"], False)
        self.assertTrue(screen["drives"][0]["checked"])
        by_id = {row["package_id"]: row for row in screen["models"]}
        self.assertNotIn("ssd", by_id["pkg-a"]["sources"])

    def test_the_plan_route_only_lists_the_volume_root(self) -> None:
        drive = self.drive("ATLAS")
        real_stat, real_scandir = os.stat, os.scandir
        touched: list[str] = []

        def guarded_stat(path, *args, **kwargs):
            if str(path).startswith(str(self.volumes)):
                touched.append(f"stat {path}")
                raise AssertionError(f"stat inside a volume: {path}")
            return real_stat(path, *args, **kwargs)

        def guarded_scandir(path=".", *args, **kwargs):
            if str(path).startswith(str(self.volumes)):
                touched.append(f"scandir {path}")
                raise AssertionError(f"scandir inside a volume: {path}")
            return real_scandir(path, *args, **kwargs)

        with mock.patch("os.stat", side_effect=guarded_stat), \
                mock.patch("os.scandir", side_effect=guarded_scandir):
            screen = plan.snapshot(manifests=self.manifests, receipts={}, library=self.base / "models",
                                   memory_gb=32, stop_bytes=1, modality_of=modality_of,
                                   volumes_root=self.volumes)
        self.assertEqual(touched, [])
        self.assertEqual([row["name"] for row in screen["drives"]], ["ATLAS"])
        self.assertTrue(drive.is_dir())


if __name__ == "__main__":
    unittest.main()
