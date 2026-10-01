#!/bin/bash
# Text-to-speech on an 8 GB Mac: Qwen3-TTS 0.6B 8-bit, VoxCPM2 4-bit, OmniVoice bf16.
#
#   bash eight-gb-tts-run.sh                 the test (two voices x the short text S2, three models; guard bypassed)
#   bash eight-gb-tts-run.sh --with-s4       also the 3-minute text S4 (slow: can take hours on 8 GB)
#   bash eight-gb-tts-run.sh --quick         a short check (S2, Chris only) - the script's own self-test
#   bash eight-gb-tts-run.sh --models qwen3,voxcpm2   only these (qwen3, qwen3-4bit, voxcpm2, omnivoice)
#   bash eight-gb-tts-run.sh --allow-non-target       run the full test on a Mac that is not 8 GB
#
# (Easier: double-click RUN-8GB-TTS.command, which runs this.)
#
# It sends each clip to the Studio Hub that is already running on THIS Mac, through the same route
# the Gateway and the Voice studio use (POST /api/hub/jobs, modality "voice"), so the Hub's own
# scheduler, section splitting and joins apply exactly as in production. It uses the same two texts
# (tts-script-S2.txt, about 175 words; tts-script-S4.txt, about 3 minutes) and the same two owner
# voices as the 32 GB Mac measurement, one job at a time, in this order:
#   Qwen3-TTS 0.6B 8-bit -> VoxCPM2 4-bit -> OmniVoice bf16.
#
# It measures per clip: finished or failed (and why), wall time, audio length, real-time factor,
# the biggest memory the voice worker used (macOS `footprint`, sampled every second), memory
# pressure, swap before/after/peak, and whether the Mac stayed responsive. A clip that takes more
# than 3x the 32 GB time is flagged. It writes a timestamped folder beside this script (or on the
# Desktop when the drive is read-only): the WAVs, results.csv and a short plain-English summary.txt.
#
# Before each model it asks the Hub to release the idle Studios' models (never a running job) and records the
# free memory the Hub sees and what holds the rest. Then it BYPASSES the Hub's free-memory guard for that model
# (PUT /api/hub/memory/admission, Hub 0.6.125+: the free-memory requirement goes to TTS8_RELAX_FREE_GB, default 0)
# with no waiting, and just runs it: the point is to find out what the Mac really does, so it never aborts a clip
# for swap or memory pressure - it records the peak, the swap and the pressure. Every clip has a hard wall-clock
# limit (TTS8_TIMEOUT, default 480 s for a short text; a 3-minute text gets 3x): a clip that thrashes is cancelled and
# recorded as "timed out", a clip that crashes (worker died, out of memory, error) is recorded with the error, and in
# both cases the model's remaining clips are skipped and the next model goes on. The original requirement is put back
# after each model and on any exit (Ctrl-C, closing the window, an error), and at the next start if a run was killed
# (kept in tts8-guard-restore.json beside the Hub's hub-ready.json). If this Hub cannot bypass (older than 0.6.125) the
# old behaviour applies: a job held for memory longer than TTS8_MEM_WAIT seconds is recorded "did not start".
#
# Default clips: the 175-word text (S2) in both owner voices, per model. The 3-minute text (S4) is OFF unless you ask
# (--with-s4 or TTS8_S4=1): on 8 GB it ran 45 minutes without finishing.
#
# It installs nothing and deletes nothing. It asks the Hub for audio (and keeps the Mac awake while it runs);
# the only setting it touches is that temporary per-model override. Safe to run again; each run gets its own folder.
#
# Nothing to edit. Knobs (all optional): TTS8_TIMEOUT=480 (seconds per short clip; S4 gets 3x), TTS8_RELAX_FREE_GB=0
# (the free-memory requirement while a model is measured), TTS8_S4=1 (also run the 3-minute text), TTS8_MEM_WAIT=300
# (only for a Hub that cannot bypass), TTS8_OUT=<folder> (where the results folder goes), TTS8_READY_FILE=<hub-ready.json>,
# STUDIOHUB_APP=<app>.
set -u
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
SELF="${BASH_SOURCE[0]}"
HERE=$(CDPATH= cd -- "$(dirname -- "$SELF")" && pwd)

# The Python that goes with the installed app: the machine may have no other, and the system
# /usr/bin/python3 can pop up a developer-tools installer.
APP=""
for candidate in "${STUDIOHUB_APP:-}" "/Applications/Studio Hub.app" "$HOME/Applications/Studio Hub.app"; do
  [ -n "$candidate" ] && [ -x "$candidate/Contents/Resources/runtimes/voice/bin/python3" ] && { APP="$candidate"; break; }
done
if [ -z "$APP" ]; then
  cat >&2 <<EOF
STOPPED: Studio Hub is not installed on this Mac, so there is nothing to test.

  Install it from the kit: double-click "1 - Install the app" (or "Update this Mac") at the kit's
  root, open Studio Hub once, then run this again. If it is installed somewhere unusual, set
  STUDIOHUB_APP to its path.

Nothing was run and nothing was written.
EOF
  exit 3
fi
# The program is the Python below (the app's own interpreter runs it); the here-document marker keeps
# the file valid shell, and sed cuts it out between the two markers.
exec "$APP/Contents/Resources/runtimes/voice/bin/python3" -c \
  "$(sed -e "1,/^: <<'__PYTHON__'\$/d" -e '/^__PYTHON__$/,$d' "$SELF")" "$HERE" "$APP" "$@"
exit 3
: <<'__PYTHON__'
import atexit, csv, json, os, re, signal, subprocess, sys, threading, time, urllib.error, urllib.parse, urllib.request, uuid
from pathlib import Path

HERE, APP, ARGS = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
QUICK = "--quick" in ARGS or os.environ.get("TTS8_QUICK") == "1"
ALLOW_NON_TARGET = "--allow-non-target" in ARGS or os.environ.get("TTS8_ALLOW_NON_TARGET") == "1"
ONLY = None
for i, a in enumerate(ARGS):
    if a == "--models" and i + 1 < len(ARGS):
        ONLY = [x.strip() for x in ARGS[i + 1].split(",") if x.strip()]
    elif a.startswith("--models="):
        ONLY = [x.strip() for x in a.split("=", 1)[1].split(",") if x.strip()]
WITH_S4 = "--with-s4" in ARGS or os.environ.get("TTS8_S4") == "1"   # the 3-minute text is opt-in: on 8 GB it can run for hours
TIMEOUT = float(os.environ.get("TTS8_TIMEOUT", "480"))   # hard wall-clock limit for a short clip; the 3-minute text gets S4_FACTOR times this
S4_FACTOR = 3.0
MEM_WAIT_S = float(os.environ.get("TTS8_MEM_WAIT", "300"))   # only on a Hub that cannot bypass: a job held for memory this long is given up on
RELAX_GB = float(os.environ.get("TTS8_RELAX_FREE_GB", "0"))  # the free-memory requirement while a model is measured (0 = guard bypassed)
MARGIN_GB = 0.25         # the recommended requirement is the measured peak plus this
MIN_VERSION = (0, 6, 125)   # the per-model override route (the bypass) arrived in 0.6.125
SLOW_FACTOR = 3.0
STALL_S = 20.0           # a poll or a sampling pass that takes this long means the Mac was not responsive

# Order matters: smallest first. `repo` is the id the Hub's jobs name the model by.
MODELS = [
    dict(key="qwen3", name="Qwen3-TTS 0.6B 8-bit", csv_model="Qwen3-TTS 0.6B", quant="8bit",
         pkg="qwen3-tts-12hz-0-6b-base-8bit", repo="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"),
    dict(key="qwen3-4bit", name="Qwen3-TTS 0.6B 4-bit", csv_model="Qwen3-TTS 0.6B", quant="4bit",
         pkg="qwen3-tts-12hz-0-6b-base-4bit", repo="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit"),
    dict(key="voxcpm2", name="VoxCPM2 4-bit", csv_model="VoxCPM2", quant="4bit",
         pkg="voxcpm2-4bit", repo="mlx-community/VoxCPM2-4bit"),
    dict(key="omnivoice", name="OmniVoice bf16", csv_model="OmniVoice", quant="bf16",
         pkg="omnivoice-bfloat16", repo="mlx-community/OmniVoice-bfloat16"),
]
VOICES = [("chris", "cd07b21a2ccf", "Chris - Soft and Soothing"),
          ("christian", "0616aec33b41", "Christian - Slow & Calming")]
SCRIPT_FILES = [("S2", "tts-script-S2.txt", "about 175 words"), ("S4", "tts-script-S4.txt", "about 3 minutes")]
# Generation seconds on the 32 GB M2 Max (audit-20260930/tts-samples/summary.csv), same texts and voices.
# qwen3-4bit has no 32 GB reference time yet, so its clips are never flagged "slow" against one.
REF32 = {("qwen3", "chris", "S2"): 42.5, ("qwen3", "chris", "S4"): 105.1,
         ("qwen3", "christian", "S2"): 44.4, ("qwen3", "christian", "S4"): 126.0,
         ("voxcpm2", "chris", "S2"): 97.0, ("voxcpm2", "chris", "S4"): 270.5,
         ("voxcpm2", "christian", "S2"): 94.7, ("voxcpm2", "christian", "S4"): 303.7,
         ("omnivoice", "chris", "S2"): 80.3, ("omnivoice", "chris", "S4"): 198.9,
         ("omnivoice", "christian", "S2"): 86.3, ("omnivoice", "christian", "S4"): 243.1}
COLUMNS = ["model", "quant", "voice", "script", "audio_seconds", "generation_seconds", "rtf", "peak_memory_gb",
           "timing_clean", "chunks", "join_notes", "file", "timing_note", "source",
           "status", "error", "memory_pressure", "swap_before_gb", "swap_after_gb", "swap_peak_gb",
           "machine_ram_gb", "macos_version", "app_version", "chip",
           "ref_32gb_seconds", "vs_32gb", "slow_flag", "cold_start", "responsive", "max_stall_s",
           "queue_seconds", "footprint_before_gb", "worker_lifetime_peak_gb", "mlx_peak_active_gb", "mem_free_pct_min",
           "free_gb_before", "memory_released", "memory_holders", "needed_free_gb", "had_free_gb", "hub_reason",
           "guard_relaxed", "override_used_gb", "measured_peak_gb", "swap_delta_gb", "hub_free_floor_gb", "verdict", "worker_restarts"]

LOG = None
def say(msg=""):
    print(msg, flush=True)
    if LOG:
        LOG.write(msg + "\n"); LOG.flush()

def run(cmd, timeout=15):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""

def stop(code, title, *lines):
    print(f"\nSTOPPED: {title}\n", file=sys.stderr)
    for l in lines:
        print(f"  {l}" if l else "", file=sys.stderr)
    print("\nNothing was run and no results were written.", file=sys.stderr)
    sys.exit(code)

# ---------------------------------------------------------------- this Mac
MEM_BYTES = int((run(["sysctl", "-n", "hw.memsize"]).strip() or 0))
RAM_GB = round(MEM_BYTES / 1073741824, 1) if MEM_BYTES else None
OSV = run(["sw_vers", "-productVersion"]).strip() or "unknown"
CHIP = run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or run(["sysctl", "-n", "hw.model"]).strip() or "unknown"
ON_TARGET = bool(MEM_BYTES) and MEM_BYTES <= 9 * 1073741824

def swap_gb():
    m = re.search(r"used = ([\d.]+)([MG])", run(["sysctl", "-n", "vm.swapusage"]))
    return None if not m else round(float(m[1]) / (1024 if m[2] == "M" else 1), 3)

def pressure_level():   # 1 normal, 2 warning, 4 critical (kern.memorystatus_vm_pressure_level)
    try:
        return int(run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"]).strip())
    except ValueError:
        return None
PRESSURE_NAME = {1: "normal", 2: "warning", 4: "critical"}

def free_pct():
    m = re.search(r"free percentage: (\d+)%", run(["memory_pressure", "-Q"]))
    return int(m[1]) if m else None

def free_gb():
    """The figure the Hub's admission reads, computed the way components/host_memory.py does (0.6.125): what macOS can
    give a new job without swapping = max(psutil's free + speculative + inactive, free + file cache + purgeable pages),
    less the kernel's free-page target, never more than total - wired - compressor. The runner cannot import the app's
    code, so this mirrors it; tests/test_tts_8gb_runner.py pins the two to the same answers."""
    out = run(["vm_stat"])
    page = re.search(r"page size of (\d+) bytes", out)
    pg = {k: int(v) for k, v in re.findall(r"^(Pages [\w ]+?|File-backed pages):\s+(\d+)\.", out, re.M)}
    if not page or "Pages free" not in pg:
        return None
    ps = int(page[1])
    def sysctl(name):
        try:
            return int(run(["sysctl", "-n", name]).strip())
        except ValueError:
            return None
    psutil_avail = (pg["Pages free"] + pg.get("Pages speculative", 0) + pg.get("Pages inactive", 0)) * ps
    free, ext, purg, comp, margin = (sysctl(n) for n in ("vm.page_free_count", "vm.page_pageable_external_count",
        "vm.page_purgeable_count", "vm.compressor_bytes_used", "kern.vm_page_free_target"))
    if None in (free, ext, purg, comp, margin) or not MEM_BYTES:
        return round(psutil_avail / 1e9, 2)
    avail = min(max(psutil_avail, (free + ext + purg - margin) * ps), MEM_BYTES - pg.get("Pages wired down", 0) * ps - comp)
    return round(max(0, avail) / 1e9, 2)

def holders(n=6):
    """What holds this Mac's memory right now: wired + compressed, then the biggest processes (resident size; a Studio
    worker is named by its script). Compressed pages belong to no process, so they are listed on their own."""
    vm = run(["vm_stat"])
    page = re.search(r"page size of (\d+) bytes", vm)
    parts = []
    if page:
        for label, key in (("wired", r"Pages wired down"), ("compressed", r"Pages occupied by compressor")):
            m = re.search(key + r":\s+(\d+)\.", vm)
            if m:
                parts.append(f"{label} {int(m[1]) * int(page[1]) / 1e9:.1f} GB")
    by = {}
    for l in run(["ps", "-axo", "rss=,command="]).splitlines():
        m = re.match(r"\s*(\d+)\s+(.*)", l)
        if not m:
            continue
        w = re.search(r"/(\w+_worker)\.py", m[2])
        name = w[1] if w else (re.match(r"(.*?)(?: -|$)", m[2])[1].rsplit("/", 1)[-1][:40] or "?")
        by[name] = by.get(name, 0) + int(m[1]) * 1024
    parts += [f"{k} {v / 1e9:.1f} GB" for k, v in sorted(by.items(), key=lambda kv: -kv[1])[:n]]
    return "; ".join(parts)

def release_idle():
    """Ask the Hub to make every idle Studio let go of its model (the Hub never touches a running job). Returns plain words."""
    try:
        s, d = call("POST", "/api/hub/memory/release", {}, timeout=120)
    except HubDown as e:
        return f"the Hub did not answer ({e})"
    if s != 200 or not isinstance(d, dict):
        return f"the Hub refused ({s})"
    rows = d.get("results") or []
    return f"asked {len(rows)} Studio(s): " + ", ".join(f"{r.get('id')} {r.get('result')}" for r in rows)

NEEDS = re.compile(r"needs ~([\d.]+) ?GB[^~]*~([\d.]+) ?GB free")
def memory_wait(note):
    """(needed GB, had GB) from the Hub's 'waiting for memory: needs ~3.2GB, ~3.0GB free' reason, else None."""
    m = NEEDS.search(note or "")
    return (float(m[1]), float(m[2])) if m and "waiting for memory" in note else None

def ver_tuple(v):
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", str(v or ""))
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)

def app_version():
    out = run(["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleShortVersionString", str(APP / "Contents/Info.plist")]).strip()
    return out or "unknown"

# ---------------------------------------------------------------- the local Hub
READY = Path(os.environ.get("TTS8_READY_FILE") or Path.home() / "Library/Application Support/StudioHub Standalone/hub-ready.json")

class HubDown(Exception):
    pass

def call(method, path, body=None, raw=False, timeout=60):
    """One call to the local Hub. The token is read from the Hub's own file on every call and is never printed."""
    try:
        r = json.loads(READY.read_text())
        req = urllib.request.Request(r["url"] + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Hub-Token": r["token"], "Content-Type": "application/json"})
    except (OSError, ValueError, KeyError):
        raise HubDown("the Hub's ready file is missing or unreadable")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:600]
    except (urllib.error.URLError, OSError) as e:
        raise HubDown(str(getattr(e, "reason", e)))
    data = resp.read()
    return (resp.status, data) if raw else (resp.status, json.loads(data))

def hub_get(path, timeout=30):
    s, d = call("GET", path, timeout=timeout)
    return d if s == 200 and isinstance(d, dict) else None

RESTORE = READY.parent / "tts8-guard-restore.json"   # what to put back if this run dies with a guard relaxed
ADMISSION = "/api/hub/memory/admission"              # Hub 0.6.125+: the per-model free-memory override

class Guard:
    """The Hub's per-model free-memory requirement, bypassed (lowered to RELAX_GB, default 0) while a model is measured,
    and always put back: after the model, on any exit (finally, atexit, Ctrl-C, closing the window), and at the next start if
    an earlier run died. Never touches the default (e.g. 3.2 GB) - it sets and clears the operator override."""
    def __init__(self):
        self.active = {}   # repo -> the override that was there before (None = no override)

    def info(self, repo):
        try:
            s, d = call("GET", ADMISSION + "?model=" + urllib.parse.quote(repo))
        except HubDown:
            return None
        return d if s == 200 and isinstance(d, dict) else None

    def _put(self, repo, free, total=None):
        body = {"model": repo, "min_free_memory_gb": free}
        if total is not None:
            body["min_total_memory_gb"] = total
        try:
            return call("PUT", ADMISSION, body)[0] == 200
        except HubDown:
            return False

    def _save(self):
        try:
            RESTORE.write_text(json.dumps(self.active))
        except OSError:
            pass

    def relax(self, repo):
        """Bypass the guard for this model (requirement -> RELAX_GB). False if this Hub cannot (older than 0.6.125)."""
        d = self.info(repo)
        if not d:
            return False
        was = ({"free": d["effective_min_free_memory_gb"], "total": d["effective_min_total_memory_gb"]}
               if d.get("overridden") else None)
        self.active[repo] = was
        self._save()   # written BEFORE the Hub changes, so a crash right after still knows what to put back
        if self._put(repo, RELAX_GB, d["effective_min_total_memory_gb"]):
            return True
        self.active.pop(repo, None); self._save()
        return False

    def restore(self, repo):
        if repo not in self.active:
            return
        was = self.active[repo]
        args = [was["free"], was["total"]] if was else [None]
        ok = self._put(repo, *args)
        for _ in range(0 if ok else 6):   # the Hub may be restarting after a crash: give it a minute before giving up
            time.sleep(10)
            if self._put(repo, *args):
                ok = True; break
        if ok:
            self.active.pop(repo); self._save()
            say(f"    Guard put back for {repo}: " + (f"your override ({was['free']:g} GB)" if was else "the Hub's own requirement"))
        else:
            say(f"    WARNING: could not put the free-memory requirement back for {repo} (the Hub did not answer). "
                f"It is saved in {RESTORE}; the next run of this script puts it back first.")

    def restore_all(self):
        for repo in list(self.active):
            self.restore(repo)

    def restore_stale(self):
        """An earlier run died with a guard relaxed: put it back before doing anything else."""
        try:
            stale = json.loads(RESTORE.read_text())
        except (OSError, ValueError):
            return
        if isinstance(stale, dict) and stale:
            self.active = stale
            say("An earlier run of this script ended with a free-memory requirement lowered. Putting it back first ...")
            self.restore_all()

guard = Guard()

def active_jobs():
    d = hub_get("/api/hub/jobs") or {}
    return [b for b in d.get("batches", [])
            if sum(int(b.get(k) or 0) for k in ("queued", "running", "retrying", "cancel_requested")) > 0]

def describe(b):
    return f"{b.get('modality', '?')} job '{b.get('label') or b.get('id')}' ({b.get('model') or '?'})"

def wait_idle(max_wait=1800):
    """Never measure while the Mac is doing fleet work: it would be timing someone else's job."""
    t0, last = time.time(), -999
    while True:
        busy = active_jobs()
        if not busy:
            return True
        if time.time() - last >= 60:
            say(f"  This Mac is busy with Hub work right now ({'; '.join(describe(b) for b in busy[:3])}). "
                f"Waiting for it to finish - close this window to stop. ({int(time.time() - t0)} s so far)")
            last = time.time()
        if time.time() - t0 > max_wait:
            return False
        time.sleep(10)

# ---------------------------------------------------------------- pre-checks
def precheck():
    if not ON_TARGET and not QUICK and not ALLOW_NON_TARGET:
        stop(4, f"this Mac has {RAM_GB} GB of memory, and this test is about 8 GB Macs.",
             "On a bigger Mac the system has room to spare, so the memory numbers would describe this Mac, not an 8 GB one.",
             "", "  - Run it on the 8 GB Mac - that is the whole point.",
             "  - A short self-check on this Mac:   bash eight-gb-tts-run.sh --quick",
             "  - Run the full test here anyway (the summary will say it is not 8 GB evidence):",
             "        bash eight-gb-tts-run.sh --allow-non-target")
    try:
        d = json.loads(READY.read_text())
        if not hub_get("/api/hub/model-health", timeout=10):
            raise HubDown("no answer")
    except (OSError, ValueError, HubDown):
        stop(3, "Studio Hub is not running on this Mac.",
             "Open Studio Hub (Applications > Studio Hub), wait until its window says it is ready, then run this again.")
    hub_ver = d.get("version") or app_version()
    if ver_tuple(hub_ver) < MIN_VERSION:
        stop(3, f"the Studio Hub running here is version {hub_ver}; this test needs 0.6.125 or later (it bypasses the memory guard through a route that arrived in 0.6.125).",
             "Update this Mac from the kit (double-click 'Update this Mac'), open Studio Hub, then run this again.")
    models = [m for m in MODELS if ONLY is None or m["key"] in ONLY]
    if not models:
        stop(3, "--models named no known model.", "Use any of: " + ", ".join(m["key"] for m in MODELS))
    packs = {p.get("package_id"): p for p in (hub_get("/api/hub/model-packages") or {}).get("models", [])}
    health = {h.get("model_id"): h for h in (hub_get("/api/hub/model-health") or {}).get("models", [])}
    bad = []
    for m in models:
        p = packs.get(m["pkg"])
        if not p:
            bad.append(f"{m['name']}: this Hub does not list the package '{m['pkg']}'.")
        elif p.get("files_state") != "files_verified" or p.get("dispatch_state") != "eligible":
            bad.append(f"{m['name']}: not ready here (files: {p.get('files_state')}, usable: {p.get('dispatch_state')})."
                       + (f" {p.get('reason')}" if p.get("reason") else ""))
        h = health.get(m["repo"], {})
        if not bad and h.get("state") not in (None, "ready"):
            say(f"  Note: the Hub reports {m['name']} as '{h.get('state')}' ({h.get('reason') or 'no reason given'}).")
    if bad:
        stop(3, "a model this test needs is missing or not verified on this Mac.", *bad, "",
             "The kit's copy step puts these in place: double-click '2 - Copy the models' (or 'Update this Mac') from the kit,",
             "then open Studio Hub > Models and let it finish checking them. This test never copies or repairs anything itself.")
    voices = {v.get("id"): v for v in (hub_get("/studio/voice/api/voices") or {}).get("voices", [])}
    missing = [f"{n} ({i})" for _, i, n in VOICES if i not in voices]
    if missing:
        stop(3, "the owner voices are not on this Mac: " + "; ".join(missing) + ".",
             "The kit copies the voices as well: run 'Update this Mac' (or '2 - Copy the models') from the kit,",
             "then run this again. The same two voices are used for every model so the numbers can be compared.")
    texts = {}
    for key, fname, _ in SCRIPT_FILES:
        f = HERE / fname
        if not f.is_file():
            stop(3, f"{fname} is not beside this script.", "Keep it in the same folder (the kit's Technical folder has it).",
                 f"Looked in: {HERE}")
        texts[key] = f.read_text(encoding="utf-8").strip()
    return hub_ver, models, texts

# ---------------------------------------------------------------- measurement
def process_rows():
    rows = []
    for l in run(["ps", "-axo", "pid=,ppid=,pcpu=,command="]).splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+([\d.]+)\s+(.*)", l)
        if m:
            rows.append((int(m[1]), int(m[2]), float(m[3]), m[4]))
    return rows

def voice_pids(rows):
    kids = {}
    for p, pp, _, _ in rows:
        kids.setdefault(pp, []).append(p)
    todo = [p for p, _, _, c in rows if c.strip().startswith("Voice Studio Mac") or "voice_worker.py" in c]
    seen = []
    while todo:
        p = todo.pop()
        if p not in seen:
            seen.append(p); todo += kids.get(p, [])
    return seen

UNIT = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9}
def footprint(pid):
    out = run(["footprint", str(pid)], timeout=30)
    cur = peak = None
    m = re.search(r"Footprint:\s*([\d.]+)\s*(\w+)", out)
    if m:
        cur = float(m[1]) * UNIT.get(m[2].upper(), 1)
    m = re.search(r"phys_footprint_peak:\s*([\d.]+)\s*(\w+)", out)
    if m:
        peak = float(m[1]) * UNIT.get(m[2].upper(), 1)
    return cur, peak

def worker_footprint_now():
    return sum((footprint(p)[0] or 0) for p in voice_pids(process_rows()))

class Sampler(threading.Thread):
    """Every second while a clip runs: the voice worker tree's phys_footprint, swap, memory pressure."""
    def __init__(self):
        super().__init__(daemon=True)
        self.halt = False
        self.max_total = 0.0; self.lifetime_peak = 0.0; self.max_level = 1
        self.swap_peak = swap_gb() or 0.0; self.min_free = None
        self.max_stall = 0.0; self.other_cpu = []
    def run(self):
        while not self.halt:
            t0 = time.time()
            rows = process_rows(); pids = voice_pids(rows); total = 0.0
            for p in pids:
                cur, peak = footprint(p)
                total += cur or 0
                self.lifetime_peak = max(self.lifetime_peak, peak or 0)
            self.max_total = max(self.max_total, total)
            self.other_cpu.append(sum(c for p, _, c, _ in rows if p not in pids))
            s = swap_gb()
            if s is not None:
                self.swap_peak = max(self.swap_peak, s)
            lv = pressure_level()
            if lv:
                self.max_level = max(self.max_level, lv)
            f = free_pct()
            if f is not None:
                self.min_free = f if self.min_free is None else min(self.min_free, f)
            self.max_stall = max(self.max_stall, time.time() - t0)
            time.sleep(1.0)

def wav_seconds(b):
    if b[:4] != b"RIFF" or b[8:12] != b"WAVE":
        return None
    i = 12; rate = ch = bits = None
    while i + 8 <= len(b):
        cid, size = b[i:i + 4], int.from_bytes(b[i + 4:i + 8], "little")
        if cid == b"fmt ":
            ch = int.from_bytes(b[i + 10:i + 12], "little"); rate = int.from_bytes(b[i + 12:i + 16], "little")
            bits = int.from_bytes(b[i + 22:i + 24], "little")
        elif cid == b"data" and rate and ch and bits:
            n = size if i + 8 + size <= len(b) else len(b) - i - 8
            return n / (rate * ch * bits / 8)
        i += 8 + size + (size & 1)
    return None

def err_text(e):
    return re.sub(r"\s+", " ", e if isinstance(e, str) else json.dumps(e) if e else "")[:300]

def clip_limit(script_key):
    return TIMEOUT * (S4_FACTOR if script_key == "S4" else 1.0)

def clip(model, voice, script_key, text, out_dir, cold, ctx, relaxed=False):
    vkey, vid, _ = voice
    ref = REF32.get((model["key"], vkey, script_key))
    wav = out_dir / f"{model['pkg']}_{vkey}_{script_key}.wav"
    rec = {"model": model["csv_model"], "quant": model["quant"], "voice": vkey, "script": script_key,
           "file": wav.name, "machine_ram_gb": RAM_GB, "macos_version": OSV, "app_version": ctx["hub_ver"], "chip": CHIP,
           "ref_32gb_seconds": ref if ref is not None else "", "cold_start": "yes" if cold else "no",
           "source": f"Hub {ctx['hub_ver']}, worker process-tree footprint (sampled every 1 s), 8 GB test script",
           "guard_relaxed": "yes" if relaxed else "no", "override_used_gb": RELAX_GB if relaxed else "",
           "hub_free_floor_gb": ctx.get("floors", {}).get(model["repo"], "")}
    if not wait_idle():
        rec.update(status="not run", error="the Mac stayed busy with other Hub jobs for 30 minutes")
        return rec
    if cold:   # a new model is about to load: let idle Studios drop theirs first, then record what is free and what holds the rest
        rec["memory_released"] = release_idle()
        time.sleep(3)
        rec["memory_holders"] = holders()
        say(f"    Hub release of idle Studios: {rec['memory_released']}")
        say(f"    Memory held by: {rec['memory_holders']}")
    fg = free_gb()
    rec["free_gb_before"] = "" if fg is None else fg
    say(f"    Free memory as the Hub counts it: {rec['free_gb_before']} GB")
    swap0, fp0 = swap_gb(), worker_footprint_now()
    rec["swap_before_gb"] = "" if swap0 is None else swap0
    rec["footprint_before_gb"] = round(fp0 / 1e9, 2)
    sampler = Sampler(); sampler.start()
    t0 = time.time(); bid = None; state = None; item = {}; batch = {}; poll_max = 0.0; down_since = None
    mem_since = None; mem_need = None; hub_note = ""; restarts = 0; was_running = False
    limit = clip_limit(script_key)
    try:
        env = {"modality": "voice", "model": model["repo"], "label": "tts-8gb-run", "voice_library_id": vid,
               "clientRequestId": "tts-8gb-" + uuid.uuid4().hex[:12],
               "items": [{"text": text, "seed": 1234, "params": {}}]}
        s, r = call("POST", "/api/hub/jobs", env)
        if s != 200:
            raise RuntimeError(f"the Hub refused the job ({s}): {err_text(r)}")
        bid = r["batch_id"]; last = None
        while True:
            time.sleep(1.0)
            p0 = time.time()
            try:
                s, batch = call("GET", f"/api/hub/jobs/{bid}", timeout=60)
                down_since = None
            except HubDown as e:
                down_since = down_since or time.time()
                if time.time() - down_since > 300:
                    raise RuntimeError(f"the Hub stopped answering for 5 minutes ({e})")
                continue
            finally:
                poll_max = max(poll_max, time.time() - p0)
            item = (batch.get("items") or [{}])[0] if isinstance(batch, dict) else {}
            state = item.get("state") or (batch.get("state") if isinstance(batch, dict) else None)
            if state != last:
                say(f"    {state} at {time.time() - t0:.0f} s"); last = state
                if state == "running":
                    was_running = True
                elif state == "queued" and was_running:
                    restarts += 1   # started, then put back in the queue: the Hub restarted the job (the worker died or was evicted)
            if state in ("done", "error", "cancelled", "failed", "uncertain"):
                break
            note = (batch.get("governor_note") or "") if isinstance(batch, dict) else ""
            if state == "queued" and memory_wait(note):   # the Hub is holding this job back for free memory
                if note != hub_note:
                    say(f"    Hub: {note}")
                if mem_since is None:
                    mem_since = time.time()
                mem_need, hub_note = memory_wait(note), note
                if time.time() - mem_since > MEM_WAIT_S:
                    call("DELETE", f"/api/hub/jobs/{bid}"); state = "no_memory"; break
            else:
                mem_since = None
            if time.time() - t0 > limit:
                call("DELETE", f"/api/hub/jobs/{bid}"); state = "timeout"; break
        wall = time.time() - t0
        audio_s = None
        if state in ("no_memory", "timeout"):
            for _ in range(30):   # let the cancel settle so the next clip does not see a live job
                time.sleep(1)
                try:
                    if (call("GET", f"/api/hub/jobs/{bid}")[1].get("items") or [{}])[0].get("state") not in ("queued", "running", "retrying"):
                        break
                except (HubDown, AttributeError):
                    break
        if state == "no_memory":
            rec.update(status="did not start", needed_free_gb=mem_need[0], had_free_gb=mem_need[1], hub_reason=hub_note,
                       error=f"did not start - needs {mem_need[0]:g} GB free, had {mem_need[1]:g} GB; waited {wall / 60:.0f} min, job cancelled",
                       generation_seconds=round(wall, 1),
                       verdict=("does not fit - even with the guard lowered to %g GB the Hub held the job back" % RELAX_GB
                                if relaxed else "held back at the default guard"))
            sampler.halt = True; sampler.join(60)
            return rec
        if state == "done":
            s, data = call("GET", f"/api/hub/jobs/{bid}/items/0/artifact", raw=True, timeout=300)
            if s != 200:
                raise RuntimeError(f"the finished audio could not be fetched ({s})")
            wav.write_bytes(data)
            audio_s = wav_seconds(data)
        rec["status"] = "completed" if state == "done" else "timed out" if state == "timeout" else "failed"
        if state == "timeout":
            rec["error"] = (f"timed out: not finished within {int(limit)} s, job cancelled"
                            + (f" (the Hub restarted the job {restarts} time(s): the voice worker kept dying)" if restarts else ""))
        elif state != "done":
            rec["error"] = err_text(item.get("error") or state) + (f" (the Hub restarted the job {restarts} time(s) first)" if restarts else "")
    except KeyboardInterrupt:
        if bid:
            try: call("DELETE", f"/api/hub/jobs/{bid}")
            except Exception: pass
        sampler.halt = True
        raise
    except Exception as e:
        wall = time.time() - t0; audio_s = None
        rec["status"] = "failed"; rec["error"] = err_text(str(e))
    sampler.halt = True; sampler.join(60)
    swap1 = swap_gb()
    ru = (item.get("resource_usage") or {}) if isinstance(item, dict) else {}
    cpu = sampler.other_cpu
    mean_cpu = sum(cpu) / len(cpu) if cpu else 0
    stall = max(sampler.max_stall, poll_max)
    gen = round(wall, 1)
    rec.update(generation_seconds=gen, audio_seconds="" if audio_s is None else round(audio_s, 2),
               rtf="" if not audio_s else round(wall / audio_s, 2),
               peak_memory_gb=round(sampler.max_total / 1e9, 2) if sampler.max_total else "",
               timing_clean="yes" if mean_cpu <= 150 else "no",
               timing_note="" if mean_cpu <= 150 else f"contended: other processes avg {mean_cpu:.0f}% CPU",
               chunks=item.get("chunk_total") or "",
               join_notes=(f"{item['chunk_total']} chunks (joins not analysed by this script)"
                           if (item.get("chunk_total") or 0) > 1 else "single section, no join"),
               memory_pressure=PRESSURE_NAME.get(sampler.max_level, str(sampler.max_level)),
               swap_after_gb="" if swap1 is None else swap1, swap_peak_gb=round(sampler.swap_peak, 3),
               vs_32gb=round(wall / ref, 2) if ref else "",
               slow_flag="yes" if ref and wall > SLOW_FACTOR * ref else "no",
               responsive="yes" if stall <= STALL_S else "NO", max_stall_s=round(stall, 1),
               worker_lifetime_peak_gb=round(sampler.lifetime_peak / 1e9, 2) if sampler.lifetime_peak else "",
               mlx_peak_active_gb=(ru.get("mlx") or {}).get("peak_active_gb", ""),
               mem_free_pct_min="" if sampler.min_free is None else sampler.min_free, worker_restarts=restarts)
    peak = max(fnum(rec.get("peak_memory_gb")) or 0, fnum(rec.get("mlx_peak_active_gb")) or 0)
    rec.update(measured_peak_gb=round(peak, 2) if peak else "", swap_delta_gb=round(max(sampler.swap_peak - (swap0 or 0), 0), 3))
    if rec["status"] == "completed":
        rec["verdict"] = ("fits at default guard" if not relaxed
                          else f"fits only with relaxed guard - real need about {peak + MARGIN_GB:.1f} GB")
    elif rec["status"] in ("timed out", "failed"):
        rec["verdict"] = "does not fit - " + ("too slow, timed out" if rec["status"] == "timed out" else "crashed or errored")
    if isinstance(batch, dict) and isinstance(item, dict) and item.get("run_started") and batch.get("created_at"):
        rec["queue_seconds"] = round(item["run_started"] - batch["created_at"], 1)
    return rec

# ---------------------------------------------------------------- summary
def fnum(x):
    return float(x) if x not in ("", None) else None

def summarise(rows, models, title, hub_ver, started, finished_all):
    lines = [title, f"This Mac: {CHIP}, {RAM_GB} GB memory, macOS {OSV}, Studio Hub {hub_ver}",
             f"Run started {started}." + ("" if finished_all else " THE RUN WAS STOPPED EARLY - clips not listed were never run.")]
    if not ON_TARGET:
        lines.append(f"NOTE: this Mac has {RAM_GB} GB, not 8 GB. The numbers are real but are NOT 8 GB evidence.")
    if QUICK:
        lines.append("NOTE: quick check (S2, Chris only, one clip per model). It proves the script works; it is not the full test.")
    lines.append("")
    for m in models:
        mine = [r for r in rows if r["model"] == m["csv_model"] and r["quant"] == m["quant"]]
        if not mine:
            continue
        done = [r for r in mine if r["status"] == "completed"]
        relaxed_rows = [r for r in mine if r.get("guard_relaxed") == "yes"]
        failed = next((r for r in mine if r["status"] in ("timed out", "failed")), None)
        # held for memory and never rescued: this Hub cannot bypass the guard (older than 0.6.125) or held the job even with it bypassed
        refused = next((r for r in mine if r["status"] == "did not start"), None)
        first = mine[0]
        if first.get("memory_holders") or first.get("free_gb_before") != "":
            lines.append(f"{m['name']} - before loading: {first.get('free_gb_before')} GB free as the Hub counts it "
                         f"(Hub release of idle Studios: {first.get('memory_released') or 'not asked'}); "
                         f"held by: {first.get('memory_holders') or 'not recorded'}")
        peak = max([fnum(r.get("peak_memory_gb")) or 0 for r in mine] or [0])
        grow = max([(fnum(r.get("swap_peak_gb")) or 0) - (fnum(r.get("swap_before_gb")) or 0) for r in mine] or [0])
        worst = {"normal": 1, "warning": 2, "critical": 4}
        level = max([worst.get(r.get("memory_pressure"), 1) for r in mine] or [1])
        slow = [r for r in mine if r.get("slow_flag") == "yes"]
        stalled = [r for r in mine if r.get("responsive") == "NO"]
        ratios = [fnum(r.get("vs_32gb")) for r in done if fnum(r.get("vs_32gb"))]
        facts = [f"completed {len(done)}/{len(mine)}", f"peak {peak:.1f} GB" if peak else "peak not measured",
                 f"swap +{max(grow, 0):.1f} GB", f"memory pressure {PRESSURE_NAME[level] if level in PRESSURE_NAME else level}"]
        if ratios:
            facts.append(f"slowest clip {max(ratios):.1f}x the 32 GB time")
        if slow:
            facts.append(f"{len(slow)} clip(s) over {SLOW_FACTOR:.0f}x the 32 GB time")
        if stalled:
            facts.append(f"Mac stopped responding for 20 s+ during {len(stalled)} clip(s)")
        measured = max([fnum(r.get("measured_peak_gb")) or 0 for r in done] or [0])
        floor = first.get("hub_free_floor_gb")
        skipped = sum(r["status"] == "skipped" for r in mine)
        if failed and not done:
            verdict = (f"DOES NOT FIT: {failed['voice']} {failed['script']} {failed['status']} with the memory guard bypassed - {failed['error']}"
                       + (f"; the model's other {skipped} clip(s) were skipped" if skipped else ""))
        elif refused:
            verdict = (f"DID NOT START on this Mac: the Hub held the job back for lack of free memory - needs {refused['needed_free_gb']:g} GB free, "
                       f"had {refused['had_free_gb']:g} GB; waited {float(refused['generation_seconds']) / 60:.0f} min, then the job was cancelled "
                       f"and the model's other {skipped} clip(s) were skipped"
                       + (f" (still held with the guard bypassed)" if relaxed_rows else " (this Hub is too old to bypass the guard)"))
        elif failed:
            verdict = f"did not complete on this Mac (guard bypassed): {failed['voice']} {failed['script']} {failed['status']}"
        elif relaxed_rows and len(done) == len(mine) and (slow or stalled or level >= 4 or grow > 2):
            verdict = (f"RAN, BUT ONLY WITH THE MEMORY GUARD BYPASSED (the Hub's own requirement is {floor or '?'} GB free) and it strained this Mac; "
                       f"it needed about {measured + MARGIN_GB:.1f} GB")
        elif relaxed_rows and len(done) == len(mine):
            verdict = (f"FITS WITH THE GUARD BYPASSED: the Hub's own requirement is {floor or '?'} GB free; "
                       f"it needed about {measured + MARGIN_GB:.1f} GB")
        elif len(done) < len(mine):
            verdict = "did not complete on this Mac"
        elif slow or stalled or level >= 4 or grow > 2:
            verdict = "completed but strained this Mac"
        else:
            verdict = "fits" + (" 8 GB" if ON_TARGET else " this Mac (not 8 GB)")
        lines.append(f"{m['name']}: {', '.join(facts)} -> {verdict}")
        if done and measured:
            lines.append(f"    Recommended free-memory requirement: {measured + MARGIN_GB:.1f} GB (worker peak {measured:.2f} GB + {MARGIN_GB:g} GB margin; "
                         f"the Hub's default is {floor or 'not recorded'} GB). Measured on this {RAM_GB} GB Mac, in this run only"
                         + ("" if ON_TARGET else " - and it is not an 8 GB Mac, so not 8 GB evidence") + ".")
        for r in mine:
            if r["status"] not in ("completed", "skipped") and not (refused and r is refused) and not (failed and not done and r is failed):
                lines.append(f"    {r['voice']} {r['script']}: {r['status']} - {r.get('error') or 'no reason given'}")
    lines += ["", "How to read it: 'fits' means every clip completed, memory pressure never reached critical, swap grew by",
              "no more than 2 GB, no clip took over 3x its 32 GB time and the Mac kept answering. Anything else is stated.",
              "Peak is the largest memory the voice worker used (macOS 'footprint', sampled every second, so a very short",
              "spike can be missed; worker_lifetime_peak_gb in results.csv is macOS's own all-time peak for that process).",
              "The first clip of each model includes loading it. results.csv has every number per clip.",
              f"Memory guard: before each model the Hub's free-memory requirement was bypassed (set to {RELAX_GB:g} GB, TTS8_RELAX_FREE_GB) with no waiting, and put back after",
              "the model and on any exit. Nothing is cancelled for swap or memory pressure - they are recorded. Every clip has a hard time limit; one that",
              "runs over is 'timed out' (cancelled), one that dies is 'failed' with the error, and the model's remaining clips are skipped.",
              "'Recommended requirement' is the measured peak plus a small margin; it is one Mac's measurement, not a decision - nothing here changes the Hub's own number.",
              f"'DID NOT START' only appears on a Hub that cannot bypass the guard: the job waited more than {MEM_WAIT_S / 60:g} min for free memory (TTS8_MEM_WAIT)."]
    return "\n".join(lines) + "\n"

# ---------------------------------------------------------------- main
def main():
    try:   # keep the Mac awake while this runs (it ends with this script; no setting is changed)
        subprocess.Popen(["caffeinate", "-ims", "-w", str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass
    hub_ver, models, texts = precheck()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))   # closing the window or `kill`:
    signal.signal(signal.SIGHUP, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))    # run the clean-up below, not die in the middle
    guard.restore_stale()
    atexit.register(guard.restore_all)
    scripts = [(k, n, d) for k, n, d in SCRIPT_FILES if k == "S2" or (WITH_S4 and not QUICK)]
    voices = VOICES[:1] if QUICK else VOICES
    jobs = [(m, v, s) for m in models for v in voices for s in scripts]
    ref_total = sum(REF32.get((m["key"], v[0], s[0]), 0) for m, v, s in jobs)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = ("TTS 8GB Quick Check " if QUICK else "TTS 8GB Run ") + stamp
    for base in [Path(os.environ["TTS8_OUT"])] if os.environ.get("TTS8_OUT") else [HERE, Path.home() / "Desktop"]:
        try:
            out = base / name; (out).mkdir(parents=True)
            break
        except OSError:
            out = None
    if out is None:
        stop(3, "cannot create a results folder beside the script or on the Desktop.", "Check that the drive or Desktop is writable.")
    global LOG
    LOG = open(out / "run.log", "a", encoding="utf-8")
    title = "Studio Hub - text-to-speech on this Mac" + (" (QUICK CHECK)" if QUICK else "")
    say(title); say("")
    say(f"This Mac:       {CHIP}, {RAM_GB} GB memory, macOS {OSV}")
    say(f"Studio Hub:     version {hub_ver} (app on disk: {app_version()})")
    say("Models:         " + ", ".join(m["name"] for m in models) + "  (all present and verified)")
    say("Voices:         " + ", ".join(f"{n} ({i})" for _, i, n in voices) + "  (present)")
    pr = pressure_level()
    say(f"Memory now:     pressure {PRESSURE_NAME.get(pr, pr)}, {free_pct()}% free (macOS's own figure), "
        f"{free_gb()} GB usable by a new job (the Hub's figure), swap in use {swap_gb()} GB")
    say("Hub activity:  checking that the Mac is not busy with fleet work ...")
    if not wait_idle(1800):
        say("")
        say("STOPPED: this Mac stayed busy with other Hub jobs for 30 minutes, so nothing was measured")
        say("(timing someone else's job would make the numbers meaningless). Run this again when the Mac is idle.")
        return 3
    say("                OK - no active Hub jobs.")
    say("")
    say(f"{len(jobs)} clip(s), one at a time" + ("" if WITH_S4 or QUICK else " (the 3-minute text is off; --with-s4 adds it)")
        + f". The 32 GB Mac needed about {max(ref_total / 60, 1):.0f} minute(s) of generation for the same work.")
    worst = sum(clip_limit(s[0]) for s in scripts) / 60 * len(models)   # one clip per model can time out, then the model's others are skipped
    say(f"The Hub's free-memory guard is bypassed for each model while it is measured (put back afterwards); nothing is aborted for swap.")
    say(f"Every clip is stopped after {TIMEOUT / 60:g} min" + (f" ({TIMEOUT * S4_FACTOR / 60:g} min for the 3-minute text)" if WITH_S4 and not QUICK else "")
        + f"; a model whose clip times out or crashes is skipped and the next model goes on. At worst about {worst:.0f} minutes.")
    if QUICK:
        say("expect a few minutes here, mostly loading each model.")
    else:
        say("Leave the Mac alone, close other apps, keep it on power. You can stop any time by closing this window; what finished is already saved.")
    say(f"Results go to: {out}")
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    rows = []; finished = False
    ctx = {"hub_ver": hub_ver, "floors": {}}
    for m in models:   # the Hub's own requirement for each model, for the summary (Hub 0.6.125+; blank on an older one)
        d = guard.info(m["repo"])
        if d:
            ctx["floors"][m["repo"]] = d.get("effective_min_free_memory_gb")
    with open(out / "results.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, COLUMNS, extrasaction="ignore"); w.writeheader()
        seen = set(); refused = {}; relaxed = None   # refused: models that did not run to the end; relaxed: the model whose guard is bypassed right now
        try:
            for n, (m, v, s) in enumerate(jobs, 1):
                cold = m["key"] not in seen; seen.add(m["key"])
                if relaxed and relaxed["key"] != m["key"]:   # next model: this one's guard goes back first
                    guard.restore(relaxed["repo"]); relaxed = None
                say(f"\n[{n}/{len(jobs)}] {m['name']} - {v[2]} - {s[0]} ({s[2]})" + ("  [first clip: includes loading the model]" if cold else ""))
                if m["key"] in refused:   # this model cannot run here: do not spend more time on its other clips
                    rec = dict(model=m["csv_model"], quant=m["quant"], voice=v[0], script=s[0], status="skipped",
                               error=f"skipped - {m['name']} did not run: {refused[m['key']]['error']}",
                               machine_ram_gb=RAM_GB, macos_version=OSV, app_version=hub_ver, chip=CHIP)
                else:
                    if cold:   # bypass the Hub's free-memory guard for this model now, no waiting (False: a Hub that cannot)
                        if guard.relax(m["repo"]):
                            relaxed = m
                            say(f"    Memory guard bypassed for {m['name']} (free-memory requirement {RELAX_GB:g} GB; put back afterwards).")
                        else:
                            say(f"    This Hub cannot bypass the memory guard (needs 0.6.125+): {m['name']} runs at the Hub's own requirement.")
                    try:
                        rec = clip(m, v, s[0], texts[s[0]], out, cold, ctx, relaxed=bool(relaxed))
                    except HubDown as e:   # the Hub died under the load: record it, carry on
                        rec = dict(model=m["csv_model"], quant=m["quant"], voice=v[0], script=s[0], status="failed",
                                   error=f"Studio Hub stopped answering ({e}) - probably crashed", machine_ram_gb=RAM_GB,
                                   macos_version=OSV, app_version=hub_ver, chip=CHIP, guard_relaxed="yes" if relaxed else "no",
                                   verdict="does not fit - crashed or errored")
                    if rec["status"] != "completed":
                        refused[m["key"]] = rec
                rows.append(rec); w.writerow(rec); fh.flush()
                if rec["status"] == "completed":
                    say(f"    completed: {rec['generation_seconds']} s for {rec['audio_seconds']} s of audio (RTF {rec['rtf']}), "
                        f"peak {rec['peak_memory_gb']} GB, swap {rec['swap_before_gb']} -> {rec['swap_after_gb']} GB, "
                        f"pressure {rec['memory_pressure']}" + (f", SLOW: {rec['vs_32gb']}x the 32 GB time" if rec.get("slow_flag") == "yes" else "")
                        + ("" if rec.get("responsive") == "yes" else ", Mac stopped responding for a while"))
                else:
                    say(f"    {rec['status'].upper()}: {rec.get('error')}")
            finished = True
        except KeyboardInterrupt:
            say("\nStopped by you. What finished is saved.")
        finally:
            guard.restore_all()
    summary = summarise(rows, models, title, hub_ver, started, finished)
    (out / "summary.txt").write_text(summary, encoding="utf-8")
    say("\n" + "-" * 72); say(summary.rstrip()); say("-" * 72)
    say(f"Everything is in: {out}")
    say("  summary.txt (read this), results.csv (every number), the WAVs (listen), run.log")
    return 0 if finished else 130

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
__PYTHON__
