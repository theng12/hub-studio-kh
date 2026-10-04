"""The kit's Lite Mode model copy (owner 2026-10-04), run for real against scratch folders.

A Lite Mode Mac is set up like any Mac (1 - Install the app, copy models, join) and Lite Mode is
ticked in Studio Hub > Settings. The one kit difference is `2 - Copy the Lite Mode models.command`: the
regular copy with one switch (`STUDIOHUB_WORKSTATION=1`) that keeps it to `components/workstation_models.py`.
The launcher is run against a stand-in for the regular copy command; the helper's filter is exercised
through its real selection with a fixture app. The retired `Set Up a Shop Mac.command` is covered in
`test_yam_display_install.py` (the prepare-kit-extras script).
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "components"))
LITE = ROOT / "copy-lite-models.command"
FAKE_COPY = '#!/bin/sh\necho "workstation=${STUDIOHUB_WORKSTATION:-}" >> "$FAKE_COPY_LOG"\nexit ${FAKE_COPY_STATUS:-0}\n'


class LiteLauncher(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lite-kit-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.kit = self.tmp / "kit"
        self.kit.mkdir()
        shutil.copy(LITE, self.kit / "2 - Copy the Lite Mode models.command")
        (self.kit / "2 - Copy the models.command").write_text(FAKE_COPY)
        self.log = self.tmp / "copy.log"

    def run_launcher(self, status: int = 0) -> subprocess.CompletedProcess:
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.tmp / "home"), "TERM": "dumb",
               "FAKE_COPY_LOG": str(self.log), "FAKE_COPY_STATUS": str(status)}
        return subprocess.run(["/bin/sh", str(self.kit / "2 - Copy the Lite Mode models.command")],
                              input="\n", text=True, capture_output=True, env=env, timeout=60)

    def test_it_runs_the_regular_copy_with_the_lite_switch_and_says_what_is_next(self) -> None:
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.log.read_text().strip(), "workstation=1")
        self.assertIn("This copied only the models Lite Mode uses (about 15 GB).", result.stdout)
        self.assertIn("Next: open Studio Hub > Settings > Lite Mode and tick it.", result.stdout)

    def test_it_does_not_tick_lite_mode_or_change_anything_else_on_the_mac(self) -> None:
        self.run_launcher()
        self.assertFalse((self.tmp / "home").exists(), "the launcher wrote into the home folder")
        text = LITE.read_text()
        self.assertNotIn("workstation.json", text)
        self.assertIn("does NOT tick Lite Mode", text)
        self.assertIn("This copies only the models Lite Mode uses (about 15 GB). Next: open Studio Hub > Settings >\n"
                      "# Lite Mode and tick it.", text)

    def test_a_failed_copy_does_not_tell_the_operator_to_move_on(self) -> None:
        result = self.run_launcher(status=1)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Next: open Studio Hub", result.stdout)
        self.assertIn("run this file again", result.stdout)

    def test_it_finds_the_regular_copy_command_by_its_repo_name_too(self) -> None:
        (self.kit / "2 - Copy the models.command").rename(self.kit / "copy-models.command")
        self.assertEqual(self.run_launcher().returncode, 0)
        self.assertEqual(self.log.read_text().strip(), "workstation=1")

    def test_without_the_regular_copy_command_it_says_so(self) -> None:
        (self.kit / "2 - Copy the models.command").unlink()
        result = self.run_launcher()
        self.assertEqual(result.returncode, 1)
        self.assertIn("I cannot find '2 - Copy the models.command'", result.stdout)

    def test_the_launcher_is_executable_and_the_regular_copy_has_no_lite_switch(self) -> None:
        self.assertTrue(os.access(LITE, os.X_OK))
        self.assertTrue(stat.S_IMODE(LITE.stat().st_mode) & 0o100)
        self.assertNotIn("WORKSTATION", (ROOT / "copy-models.command").read_text())

    def test_the_set_up_a_shop_mac_command_is_gone_from_the_kit_sources(self) -> None:
        self.assertFalse((ROOT / "packaging" / "kit-extras" / "Set Up a Shop Mac.command").exists())
        self.assertNotIn("copy_checked \"$HERE/Set Up a Shop Mac",
                         (ROOT / "packaging" / "kit-extras" / "prepare-kit-extras.sh").read_text())


class CopyFilter(unittest.TestCase):
    def _harness(self, tmp: str, extra: tuple[str, ...]):
        from workstation_models import WORKSTATION_MODELS
        from test_copy_models_for_this_mac import Harness
        kit = Harness(Path(tmp))
        components = kit.kit / kit.APP / "Contents/Resources/components"
        shutil.copy(ROOT / "components" / "workstation_models.py", components / "workstation_models.py")
        allowed = sorted(item for item in WORKSTATION_MODELS if "/" not in item)   # package ids; repo ids carry a "/"
        for package_id in (*allowed, *extra):
            kit.ship(package_id)
        return kit, allowed

    def test_lite_only_copies_exactly_the_allowlist_and_the_regular_copy_is_unchanged(self) -> None:
        from unittest import mock
        extra = ("voxcpm2-8bit", "fish-s2-pro-4bit")
        with tempfile.TemporaryDirectory() as tmp:
            kit, allowed = self._harness(tmp, extra)
            self.assertEqual(len(allowed), 6)
            with mock.patch.dict(os.environ, {"STUDIOHUB_WORKSTATION": "1"}):
                code, output, _prompt = kit.run()
            self.assertEqual(code, 0, output)
            self.assertIn("Lite Mode models only: 2 other model(s) stay on the SSD.", output)
            self.assertEqual(sorted(p.name for p in kit.home.iterdir() if not p.name.startswith(".")), allowed)
            # The next run, as an ordinary Mac (no switch), brings in everything else and leaves the lite ones.
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("STUDIOHUB_WORKSTATION", None)
                code, output, _prompt = kit.run()
            self.assertEqual(code, 0, output)
            self.assertEqual(sorted(p.name for p in kit.home.iterdir() if not p.name.startswith(".")),
                             sorted((*allowed, *extra)))

    def test_lite_only_never_moves_a_model_already_on_the_mac(self) -> None:
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            kit, _allowed = self._harness(tmp, ("voxcpm2-8bit",))
            kit.receive("voxcpm2-8bit")
            with mock.patch.dict(os.environ, {"STUDIOHUB_WORKSTATION": "1"}):
                code, output, _prompt = kit.run()
            self.assertEqual(code, 0, output)
            self.assertTrue((kit.home / "voxcpm2-8bit").is_dir(), "a model this kit still offers must stay")
            self.assertFalse((kit.home / "_trash").exists(), output)


if __name__ == "__main__":
    unittest.main()
