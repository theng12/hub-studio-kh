"""The 8 GB image runner (`qualification/eight-gb-image-run.sh` + `eight_gb_image_run.py`) must name real things and ship.

It is the image twin of the 8 GB text-to-speech runner: a shell shim around a Python program that sends Hub image jobs. A typo in a
package id, a size the Image lane refuses, or a step count that is not what Story Studio gets would make it measure something the
fleet does not run, so each is checked against the catalog and the Image worker's own validator. The measuring loop runs against a fake
Hub, never the real one.
"""
import ast
import contextlib
import csv
import io
import itertools
import json
import re
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
QUAL = ROOT / "qualification"
SCRIPT = QUAL / "eight_gb_image_run.py"
sys.path.insert(0, str(QUAL))
import ssd_wear   # noqa: E402 - the helper the runner imports from beside itself (shared with the TTS runner)

ZIMAGE, KLEIN = "filipstrand/Z-Image-Turbo-mflux-4bit", "AITRADER/FLUX2-klein-4B-mlx-4bit"
FILES = ("RUN-8GB-IMAGE.command", "eight-gb-image-run.sh", "eight_gb_image_run.py", "README-8gb-image.md", "ssd_wear.py")


def png(w, h):
    return b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + w.to_bytes(4, "big") + h.to_bytes(4, "big") + b"\x08\x06\0\0\0"


def runner(**env):
    """The program as a module namespace, with a clock that does not wait and nothing that reads this Mac."""
    ready = Path(tempfile.mkdtemp()) / "hub-ready.json"   # the guard's restore file and the worker's jobs.json live beside it: never the real ones
    ns = {"__name__": "img8"}
    with mock.patch.object(sys, "argv", ["x", str(QUAL), "/nonexistent.app"]), \
            mock.patch.dict("os.environ", {"IMG8_READY_FILE": str(ready), **env}):
        exec(compile(SCRIPT.read_text(), "eight_gb_image_run", "exec"), ns)
    real, tick = time, itertools.count(1000.0, 1.0)   # a clock that moves one second per reading, so every image has a duration
    ns["time"] = types.SimpleNamespace(time=lambda: next(tick), sleep=lambda s: real.sleep(0.001), strftime=real.strftime)
    for name, value in dict(process_rows=lambda: [(11, 1, 0.0, "/x/runtimes/image/bin/python3 -I -B /x/image_worker.py"), (12, 1, 5.0, "/bin/zsh")],
                            swap_gb=lambda: 1.0, pressure_level=lambda: 1, free_pct=lambda: 40,
                            free_gb=lambda: 3.1, holders=lambda: "wired 1.0 GB", worker_footprint_now=lambda: 0,
                            footprint=lambda pid: (6.5e9, 6.6e9)).items():
        ns[name] = value
    ns["ssd_wear"] = fake_ssd_wear()
    return ns


def fake_ssd_wear():
    """ssd_wear with the readers of this Mac replaced: every snapshot is one step later, +3 GB written, +1 GB swapped out, +60 s."""
    fake = types.SimpleNamespace(**{k: v for k, v in vars(ssd_wear).items() if not k.startswith("__")})
    state = {"n": 0}

    def snapshot():
        state["n"] += 1
        return {"t": 1000.0 + 60 * state["n"], "ssd": 5e9 + 3e9 * state["n"], "swapouts": 2e9 + 1e9 * state["n"]}
    fake.snapshot, fake.baseline_gb_per_min = snapshot, lambda seconds, **_: 0.6
    return fake


class ShippingTests(unittest.TestCase):
    def test_the_program_parses_and_the_shell_around_it_is_valid(self):
        ast.parse(SCRIPT.read_text())
        for name in ("eight-gb-image-run.sh", "RUN-8GB-IMAGE.command"):
            self.assertEqual(subprocess.run(["bash", "-n", str(QUAL / name)]).returncode, 0, name)

    def test_the_wrapper_chain_runs_the_runner_and_is_executable(self):
        self.assertIn("eight-gb-image-run.sh", (QUAL / "RUN-8GB-IMAGE.command").read_text())
        self.assertIn("eight_gb_image_run.py", (QUAL / "eight-gb-image-run.sh").read_text())
        for name in ("RUN-8GB-IMAGE.command", "eight-gb-image-run.sh"):
            self.assertTrue((QUAL / name).stat().st_mode & 0o100, name)

    def test_the_kit_step_carries_all_of_it_and_does_not_prune_it(self):
        prepare = ROOT / ".build" / "prepare-kit.sh"
        if not prepare.is_file():
            self.skipTest("prepare-kit.sh is local to the release Mac")
        text = prepare.read_text().replace("\\\n", " ")
        managed = re.search(r'MANAGED="(.*?)"', text, re.S).group(1).split()
        for name in FILES:
            self.assertTrue((QUAL / name).is_file(), name)
            self.assertIn(f'"$ROOT/qualification/{name}" "$KIT/Technical/{name}"', text, f"{name} never reaches a drive")
            self.assertIn(name, managed, f"{name} would be moved to _trash by the Technical prune")
        for pattern in ('"TTS 8GB Run "*', '"Image 8GB Run "*', '*" Quick Check "*'):
            self.assertIn(pattern, text, f"the prune would move {pattern} result folders, which are evidence")


class WhatItRunsTests(unittest.TestCase):
    """The models, sizes and steps are the catalog's and Story Studio's, not the runner's own idea."""

    @classmethod
    def setUpClass(cls):
        cls.ns = runner()
        cls.models = {m["key"]: m for m in cls.ns["MODELS"]}

    def test_the_two_models_are_the_catalog_ones_with_their_quantization_named(self):
        manifests = {m["package_id"]: m for m in (json.loads(p.read_text()) for p in (ROOT / "model-manifests").glob("*.json"))}
        self.assertEqual([m["pkg"] for m in self.ns["MODELS"]], ["z-image-turbo-mflux-4bit", "flux2-klein-4b-mlx-4bit"])
        for m in self.ns["MODELS"]:
            self.assertEqual(manifests[m["pkg"]]["model_id"], m["repo"])
            self.assertEqual(m["quant"], "4bit")
            self.assertIn("4-bit", m["name"])
        self.assertEqual((self.models["zimage"]["role"], self.models["klein"]["role"]), ("thumbnail", "scene"))

    def test_the_steps_and_size_are_what_the_image_lane_defaults_to(self):
        """Story Studio sends no size or steps of its own: the model's published defaults apply (16:9 = 1280x720)."""
        manifests = {m["package_id"]: m for m in (json.loads(p.read_text()) for p in (ROOT / "model-manifests").glob("*.json"))}
        self.assertEqual(self.models["zimage"]["steps"], manifests["z-image-turbo-mflux-4bit"]["defaults"]["steps"])
        self.assertEqual(self.models["klein"]["steps"], manifests["flux2-klein-4b-mlx-4bit"]["defaults"]["steps"])
        self.assertEqual(self.ns["PROD_SIZE"], (manifests["z-image-turbo-mflux-4bit"]["defaults"]["width"],
                                                manifests["z-image-turbo-mflux-4bit"]["defaults"]["height"]))

    def test_every_request_it_sends_passes_the_image_workers_own_validator(self):
        try:
            sys.path.insert(0, str(ROOT))
            from components.image.app.backend import generation
        except Exception as exc:   # the worker's imports are the app's; a bare checkout may not have them
            self.skipTest(f"the Image worker module cannot be imported here: {exc}")
        for m in self.ns["MODELS"]:
            for size, steps in ((self.ns["PROD_SIZE"], m["steps"]), (self.ns["QUICK_SIZE"], self.ns["QUICK_STEPS"])):
                params = generation.validate_request({"repo": m["repo"], "prompt": m["prompt"], "seed": self.ns["SEED0"], "width": size[0],
                                                      "height": size[1], "steps": steps, "guidance": self.ns["GUIDANCE"]})
                self.assertEqual((params["width"], params["height"], params["steps"]), (size[0], size[1], steps))
        self.assertEqual(generation.MODELS[ZIMAGE].memory.low_ram_below_bytes, 24_000_000_000, "low-RAM on every Mac below 24 GB is what the summary assumes")
        self.assertIsNone(generation.MODELS[KLEIN].memory.low_ram_below_bytes)

    def test_it_asks_for_what_the_default_run_promises(self):
        self.assertEqual(self.ns["IMAGES"], 3)
        self.assertEqual(self.ns["TIMEOUT"], 900.0)
        self.assertEqual(self.ns["PER_DAY"], 200.0)
        self.assertEqual(self.ns["RELAX_GB"], 0.0)
        self.assertEqual(self.ns["MIN_VERSION"], (0, 6, 125))


class ReadingTheMachineTests(unittest.TestCase):
    def test_a_png_is_measured_from_its_header(self):
        ns = runner()
        self.assertEqual(ns["png_size"](png(1280, 720)), (1280, 720))
        self.assertIsNone(ns["png_size"](b"not a png"))

    def test_the_workers_record_gives_memory_mode_and_the_mlx_peak_of_the_newest_job_for_the_model(self):
        ns = runner()
        ns["WORKER_JOBS"].parent.mkdir(parents=True)
        jobs = {"a": {"id": "a", "created_at": 100.0, "params": {"repo": ZIMAGE}, "memory_mode": "default", "peak_mlx_bytes": 1},
                "b": {"id": "b", "created_at": 200.0, "params": {"repo": ZIMAGE}, "memory_mode": "low_ram", "peak_mlx_bytes": 5_700_000_000},
                "c": {"id": "c", "created_at": 300.0, "params": {"repo": KLEIN}, "memory_mode": "default"}}
        ns["WORKER_JOBS"].write_text(json.dumps(jobs))
        self.assertEqual(ns["worker_job"](ZIMAGE, 150.0)["id"], "b")
        self.assertIsNone(ns["worker_job"](ZIMAGE, 250.0), "a job from before this image started must not be read as its")
        self.assertEqual(ns["worker_job"](ZIMAGE, 0, job_id="a")["id"], "a")
        self.assertIsNone(runner()["worker_job"](ZIMAGE, 0), "no record yet: blank, not an error")


class MeasuringLoopTests(unittest.TestCase):
    """The whole run against a fake Hub: rows, running totals, the guard bypassed and put back, a hung model, a dead one."""

    def fake_hub(self, ns, hung=(), broken=(), admission=True, restore_file=None, crash=None):
        log = {"posts": [], "bodies": [], "deleted": [], "released": 0, "puts": [], "override": None, "override_at_post": [], "file_at_lowering": None}
        sizes = {}

        def call(method, path, body=None, raw=False, timeout=60):
            if path.startswith("/api/hub/memory/admission"):
                if not admission:
                    return 404, "not found"
                if method == "GET":
                    o = log["override"]
                    return 200, {"overridden": bool(o), "effective_min_free_memory_gb": o["free"] if o else 3.0,
                                 "effective_min_total_memory_gb": o["total"] if o else 8.0}
                log["puts"].append((body["model"], body["min_free_memory_gb"]))
                if body["min_free_memory_gb"] is not None and restore_file:
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
                log["posts"].append(body["model"]); log["bodies"].append(body)
                log["override_at_post"].append((body["model"], log["override"]))
                sizes[bid] = (body["items"][0]["params"]["width"], body["items"][0]["params"]["height"], body["model"])
                return 200, {"batch_id": bid}
            bid = path.split("/")[4]
            if method == "DELETE":
                log["deleted"].append(bid)
                return 200, {"ok": True}
            if path.endswith("/artifact"):
                return 200, png(*sizes[bid][:2])
            if crash and sizes[bid][2] == crash:
                raise KeyboardInterrupt   # the owner presses Ctrl-C (or closes the window) mid-measurement
            model = sizes[bid][2]
            state = "cancelled" if bid in log["deleted"] else "running" if model in hung else "error" if model in broken else "done"
            return 200, {"created_at": 0, "items": [{"state": state, "error": "worker died" if state == "error" else None}]}
        ns["call"] = call
        return log

    def run_it(self, ns, models=None, quick=False):
        out = tempfile.mkdtemp()
        chosen = models or ns["MODELS"]
        ns.update(precheck=lambda: ("0.6.126", chosen), QUICK=quick, ONLY=None, BASELINE_S=30.0)
        ns["WORKER_JOBS"].parent.mkdir(parents=True, exist_ok=True)   # what the Image worker keeps about its jobs: Z-Image-Turbo runs low-RAM below 24 GB, Klein has no such mode
        ns["WORKER_JOBS"].write_text(json.dumps({
            "z": {"id": "z", "created_at": 1e12, "params": {"repo": ZIMAGE}, "memory_mode": "low_ram", "peak_mlx_bytes": 5_700_000_000},
            "k": {"id": "k", "created_at": 1e12, "params": {"repo": KLEIN}, "memory_mode": "default", "peak_mlx_bytes": 9_100_000_000}}))
        with mock.patch.dict("os.environ", {"IMG8_OUT": out}), contextlib.redirect_stdout(io.StringIO()), mock.patch.object(ns["subprocess"], "Popen"):
            code = ns["main"]()
        folder = next(Path(out).iterdir())
        return code, folder, list(csv.DictReader(io.StringIO((folder / "results.csv").read_text())))

    def test_three_images_per_model_with_per_image_and_running_totals_and_the_guard_put_back(self):
        ns = runner()
        log = self.fake_hub(ns, restore_file=ns["RESTORE"])
        code, folder, rows = self.run_it(ns)
        self.assertEqual(code, 0)
        self.assertEqual([(r["model"], r["image"], r["status"]) for r in rows],
                         [(m, str(i), "completed") for m in ("Z-Image-Turbo", "FLUX.2 Klein 4B") for i in (1, 2, 3)])
        z = rows[:3]
        self.assertEqual([r["seed"] for r in z], ["42", "43", "44"])
        self.assertEqual({r["resolution"] for r in rows}, {"1280x720"})
        self.assertEqual([r["steps"] for r in rows], ["9"] * 3 + ["4"] * 3)
        self.assertEqual([(r["low_ram"], r["memory_mode"], r["peak_memory_gb"], r["mlx_peak_active_gb"]) for r in (rows[0], rows[3])],
                         [("yes", "low_ram", "6.5", "5.7"), ("no", "default", "6.5", "9.1")])
        self.assertEqual([r["cold_start"] for r in z], ["yes", "no", "no"])
        # every snapshot is +3 GB written / +1 GB swapped out / +60 s; baseline 0.6 GB/min -> net 3 - 0.6 = 2.4 per image
        self.assertEqual([(r["ssd_written_gb"], r["swapouts_gb"], r["ssd_written_net_gb"]) for r in z], [("3.0", "1.0", "2.4")] * 3)
        self.assertEqual([r["cumulative_ssd_written_gb"] for r in z], ["3.0", "6.0", "9.0"])
        self.assertEqual([r["cumulative_swapouts_gb"] for r in z], ["1.0", "2.0", "3.0"])
        self.assertEqual([r["cumulative_ssd_written_net_gb"] for r in rows[3:]], ["2.4", "4.8", "7.2"], "the running total starts over for the next model")
        # the job the Hub got: an image job with the production size, steps and the fixed seed, through the same route as the fleet
        body = log["bodies"][0]
        self.assertEqual((body["modality"], body["model"], body["items"][0]["seed"]), ("image", ZIMAGE, 42))
        self.assertEqual(body["items"][0]["params"], {"width": 1280, "height": 720, "steps": 9, "guidance": 1.0})
        # the guard: bypassed for each model while its images run, then put back; the file that would put it back existed first
        self.assertEqual([o for _, o in log["override_at_post"]], [{"free": 0.0, "total": 8.0}] * 6)
        self.assertEqual(log["puts"], [(ZIMAGE, 0.0), (ZIMAGE, None), (KLEIN, 0.0), (KLEIN, None)])
        self.assertIsNone(log["override"])
        self.assertEqual(log["file_at_lowering"], {KLEIN: None}, "what to put back is written BEFORE the Hub is changed")
        self.assertEqual(json.loads(ns["RESTORE"].read_text()), {})
        self.assertEqual(log["released"], 2, "idle Studios are asked to release before each model")
        self.assertEqual(len(list(folder.glob("*.png"))), 6)

    def test_the_summary_says_it_in_plain_words(self):
        ns = runner()
        self.fake_hub(ns)
        _, folder, _ = self.run_it(ns)
        text = (folder / "summary.txt").read_text()
        for expected in ("Z-Image-Turbo 4-bit (thumbnails, 1280x720, 9 steps; memory mode: low_ram)", "FLUX.2 Klein 4B 4-bit (scenes, 1280x720, 4 steps; memory mode: default)",
                         "completed 3/3 images", "s per image", "peak memory 6.5 GB by the worker's footprint, 5.7 GB by MLX", "swap grew by",
                         "≈ 2.40 GB written to this Mac's SSD per image", "At 200 images a day that is ≈ 175.2 TB a year",
                         "an assumption", "Over consecutive images - image 1:", "Running total 3.0 GB swapped out", "Verdict:",
                         "standalone run"):
            self.assertIn(expected, text)
        self.assertEqual("NOT 8 GB evidence" in text, not ns["ON_TARGET"])

    def test_the_summary_warns_when_the_worker_was_already_full_or_the_mac_was_busy(self):
        ns = runner()
        row = dict(model="Z-Image-Turbo", quant="4bit", role="thumbnail", image="1", resolution="1280x720", steps="9", status="completed",
                   generation_seconds="100", footprint_before_gb="22.0", timing_clean="no", timing_note="contended: other processes avg 600% CPU",
                   guard_relaxed="yes", swap_delta_gb="0", memory_pressure="normal", free_gb_before="3.0", measured_peak_gb="5.0")
        text = ns["summarise"]([row], ns["MODELS"][:1], "t", "0.6.126", "now", True)
        self.assertIn("already held 22.0 GB", text)
        self.assertIn("other processes were busy during 1 of these images", text)

    def test_a_model_that_hangs_times_out_is_cancelled_skipped_and_the_next_model_still_runs(self):
        ns = runner(IMG8_TIMEOUT="0.05")
        log = self.fake_hub(ns, hung={ZIMAGE})
        ns["TIMEOUT"], ns["SETTLE_S"] = 0.05, 2.0
        _, folder, rows = self.run_it(ns)
        self.assertEqual([r["status"] for r in rows[:3]], ["timed out", "skipped", "skipped"])
        self.assertEqual([r["status"] for r in rows[3:]], ["completed"] * 3)
        self.assertIn("timed out", rows[0]["error"])
        self.assertEqual(log["posts"].count(ZIMAGE), 1, "skipped images were queued anyway")
        self.assertEqual(len(log["deleted"]), 1)
        self.assertIsNone(log["override"], "the guard stays bypassed after a timeout")
        summary = (folder / "summary.txt").read_text()
        self.assertIn("DOES NOT FIT", summary)
        self.assertIn("FLUX.2 Klein 4B 4-bit (scenes", summary)

    def test_a_crash_is_a_row_not_a_stop(self):
        ns = runner()
        self.fake_hub(ns, broken={KLEIN})
        _, folder, rows = self.run_it(ns)
        klein = [r for r in rows if r["model"] == "FLUX.2 Klein 4B"]
        self.assertEqual([r["status"] for r in klein], ["failed", "skipped", "skipped"])
        self.assertEqual(klein[0]["error"], "worker died")
        self.assertEqual([r["status"] for r in rows if r["model"] == "Z-Image-Turbo"], ["completed"] * 3)

    def test_ctrl_c_mid_measurement_still_puts_the_guard_back(self):
        ns = runner()
        log = self.fake_hub(ns, crash=KLEIN)
        code, folder, rows = self.run_it(ns)
        self.assertEqual(code, 130)
        self.assertIsNone(log["override"])
        self.assertIn("THE RUN WAS STOPPED EARLY", (folder / "summary.txt").read_text())

    def test_a_hub_without_the_bypass_route_runs_at_its_own_requirement(self):
        ns = runner()
        log = self.fake_hub(ns, admission=False)
        _, _, rows = self.run_it(ns)
        self.assertEqual({r["guard_relaxed"] for r in rows}, {"no"})
        self.assertEqual(log["puts"], [])

    def test_quick_is_one_small_image_per_model_and_says_it_is_not_the_test(self):
        ns = runner()
        log = self.fake_hub(ns)
        ns["IMAGES"] = 1
        _, folder, rows = self.run_it(ns, quick=True)
        self.assertEqual({r["resolution"] for r in rows}, {"1024x576"})
        self.assertEqual({r["steps"] for r in rows}, {"2"})
        self.assertEqual(len(rows), 2)
        self.assertIn("quick check", (folder / "summary.txt").read_text())
        self.assertTrue(folder.name.startswith("Image 8GB Quick Check "))

    def test_the_columns_are_the_tts_ones_that_apply_plus_the_image_ones(self):
        cols = runner()["COLUMNS"]
        for c in ("model", "quant", "resolution", "steps", "low_ram", "generation_seconds", "peak_memory_gb", "mlx_peak_active_gb", "memory_pressure",
                  "swap_before_gb", "swap_after_gb", "swap_peak_gb", "free_gb_before", "memory_holders", "guard_relaxed", "verdict",
                  "worker_restarts", "ssd_written_gb", "swapouts_gb", "ssd_written_baseline_gb_per_min", "ssd_written_net_gb",
                  "cumulative_ssd_written_gb", "cumulative_swapouts_gb"):
            self.assertIn(c, cols)
        for c in ssd_wear.COLUMNS:
            self.assertIn(c, cols)
        self.assertEqual(len(cols), len(set(cols)))


if __name__ == "__main__":
    unittest.main()
