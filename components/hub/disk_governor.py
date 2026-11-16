"""One governor decides what a Mac gives back when its disk fills.

It runs in the Hub's existing cleanup thread (`output_storage.start_background`): once at start-up
and every five minutes, not only when a job is submitted. It reads free space and puts the Mac in a
tier:

    green   free >= 12% of the disk
    amber   free <  12%
    red     free <   6%
    stop    free <   5 GB      (new work is refused; the existing admission floor does the refusing)

Housekeeping (temp and partial files, leftover release downloads, orphaned app copies) runs in every
tier. In amber and red the governor goes on, in this order, and stops the moment free space is back
to green:

    1 temp / partial files      4 orphans: snapshot views of absent models, partial model staging,
    2 logs                        Hugging Face mirror entries of packages that are not installed
    3 stale Updates, orphan     5 acked outputs, oldest first (red may take ones younger than the
      .studiohub-old/-staged      retention window), then the finished transcription spool

Only scratch is deleted: temp and partial files, rotated logs, snapshot views (links into the
library) and partial model staging. Everything else it retires - downloads, app copies, model
mirrors, outputs, the spool - is MOVED into the Hub's Trash Folder (`output_storage.retire`), which
frees no space on the same volume; a move that fails leaves the item where it is. The owner empties
the Trash Folder through macOS Trash; the governor only reports its size against a cap.

It NEVER touches an installed model, an output that is unacked or leased, or anything in the Hub's
Trash Folder or any `_trash` folder (those are the owner's to empty).

Sizes are PHYSICAL. An APFS clone shares blocks with the file it was cloned from (a previous app
copy is usually a clone of the live app), so a logical size says nothing about what deleting it
gives back. `private_size` asks the file system for the blocks a file does not share.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import json
import logging
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from . import output_storage
from .backend import ledger

log = logging.getLogger("studiohub.disk_governor")

GB = 1_000_000_000
AMBER_FRACTION = 0.12            # free below this share of the disk: amber
RED_FRACTION = 0.06              # ... red
STOP_BYTES = 5 * GB              # free below this: stop new work
TRASH_CAP_BYTES = 15 * GB        # Trash Folder cap = min(this, TRASH_CAP_FRACTION of the disk), physical
TRASH_CAP_FRACTION = 0.06
HISTORY_DAYS = 90                # hub.db batch history
TEMP_IDLE_S = 6 * 3600           # a partial file untouched this long belongs to no running job
ORPHAN_APP_IDLE_S = 3600         # .studiohub-old-* / .studiohub-staged-* / .studiohub-tmp-*
STAGING_IDLE_S = 24 * 3600       # partial model download nobody has touched (green)
STAGING_IDLE_RED_S = 3600        # ... when space is short
MEASURE_EVERY_S = 3600           # apps and models are big walks: remeasured this often
HISTORY_EVERY_S = 24 * 3600
LOG_NAMES = ("hub-worker.log", "hub-worker.log.1")
OLD_PREFIX = ".studiohub-old-"
STAGED_PREFIX = ".studiohub-staged-"
TMP_PREFIX = ".studiohub-tmp-"
_VIEW = re.compile(r"(?P<package>.+)-(?P<revision>[0-9a-f]{16})")
_PARTIAL_SUFFIXES = (".partial.wav", ".partial", ".part", ".tmp")
_TEMP_DIR_PREFIXES = ("voice_sections_", "mlxaudio_")
_LOCK = threading.RLock()


def disk_usage(path):
    """The one place free space is read, so a test can say how full the disk is."""
    return shutil.disk_usage(path)


# ── tiers ──────────────────────────────────────────────────────────────────

def tier(free: int, total: int) -> str:
    if free < STOP_BYTES:
        return "stop"
    if total and free < RED_FRACTION * total:
        return "red"
    if total and free < AMBER_FRACTION * total:
        return "amber"
    return "green"


def trash_cap_bytes(total: int) -> int:
    return int(min(TRASH_CAP_BYTES, TRASH_CAP_FRACTION * total))


# ── physical size ──────────────────────────────────────────────────────────

_LIBC = None


class _AttrList(ctypes.Structure):
    _fields_ = [("bitmapcount", ctypes.c_ushort), ("reserved", ctypes.c_uint16),
                ("commonattr", ctypes.c_uint32), ("volattr", ctypes.c_uint32),
                ("dirattr", ctypes.c_uint32), ("fileattr", ctypes.c_uint32),
                ("forkattr", ctypes.c_uint32)]


def _fs_private_size(path: str) -> int | None:
    """`ATTR_CMNEXT_PRIVATESIZE` through getattrlist(2): the bytes this file holds that no clone
    shares, i.e. what deleting it would give back. None when the file system cannot say."""
    global _LIBC
    if sys.platform != "darwin":
        return None
    try:
        if _LIBC is None:
            _LIBC = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        request = _AttrList(5, 0, 0x80000000, 0, 0, 0, 0x8)   # RETURNED_ATTRS + CMNEXT_PRIVATESIZE
        buffer = ctypes.create_string_buffer(64)
        # FSOPT_ATTR_CMN_EXTENDED | FSOPT_NOFOLLOW
        if _LIBC.getattrlist(os.fsencode(path), ctypes.byref(request), buffer, 64, 0x20 | 0x1) != 0:
            return None
        return max(0, struct.unpack_from("q", buffer, 24)[0])
    except (OSError, ValueError, AttributeError, struct.error):
        return None


def private_size(path: str, info: os.stat_result | None = None) -> int:
    """Physical bytes of one file that deleting it frees. Falls back to allocated blocks (which
    over-counts a clone, the safe direction for a cleanup) where the file system cannot say."""
    value = _fs_private_size(path)
    if value is not None:
        return value
    info = info or os.lstat(path)
    return info.st_blocks * 512


def physical_bytes(path: Path) -> int:
    """Physical bytes under `path`: unshared blocks only, a hard-linked file counted once."""
    path = Path(path)
    try:
        info = os.lstat(path)
    except OSError:
        return 0
    if not stat.S_ISDIR(info.st_mode):
        return 0 if stat.S_ISLNK(info.st_mode) else private_size(str(path), info)
    total, seen = 0, set()
    for base, _dirs, names in os.walk(path):
        for name in names:
            full = os.path.join(base, name)
            try:
                item = os.lstat(full)
                if stat.S_ISLNK(item.st_mode) or stat.S_ISDIR(item.st_mode):
                    continue
                if item.st_nlink > 1:
                    key = (item.st_dev, item.st_ino)
                    if key in seen:
                        continue
                    seen.add(key)
                total += private_size(full, item)
            except OSError:
                continue
    return total


# A previous app copy is ~90,000 files and measuring it costs ~11 s (one getattrlist each). A Trash
# entry does not change once it is there, so its size is remembered for an hour (and re-measured
# at once if the entry itself changes).
SIZE_CACHE_S = 3600.0
_SIZE_CACHE: dict[str, tuple[float, float, int]] = {}


def cached_physical(path: Path, now: float) -> int:
    key, stamp = str(path), _mtime(path)
    hit = _SIZE_CACHE.get(key)
    if hit and hit[1] == stamp and 0 <= now - hit[0] < SIZE_CACHE_S:
        return hit[2]
    size = physical_bytes(path)
    _SIZE_CACHE[key] = (now, stamp, size)
    return size


def logical_allocated(path: Path) -> int:
    """Allocated bytes under `path` (what `du` shows), hard links counted once. For the 'where did
    the disk go' picture of the app and the model library, which are not reclaimed."""
    total, seen = 0, set()
    try:
        if not os.path.isdir(path):
            return os.lstat(path).st_blocks * 512
    except OSError:
        return 0
    for base, _dirs, names in os.walk(path):
        for name in names:
            try:
                item = os.lstat(os.path.join(base, name))
            except OSError:
                continue
            if item.st_nlink > 1:
                key = (item.st_dev, item.st_ino)
                if key in seen:
                    continue
                seen.add(key)
            total += item.st_blocks * 512
    return total


# ── what the governor is told ──────────────────────────────────────────────

@dataclass
class Context:
    """Everything outside this module the sweep needs, so a test can point it at a temp folder."""
    data_dir: Path                                  # the Hub's state root (hub.db, trash/)
    base_dir: Path | None = None                    # its parent: logs, Updates/, snapshot-views/, worker state
    library: Path | None = None                     # the model library
    manifests: list = field(default_factory=list)   # the model catalog (package_id, model_id, revision)
    app_root: Path | None = None                    # the installed .app this Hub runs from
    machines: Iterable[str] = ()
    output_roots: Iterable[Path] = ()               # managed output folders, for partial files
    # The Voice worker's output folders: audio in them that the Hub never registered is kept for
    # `UNACKED_DAYS` and then moved to the Trash Folder like any other unacknowledged output.
    unregistered_roots: Iterable[Path] = ()
    settings: dict = field(default_factory=dict)    # effective device settings
    running_staging: Callable[[], set] = lambda: set()
    # The transcription spool (uploaded media and subtitle files, `job_storage`): its own policy runs on
    # its own timer; under pressure the governor asks it for less. Both None where there is no spool.
    spool_limit: Callable[[], int] | None = None
    spool_enforce: Callable[[int | None], dict] | None = None


@dataclass
class _Run:
    actions: dict = field(default_factory=dict)     # step -> [count, bytes]

    def note(self, step: str, nbytes: int, count: int = 1) -> None:
        entry = self.actions.setdefault(step, [0, 0])
        entry[0] += count
        entry[1] += max(0, int(nbytes))

    def total(self) -> int:
        return sum(entry[1] for entry in self.actions.values())

    def summary(self) -> list[dict]:
        return [{"step": step, "count": count, "gb": round(nbytes / GB, 3)}
                for step, (count, nbytes) in self.actions.items() if count]


def _rmtree(path: Path) -> bool:
    """Delete one folder or file; the app copies can hold read-only parts."""
    def fix(function, target, _exc):
        try:
            os.chmod(os.path.dirname(target), 0o755)
            os.chmod(target, 0o755)
            function(target)
        except OSError:
            pass
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path, onerror=fix)
    except OSError:
        pass
    return not os.path.lexists(path)


def _mtime(path: Path) -> float:
    try:
        return os.lstat(path).st_mtime
    except OSError:
        return 0.0


def _tree_mtime(path: Path) -> float:
    """The newest modification under `path` (a download in progress keeps touching its files)."""
    newest = _mtime(path)
    for base, dirs, names in os.walk(path):
        for name in dirs + names:
            newest = max(newest, _mtime(Path(base) / name))
    return newest


def _remove(run: _Run, step: str, path: Path, *, size: int | None = None) -> bool:
    """Delete scratch only (temp and partial files, logs, snapshot views). Anything else: `_retire`."""
    size = cached_physical(path, time.time()) if size is None else size
    if _rmtree(path):
        run.note(step, size)
        _SIZE_CACHE.pop(str(path), None)
        return True
    return False


def _retire(run: _Run, step: str, path: Path) -> bool:
    """Move to the Trash Folder, never a delete. Counted, but 0 bytes: a same-volume move frees nothing."""
    if output_storage.retire(path, step.replace(" ", "-")):
        run.note(step, 0)
        return True
    return False


# ── steps ──────────────────────────────────────────────────────────────────

def _temp_files(ctx: Context, now: float, run: _Run, done: Callable[[], bool] | None) -> None:
    """Partial files nobody is writing: a crashed job's `.partial.wav`, an abandoned upload, a stale
    `.studiohub-tmp-*`, a Voice scratch folder. Idle for six hours means no job owns it."""
    roots = [Path(root) for root in ctx.output_roots]
    base = ctx.base_dir
    if base is not None:
        roots += [base / "voice" / "output", base / "music" / "outputs", base / "image" / "outputs",
                  base / "video" / "outputs", base / "subtitle" / "artifacts", base / "subtitle" / "inputs"]
    roots.append(ctx.data_dir / "release" / "blobs")
    for root in dict.fromkeys(roots):
        if not root.is_dir() or root.is_symlink():
            continue
        for current, dirs, names in os.walk(root):
            if Path(current).relative_to(root).parts[1:]:
                dirs[:] = []                              # two levels are enough; outputs are flat
            for name in names:
                if name.endswith(_PARTIAL_SUFFIXES):
                    path = Path(current) / name
                    if now - _mtime(path) > TEMP_IDLE_S:
                        _remove(run, "temp files", path)
    scratch = Path(tempfile.gettempdir())
    try:
        for entry in scratch.iterdir():
            if entry.name.startswith(_TEMP_DIR_PREFIXES) and entry.is_dir() and not entry.is_symlink() \
                    and now - _tree_mtime(entry) > TEMP_IDLE_S:
                _remove(run, "temp files", entry)
    except OSError:
        pass
    if ctx.app_root is not None:
        _app_leftovers(ctx.app_root.parent, now, run, TMP_PREFIX, "temp files", _remove)


def _app_leftovers(parent: Path, now: float, run: _Run, prefix: str, step: str, handle) -> None:
    try:
        names = [name for name in os.listdir(parent) if name.startswith(prefix)]
    except OSError:
        return
    for name in names:
        path = parent / name
        if not path.is_symlink() and now - _mtime(path) > ORPHAN_APP_IDLE_S:
            handle(run, step, path)


def _logs(ctx: Context, run: _Run, severe: bool) -> None:
    """Logs have no function. The rotated copy goes to the Trash Folder (never a delete); under red
    the live one is truncated too, which is the only way left to give the disk its bytes back."""
    if ctx.base_dir is None:
        return
    rotated = ctx.base_dir / "hub-worker.log.1"
    if rotated.is_file() and not rotated.is_symlink():
        _retire(run, "logs", rotated)
    live = ctx.base_dir / "hub-worker.log"
    if severe and live.is_file() and not live.is_symlink():
        size = physical_bytes(live)
        try:
            os.truncate(live, 0)
            run.note("logs", size)
        except OSError:
            pass


def _download_runs(ctx: Context, now: float, run: _Run) -> None:
    """A finished model-download run's folder (its manifest, source map and output.log) is kept for the
    shared retention window, then retired to the Trash Folder - never a delete. A run still downloading
    keeps touching its files, so a folder untouched for the whole window belongs to no live download."""
    window = float((ctx.settings or {}).get("retention_days", 3)) * 86400
    try:
        entries = list((ctx.data_dir / "model-download-runs").iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.is_dir() and not entry.is_symlink() and now - _tree_mtime(entry) > window:
            _retire(run, "model download logs", entry)


def _trash_entries() -> list[tuple[Path, Path]]:
    """(day folder, entry) for everything in the Trash Folder, oldest first."""
    rows = []
    for day in output_storage._trash_days():
        try:
            kids = list(day.iterdir())
        except OSError:
            continue
        rows += [(day, kid) for kid in sorted(kids, key=_mtime)]
    return rows


def _drop_empty_days() -> None:
    for day in output_storage._trash_days():
        try:
            day.rmdir()                                   # only when empty
        except OSError:
            pass


def _installed_app_present(app_root: Path | None) -> bool:
    return bool(app_root and (app_root / "Contents").is_dir())


def _updates(ctx: Context, now: float, run: _Run, severe: bool) -> None:
    """`Updates/<key>/` holds a downloaded release. Only the newest can be a download in progress,
    so older ones go to the Trash Folder; under space pressure the newest goes too once nothing has
    touched it for an hour."""
    if ctx.base_dir is None:
        return
    updates = ctx.base_dir / "Updates"
    try:
        entries = sorted((e for e in updates.iterdir() if not e.is_symlink()), key=_tree_mtime)
    except OSError:
        return
    for position, entry in enumerate(entries):
        newest = position == len(entries) - 1
        if not newest or (severe and now - _tree_mtime(entry) > ORPHAN_APP_IDLE_S):
            _retire(run, "stale updates", entry)


def _orphan_apps(ctx: Context, now: float, run: _Run) -> None:
    """An update that crashed between steps leaves a staged or moved-aside copy beside the app. They are
    moved to the Trash Folder only while the installed app is whole: `.studiohub-old-*` can be the only copy of it."""
    if not _installed_app_present(ctx.app_root):
        return
    parent = ctx.app_root.parent
    _app_leftovers(parent, now, run, OLD_PREFIX, "orphan app copies", _retire)
    _app_leftovers(parent, now, run, STAGED_PREFIX, "orphan app copies", _retire)


def _trash() -> None:
    """The Trash Folder is the owner's to empty (macOS Trash): nothing in it is deleted. This only
    forgets the sizes of entries that left it and drops dated folders that are already empty."""
    entries = {str(entry) for _day, entry in _trash_entries()}
    for key in set(_SIZE_CACHE) - entries:
        _SIZE_CACHE.pop(key, None)
    _drop_empty_days()


def _installed_packages(library: Path) -> dict | None:
    """The receipts, or None when they cannot be read (then nothing is judged an orphan)."""
    try:
        value = json.loads((library / ".studiohub" / "installed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _package_present(library: Path, receipts: dict, package_id: str, revision_prefix: str) -> bool:
    row = receipts.get(package_id)
    if isinstance(row, dict) and str(row.get("revision") or "").lower().startswith(revision_prefix):
        folder = row.get("folder")
        if isinstance(folder, str) and folder and os.path.lexists(library / folder):
            return True
    try:
        return any(entry.name.lower().startswith(revision_prefix) and entry.is_dir()
                   for entry in (library / package_id).iterdir())
    except OSError:
        return False


def _orphans(ctx: Context, now: float, run: _Run, severe: bool, done: Callable[[], bool] | None) -> None:
    """Derived data that belongs to no installed model: snapshot views of absent models, partial model
    staging nobody is downloading into, Hugging Face mirror copies of packages that are not installed."""
    library = ctx.library
    if library is None or not library.is_dir():
        return                                            # a disconnected library is never "everything absent"
    receipts = _installed_packages(library)
    if receipts is None:
        return
    states = [ctx.base_dir, ctx.base_dir / "subtitle"] if ctx.base_dir is not None else []
    for state in states:
        views = state / "snapshot-views"
        try:
            entries = list(views.iterdir())
        except OSError:
            continue
        for view in entries:
            match = _VIEW.fullmatch(view.name)
            if (match and view.is_dir() and not view.is_symlink()
                    and not _package_present(library, receipts, match["package"], match["revision"])):
                _remove(run, "orphan snapshot views", view)
    running = {os.path.realpath(path) for path in ctx.running_staging()}
    idle = STAGING_IDLE_RED_S if severe else STAGING_IDLE_S
    try:
        staging = list((library / ".studiohub" / "staging").iterdir())
    except OSError:
        staging = []
    for entry in staging:
        if (entry.is_dir() and not entry.is_symlink() and os.path.realpath(entry) not in running
                and now - _tree_mtime(entry) > idle):
            _remove(run, "partial model downloads", entry)
    wanted = {}
    for manifest in ctx.manifests:
        if isinstance(manifest, dict) and manifest.get("model_id") and manifest.get("package_id"):
            wanted[str(manifest["model_id"])] = (str(manifest["package_id"]),
                                                 str(manifest.get("revision") or "").lower()[:16])
    try:
        mirrors = list((library / "hub").glob("models--*"))
    except OSError:
        mirrors = []
    from components import library_ownership
    if library_ownership.moving_into_temp(library, output_storage.trash_root()):
        return                                            # never carry real weights into a throwaway folder
    for mirror in mirrors:
        repo = mirror.name.removeprefix("models--").replace("--", "/", 1)
        known = wanted.get(repo)
        if (known and mirror.is_dir() and not mirror.is_symlink()
                and not _package_present(library, receipts, known[0], known[1])
                and not _linked_into_library(mirror)):
            _retire(run, "orphan model mirror", mirror)


def _linked_into_library(mirror: Path) -> bool:
    """A mirror file hard-linked to another file is shared bytes, not an orphan's own."""
    for base, _dirs, names in os.walk(mirror):
        for name in names:
            try:
                if os.lstat(os.path.join(base, name)).st_nlink > 1:
                    return True
            except OSError:
                return True
    return False


def _acked_outputs(ctx: Context, now: float, run: _Run, severe: bool, done: Callable[[], bool]) -> None:
    """Outputs the Gateway already holds, oldest first, to the Trash Folder. Amber takes those past the
    retention window (cleanup switched off by the operator stops that); red goes younger. Never unacked
    or leased. Counted with 0 bytes: the move frees nothing until the owner empties the Trash Folder."""
    settings = ctx.settings or {}
    if not severe and not settings.get("cleanup_enabled", True):
        return
    min_age = 3600.0 if severe else float(settings.get("retention_days", 3)) * 86400
    for machine in ctx.machines:
        if done():
            return
        _moved, files = output_storage.reclaim_acked(machine=machine, min_age_seconds=min_age,
                                                     done=done, now=now)
        if files:
            run.note("acked outputs", 0, files)


def _unregistered_outputs(ctx: Context, now: float, run: _Run, severe: bool) -> None:
    """Voice audio the Hub never registered (the worker's own retention loop is off in the packaged
    app). Ordinary retention for an unacknowledged output is `UNACKED_DAYS`; a Mac that is filling
    shortens it to the operator's retention window, never below an hour. Cleanup switched off by the
    operator stops it, as for registered outputs. To the Trash Folder, never a delete."""
    settings = ctx.settings or {}
    roots = list(ctx.unregistered_roots)
    if not roots or not settings.get("cleanup_enabled", True):
        return
    window = output_storage.UNACKED_DAYS * 86400.0
    if severe:
        window = min(window, max(3600.0, float(settings.get("retention_days", 3)) * 86400))
    freed, files = output_storage.trash_unregistered(roots, min_age_seconds=window, now=now)
    if files:
        run.note("unregistered outputs", freed, files)


def _spool(ctx: Context, run: _Run, severe: bool, done: Callable[[], bool]) -> None:
    """Finished transcription jobs' files, oldest first, to the Trash Folder: amber halves the spool's own
    limit, red empties what is finished. A job still queued or running is never eligible (`job_storage`
    guarantees it). Counted with 0 bytes: the move frees nothing until the owner empties the Trash Folder."""
    if ctx.spool_limit is None or ctx.spool_enforce is None or done():
        return
    target = 0 if severe else int(ctx.spool_limit()) // 2
    result = ctx.spool_enforce(target)
    run.note("transcription spool", 0, result.get("cleared", 0))


def _history(now: float, run: _Run) -> None:
    result = ledger.compact_history(now=now, days=HISTORY_DAYS)
    rows = result["batches"] + result["output_groups"]
    if rows or result["vacuumed"]:
        run.note("job history", 0, rows or 1)


# ── the sweep ──────────────────────────────────────────────────────────────

_STATE: dict = {"last_sweep": None, "warnings": [], "measured": {}, "measured_at": 0.0,
                "history_at": 0.0, "last_tier": None, "system_warnings": []}


def sweep(ctx: Context, *, now: float | None = None, startup: bool = False) -> dict:
    """One pass. Returns `{"tier", "reclaimed_bytes", "actions", ...}` and remembers it for `snapshot`."""
    with _LOCK:
        now = time.time() if now is None else float(now)
        started = time.monotonic()
        run = _Run()
        usage = disk_usage(ctx.data_dir)
        begin = tier(usage.free, usage.total)

        def done() -> bool:
            current = disk_usage(ctx.data_dir)
            return tier(current.free, current.total) == "green"

        def guarded(step: Callable[[], None]) -> None:
            try:
                step()
            except Exception:
                log.exception("disk governor step failed (continuing)")

        # Every tier: scratch goes, retired copies move to the Trash Folder.
        guarded(lambda: _temp_files(ctx, now, run, None))
        guarded(lambda: _download_runs(ctx, now, run))
        guarded(lambda: _updates(ctx, now, run, False))
        guarded(lambda: _orphan_apps(ctx, now, run))
        guarded(lambda: _unregistered_outputs(ctx, now, run, False))
        guarded(_trash)
        if startup:
            guarded(lambda: _orphans(ctx, now, run, False, None))
        if now - _STATE["history_at"] >= HISTORY_EVERY_S or startup:
            _STATE["history_at"] = now
            guarded(lambda: _history(now, run))
        # Amber and red: keep going, in order, until the disk is green again.
        severe = begin in ("red", "stop")
        if begin != "green" and not done():
            # ponytail: the moves below free 0 bytes on one volume but are one-shot (a moved item is gone),
            # so a repeated red sweep only re-scans; add a "nothing left to move" memo if the scan cost shows.
            ordered = [
                lambda: _logs(ctx, run, severe),
                lambda: _updates(ctx, now, run, True),
                lambda: _unregistered_outputs(ctx, now, run, severe),
                lambda: _orphans(ctx, now, run, severe, done),
                lambda: _acked_outputs(ctx, now, run, severe, done),
                lambda: _spool(ctx, run, severe, done),
            ]
            for step in ordered:
                guarded(step)
                if done():
                    break
        end = disk_usage(ctx.data_dir)
        result = {"at": now, "tier_before": begin, "tier": tier(end.free, end.total),
                  "reclaimed_bytes": run.total(), "reclaimed_gb": round(run.total() / GB, 3),
                  "actions": run.summary(), "seconds": round(time.monotonic() - started, 2),
                  "startup": bool(startup)}
        _STATE["last_sweep"] = result
        _STATE["last_tier"] = result["tier"]
        guarded(lambda: _measure(ctx, now, force=startup))
        return result


def _measure(ctx: Context, now: float, *, force: bool = False) -> None:
    """Where the disk went. Trash, outputs and logs every sweep; the app and the models (big walks) hourly."""
    measured = _STATE["measured"]
    measured["trash"] = sum(cached_physical(entry, now) for _day, entry in _trash_entries())
    try:
        with ledger._conn() as conn:
            measured["outputs"] = conn.execute(
                "SELECT COALESCE(SUM(b),0) FROM (SELECT MAX(bytes) b FROM output_files "
                "WHERE present=1 GROUP BY device, inode)").fetchone()[0]
    except Exception:
        measured.setdefault("outputs", 0)
    logs = 0
    if ctx.base_dir is not None:
        for name in LOG_NAMES:
            try:
                logs += os.lstat(ctx.base_dir / name).st_blocks * 512
            except OSError:
                pass
    measured["logs"] = logs
    # The whole macOS Trash (0.6.243) is its own consumer, so "other" shrinks by it. None when macOS
    # keeps the Trash private (no Full Disk Access): "no data" must never read as 0.
    measured["macos_trash"] = output_storage.trash_sizes(max_age=60.0)["macos_trash"]["bytes"]
    if force or now - _STATE["measured_at"] >= MEASURE_EVERY_S:
        _STATE["measured_at"] = now
        measured["apps"] = logical_allocated(ctx.app_root) if ctx.app_root else 0
        measured["models"] = _model_bytes(ctx.library)
        _STATE["system_warnings"] = system_settings_warnings()


def _model_bytes(library: Path | None) -> int:
    if library is None or not library.is_dir():
        return 0
    total = 0
    try:
        entries = list(library.iterdir())
    except OSError:
        return 0
    for entry in entries:
        if entry.name.startswith(("_trash", ".")) or entry.is_symlink():
            continue
        total += logical_allocated(entry)
    return total


def _read(command: list[str]) -> str | None:
    """stdout of one read-only system command, or None (not macOS, not found, refused, timed out)."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def system_settings_warnings() -> list[str]:
    """Two macOS settings that quietly eat 8-15 GB each on a node, read without sudo: automatic
    download of macOS updates and Time Machine (a destination, or local snapshots on this disk)."""
    out = []
    value = _read(["/usr/bin/defaults", "read", "/Library/Preferences/com.apple.SoftwareUpdate", "AutomaticDownload"])
    if value is not None and value.strip() == "1":
        out.append("macOS is set to download updates automatically (8-15 GB each). Turn it off in System Settings > "
                   "General > Software Update > Automatic Updates > Download new updates when available.")
    destinations = _read(["/usr/bin/tmutil", "destinationinfo"])
    snapshots = _read(["/usr/bin/tmutil", "listlocalsnapshots", "/"])
    count = sum(1 for line in (snapshots or "").splitlines() if line.startswith("com.apple.TimeMachine"))
    if (destinations is not None and "No destinations configured" not in destinations and destinations.strip()) or count:
        detail = f" ({count} local snapshot{'s' if count != 1 else ''} on this disk)" if count else ""
        out.append(f"Time Machine is on for this Mac{detail}; its snapshots take space the Hub cannot free. "
                   f"Turn it off in System Settings > General > Time Machine.")
    return out


def _swap_file_bytes() -> int:
    try:
        import psutil
        return int(psutil.swap_memory().total)
    except Exception:
        return 0


def _swap_used_gb() -> float | None:
    try:
        import psutil
        return round(psutil.swap_memory().used / GB, 2)
    except Exception:
        return None


def _warnings(state: str, free: int, total: int, last: dict | None, trash: int) -> list[str]:
    out = []
    percent = (100 * free / total) if total else 0
    folder = output_storage.trash_root()
    if state == "stop":
        out.append(f"Only {free / GB:.1f} GB is free ({percent:.1f}%). This Mac refuses new work until space is freed; "
                   f"empty the Trash Folder ({folder}) to free space.")
    elif state == "red":
        out.append(f"Disk space is critically low: {free / GB:.1f} GB free ({percent:.1f}%). "
                   f"Old files are being moved to the Trash Folder ({folder}); empty it to free space.")
    elif state == "amber":
        out.append(f"Disk space is getting low: {free / GB:.1f} GB free ({percent:.1f}%).")
    if state != "green" and last and last.get("tier") != "green":
        out.append("The last cleanup could not bring free space back to normal; what remains is "
                   "models, unacknowledged outputs or other files that are not removed automatically.")
    if trash > trash_cap_bytes(total) * 1.1:
        out.append(f"The Trash Folder holds {trash / GB:.1f} GB, over its limit of {trash_cap_bytes(total) / GB:.1f} GB. "
                   f"Nothing in it is deleted automatically; empty it to free space ({folder}).")
    return out


def snapshot(data_dir: Path | None = None) -> dict:
    """The `disk` object the Hub publishes in its resources and health payloads. Cheap: free space
    and swap are read now; the consumer sizes are from the last sweep."""
    root = data_dir or ledger.DATA_DIR
    try:
        usage = disk_usage(root)
    except OSError:
        return {"state": "unknown", "free_gb": None, "total_gb": None, "warnings": []}
    state = tier(usage.free, usage.total)
    measured = dict(_STATE["measured"])
    used = max(0, usage.total - usage.free)
    known = {name: int(measured.get(name) or 0)
             for name in ("outputs", "trash", "logs", "apps", "models")}
    known["macos_trash"] = measured.get("macos_trash")   # None when it could not be read, never 0
    known["swap"] = _swap_file_bytes()     # the swap files share this APFS container's space
    consumers = {f"{name}_gb": (None if size is None else round(size / GB, 2)) for name, size in known.items()}
    # With the Trash unknown, "other" would silently absorb it: say not known rather than overstate it.
    consumers["other_gb"] = (None if None in known.values()
                             else round(max(0, used - sum(known.values())) / GB, 2))
    top = sorted(({"name": name.removesuffix("_gb"), "gb": gb} for name, gb in consumers.items()
                  if gb is not None),
                 key=lambda item: item["gb"], reverse=True)
    if not measured:                       # before the first sweep has measured anything, say nothing rather than guess
        consumers, top = None, []
    last = _STATE["last_sweep"]
    return {
        "state": state,
        "free_gb": round(usage.free / GB, 2),
        "total_gb": round(usage.total / GB, 2),
        "free_percent": round(100 * usage.free / usage.total, 1) if usage.total else None,
        "trash_gb": round(known["trash"] / GB, 2) if measured else None,
        "trash_cap_gb": round(trash_cap_bytes(usage.total) / GB, 2),
        "consumers": consumers,
        "top": top,
        "swap_used_gb": _swap_used_gb(),
        "last_sweep": ({"at": last["at"], "reclaimed_gb": last["reclaimed_gb"], "actions": last["actions"]}
                       if last else None),
        "warnings": _warnings(state, usage.free, usage.total, last, known["trash"]) + list(_STATE["system_warnings"]),
    }
