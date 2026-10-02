"""Image generation on an 8 GB Mac, through the installed Studio Hub: Z-Image-Turbo 4-bit (thumbnails) and
FLUX.2 Klein 4B 4-bit (scenes). Run it with eight-gb-image-run.sh (or RUN-8GB-IMAGE.command); see README-8gb-image.md.

The same measurement as the 8 GB text-to-speech test (eight-gb-tts-run.sh): each image is a Hub job (POST /api/hub/jobs,
modality "image"), so the Hub's own scheduler and Image worker run it as in production. The free-memory guard is bypassed for the
model being measured (PUT /api/hub/memory/admission, Hub 0.6.125+) and always put back; nothing is aborted for swap; every image has a
hard time limit; a crash or timeout is a row, and the next model goes on. Per image it records time, memory, swap, memory pressure and
what the internal SSD wrote (ssd_wear.py, shared with the TTS runner).

ponytail: the Hub client, Guard, sampler and memory helpers below are copied from eight-gb-tts-run.sh (its Python is embedded in a
shell file, so it cannot be imported). When that file is next opened, move the shared part into a module both import.
"""
import atexit, csv, json, os, re, signal, subprocess, sys, threading, time, urllib.error, urllib.parse, urllib.request, uuid
from pathlib import Path

HERE, ARGS = Path(sys.argv[1]), sys.argv[3:]
APP = Path(sys.argv[2])
sys.path.insert(0, str(HERE))
try:
    import ssd_wear   # beside this script: internal-SSD write counter and swapouts (shared with the TTS runner)
except ImportError:
    print(f"\nSTOPPED: ssd_wear.py is not beside this script.\n\n  Keep it in the same folder (the kit's Technical folder has it).\n  Looked in: {HERE}\n\nNothing was run and no results were written.", file=sys.stderr)
    sys.exit(3)

def arg_value(name):
    for i, a in enumerate(ARGS):
        if a == name and i + 1 < len(ARGS):
            return ARGS[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None

QUICK = "--quick" in ARGS or os.environ.get("IMG8_QUICK") == "1"
ALLOW_NON_TARGET = "--allow-non-target" in ARGS or os.environ.get("IMG8_ALLOW_NON_TARGET") == "1"
ONLY = [x.strip() for x in (arg_value("--models") or "").split(",") if x.strip()] or None
IMAGES = 1 if QUICK else int(arg_value("--images") or os.environ.get("IMG8_IMAGES", "3"))   # images per model
TIMEOUT = float(os.environ.get("IMG8_TIMEOUT", "900"))        # hard wall-clock limit per image
RELAX_GB = float(os.environ.get("IMG8_RELAX_FREE_GB", "0"))   # the free-memory requirement while a model is measured (0 = bypassed)
PER_DAY = float(os.environ.get("IMG8_PER_DAY", "200"))        # the TB/year illustration: images a day (an assumption, not a measurement)
BASELINE_S = float(os.environ.get("IMG8_BASELINE_S", "30"))   # idle seconds of SSD-write sampling before the first image (0 = skip)
SETTLE_S = float(os.environ.get("IMG8_SETTLE_S", "600"))      # after a cancel, how long to wait for the worker to finish the image it was on
MARGIN_GB = 0.25
MIN_VERSION = (0, 6, 125)   # the per-model override route (the bypass) arrived in 0.6.125
STALL_S = 20.0
SEED0 = 42                  # image n uses seed SEED0 + n - 1; the old standalone 8 GB test used 42 too
GUIDANCE = 1.0              # both models' own default (Z-Image-Turbo ignores it: distilled)

# What Story Studio actually asks the Hub for (it sends no size or steps of its own: the model's published defaults apply -
# components/image/app/backend/generation.py): 16:9 = 1280x720 for scenes and thumbnails alike, Z-Image-Turbo 9 steps, FLUX.2 Klein 4 steps.
# --quick uses the small 16:9 size (1024x576) and 2 steps (the smallest the lane accepts): it proves the script works, nothing more.
PROD_SIZE, QUICK_SIZE, QUICK_STEPS = (1280, 720), (1024, 576), 2
THUMB_PROMPT = ('YouTube sleep-story cover art, golden magical theme. Large elegant gold serif title text reading exactly "GOLDEN SLUMBER" '
                'across the top. A moonlit enchanted forest with a winding path, glowing golden fireflies and drifting sparkles, deep '
                'midnight-blue and gold palette, soft dreamy haze, cinematic, highly detailed.')
SCENE_PROMPT = ('A quiet moonlit village at the edge of a pine forest, snow on the rooftops, warm light in a few windows, a winding lane '
                'leading to a small stone bridge over a frozen stream, deep blue night sky full of stars, soft painterly storybook '
                'illustration, calm and cozy, wide cinematic composition.')
# Smallest first is not the point here: thumbnails (Z-Image) then scenes (Klein), the order the owner named them. `repo` is the id Hub jobs use.
MODELS = [
    dict(key="zimage", name="Z-Image-Turbo 4-bit", csv_model="Z-Image-Turbo", quant="4bit", role="thumbnail", steps=9,
         pkg="z-image-turbo-mflux-4bit", repo="filipstrand/Z-Image-Turbo-mflux-4bit", prompt=THUMB_PROMPT, low_ram_below_gb=24),
    dict(key="klein", name="FLUX.2 Klein 4B 4-bit", csv_model="FLUX.2 Klein 4B", quant="4bit", role="scene", steps=4,
         pkg="flux2-klein-4b-mlx-4bit", repo="AITRADER/FLUX2-klein-4B-mlx-4bit", prompt=SCENE_PROMPT, low_ram_below_gb=12),
]
# The old standalone 8 GB Z-Image-Turbo run (CHANGELOG, 2026-09-26, M1 8 GB, low-RAM, mflux direct, not through the Hub), for the summary.
OLD_STANDALONE = {"zimage": "the earlier standalone run (mflux direct, M1 8 GB, low-RAM, 1280x720): 336 s, 5.70 GB peak MLX, swap 1.3-1.9 GB"}
COLUMNS = ["model", "quant", "role", "image", "seed", "resolution", "steps", "low_ram", "memory_mode", "file", "source",
           "status", "error", "generation_seconds", "peak_memory_gb", "mlx_peak_active_gb", "worker_lifetime_peak_gb",
           "memory_pressure", "swap_before_gb", "swap_after_gb", "swap_peak_gb", "swap_delta_gb", "mem_free_pct_min",
           "machine_ram_gb", "macos_version", "app_version", "chip", "cold_start", "timing_clean", "timing_note", "responsive",
           "max_stall_s", "queue_seconds", "footprint_before_gb", "free_gb_before", "memory_released", "memory_holders",
           "guard_relaxed", "override_used_gb", "measured_peak_gb", "hub_free_floor_gb", "verdict", "worker_restarts",
           ] + ssd_wear.COLUMNS + ["cumulative_ssd_written_gb", "cumulative_ssd_written_net_gb", "cumulative_swapouts_gb"]

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

def fnum(x):
    try:
        return None if x in ("", None) else float(x)
    except (TypeError, ValueError):
        return None

# ---------------------------------------------------------------- this Mac
MEM_BYTES = int((run(["sysctl", "-n", "hw.memsize"]).strip() or 0))
RAM_GB = round(MEM_BYTES / 1073741824, 1) if MEM_BYTES else None
OSV = run(["sw_vers", "-productVersion"]).strip() or "unknown"
CHIP = run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or run(["sysctl", "-n", "hw.model"]).strip() or "unknown"
ON_TARGET = bool(MEM_BYTES) and MEM_BYTES <= 9 * 1073741824

def swap_gb():
    m = re.search(r"used = ([\d.]+)([MG])", run(["sysctl", "-n", "vm.swapusage"]))
    return None if not m else round(float(m[1]) / (1024 if m[2] == "M" else 1), 3)

def pressure_level():   # 1 normal, 2 warning, 4 critical
    try:
        return int(run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"]).strip())
    except ValueError:
        return None
PRESSURE_NAME = {1: "normal", 2: "warning", 4: "critical"}

def free_pct():
    m = re.search(r"free percentage: (\d+)%", run(["memory_pressure", "-Q"]))
    return int(m[1]) if m else None

def free_gb():
    """What macOS can give a new job without swapping, the way the Hub's admission counts it (components/host_memory.py, 0.6.125);
    copied from eight-gb-tts-run.sh."""
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
    """What holds this Mac's memory: wired + compressed, then the biggest processes (a Studio worker is named by its script)."""
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

def ver_tuple(v):
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", str(v or ""))
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)

def app_version():
    out = run(["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleShortVersionString", str(APP / "Contents/Info.plist")]).strip()
    return out or "unknown"

# ---------------------------------------------------------------- the local Hub
READY = Path(os.environ.get("IMG8_READY_FILE") or Path.home() / "Library/Application Support/StudioHub Standalone/hub-ready.json")

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

RESTORE = READY.parent / "image8-guard-restore.json"   # what to put back if this run dies with a guard relaxed (the TTS runner has its own file)
ADMISSION = "/api/hub/memory/admission"                # Hub 0.6.125+: the per-model free-memory override

class Guard:
    """The Hub's per-model free-memory requirement, bypassed (lowered to RELAX_GB, default 0) while a model is measured, and always
    put back: after the model, on any exit (finally, atexit, Ctrl-C, closing the window), and at the next start if an earlier run
    died. Never touches the default - it sets and clears the operator override."""
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
        """Bypass the guard for this model. False if this Hub cannot (older than 0.6.125)."""
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

def release_idle():
    """Ask the Hub to make every idle Studio let go of its model (the Hub never touches a running job)."""
    try:
        s, d = call("POST", "/api/hub/memory/release", {}, timeout=120)
    except HubDown as e:
        return f"the Hub did not answer ({e})"
    if s != 200 or not isinstance(d, dict):
        return f"the Hub refused ({s})"
    rows = d.get("results") or []
    return f"asked {len(rows)} Studio(s): " + ", ".join(f"{r.get('id')} {r.get('result')}" for r in rows)

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
             "  - A short self-check on this Mac:   bash eight-gb-image-run.sh --quick",
             "  - Run the full test here anyway (the summary will say it is not 8 GB evidence):",
             "        bash eight-gb-image-run.sh --allow-non-target")
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
    return hub_ver, models

# ---------------------------------------------------------------- measurement
def process_rows():
    rows = []
    for l in run(["ps", "-axo", "pid=,ppid=,pcpu=,command="]).splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+([\d.]+)\s+(.*)", l)
        if m:
            rows.append((int(m[1]), int(m[2]), float(m[3]), m[4]))
    return rows

def image_pids(rows):
    """The Image worker (image_worker.py) and anything it started."""
    kids = {}
    for p, pp, _, _ in rows:
        kids.setdefault(pp, []).append(p)
    todo = [p for p, _, _, c in rows if "image_worker.py" in c]
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
    return sum((footprint(p)[0] or 0) for p in image_pids(process_rows()))

class Sampler(threading.Thread):
    """Every second while an image runs: the Image worker tree's phys_footprint, swap, memory pressure."""
    def __init__(self):
        super().__init__(daemon=True)
        self.halt = False
        self.max_total = 0.0; self.lifetime_peak = 0.0; self.max_level = 1
        self.swap_peak = swap_gb() or 0.0; self.min_free = None
        self.max_stall = 0.0; self.other_cpu = []
    def run(self):
        while not self.halt:
            t0 = time.time()
            rows = process_rows(); pids = image_pids(rows); total = 0.0
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

WORKER_JOBS = READY.parent / "image" / "jobs.json"   # the Image worker's own job record: what the Hub does not pass on (memory mode, MLX peak)
def worker_job(repo, since, job_id=None):
    """The Image worker's record of the newest job for this model created after `since`, or None (read only)."""
    try:
        jobs = json.loads(WORKER_JOBS.read_text())
    except (OSError, ValueError):
        return None
    mine = [j for j in (jobs.values() if isinstance(jobs, dict) else []) if isinstance(j, dict)
            and ((job_id and j.get("id") == job_id) or (not job_id and (j.get("params") or {}).get("repo") == repo
                                                        and (j.get("created_at") or 0) >= since - 2))]
    return max(mine, key=lambda j: j.get("created_at") or 0, default=None)

def png_size(b):
    return (int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")) if b[:8] == b"\x89PNG\r\n\x1a\n" and len(b) >= 24 else None

def err_text(e):
    return re.sub(r"\s+", " ", e if isinstance(e, str) else json.dumps(e) if e else "")[:300]

def image(model, n, size, steps, out_dir, cold, ctx, relaxed=False):
    """One image through the Hub. Returns the results row; never raises for a crash, a timeout or a refused job."""
    seed = SEED0 + n - 1
    png = out_dir / f"{model['pkg']}_{n}_seed{seed}.png"
    rec = {"model": model["csv_model"], "quant": model["quant"], "role": model["role"], "image": n, "seed": seed,
           "resolution": f"{size[0]}x{size[1]}", "steps": steps, "file": png.name, "machine_ram_gb": RAM_GB, "macos_version": OSV,
           "app_version": ctx["hub_ver"], "chip": CHIP, "cold_start": "yes" if cold else "no",
           "source": f"Hub {ctx['hub_ver']}, Image worker process-tree footprint (sampled every 1 s), 8 GB image test",
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
    snap0 = ssd_wear.snapshot()   # internal-SSD writes and swapouts at the moment the job is sent
    rec["swap_before_gb"] = "" if swap0 is None else swap0
    rec["footprint_before_gb"] = round(fp0 / 1e9, 2)
    sampler = Sampler(); sampler.start()
    t0 = time.time(); bid = None; state = None; item = {}; batch = {}; poll_max = 0.0; down_since = None
    restarts = 0; was_running = False; png_bytes = None
    try:
        env = {"modality": "image", "model": model["repo"], "label": "image-8gb-run", "clientRequestId": "img-8gb-" + uuid.uuid4().hex[:12],
               "items": [{"prompt": model["prompt"], "seed": seed,
                          "params": {"width": size[0], "height": size[1], "steps": steps, "guidance": GUIDANCE}}]}
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
            if time.time() - t0 > TIMEOUT:
                call("DELETE", f"/api/hub/jobs/{bid}"); state = "timeout"; break
        wall = time.time() - t0
        if state == "timeout":   # a cancelled image may still be running inside the worker: do not start the next one on top of it
            for _ in range(int(SETTLE_S / 2)):
                time.sleep(2)
                try:
                    hub_state = (call("GET", f"/api/hub/jobs/{bid}")[1].get("items") or [{}])[0].get("state")
                except (HubDown, AttributeError):
                    break
                w = worker_job(model["repo"], t0)
                if hub_state not in ("queued", "running", "retrying") and (w is None or w.get("state") not in ("queued", "running", "cancel_requested")):
                    break
        if state == "done":
            s, data = call("GET", f"/api/hub/jobs/{bid}/items/0/artifact", raw=True, timeout=300)
            if s != 200:
                raise RuntimeError(f"the finished image could not be fetched ({s})")
            png.write_bytes(data); png_bytes = data
            if png_size(data) != size:
                raise RuntimeError(f"the image is {png_size(data)}, not the {size[0]}x{size[1]} that was asked for")
        rec["status"] = "completed" if state == "done" else "timed out" if state == "timeout" else "failed"
        if state == "timeout":
            rec["error"] = (f"timed out: not finished within {int(TIMEOUT)} s, job cancelled"
                            + (f" (the Hub restarted the job {restarts} time(s): the Image worker kept dying)" if restarts else ""))
        elif state != "done":
            rec["error"] = err_text(item.get("error") or state) + (f" (the Hub restarted the job {restarts} time(s) first)" if restarts else "")
    except KeyboardInterrupt:
        if bid:
            try: call("DELETE", f"/api/hub/jobs/{bid}")
            except Exception: pass
        sampler.halt = True
        raise
    except Exception as e:
        wall = time.time() - t0
        rec["status"] = "failed"; rec["error"] = err_text(str(e))
    sampler.halt = True; sampler.join(60)
    swap1 = swap_gb()
    rec.update(ssd_wear.clip_columns(snap0, ssd_wear.snapshot(), ctx.get("ssd_base")))
    wj = worker_job(model["repo"], t0, item.get("studio_job_id") if isinstance(item, dict) else None) or {}
    mode = wj.get("memory_mode") or ""
    mlx_gb = round(wj["peak_mlx_bytes"] / 1e9, 2) if isinstance(wj.get("peak_mlx_bytes"), int) else ""
    cpu = sampler.other_cpu
    mean_cpu = sum(cpu) / len(cpu) if cpu else 0
    stall = max(sampler.max_stall, poll_max)
    rec.update(generation_seconds=round(wall, 1),
               peak_memory_gb=round(sampler.max_total / 1e9, 2) if sampler.max_total else "", mlx_peak_active_gb=mlx_gb,
               worker_lifetime_peak_gb=round(sampler.lifetime_peak / 1e9, 2) if sampler.lifetime_peak else "",
               memory_mode=mode, low_ram=("yes" if mode == "low_ram" else "no") if mode else "",
               timing_clean="yes" if mean_cpu <= 150 else "no",
               timing_note="" if mean_cpu <= 150 else f"contended: other processes avg {mean_cpu:.0f}% CPU",
               memory_pressure=PRESSURE_NAME.get(sampler.max_level, str(sampler.max_level)),
               swap_after_gb="" if swap1 is None else swap1, swap_peak_gb=round(sampler.swap_peak, 3),
               swap_delta_gb=round(max(sampler.swap_peak - (swap0 or 0), 0), 3),
               responsive="yes" if stall <= STALL_S else "NO", max_stall_s=round(stall, 1),
               mem_free_pct_min="" if sampler.min_free is None else sampler.min_free, worker_restarts=restarts)
    peak = max(fnum(rec.get("peak_memory_gb")) or 0, fnum(mlx_gb) or 0)
    rec["measured_peak_gb"] = round(peak, 2) if peak else ""
    if rec["status"] == "completed":
        rec["verdict"] = "fits at default guard" if not relaxed else f"fits only with relaxed guard - real need about {peak + MARGIN_GB:.1f} GB"
    else:
        rec["verdict"] = "does not fit - " + ("too slow, timed out" if rec["status"] == "timed out" else "crashed or errored")
    if isinstance(batch, dict) and isinstance(item, dict) and item.get("run_started") and batch.get("created_at"):
        rec["queue_seconds"] = round(item["run_started"] - batch["created_at"], 1)
    return rec

def add_cumulative(rec, prior):
    """Running totals over this model's images so far (this one included), from the per-image columns."""
    for col, src in (("cumulative_ssd_written_gb", "ssd_written_gb"), ("cumulative_ssd_written_net_gb", "ssd_written_net_gb"),
                     ("cumulative_swapouts_gb", "swapouts_gb")):
        vals = [fnum(r.get(src)) for r in prior + [rec]]
        rec[col] = round(sum(v for v in vals if v is not None), 3) if any(v is not None for v in vals) else ""

# ---------------------------------------------------------------- summary
def tb_per_year(gb_per_image):
    return gb_per_image * PER_DAY * 365 / 1000

def summarise(rows, models, title, hub_ver, started, finished_all):
    lines = [title, f"This Mac: {CHIP}, {RAM_GB} GB memory, macOS {OSV}, Studio Hub {hub_ver}",
             f"Run started {started}." + ("" if finished_all else " THE RUN WAS STOPPED EARLY - images not listed were never run.")]
    if not ON_TARGET:
        lines.append(f"NOTE: this Mac has {RAM_GB} GB, not 8 GB. The numbers are real but are NOT 8 GB evidence.")
    if QUICK:
        lines.append(f"NOTE: quick check (1 image per model at {QUICK_SIZE[0]}x{QUICK_SIZE[1]}, {QUICK_STEPS} steps, not the sizes Story Studio uses). "
                     "It proves the script works; it is not the test.")
    lines.append("")
    worst = {"normal": 1, "warning": 2, "critical": 4}
    for m in models:
        mine = [r for r in rows if r["model"] == m["csv_model"] and r["quant"] == m["quant"]]
        if not mine:
            continue
        done = [r for r in mine if r["status"] == "completed"]
        failed = next((r for r in mine if r["status"] in ("timed out", "failed")), None)
        first = mine[0]
        relaxed_rows = [r for r in mine if r.get("guard_relaxed") == "yes"]
        secs = [fnum(r["generation_seconds"]) for r in done if fnum(r.get("generation_seconds"))]
        peak = max([fnum(r.get("peak_memory_gb")) or 0 for r in mine] or [0])
        mlx = max([fnum(r.get("mlx_peak_active_gb")) or 0 for r in mine] or [0])
        grow = max([fnum(r.get("swap_delta_gb")) or 0 for r in mine] or [0])
        swap_peak = max([fnum(r.get("swap_peak_gb")) or 0 for r in mine] or [0])
        level = max([worst.get(r.get("memory_pressure"), 1) for r in mine] or [1])
        stalled = [r for r in mine if r.get("responsive") == "NO"]
        modes = sorted({r["memory_mode"] for r in mine if r.get("memory_mode")})
        measured = max([fnum(r.get("measured_peak_gb")) or 0 for r in done] or [0])
        floor = first.get("hub_free_floor_gb")
        skipped = sum(r["status"] == "skipped" for r in mine)
        head = (f"{m['name']} ({m['role']}s, {first['resolution']}, {first['steps']} steps; "
                + ("memory mode: " + ", ".join(modes) if modes else "memory mode not reported") + ")")
        lines.append(head)
        held = fnum(first.get("footprint_before_gb")) or 0
        if held > 1.0:
            lines.append(f"    NOTE: the Image worker already held {held:.1f} GB when this model's first image started (left from earlier work), and the MLX "
                         "peak is the worker's all-time high-water mark (the worker never resets it), so the peak figures below may include that.")
        busy = [r for r in mine if r.get("timing_clean") == "no"]
        if busy:
            lines.append(f"    NOTE: other processes were busy during {len(busy)} of these images ({busy[0]['timing_note']}); times, swap and SSD writes include their work. "
                         "Run it with nothing else going for clean numbers.")
        if first.get("memory_holders") or first.get("free_gb_before") != "":
            lines.append(f"    Before loading: {first.get('free_gb_before')} GB free as the Hub counts it "
                         f"(Hub release of idle Studios: {first.get('memory_released') or 'not asked'}); held by: {first.get('memory_holders') or 'not recorded'}")
        facts = [f"completed {len(done)}/{len(mine)} images"]
        if secs:
            facts.append(f"{sum(secs) / len(secs):.0f} s per image" + (f" (fastest {min(secs):.0f}, slowest {max(secs):.0f})" if len(secs) > 1 else ""))
        facts += [f"peak memory {peak:.1f} GB by the worker's footprint" + (f", {mlx:.1f} GB by MLX" if mlx else "") if peak else "peak not measured",
                  f"swap grew by up to {grow:.1f} GB in one image (most in use {swap_peak:.1f} GB)",
                  f"memory pressure {PRESSURE_NAME.get(level, level)}"]
        if stalled:
            facts.append(f"the Mac stopped responding for 20 s+ during {len(stalled)} image(s)")
        lines.append("    " + "; ".join(facts) + ".")
        if failed and not done:
            verdict = (f"DOES NOT FIT: image {failed['image']} {failed['status']} with the memory guard bypassed - {failed['error']}"
                       + (f"; the model's other {skipped} image(s) were skipped" if skipped else ""))
        elif failed:
            verdict = f"did not finish every image (guard bypassed): image {failed['image']} {failed['status']}"
        elif relaxed_rows and (stalled or level >= 4 or grow > 2):
            verdict = (f"RAN, BUT ONLY WITH THE MEMORY GUARD BYPASSED (the Hub's own requirement is {floor or '?'} GB free) and it strained this Mac; "
                       f"it needed about {measured + MARGIN_GB:.1f} GB")
        elif relaxed_rows:
            verdict = f"FITS WITH THE GUARD BYPASSED: the Hub's own requirement is {floor or '?'} GB free; it needed about {measured + MARGIN_GB:.1f} GB"
        elif stalled or level >= 4 or grow > 2:
            verdict = "completed but strained this Mac"
        else:
            verdict = "fits" + (" 8 GB" if ON_TARGET else " this Mac (not 8 GB)")
        lines.append(f"    Verdict: {verdict}")
        if done and measured:
            lines.append(f"    Recommended free-memory requirement: {measured + MARGIN_GB:.1f} GB (peak {measured:.2f} GB + {MARGIN_GB:g} GB margin; the Hub's "
                         f"default is {floor or 'not recorded'} GB). One Mac, this run only" + ("" if ON_TARGET else " - and not an 8 GB Mac") + ".")
        # Swap and SSD writes, image by image and running total: does it pile up over consecutive jobs?
        steps_line = []
        for r in mine:
            if r["status"] == "skipped":
                continue
            steps_line.append(f"image {r['image']}: {r.get('swapouts_gb', '') if r.get('swapouts_gb', '') != '' else '?'} GB swapped out, "
                              f"{r.get('ssd_written_net_gb') if r.get('ssd_written_net_gb') not in ('', None) else '?'} GB written to the SSD (net)")
        if steps_line:
            lines.append("    Over consecutive images - " + "; ".join(steps_line)
                         + f". Running total {mine[-1].get('cumulative_swapouts_gb') or '?'} GB swapped out, "
                           f"{mine[-1].get('cumulative_ssd_written_net_gb') or '?'} GB written (net).")
        net = [fnum(r.get("ssd_written_net_gb")) for r in done]
        net = [v for v in net if v is not None]
        if net:
            # ssd_wear's own sentence (net GB per unit, with the swapouts of that window); its hours-a-day line is for audio, ours is images a day.
            per_image = ssd_wear.wear_lines([dict(r, images=1) for r in done], unit="image", units_key="images", units_per=1.0)[0].replace("clip(s)", "image(s)")
            lines.append("    " + per_image.replace("About ", "≈ ", 1))
            lines.append(f"    At {PER_DAY:g} images a day that is ≈ {tb_per_year(sum(net) / len(net)):.1f} TB a year written to the SSD "
                         f"({PER_DAY:g} images a day is an assumption for illustration, not a measurement - IMG8_PER_DAY changes it; "
                         f"it also assumes every image costs what these {len(net)} did).")
        elif done:
            lines.append("    SSD writes: not measured (the internal disk was not found, or a counter was missing) - see results.csv.")
        if m["key"] in OLD_STANDALONE:
            lines.append(f"    For comparison, {OLD_STANDALONE[m['key']]}.")
        for r in mine:
            if r["status"] not in ("completed", "skipped") and not (failed and not done and r is failed):
                lines.append(f"    image {r['image']}: {r['status']} - {r.get('error') or 'no reason given'}")
        lines.append("")
    lines += ["How to read it: 'fits' means every image completed, memory pressure never reached critical, swap grew by no more than 2 GB in any one",
              "image and the Mac kept answering. Anything else is stated. Peak memory is the Image worker's footprint (macOS 'footprint', sampled every",
              "second, so a very short spike can be missed) and MLX's own peak as the worker reports it; the first image of each model also pays for loading",
              "it (the Image worker drops the model after every job, so every image loads it again).",
              "SSD writes: bytes written to this Mac's INTERNAL disk while the image ran (ioreg, no sudo; the external kit SSD is excluded), swapouts from",
              f"vm_stat, and the idle baseline measured for {BASELINE_S:g} s before the first image; net = gross less baseline x the image's duration. Decimal GB.",
              "Everything the Mac writes in that window counts, not only swap, which is why the baseline is taken off. TB/year is simple arithmetic on this run's",
              "average, not a prediction of drive life: it says how much would be written, not when a drive wears out.",
              f"Memory guard: before each model the Hub's free-memory requirement was bypassed (set to {RELAX_GB:g} GB, IMG8_RELAX_FREE_GB) with no waiting, and put",
              "back after the model and on any exit. Nothing is cancelled for swap or memory pressure - they are recorded. Every image has a hard time limit",
              f"({TIMEOUT / 60:g} min, IMG8_TIMEOUT); one that runs over is 'timed out' (cancelled), one that dies is 'failed' with the error, and the model's remaining",
              "images are skipped."]
    return "\n".join(lines) + "\n"

# ---------------------------------------------------------------- main
def main():
    try:   # keep the Mac awake while this runs (it ends with this script; no setting is changed)
        subprocess.Popen(["caffeinate", "-ims", "-w", str(os.getpid())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass
    hub_ver, models = precheck()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))   # closing the window or `kill`:
    signal.signal(signal.SIGHUP, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))    # run the clean-up below, not die in the middle
    guard.restore_stale()
    atexit.register(guard.restore_all)
    size, jobs_n = (QUICK_SIZE if QUICK else PROD_SIZE), len(models) * IMAGES
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = ("Image 8GB Quick Check " if QUICK else "Image 8GB Run ") + stamp
    for base in [Path(os.environ["IMG8_OUT"])] if os.environ.get("IMG8_OUT") else [HERE, Path.home() / "Desktop"]:
        try:
            out = base / name; out.mkdir(parents=True)
            break
        except OSError:
            out = None
    if out is None:
        stop(3, "cannot create a results folder beside the script or on the Desktop.", "Check that the drive or Desktop is writable.")
    global LOG
    LOG = open(out / "run.log", "a", encoding="utf-8")
    title = "Studio Hub - image generation on this Mac" + (" (QUICK CHECK)" if QUICK else "")
    say(title); say("")
    say(f"This Mac:       {CHIP}, {RAM_GB} GB memory, macOS {OSV}")
    say(f"Studio Hub:     version {hub_ver} (app on disk: {app_version()})")
    say("Models:         " + ", ".join(f"{m['name']} ({m['role']}s)" for m in models) + "  (all present and verified)")
    say(f"Each image:     {size[0]}x{size[1]}, " + ("2 steps (quick check)" if QUICK else ", ".join(f"{m['name'].split(' 4')[0]} {m['steps']} steps" for m in models))
        + f", seeds {SEED0}-{SEED0 + IMAGES - 1}, {IMAGES} image(s) per model, one at a time")
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
    base = None
    if BASELINE_S > 0:
        say(f"Measuring this Mac's background SSD writes for {BASELINE_S:g} s while it is idle (so they can be taken off each image) ...")
        base = ssd_wear.baseline_gb_per_min(BASELINE_S)
        say("  internal SSD, idle: " + ("not measurable here (internal disk or counter not found); the ssd_* columns stay empty" if base is None else f"{base:.3f} GB per minute"))
        say("")
    say(f"{jobs_n} image(s). The Hub's free-memory guard is bypassed for each model while it is measured (put back afterwards); nothing is aborted for swap.")
    say(f"Every image is stopped after {TIMEOUT / 60:g} min; a model whose image times out or crashes is skipped and the next model goes on. "
        f"At worst about {TIMEOUT / 60 * len(models):.0f} minutes plus loading.")
    say("Quick check: expect a few minutes, mostly loading each model." if QUICK else
        "Leave the Mac alone, close other apps, keep it on power. You can stop any time by closing this window; what finished is already saved.")
    say(f"Results go to: {out}")
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    rows = []; finished = False
    ctx = {"hub_ver": hub_ver, "floors": {}, "ssd_base": base}
    for m in models:   # the Hub's own requirement for each model, for the summary (Hub 0.6.125+; blank on an older one)
        d = guard.info(m["repo"])
        if d:
            ctx["floors"][m["repo"]] = d.get("effective_min_free_memory_gb")
    with open(out / "results.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, COLUMNS, extrasaction="ignore"); w.writeheader()
        refused = {}; relaxed = None   # refused: models that did not run to the end; relaxed: the model whose guard is bypassed right now
        try:
            for m in models:
                steps = QUICK_STEPS if QUICK else m["steps"]
                mine = []
                if relaxed:   # next model: the previous one's guard goes back first
                    guard.restore(relaxed["repo"]); relaxed = None
                for n in range(1, IMAGES + 1):
                    cold = n == 1
                    say(f"\n[{m['name']}] image {n}/{IMAGES} - {m['role']}, {size[0]}x{size[1]}, {steps} steps, seed {SEED0 + n - 1}"
                        + ("  [first image: includes loading the model]" if cold else ""))
                    if m["key"] in refused:   # this model cannot run here: do not spend more time on its other images
                        rec = dict(model=m["csv_model"], quant=m["quant"], role=m["role"], image=n, seed=SEED0 + n - 1, resolution=f"{size[0]}x{size[1]}",
                                   steps=steps, status="skipped", error=f"skipped - an earlier image failed: {refused[m['key']]['error']}",
                                   machine_ram_gb=RAM_GB, macos_version=OSV, app_version=hub_ver, chip=CHIP)
                    else:
                        if cold:   # bypass the Hub's free-memory guard for this model now, no waiting (False: a Hub that cannot)
                            if guard.relax(m["repo"]):
                                relaxed = m
                                say(f"    Memory guard bypassed for {m['name']} (free-memory requirement {RELAX_GB:g} GB; put back afterwards).")
                            else:
                                say(f"    This Hub cannot bypass the memory guard (needs 0.6.125+): {m['name']} runs at the Hub's own requirement.")
                        try:
                            rec = image(m, n, size, steps, out, cold, ctx, relaxed=bool(relaxed))
                        except HubDown as e:   # the Hub died under the load: record it, carry on
                            rec = dict(model=m["csv_model"], quant=m["quant"], role=m["role"], image=n, seed=SEED0 + n - 1,
                                       resolution=f"{size[0]}x{size[1]}", steps=steps, status="failed",
                                       error=f"Studio Hub stopped answering ({e}) - probably crashed", machine_ram_gb=RAM_GB,
                                       macos_version=OSV, app_version=hub_ver, chip=CHIP, guard_relaxed="yes" if relaxed else "no",
                                       verdict="does not fit - crashed or errored")
                        if rec["status"] != "completed":
                            refused[m["key"]] = rec
                    add_cumulative(rec, mine)
                    mine.append(rec); rows.append(rec); w.writerow(rec); fh.flush()
                    if rec["status"] == "completed":
                        say(f"    completed: {rec['generation_seconds']} s, peak {rec['peak_memory_gb']} GB, swap {rec['swap_before_gb']} -> {rec['swap_after_gb']} GB, "
                            f"pressure {rec['memory_pressure']}, memory mode {rec.get('memory_mode') or '?'}"
                            + (f", SSD written {rec['ssd_written_gb']} GB (net {rec['ssd_written_net_gb'] if rec['ssd_written_net_gb'] != '' else 'n/a'}), "
                               f"swapouts {rec['swapouts_gb']} GB; running total {rec['cumulative_ssd_written_gb']} GB written" if rec.get("ssd_written_gb") != "" else "")
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
    say("  summary.txt (read this), results.csv (every number), the PNGs (look), run.log")
    return 0 if finished else 130

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
