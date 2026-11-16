"""The build survives an emptied `_trash` (owner empties the Desktop trash whenever they like).

2026-11-16: an emptied `~/Desktop/_trash` left every app's `_trash` link dangling, and a build step that
needs the folder died with ENOENT - it broke a real DMG build. Anything preserved there is genuinely
unused, so a missing destination is created, never fatal.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packaging"))

import make_dmg  # noqa: E402
import stage_voice  # noqa: E402


class DanglingTrash(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(os.path.realpath(temporary.name))
        self.project = self.root / "project"
        self.project.mkdir()
        self.gone = self.root / "Desktop/_trash/app"          # the emptied, missing target
        (self.project / "_trash").symlink_to(self.gone)

    def test_a_replaced_artifact_is_archived_through_a_dangling_link(self) -> None:
        old = self.project / "dist/Hub.dmg"
        old.parent.mkdir()
        old.write_bytes(b"old artifact")
        assert not self.gone.exists()
        kept = make_dmg.preserve(old, self.project, "superseded")
        assert kept is not None
        self.assertTrue(self.gone.is_dir(), "the missing archive target is created, not fatal")
        self.assertFalse(old.exists())
        self.assertEqual(kept.read_bytes(), b"old artifact")
        self.assertIn(self.gone / "superseded", kept.resolve().parents)

    def test_a_failed_stage_is_archived_through_a_dangling_link(self) -> None:
        stage = self.root / "staged-app"
        stage.mkdir()
        with mock.patch.object(stage_voice, "ROOT", self.project):
            kept = stage_voice.archive_failed_stage(stage)
        self.assertTrue(self.gone.joinpath("failed-stages").is_dir())
        self.assertFalse(stage.exists())
        self.assertTrue(Path(kept).exists())


if __name__ == "__main__":
    unittest.main()
