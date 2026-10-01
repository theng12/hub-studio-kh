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
    ready = Path(tempfile.mkdtemp()) / "hub-ready.json"   # the guard's restore file lives beside it: never the real one
    with mock.patch.object(sys, "argv", ["x", str(QUAL), "/nonexistent.app"]), \
            mock.patch.dict("os.environ", {"TTS8_READY_FILE": str(ready)}):
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

    def fake_hub(self, stalled, admission=False, was=None, crash_while_relaxed=False, restore_file=None):
        """A Hub whose `stalled` models stay queued behind the memory guard until the operator override lowers it.
        `admission=False` is a Hub older than 0.6.125 (no override route); `was` is an override already in force."""
        wav = io.BytesIO()
        with wave.open(wav, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b"\0\0" * 8000)
        log = {"jobs": {}, "posts": [], "deleted": [], "released": 0, "polls": 0, "puts": [], "running": {}, "override_at_post": [], "file_at_lowering": None,
               "override": dict(was) if was else None}

        def call(method, path, body=None, raw=False, timeout=60):
            if path.startswith("/api/hub/memory/admission"):
                if not admission:
                    return 404, "not found"
                if method == "GET":
                    o = log["override"]
                    return 200, {"overridden": bool(o), "effective_min_free_memory_gb": o["free"] if o else 3.2,
                                 "effective_min_total_memory_gb": o["total"] if o else 8.0}
                log["puts"].append((body["model"], body["min_free_memory_gb"], body.get("min_total_memory_gb")))
                if body["min_free_memory_gb"] is not None and restore_file:   # what a crash right now would leave behind
                    log["file_at_lowering"] = json.loads(Path(restore_file).read_text())
                log["override"] = ({"free": body["min_free_memory_gb"], "total": body["min_total_memory_gb"]}
                                   if body["min_free_memory_gb"] is not None else None)
                return 200, {}
            if path == "/api/hub/memory/release":
                log["released"] += 1
                return 200, {"results": [{"id": "voice", "result": "released"}]}
            if path == "/api/hub/jobs" and method == "GET":
                return 200, {"batches": []}
            if path == "/api/hub/jobs" and method == "POST":
                bid = f"b{len(log['posts'])}"
                log["posts"].append(body["model"]); log["jobs"][bid] = body["model"]
                log["override_at_post"].append((body["model"], log["override"]))
                return 200, {"batch_id": bid}
            bid = path.split("/")[4]
            if method == "DELETE":
                log["deleted"].append(bid)
                return 200, {"ok": True}
            if path.endswith("/artifact"):
                return 200, wav.getvalue()
            log["polls"] += 1
            if crash_while_relaxed and log["override"] and log["override"]["free"] <= 1.0 and bid not in log["deleted"]:
                raise KeyboardInterrupt   # the owner presses Ctrl-C (or closes the window) mid-measurement
            if bid in log["deleted"]:
                state = "cancelled"
            elif log["jobs"][bid] in stalled and not (log["override"] and log["override"]["free"] is not None
                                                      and log["override"]["free"] <= 1.0):
                state = "queued" if log["polls"] < 400 else "error"   # never ends by itself; the cap only stops a hung test
            elif log["jobs"][bid] in stalled:   # runs under the lowered guard: takes a while, so the sampler can watch it
                log["running"][bid] = log["running"].get(bid, 0) + 1
                state = "running" if log["running"][bid] < 40 else "done"
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


QWEN = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"


class MeasureAnywayTests(unittest.TestCase):
    """A model the default guard holds back is measured with the guard lowered, safely, and the guard is always put back."""

    def run_main(self, hub_kwargs, swap=None, models=("qwen3", "voxcpm2")):
        ns = runner()
        call, log = MemoryStallTests.fake_hub(self, {QWEN}, restore_file=ns["RESTORE"], **hub_kwargs)
        chosen = [m for m in ns["MODELS"] if m["key"] in models]
        out = tempfile.mkdtemp()
        ns.update(call=call, MEM_WAIT_S=0.0, QUICK=False, ONLY=None, voice_pids=lambda rows: [1], pressure_level=lambda: 1,
                  footprint=lambda pid: (2.35e9, 2.6e9),
                  swap_gb=(lambda: swap(log)) if swap else (lambda: 0.0),
                  precheck=lambda: ("0.6.125", chosen, {"S2": "hello", "S4": "hello"}))
        status = "not run"
        with mock.patch.dict("os.environ", {"TTS8_OUT": out}), contextlib.redirect_stdout(io.StringIO()):
            try:
                status = ns["main"]()
            except KeyboardInterrupt:
                status = "interrupted"
        folder = next(Path(out).iterdir())
        rows = list(csv.DictReader(io.StringIO((folder / "results.csv").read_text())))
        return ns, log, rows, (folder / "summary.txt").read_text(), status

    def test_a_held_model_is_measured_with_the_guard_lowered_and_the_guard_is_put_back(self):
        ns, log, rows, summary, status = self.run_main({"admission": True})
        self.assertEqual(status, 0)
        qwen = [r for r in rows if r["model"] == "Qwen3-TTS 0.6B"]
        self.assertEqual([r["status"] for r in qwen], ["did not start"] + ["completed"] * 4)
        self.assertEqual([r["guard_relaxed"] for r in qwen], ["no"] + ["yes"] * 4)
        self.assertEqual(qwen[1]["override_used_gb"], "0.5")
        self.assertEqual(qwen[1]["measured_peak_gb"], "2.35")
        self.assertEqual(qwen[1]["verdict"], "fits only with relaxed guard - real need about 2.6 GB")
        self.assertEqual(qwen[1]["hub_free_floor_gb"], "3.2")
        vox = [r for r in rows if r["model"] == "VoxCPM2"]
        self.assertEqual({r["guard_relaxed"] for r in vox}, {"no"})
        self.assertEqual({r["verdict"] for r in vox}, {"fits at default guard"})
        # set once for the held model, put back (cleared: there was no override) before the next model; nothing else touched
        self.assertEqual(log["puts"], [(QWEN, 0.5, 8.0), (QWEN, None, None)])
        self.assertIsNone(log["override"])
        self.assertEqual(log["file_at_lowering"], {QWEN: None}, "what to put back must be saved before the Hub is changed")
        vox_posts = [o for m, o in log["override_at_post"] if "VoxCPM2" in m]
        self.assertTrue(vox_posts and all(o is None for o in vox_posts), "the next model ran with the guard still lowered")
        self.assertIn("FITS ONLY WITH THE GUARD RELAXED", summary)
        self.assertIn("Recommended free-memory requirement: 2.6 GB (worker peak 2.35 GB + 0.25 GB margin; the Hub's default is 3.2 GB)", summary)
        self.assertEqual(json.loads(ns["RESTORE"].read_text()), {})

    def test_an_operator_override_that_was_already_there_is_restored_exactly(self):
        ns, log, rows, summary, status = self.run_main({"admission": True, "was": {"free": 4.0, "total": 8.0}})
        self.assertEqual(log["puts"], [(QWEN, 0.5, 8.0), (QWEN, 4.0, 8.0)])
        self.assertEqual(log["override"], {"free": 4.0, "total": 8.0})

    def test_a_clip_that_swaps_hard_is_cancelled_recorded_and_the_next_model_still_runs(self):
        grow = {"n": 0}
        def swap(log):   # swap rises 0.5 GB per reading once the guard is lowered
            if log["override"]:
                grow["n"] += 1
                return 0.5 * grow["n"]
            return 0.0
        ns, log, rows, summary, status = self.run_main({"admission": True}, swap=swap)
        qwen = [r for r in rows if r["model"] == "Qwen3-TTS 0.6B"]
        self.assertEqual([r["status"] for r in qwen], ["did not start", "stopped for safety", "skipped", "skipped", "skipped"])
        self.assertTrue(qwen[1]["error"].startswith("needs more than this Mac can give: peak 2.4 GB, swap +"), qwen[1]["error"])
        self.assertEqual(qwen[1]["verdict"], "does not fit - needs more than this Mac can give")
        self.assertEqual([r["status"] for r in rows if r["model"] == "VoxCPM2"], ["completed"] * 4)
        self.assertEqual(len(log["deleted"]), 2, "the held job and the unsafe job are both cancelled")
        self.assertIsNone(log["override"], "the guard must be put back")
        self.assertIn("DOES NOT FIT: needs more than this Mac can give", summary)

    def test_ctrl_c_in_the_middle_of_a_measurement_puts_the_guard_back(self):
        ns, log, rows, summary, status = self.run_main({"admission": True, "crash_while_relaxed": True}, models=("qwen3",))
        self.assertIsNone(log["override"])
        self.assertEqual(log["puts"][-1], (QWEN, None, None))

    def test_a_guard_left_lowered_by_a_run_that_died_is_put_back_at_the_next_start(self):
        ns = runner()
        call, log = MemoryStallTests.fake_hub(self, set(), admission=True, was={"free": 0.5, "total": 8.0})
        ns["call"] = call
        ns["RESTORE"].write_text(json.dumps({QWEN: {"free": 4.0, "total": 8.0}}))
        with contextlib.redirect_stdout(io.StringIO()):
            ns["guard"].restore_stale()
        self.assertEqual(log["override"], {"free": 4.0, "total": 8.0})
        self.assertEqual(json.loads(ns["RESTORE"].read_text()), {})

    def test_an_older_hub_without_the_override_route_still_skips_the_model_as_before(self):
        ns, log, rows, summary, status = self.run_main({"admission": False})
        self.assertEqual([r["status"] for r in rows if r["model"] == "Qwen3-TTS 0.6B"], ["did not start", "skipped", "skipped", "skipped"])
        self.assertEqual(log["puts"], [])

    def test_the_runners_free_figure_is_the_hubs(self):
        """The runner cannot import the app's code, so it mirrors components/host_memory.py; same recorded 32 GB Mac numbers."""
        sys.path.insert(0, str(ROOT))
        from components import host_memory
        vm_stat = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                   134096.
Pages active:                                 635769.
Pages inactive:                               953112.
Pages speculative:                             82107.
Pages wired down:                             168087.
Pages purgeable:                                5127.
File-backed pages:                           1149875.
Pages occupied by compressor:                  87916.
"""
        sysctls = {"vm.page_free_count": 133732, "vm.page_pageable_external_count": 1149866, "vm.page_purgeable_count": 5127,
                   "vm.compressor_bytes_used": 1229651968, "kern.vm_page_free_target": 4000}
        ns = runner()
        ns["MEM_BYTES"] = 34359738368
        ns["run"] = lambda cmd, timeout=15: vm_stat if cmd[0] == "vm_stat" else f"{sysctls[cmd[-1]]}\n"
        expected = host_memory.macos_available(
            (134096 + 82107 + 953112) * 16384, 34359738368, 168087 * 16384, page=16384, free=133732, file_backed=1149866,
            purgeable=5127, compressor_bytes=1229651968, margin=4000)
        self.assertEqual(ns["free_gb"](), round(expected / 1e9, 2))
        self.assertGreater(ns["free_gb"](), round((134096 + 82107 + 953112) * 16384 / 1e9, 2))   # more than psutil's figure


if __name__ == "__main__":
    unittest.main()
