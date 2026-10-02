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
import plistlib
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
sys.path.insert(0, str(QUAL))
import ssd_wear   # noqa: E402 - the helper the runner imports from beside itself


def program():
    body = SOURCE.split(": <<'__PYTHON__'\n", 1)[1].split("\n__PYTHON__", 1)[0]
    return body


class RunnerTests(unittest.TestCase):
    def test_the_embedded_program_parses_and_the_shell_around_it_is_valid(self):
        ast.parse(program())
        self.assertEqual(subprocess.run(["bash", "-n", str(QUAL / "eight-gb-tts-run.sh")]).returncode, 0)

    def test_the_models_are_the_catalog_ones_in_the_owner_order(self):
        manifests = {m["package_id"]: m for m in (json.loads(p.read_text()) for p in (ROOT / "model-manifests").glob("*.json"))}
        found = re.findall(r'pkg="([^"]+)", repo="([^"]+)"', program())
        self.assertEqual([p for p, _ in found],
                         ["qwen3-tts-12hz-0-6b-base-8bit", "qwen3-tts-12hz-0-6b-base-4bit",
                          "voxcpm2-4bit", "omnivoice-bfloat16", "breeze-tts-2-mlx-4bit"])
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
        for name in ("RUN-8GB-TTS.command", "eight-gb-tts-run.sh", "README-8gb-tts.md", "tts-script-S2.txt", "tts-script-S4.txt", "ssd_wear.py"):
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
    ns["ssd_wear"] = fake_ssd_wear()
    return ns


def fake_ssd_wear():
    """ssd_wear with the two functions that read the Mac replaced: no ioreg, no vm_stat, no 30 s wait. Each snapshot is
    one step later: +3 GB written to the internal SSD, +1 GB swapped out, +60 s; the idle baseline is 0.6 GB per minute."""
    fake = types.SimpleNamespace(**{k: v for k, v in vars(ssd_wear).items() if not k.startswith("__")})
    state = {"n": 0, "baselines": 0}

    def snapshot():
        state["n"] += 1
        return {"t": 1000.0 + 60 * state["n"], "ssd": 5e9 + 3e9 * state["n"], "swapouts": 2e9 + 1e9 * state["n"]}

    def baseline(seconds, **_):
        state["baselines"] += 1
        return 0.6
    fake.snapshot, fake.baseline_gb_per_min, fake.state = snapshot, baseline, state
    return fake


class MemoryStallTests(unittest.TestCase):
    """A job the Hub holds back for memory must be given up on, recorded with the Hub's numbers, and not block the rest."""

    def test_the_hubs_reason_is_read_for_its_numbers(self):
        ns = runner()
        self.assertEqual(ns["memory_wait"](NOTE), (3.2, 3.0))
        self.assertEqual(ns["memory_wait"]("released 2 idle sibling(s); " + NOTE), (3.2, 3.0))
        self.assertIsNone(ns["memory_wait"]("voice: needs a ~16GB machine; this one has 8GB"))
        self.assertIsNone(ns["memory_wait"](None))

    def fake_hub(self, stalled, admission=False, was=None, crash_while_relaxed=False, restore_file=None, hung=(), broken=()):
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
            elif log["jobs"][bid] in hung:   # a thrashing model: running, never finishes
                state = "running"
            elif log["jobs"][bid] in broken:   # a model whose worker dies
                state = "error"
            elif log["jobs"][bid] in stalled and not (log["override"] and log["override"]["free"] is not None
                                                      and log["override"]["free"] <= 1.0):
                state = "queued" if log["polls"] < 400 else "error"   # never ends by itself; the cap only stops a hung test
            elif log["jobs"][bid] in stalled:   # runs under the lowered guard: takes a while, so the sampler can watch it
                log["running"][bid] = log["running"].get(bid, 0) + 1
                state = "running" if log["running"][bid] < 40 else "done"
            else:
                state = "done"
            return 200, {"governor_note": NOTE if state == "queued" else None, "created_at": 0,
                         "items": [{"state": state, "chunk_total": 1, "error": ("worker died" if log["jobs"][bid] in broken else "the cap") if state == "error" else None}]}
        return call, log

    def test_a_stalled_model_is_recorded_cancelled_skipped_and_the_next_model_still_runs(self):
        ns = runner()
        call, log = self.fake_hub({"mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"})
        models = [m for m in ns["MODELS"] if m["key"] in ("qwen3", "voxcpm2")]
        out = tempfile.mkdtemp()
        ns.update(call=call, MEM_WAIT_S=0.0, QUICK=False, ONLY=None,
                  precheck=lambda: ("0.6.124", models, {"S2": "hello", "S4": "hello"}))   # a Hub with no override route: it cannot bypass
        with mock.patch.dict("os.environ", {"TTS8_OUT": out}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ns["main"](), 0)
        folder = next(Path(out).iterdir())
        rows = list(csv.DictReader(io.StringIO((folder / "results.csv").read_text())))
        qwen = [r["status"] for r in rows if r["model"] == "Qwen3-TTS 0.6B"]
        self.assertEqual(qwen, ["did not start", "skipped"])
        self.assertEqual([r["status"] for r in rows if r["model"] == "VoxCPM2"], ["completed"] * 2)
        first = rows[0]
        self.assertEqual((first["needed_free_gb"], first["had_free_gb"], first["hub_reason"]), ("3.2", "3.0", NOTE))
        self.assertEqual(log["posts"].count("mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"), 1, "skipped clips were queued anyway")
        self.assertEqual(len(log["deleted"]), 1, "the stalled job was not cancelled")
        self.assertEqual(log["released"], 2, "idle Studios are asked to release before each model")
        summary = (folder / "summary.txt").read_text()
        self.assertIn("DID NOT START", summary)
        self.assertIn("needs 3.2 GB free, had 3 GB", summary)
        self.assertIn("VoxCPM2 4-bit: completed 2/2", summary)


QWEN = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"

# --- fixtures: what `ioreg -a -c IOBlockStorageDevice -r -l -d 2` and `vm_stat` print on a Mac with an external kit SSD attached
def ioreg_device(interconnect, location, written, product="x"):
    return {"IOObjectClass": "IOBlockStorageDevice", "Device Characteristics": {"Product Name": product},
            "Protocol Characteristics": {"Physical Interconnect": interconnect, "Physical Interconnect Location": location},
            "IORegistryEntryChildren": [{"IOObjectClass": "IOBlockStorageDriver", "Statistics": {
                "Bytes (Write)": written, "Bytes (Read)": 990_000_000_000, "Operations (Write)": 14675448}}]}


IOREG_FIXTURE = plistlib.dumps([
    ioreg_device("Secure Digital", "Internal", 0, "Built In SDXC Reader"),
    ioreg_device("Apple Fabric", "Internal", 638_973_984_768, "APPLE SSD AP0512Z"),
    ioreg_device("USB", "External", 17_216_610_304, "ORICO-1T B"),            # the kit SSD the runner lives on
    ioreg_device("USB", "External", 9_670_848_512, "ORICO"),
    ioreg_device("PCI-Express", "External", 4_000_000_000_000, "Thunderbolt NVMe"),
])

VM_STAT_FIXTURE = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                   566265.
Pages wired down:                             385412.
"Translation faults":                      803171829.
Pageouts:                                      42961.
Swapins:                                    11673995.
Swapouts:                                   20938938.
"""


class SsdWearParsingTests(unittest.TestCase):
    """The SSD-write and swapout readers, on fixture text: no sudo, no real disk."""

    def test_only_the_internal_apple_fabric_disk_is_counted(self):
        self.assertEqual(ssd_wear.internal_ssd_written(IOREG_FIXTURE), 638_973_984_768,
                         "the SD reader, the USB kit SSDs and a Thunderbolt NVMe must not be counted")

    def test_two_internal_nand_drivers_are_added_and_no_internal_disk_is_none(self):
        two = plistlib.dumps([ioreg_device("Apple Fabric", "Internal", 10), ioreg_device("Apple Fabric", "Internal", 5)])
        self.assertEqual(ssd_wear.internal_ssd_written(two), 15)
        self.assertIsNone(ssd_wear.internal_ssd_written(plistlib.dumps([ioreg_device("USB", "External", 7)])))
        self.assertIsNone(ssd_wear.internal_ssd_written(b"not a plist"))
        self.assertIsNone(ssd_wear.internal_ssd_written(b""))
        self.assertIsNone(ssd_wear.internal_ssd_written(plistlib.dumps({"a": 1})))

    def test_swapouts_pages_are_multiplied_by_the_page_size(self):
        self.assertEqual(ssd_wear.swapouts_bytes(VM_STAT_FIXTURE, "16384\n"), 20938938 * 16384)
        self.assertEqual(ssd_wear.swapouts_bytes(VM_STAT_FIXTURE), 20938938 * 16384, "the vm_stat header is the fallback")
        self.assertEqual(ssd_wear.swapouts_bytes(VM_STAT_FIXTURE, "4096"), 20938938 * 4096, "`pagesize` wins")
        self.assertEqual(ssd_wear.swapouts_bytes("Swapouts: 10.\n"), 10 * 16384, "no header at all: Apple Silicon's 16384")
        self.assertIsNone(ssd_wear.swapouts_bytes("Pages free: 1.\n"))
        self.assertIsNone(ssd_wear.swapouts_bytes(""))

    def test_a_clip_gets_gross_swapouts_and_net_after_the_idle_baseline(self):
        a = {"t": 100.0, "ssd": 10e9, "swapouts": 1e9}
        b = {"t": 160.0, "ssd": 13e9, "swapouts": 3.5e9}
        self.assertEqual(ssd_wear.clip_columns(a, b, 0.6),
                         {"ssd_written_gb": 3.0, "swapouts_gb": 2.5, "ssd_written_baseline_gb_per_min": 0.6, "ssd_written_net_gb": 2.4})
        self.assertEqual(ssd_wear.clip_columns(a, {**b, "t": 100.0 + 600}, 0.6)["ssd_written_net_gb"], 0.0, "net never goes below zero")
        none = ssd_wear.clip_columns(a, b, None)
        self.assertEqual((none["ssd_written_gb"], none["ssd_written_net_gb"], none["ssd_written_baseline_gb_per_min"]), (3.0, "", ""))
        back = ssd_wear.clip_columns(b, a, 0.6)   # a reboot between the readings: the counter went backwards
        self.assertEqual((back["ssd_written_gb"], back["swapouts_gb"], back["ssd_written_net_gb"]), ("", "", ""))
        missing = ssd_wear.clip_columns({"t": 1.0, "ssd": None, "swapouts": None}, b, 0.6)
        self.assertEqual((missing["ssd_written_gb"], missing["swapouts_gb"]), ("", ""))
        self.assertEqual(list(ssd_wear.clip_columns(a, b, 0.6)), ssd_wear.COLUMNS)

    def test_the_idle_baseline_is_gb_per_minute_over_the_real_elapsed_time(self):
        snaps = iter([{"t": 0.0, "ssd": 1e9, "swapouts": 0}, {"t": 30.0, "ssd": 1.3e9, "swapouts": 0}])
        slept = []
        with mock.patch.object(ssd_wear, "snapshot", lambda: next(snaps)):
            self.assertAlmostEqual(ssd_wear.baseline_gb_per_min(30, sleep=slept.append), 0.6)
        self.assertEqual(slept, [30])
        snaps = iter([{"t": 0.0, "ssd": None, "swapouts": 0}, {"t": 30.0, "ssd": 1e9, "swapouts": 0}])
        with mock.patch.object(ssd_wear, "snapshot", lambda: next(snaps)):
            self.assertIsNone(ssd_wear.baseline_gb_per_min(30, sleep=lambda s: None))

    def test_the_summary_lines_say_gb_per_minute_of_audio_and_tb_per_year(self):
        rows = [{"ssd_written_net_gb": "2.4", "audio_seconds": "30", "generation_seconds": "90", "swapouts_gb": "1.0"},
                {"ssd_written_net_gb": "3.6", "audio_seconds": "30", "generation_seconds": "90", "swapouts_gb": "2.0"},
                {"ssd_written_net_gb": "", "audio_seconds": "30", "generation_seconds": "90"}]   # unmeasured: left out
        per_min, year = ssd_wear.wear_lines(rows)
        self.assertIn("About 6.00 GB written to this Mac's SSD per minute of audio", per_min)   # 6.0 GB over 1 minute of audio
        self.assertIn("swapouts 3.00 GB", per_min)
        # 6.0 GB per 180 s = 120 GB per hour of generation; x 8 h x 365 days = 350.4 TB
        self.assertIn("At 8 hours of generation a day, \u2248 350.4 TB/year", year)
        self.assertIn("120.0 GB per hour of generation", year)
        self.assertEqual(ssd_wear.wear_lines([]), [])
        self.assertEqual(ssd_wear.wear_lines([{"ssd_written_net_gb": "", "audio_seconds": "1", "generation_seconds": "1"}]), [])
        self.assertAlmostEqual(ssd_wear.tb_per_year(6.0, 180, 8), 350.4)
        # another runner reuses it per image
        img = ssd_wear.wear_lines([{"ssd_written_net_gb": "1.5", "images": "3", "generation_seconds": "60"}], unit="image", units_key="images", units_per=1.0)
        self.assertIn("About 0.50 GB written to this Mac's SSD per image", img[0])


class BypassTests(unittest.TestCase):
    """The guard is bypassed at once, nothing waits or aborts for swap, a hung or dead model is recorded and skipped, and the guard is put back."""

    def run_main(self, hub_kwargs, swap=None, models=("qwen3", "voxcpm2"), timeout=None, with_s4=False):
        ns = runner()
        call, log = MemoryStallTests.fake_hub(self, {QWEN}, restore_file=ns["RESTORE"], **hub_kwargs)
        chosen = [m for m in ns["MODELS"] if m["key"] in models]
        out = tempfile.mkdtemp()
        ns.update(call=call, MEM_WAIT_S=0.0, QUICK=False, ONLY=None, WITH_S4=with_s4, voice_pids=lambda rows: [1], pressure_level=lambda: 1,
                  footprint=lambda pid: (2.35e9, 2.6e9),
                  swap_gb=(lambda: swap(log)) if swap else (lambda: 0.0),
                  precheck=lambda: ("0.6.125", chosen, {"S2": "hello", "S4": "hello"}))
        if timeout is not None:
            ns["TIMEOUT"] = timeout
        status = "not run"
        with mock.patch.dict("os.environ", {"TTS8_OUT": out}), contextlib.redirect_stdout(io.StringIO()):
            try:
                status = ns["main"]()
            except KeyboardInterrupt:
                status = "interrupted"
        folder = next(Path(out).iterdir())
        rows = list(csv.DictReader(io.StringIO((folder / "results.csv").read_text())))
        return ns, log, rows, (folder / "summary.txt").read_text(), status

    def test_a_held_model_runs_at_once_with_the_guard_bypassed_and_the_guard_is_put_back(self):
        ns, log, rows, summary, status = self.run_main({"admission": True})
        self.assertEqual(status, 0)
        qwen = [r for r in rows if r["model"] == "Qwen3-TTS 0.6B"]
        self.assertEqual([r["status"] for r in qwen], ["completed"] * 2, "no 'did not start' wait row: the bypass is set before the first clip")
        self.assertEqual([r["guard_relaxed"] for r in qwen], ["yes"] * 2)
        self.assertEqual(qwen[0]["override_used_gb"], "0.0")
        self.assertEqual(qwen[0]["measured_peak_gb"], "2.35")
        self.assertEqual(qwen[0]["verdict"], "fits only with relaxed guard - real need about 2.6 GB")
        self.assertEqual(qwen[0]["hub_free_floor_gb"], "3.2")
        self.assertEqual(qwen[0]["script"], "S2")
        self.assertEqual({r["script"] for r in rows}, {"S2"}, "the 3-minute text is opt-in")
        self.assertEqual(len(log["posts"]), 4)
        # every model is bypassed before its first clip and put back before the next one
        self.assertEqual(log["puts"][:2], [(QWEN, 0.0, 8.0), (QWEN, None, None)])
        self.assertEqual({p[1] for p in log["puts"] if p[1] is not None}, {0.0})
        self.assertIsNone(log["override"])
        self.assertEqual(log["file_at_lowering"] is not None, True, "what to put back must be saved before the Hub is changed")
        self.assertEqual([o for m, o in log["override_at_post"] if m == QWEN and o is not None][:1], [{"free": 0.0, "total": 8.0}])
        self.assertIn("FITS WITH THE GUARD BYPASSED", summary)
        self.assertIn("Recommended free-memory requirement: 2.6 GB (worker peak 2.35 GB + 0.25 GB margin; the Hub's default is 3.2 GB)", summary)
        self.assertEqual(json.loads(ns["RESTORE"].read_text()), {})

    def test_every_clip_records_ssd_writes_swapouts_the_baseline_and_net_and_the_summary_says_it_in_words(self):
        ns, log, rows, summary, status = self.run_main({"admission": True}, models=("qwen3",))
        self.assertEqual(ns["ssd_wear"].state["baselines"], 1, "one idle baseline per run, before the first clip")
        for r in rows:   # fake: 3 GB written, 1 GB swapped out, 60 s per clip window, idle baseline 0.6 GB/min
            self.assertEqual((r["ssd_written_gb"], r["swapouts_gb"], r["ssd_written_baseline_gb_per_min"], r["ssd_written_net_gb"]),
                             ("3.0", "1.0", "0.6", "2.4"))
        # 2 clips x 2.4 GB net over 2 x 1 s of audio = 144 GB per minute of audio
        self.assertIn("About 144.00 GB written to this Mac's SSD per minute of audio", summary)
        self.assertRegex(summary, r"At 8 hours of generation a day, \u2248 [\d.]+ TB/year")
        self.assertIn("INTERNAL disk", summary)

    def test_when_the_internal_disk_cannot_be_read_the_columns_are_blank_and_the_summary_says_so(self):
        ns = runner()
        ns["ssd_wear"].snapshot = lambda: {"t": time.time(), "ssd": None, "swapouts": None}
        ns["ssd_wear"].baseline_gb_per_min = lambda seconds, **_: None
        call, log = MemoryStallTests.fake_hub(self, {QWEN}, restore_file=ns["RESTORE"], admission=True)
        chosen = [m for m in ns["MODELS"] if m["key"] == "qwen3"]
        out = tempfile.mkdtemp()
        ns.update(call=call, voice_pids=lambda rows: [1], pressure_level=lambda: 1, footprint=lambda pid: (1e9, 1e9), swap_gb=lambda: 0.0,
                  precheck=lambda: ("0.6.125", chosen, {"S2": "hello", "S4": "hello"}))
        with mock.patch.dict("os.environ", {"TTS8_OUT": out}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ns["main"](), 0)
        folder = next(Path(out).iterdir())
        rows = list(csv.DictReader(io.StringIO((folder / "results.csv").read_text())))
        self.assertEqual({tuple(r[c] for c in ssd_wear.COLUMNS) for r in rows}, {("", "", "", "")})
        self.assertEqual([r["status"] for r in rows], ["completed"] * 2)
        self.assertIn("SSD writes: not measured", (folder / "summary.txt").read_text())

    def test_s4_is_opt_in(self):
        ns, log, rows, summary, status = self.run_main({"admission": True}, models=("voxcpm2",), with_s4=True)
        self.assertEqual([r["script"] for r in rows], ["S2", "S4", "S2", "S4"])

    def test_an_operator_override_that_was_already_there_is_restored_exactly(self):
        ns, log, rows, summary, status = self.run_main({"admission": True, "was": {"free": 4.0, "total": 8.0}})
        self.assertEqual(log["puts"][:2], [(QWEN, 0.0, 8.0), (QWEN, 4.0, 8.0)])
        self.assertEqual(log["override"], {"free": 4.0, "total": 8.0})

    def test_heavy_swap_is_recorded_not_a_reason_to_abort(self):
        grow = {"n": 0}
        def swap(log):   # swap rises 0.5 GB per reading once the guard is bypassed
            if log["override"]:
                grow["n"] += 1
                return 0.5 * grow["n"]
            return 0.0
        ns, log, rows, summary, status = self.run_main({"admission": True}, swap=swap)
        qwen = [r for r in rows if r["model"] == "Qwen3-TTS 0.6B"]
        self.assertEqual([r["status"] for r in qwen], ["completed"] * 2)
        self.assertGreater(float(qwen[0]["swap_delta_gb"]), 1.5)
        self.assertEqual(log["deleted"], [], "nothing is cancelled for swap")

    def test_a_clip_that_never_finishes_is_timed_out_the_model_skipped_and_the_next_model_runs(self):
        ns, log, rows, summary, status = self.run_main({"admission": True, "hung": {QWEN}}, timeout=0.3)
        self.assertEqual(status, 0)
        qwen = [r for r in rows if r["model"] == "Qwen3-TTS 0.6B"]
        self.assertEqual([r["status"] for r in qwen], ["timed out", "skipped"])
        self.assertTrue(qwen[0]["error"].startswith("timed out: not finished within"), qwen[0]["error"])
        self.assertEqual(qwen[0]["verdict"], "does not fit - too slow, timed out")
        self.assertEqual([r["status"] for r in rows if r["model"] == "VoxCPM2"], ["completed"] * 2)
        self.assertEqual(len(log["deleted"]), 1, "the hung job is cancelled")
        self.assertIsNone(log["override"])
        self.assertIn("DOES NOT FIT", summary)
        self.assertIn("VoxCPM2 4-bit: completed 2/2", summary)

    def test_a_model_whose_worker_dies_is_recorded_with_the_error_and_the_next_model_runs(self):
        ns, log, rows, summary, status = self.run_main({"admission": True, "broken": {QWEN}})
        qwen = [r for r in rows if r["model"] == "Qwen3-TTS 0.6B"]
        self.assertEqual([r["status"] for r in qwen], ["failed", "skipped"])
        self.assertEqual(qwen[0]["error"], "worker died")
        self.assertEqual([r["status"] for r in rows if r["model"] == "VoxCPM2"], ["completed"] * 2)
        self.assertIsNone(log["override"])
        self.assertIn("worker died", summary)

    def test_ctrl_c_in_the_middle_of_a_measurement_puts_the_guard_back(self):
        ns, log, rows, summary, status = self.run_main({"admission": True, "crash_while_relaxed": True}, models=("qwen3",))
        self.assertIn(status, (130, "interrupted"))
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
        self.assertEqual([r["status"] for r in rows if r["model"] == "Qwen3-TTS 0.6B"], ["did not start", "skipped"])
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
