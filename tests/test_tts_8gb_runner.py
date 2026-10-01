"""The 8 GB text-to-speech runner (`qualification/eight-gb-tts-run.sh`) must name real things and ship.

It is a shell shim around an embedded Python program. A typo in a package id or voice id would make it
refuse on the one Mac it exists for, so the ids are checked against the catalog, the two texts against the
files the 32 GB measurement used, and the kit step against the files it has to carry.
"""
import ast
import json
import re
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
QUAL = ROOT / "qualification"
SOURCE = (QUAL / "eight-gb-tts-run.sh").read_text()


def program():
    body = SOURCE.split(": <<'__PYTHON__'\n", 1)[1].split("\n__PYTHON__", 1)[0]
    return body


class RunnerTests(unittest.TestCase):
    def test_the_embedded_program_parses_and_the_shell_around_it_is_valid(self):
        ast.parse(program())
        self.assertEqual(subprocess.run(["bash", "-n", str(QUAL / "eight-gb-tts-run.sh")]).returncode, 0)

    def test_the_three_models_are_the_catalog_ones_in_the_owner_order(self):
        manifests = {m["package_id"]: m for m in (json.loads(p.read_text()) for p in (ROOT / "model-manifests").glob("*.json"))}
        found = re.findall(r'pkg="([^"]+)", repo="([^"]+)"', program())
        self.assertEqual([p for p, _ in found],
                         ["qwen3-tts-12hz-0-6b-base-8bit", "voxcpm2-4bit", "omnivoice-bfloat16"])
        for package_id, repo in found:
            self.assertEqual(manifests[package_id]["model_id"], repo)

    def test_the_voices_and_texts_are_the_ones_the_32gb_run_used(self):
        for voice in ("cd07b21a2ccf", "Chris - Soft and Soothing", "0616aec33b41", "Christian - Slow & Calming"):
            self.assertIn(voice, program())
        self.assertEqual(len((QUAL / "tts-script-S2.txt").read_text().split()), 175)
        self.assertEqual(len((QUAL / "tts-script-S4.txt").read_text().split()), 479)

    def test_the_double_click_wrapper_runs_the_runner_and_is_executable(self):
        wrapper = QUAL / "RUN-8GB-TTS.command"
        self.assertIn("eight-gb-tts-run.sh", wrapper.read_text())
        self.assertTrue(wrapper.stat().st_mode & 0o100 and (QUAL / "eight-gb-tts-run.sh").stat().st_mode & 0o100)

    def test_the_kit_step_carries_all_of_it_and_does_not_prune_it(self):
        prepare = ROOT / ".build" / "prepare-kit.sh"
        if not prepare.is_file():
            self.skipTest("prepare-kit.sh is local to the release Mac")
        text = prepare.read_text().replace("\\\n", " ")
        managed = re.search(r'MANAGED="(.*?)"', text, re.S).group(1).split()
        for name in ("RUN-8GB-TTS.command", "eight-gb-tts-run.sh", "README-8gb-tts.md", "tts-script-S2.txt", "tts-script-S4.txt"):
            self.assertTrue((QUAL / name).is_file(), name)
            self.assertIn(f'"$ROOT/qualification/{name}" "$KIT/Technical/{name}"', text, f"{name} never reaches a drive")
            self.assertIn(name, managed, f"{name} would be moved to _trash by the Technical prune")


if __name__ == "__main__":
    unittest.main()
