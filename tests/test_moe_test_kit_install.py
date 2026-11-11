"""Offline integrity/recoverability check; fixtures are archived, never deleted."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("moe_kit", ROOT / "qualification/install_moe_test_kit.py")
kit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(kit)


class TestKitInstall(unittest.TestCase):
    def test_copy_verify_repeat_and_recover(self):
        fixture = Path(tempfile.mkdtemp(prefix="moe kit check ")).resolve()
        try:
            technical = fixture / "SSD Kit With Spaces/Technical"
            technical.mkdir(parents=True)
            unrelated = technical / "owner-notes.txt"
            unrelated.write_text("keep my notes")
            first = kit.install(technical)
            self.assertEqual(10, len(first["files"]))
            self.assertFalse(first["inference_run"])
            for row in first["files"]:
                self.assertEqual(row["sha256"], kit.digest(technical / row["file"]))
            self.assertEqual(first, kit.install(technical))
            changed = technical / "RUN-NEMOTRON-LLM.command"
            changed.write_text("owner's older launcher")
            kit.install(technical)
            backups = list((technical / "_trash").rglob("RUN-NEMOTRON-LLM.command"))
            self.assertEqual(1, len(backups))
            self.assertEqual("owner's older launcher", backups[0].read_text())
            self.assertEqual("keep my notes", unrelated.read_text())
            self.assertTrue(changed.stat().st_mode & 0o111)
            self.assertEqual(first, json.loads((technical / "moe-test-kit.json").read_text()))
            # Refuse a destination link before moving or changing any payload.
            linked = fixture / "linked/Technical"
            linked.parent.mkdir()
            linked.symlink_to(technical, target_is_directory=True)
            with self.assertRaises(ValueError):
                kit.install(linked)
        finally:
            archive = ROOT / "_trash/tests" / ("moe-kit-install-" + uuid.uuid4().hex)
            archive.parent.mkdir(parents=True, exist_ok=True)
            # TMPDIR and the checkout can be different volumes; rename() cannot cross them.
            shutil.move(str(fixture), str(archive))
            print("Fixture preserved:", archive)
