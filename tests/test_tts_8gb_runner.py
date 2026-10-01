"""The 8 GB text-to-speech runner (`qualification/eight-gb-tts-run.sh`) must name real things and ship.

It is a shell shim around an embedded Python program. A typo in a package id or voice id would make it
refuse on the one Mac it exists for, so the ids are checked against the catalog, the two texts against the
files the 32 GB measurement used, and the kit step against the files it has to carry.
"""
import ast
import contextlib
import csv
import io
import json
import re
import sys
import tempfile
import time
import types
import unittest
import wave
from pathlib import Path
from unittest import mock
import subprocess

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


NOTE = "voice: waiting for memory: needs ~3.2GB, ~3.0GB free"   # the Hub's own admission reason (broker._memory_gate)


def runner():
    """The embedded program as a module namespace, with a clock that does not wait."""
    ns = {"__name__": "tts8"}
    with mock.patch.object(sys, "argv", ["x", str(QUAL), "/nonexistent.app"]):
        exec(compile(program(), "eight-gb-tts-run", "exec"), ns)
    real = time
    ns["time"] = types.SimpleNamespace(time=real.time, sleep=lambda s: real.sleep(0.01), strftime=real.strftime)
    return ns


class MemoryStallTests(unittest.TestCase):
    """A job the Hub holds back for memory must be given up on, recorded with the Hub's numbers, and not block the rest."""

    def test_the_hubs_reason_is_read_for_its_numbers(self):
        ns = runner()
        self.assertEqual(ns["memory_wait"](NOTE), (3.2, 3.0))
        self.assertEqual(ns["memory_wait"]("released 2 idle sibling(s); " + NOTE), (3.2, 3.0))
        self.assertIsNone(ns["memory_wait"]("voice: needs a ~16GB machine; this one has 8GB"))
        self.assertIsNone(ns["memory_wait"](None))

    def fake_hub(self, stalled):
        wav = io.BytesIO()
        with wave.open(wav, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b"\0\0" * 8000)
        log = {"jobs": {}, "posts": [], "deleted": [], "released": 0, "polls": 0}

        def call(method, path, body=None, raw=False, timeout=60):
            if path == "/api/hub/memory/release":
                log["released"] += 1
                return 200, {"results": [{"id": "voice", "result": "released"}]}
            if path == "/api/hub/jobs" and method == "GET":
                return 200, {"batches": []}
            if path == "/api/hub/jobs" and method == "POST":
                bid = f"b{len(log['posts'])}"
                log["posts"].append(body["model"]); log["jobs"][bid] = body["model"]
                return 200, {"batch_id": bid}
            bid = path.split("/")[4]
            if method == "DELETE":
                log["deleted"].append(bid)
                return 200, {"ok": True}
            if path.endswith("/artifact"):
                return 200, wav.getvalue()
            log["polls"] += 1
            if bid in log["deleted"]:
                state = "cancelled"
            elif log["jobs"][bid] in stalled:
                state = "queued" if log["polls"] < 400 else "error"   # never ends by itself; the cap only stops a hung test
            else:
                state = "done"
            return 200, {"governor_note": NOTE if state == "queued" else None, "created_at": 0,
                         "items": [{"state": state, "chunk_total": 1, "error": "the cap" if state == "error" else None}]}
        return call, log

    def test_a_stalled_model_is_recorded_cancelled_skipped_and_the_next_model_still_runs(self):
        ns = runner()
        call, log = self.fake_hub({"mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"})
        models = [m for m in ns["MODELS"] if m["key"] in ("qwen3", "voxcpm2")]
        out = tempfile.mkdtemp()
        ns.update(call=call, MEM_WAIT_S=0.0, QUICK=False, ONLY=None,
                  precheck=lambda: ("0.6.124", models, {"S2": "hello", "S4": "hello"}))
        with mock.patch.dict("os.environ", {"TTS8_OUT": out}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ns["main"](), 0)
        folder = next(Path(out).iterdir())
        rows = list(csv.DictReader(io.StringIO((folder / "results.csv").read_text())))
        qwen = [r["status"] for r in rows if r["model"] == "Qwen3-TTS 0.6B"]
        self.assertEqual(qwen, ["did not start", "skipped", "skipped", "skipped"])
        self.assertEqual([r["status"] for r in rows if r["model"] == "VoxCPM2"], ["completed"] * 4)
        first = rows[0]
        self.assertEqual((first["needed_free_gb"], first["had_free_gb"], first["hub_reason"]), ("3.2", "3.0", NOTE))
        self.assertEqual(log["posts"].count("mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"), 1, "skipped clips were queued anyway")
        self.assertEqual(len(log["deleted"]), 1, "the stalled job was not cancelled")
        self.assertEqual(log["released"], 2, "idle Studios are asked to release before each model")
        summary = (folder / "summary.txt").read_text()
        self.assertIn("DID NOT START", summary)
        self.assertIn("needs 3.2 GB free, had 3 GB", summary)
        self.assertIn("VoxCPM2 4-bit: completed 4/4", summary)


if __name__ == "__main__":
    unittest.main()
