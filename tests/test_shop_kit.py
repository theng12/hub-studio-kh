"""The Shops kit commands (workstation mode, owner 2026-10-03), run for real against scratch folders.

`STUDIOHUB_HUB_DATA` stands in for the Hub's state folder and `STUDIOHUB_UPDATE_CMD` for
`Update this Mac.command` (a stand-in that records how it was called), so nothing touches this Mac's
own app, models or settings. The copy helper's Shop Mac filter is exercised through its real
selection with a fixture app.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTRAS = ROOT / "packaging" / "kit-extras"
SHOP = "Set Up a Shop Mac.command"
CONTROLLER = "Set Up the Shops Controller.command"
FAKE_UPDATE = '#!/bin/sh\necho "workstation=${STUDIOHUB_WORKSTATION:-}" >> "$FAKE_UPDATE_LOG"\nexit 0\n'


class ShopKit(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="shop-kit-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = self.tmp / "Support" / "hub"
        self.update = self.tmp / "update.command"
        self.update.write_text(FAKE_UPDATE)
        self.log = self.tmp / "update.log"

    def run_command(self, name: str) -> subprocess.CompletedProcess:
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.tmp), "TERM": "dumb",
               "STUDIOHUB_HUB_DATA": str(self.data), "STUDIOHUB_UPDATE_CMD": str(self.update),
               "FAKE_UPDATE_LOG": str(self.log), "STUDIOHUB_PGREP": "/usr/bin/false"}
        return subprocess.run(["/bin/sh", str(EXTRAS / name)], input="\n", text=True,
                              capture_output=True, env=env, timeout=60)

    def test_a_shop_mac_gets_workstation_mode_and_only_the_workstation_models(self) -> None:
        result = self.run_command(SHOP)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads((self.data / "workstation.json").read_text()), {"enabled": True})
        self.assertEqual(stat.S_IMODE(self.data.stat().st_mode), 0o700, "the Hub refuses a readable state dir")
        self.assertEqual(self.log.read_text().strip(), "workstation=1")
        self.assertIn("Join", result.stdout)

    def test_running_it_again_keeps_settings_changed_from_the_gateway(self) -> None:
        self.data.mkdir(parents=True, mode=0o700)
        (self.data / "workstation.json").write_text('{"enabled": true, "step_aside": true}')
        self.run_command(SHOP)
        self.assertTrue(json.loads((self.data / "workstation.json").read_text())["step_aside"])

    def test_the_controller_pauses_its_own_generation_and_keeps_other_flags(self) -> None:
        result = self.run_command(CONTROLLER)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads((self.data / "machine_flags.json").read_text()), {"local": {"enabled": False}})
        self.assertIn("PAUSED", result.stdout)
        (self.data / "machine_flags.json").write_text(json.dumps(
            {"local": {"enabled": True, "studios": {"image": {"enabled": False}}}, "node-a": {"enabled": True}}))
        self.run_command(CONTROLLER)
        flags = json.loads((self.data / "machine_flags.json").read_text())
        self.assertEqual(flags, {"local": {"enabled": False, "studios": {"image": {"enabled": False}}},
                                 "node-a": {"enabled": True}})
        (self.data / "machine_flags.json").write_text(json.dumps({"node-a": {"enabled": True}}))
        self.run_command(CONTROLLER)
        self.assertFalse(json.loads((self.data / "machine_flags.json").read_text())["local"]["enabled"])

    def test_the_extras_prepare_script_ships_both_commands(self) -> None:
        text = (EXTRAS / "prepare-kit-extras.sh").read_text()
        for name in (SHOP, CONTROLLER):
            self.assertIn(name, text)
            self.assertTrue(os.access(EXTRAS / name, os.X_OK), name)


class CopyFilter(unittest.TestCase):
    def test_a_shop_mac_copies_only_the_workstation_models_end_to_end(self) -> None:
        from unittest import mock
        from test_copy_models_for_this_mac import Harness
        with tempfile.TemporaryDirectory() as tmp:
            kit = Harness(Path(tmp))
            components = kit.kit / kit.APP / "Contents/Resources/components"
            shutil.copy(ROOT / "components" / "workstation_models.py", components / "workstation_models.py")
            kit.ship("voxcpm2-4bit")
            kit.ship("voxcpm2-8bit")
            with mock.patch.dict(os.environ, {"STUDIOHUB_WORKSTATION": "1"}):
                code, output, _prompt = kit.run()
            self.assertEqual(code, 0, output)
            self.assertIn("Shop Mac: only the workstation models are copied; 1 other", output)
            self.assertTrue((kit.home / "voxcpm2-4bit").is_dir(), output)
            self.assertFalse((kit.home / "voxcpm2-8bit").exists(), "not a workstation model")
            code, output, _prompt = kit.run()
            self.assertTrue((kit.home / "voxcpm2-8bit").is_dir(), "an ordinary Mac still gets it")


if __name__ == "__main__":
    unittest.main()
