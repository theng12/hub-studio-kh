"""Hub-owned retention for generated worker output.

Workers retain their files.  This index only accepts paths under roots supplied
by the packaged owner descriptor, so a worker result can never turn an
arbitrary local pathname into a deletion candidate.

Cleanup never deletes an output.  It **moves** acknowledged, unleased output into the Trash Folder
(`<data dir>/trash/<UTC date>/<batch>/`), a same-volume rename; a move that fails is skipped, never
retried as a delete.  An output nobody acknowledged is moved the same way once it is `UNACKED_DAYS`
old (the Gateway never collected it; leased ones are never moved).  Nothing here deletes on its own, and
the Hub never empties the Trash Folder: the owner does, through macOS Trash (owner request 2026-10-15)
or from the Gateway (`trash_to_macos`, and `trash_delete` on an explicit, count-and-bytes confirmation).
`max_bytes` counts live outputs only.
"""
from __future__ import annotations

import logging
import os
import re
import copy
import hashlib
import json
import stat
import subprocess
import threading
import time
import shutil
import sqlite3
from pathlib import Path

from .backend import ledger

# An output the Gateway never acknowledged is not kept for ever: after this long it moves to the Trash Folder.
UNACKED_DAYS = 7
# Where a managed output root that is on another volume than the Hub's data folder keeps its own
# Trash Folder: inside the root itself, so the move is always a same-volume rename.
FALLBACK_TRASH_NAME = ".studiohub-trash"
log = logging.getLogger("studiohub.output_storage")
_TRASH_STATS = {"move_failures": 0, "fallback_moves": 0, "last_failure": None}
_FALLBACK_LOGGED: set[str] = set()
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_START_LOCK = threading.Lock()
_LOCK = threading.RLock()
_STARTED = False


def _schema() -> None:
    with ledger._conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS output_groups (
          id TEXT PRIMARY KEY, machine TEXT NOT NULL, studio TEXT NOT NULL,
          job_id TEXT NOT NULL, batch_id TEXT, item_index INTEGER, attempt INTEGER NOT NULL,
          model TEXT, runtime_identity TEXT, generated_at REAL NOT NULL,
          receipt_acked_at REAL, state TEXT NOT NULL DEFAULT 'finalized',
          recoverable INTEGER NOT NULL DEFAULT 0,
          reader_leases INTEGER NOT NULL DEFAULT 0, transfer_leases INTEGER NOT NULL DEFAULT 0,
          created_at REAL NOT NULL, UNIQUE(machine, studio, job_id, attempt)
        );
        CREATE TABLE IF NOT EXISTS output_files (
          id INTEGER PRIMARY KEY AUTOINCREMENT, group_id TEXT NOT NULL,
          path TEXT NOT NULL, managed_root TEXT NOT NULL, device INTEGER, inode INTEGER,
          bytes INTEGER NOT NULL, sha256 TEXT, modified_ns INTEGER, changed_ns INTEGER,
          present INTEGER NOT NULL DEFAULT 1,
          UNIQUE(group_id, path)
        );
        CREATE INDEX IF NOT EXISTS idx_output_groups_device ON output_groups(machine, generated_at);
        CREATE INDEX IF NOT EXISTS idx_output_files_group ON output_files(group_id);
        """)
        try:
            conn.execute("ALTER TABLE output_groups ADD COLUMN recoverable INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise


def prepare() -> None:
    _schema()


def recover_process_leases() -> None:
    """Leases belong to this Hub process, so a previous crash cannot retain them."""
    _schema()
    with _LOCK, ledger._conn() as conn:
        conn.execute("UPDATE output_groups SET reader_leases=0, transfer_leases=0 "
                     "WHERE reader_leases!=0 OR transfer_leases!=0")


def trash_root() -> Path:
    return ledger.DATA_DIR / "trash"


def trash_stats() -> dict:
    """Counters since this Hub started: moves that fell back to a root's own Trash Folder, and moves
    that failed outright (the file stays live and is retried). Published with the retention status."""
    return dict(_TRASH_STATS)


def _trash_bases() -> list[Path]:
    """Every Trash Folder: the Hub's, and the one inside each managed root that needed its own."""
    bases = [trash_root()]
    if ledger.DB_FILE.exists():
        try:
            with ledger._conn() as conn:
                roots = [row[0] for row in conn.execute("SELECT DISTINCT managed_root FROM output_files")]
        except sqlite3.Error:
            roots = []
        for root in roots:
            fallback = Path(root) / FALLBACK_TRASH_NAME
            if fallback.is_dir() and not fallback.is_symlink():
                bases.append(fallback)
    return bases


def _trash_days() -> list[Path]:
    """Dated Trash Folder subfolders, oldest first.  Anything not named like a date is not ours."""
    days: list[Path] = []
    for base in _trash_bases():
        try:
            days += [p for p in base.iterdir() if _DAY.fullmatch(p.name) and p.is_dir() and not p.is_symlink()]
        except OSError:
            continue
    return sorted(days, key=lambda p: (p.name, str(p)))


_TRASH_SIZE: tuple[float, int] = (0.0, 0)


def trash_bytes(max_age: float = 0.0) -> int:
    """Disk used by the Trash Folder (physical bytes), for the storage screen.

    A replaced app can sit in it (about 90,000 files), so the screen, which asks on every preview,
    passes `max_age` to reuse a recent walk instead of stat-ing them all again.
    """
    global _TRASH_SIZE
    if max_age and time.monotonic() - _TRASH_SIZE[0] < max_age:
        return _TRASH_SIZE[1]
    from . import disk_governor
    # Physical bytes: an APFS clone (a previous app copy) shares its blocks with the live app, so
    # its logical size is not what the disk gives back by emptying it.
    now, total = time.time(), 0
    for day in _trash_days():
        try:
            total += sum(disk_governor.cached_physical(entry, now) for entry in day.iterdir())
        except OSError:
            continue
    _TRASH_SIZE = (time.monotonic(), total)
    return total


def retire(path: Path, label: str, now: float | None = None) -> bool:
    """Move a retired file or folder into `<trash>/<UTC date>/<label>-<id>/<name>`.

    The one way the Hub retires anything that is not an indexed output. A same-volume rename only:
    False, with `path` left where it is and a log line, when it fails (another volume, a permission).
    It never copies and never deletes. Takes no `_LOCK` (callers include async request paths that must not
    wait behind a Trash Folder action); `trash_delete` re-measures and stages its items in one short
    step, so an entry that appears in the middle of a delete is simply left for the next look."""
    day = time.strftime("%Y-%m-%d", time.gmtime(time.time() if now is None else float(now)))
    target = None
    try:
        (trash_root() / day).mkdir(parents=True, exist_ok=True)
        for _ in range(8):
            leaf = trash_root() / day / f"{label}-{os.urandom(4).hex()}"
            try:
                leaf.mkdir()                    # plain mkdir: a name that is taken is never reused
            except FileExistsError:
                continue
            target = leaf / Path(path).name
            break
        else:
            raise OSError("no free folder name in the Trash Folder")
        os.rename(path, target)
        return True
    except OSError as error:
        if target is not None:
            try:
                target.parent.rmdir()           # only the empty folder made above
            except OSError:
                pass
        log.warning("could not move %s to the Trash Folder (%s); it is left where it is", path, error)
        return False


# Old per-install Trash roots still holding retired files (owner request 2026-10-15: one unified
# Trash Folder on every Mac). Adoption moves their *contents* into the unified root, never a live
# app, a model in use, or a leased output; nothing is deleted. Same-volume renames only.
LEGACY_APP_TRASH = Path("/Applications/_trash")
_ADOPTED_MANIFEST = "adopted-legacy.json"


def _legacy_sources() -> tuple[list[tuple[Path, str]], list[dict]]:
    """The roots adoption may touch, and the ones it must leave alone with the reason.

    A model library this Hub does not own is read-only (`components/library_ownership`), so a Hub
    started with a throwaway data directory never adopts the real `~/StudioHubModels/_trash` into a
    temp Trash Folder.
    """
    sources = [(LEGACY_APP_TRASH, "legacy-app"),
               (ledger.DATA_DIR / "_trash" / "failed-writes", "legacy-failed-write")]
    skipped: list[dict] = []
    library = Path.home() / "StudioHubModels"
    model_trash = library / "_trash"
    if model_trash.is_dir():
        try:
            from components import library_ownership
            reason = library_ownership.refusal(library, ledger.DATA_DIR, "adopting retired models")
        except (OSError, ValueError) as error:
            reason = str(error)
        if reason:
            skipped.append({"source": str(model_trash), "reason": reason})
        else:
            sources.append((model_trash, "legacy-model"))
    sources += [(base, "legacy-output-trash") for base in _trash_bases()[1:]]
    return sources, skipped


def _same_volume(path: Path) -> bool:
    try:
        return os.stat(path).st_dev == os.stat(trash_root()).st_dev
    except OSError:
        return False


def _read_manifest(path: Path) -> dict[str, dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        rows = value.get("adopted") if isinstance(value, dict) else None
    except (OSError, ValueError):
        return {}
    return {row["source"]: row for row in rows or []
            if isinstance(row, dict) and isinstance(row.get("source"), str)}


def _write_manifest(path: Path, rows: dict[str, dict], now: float) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    temporary = path.with_name(f".{path.name}-{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps({"updated_at": stamp,
                                         "adopted": sorted(rows.values(), key=lambda row: row["source"])},
                                        indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError as error:
        log.warning("Trash Folder: could not write the adoption record %s (%s)", path, error)


def adopt_legacy(now: float | None = None,
                 sources: list[tuple[Path, str]] | None = None) -> dict:
    """Adopt already-retired files from the old per-install Trash roots into the unified one.

    Runs at every start-up and is idempotent: a moved entry is gone from its source, so a restart
    neither duplicates nor loses it. Each entry keeps its own name inside a new
    `<date>/<label>-<id>/` folder (the same collision-safe leaf `retire()` uses), and every move is
    recorded in `<trash>/adopted-legacy.json` with its source path and UTC adoption date. A root on
    another volume is left in place and reported (a rename could not be safe); a missing root is
    reported. Nothing here deletes, and nothing outside a Trash root is read or moved.
    """
    now = time.time() if now is None else float(now)
    trash_root().mkdir(parents=True, exist_ok=True)
    manifest = trash_root() / _ADOPTED_MANIFEST
    rows = _read_manifest(manifest)
    if sources is None:
        pairs, skipped = _legacy_sources()
    else:
        pairs, skipped = list(sources), []
    moved, failed, missing, cross_volume = [], [], [], []
    for base, label in pairs:
        base = Path(base)
        try:
            info = base.lstat()
        except FileNotFoundError:
            missing.append(str(base))
            continue
        except OSError as error:
            failed.append({"source": str(base), "error": str(error)[:300]})
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            continue                              # not a Trash folder: never a candidate
        if not _same_volume(base):
            cross_volume.append(str(base))        # another volume: leave it, list it
            continue
        try:
            entries = sorted(base.iterdir(), key=lambda entry: entry.name)
        except OSError as error:
            failed.append({"source": str(base), "error": str(error)[:300]})
            continue
        for entry in entries:
            if retire(entry, label, now):
                moved.append(str(entry))
                rows[str(entry)] = {"source": str(entry), "label": label,
                                    "adopted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}
                _write_manifest(manifest, rows, now)
            else:
                failed.append({"source": str(entry),
                               "error": "could not move it into the Trash Folder; it is left where it is"})
    if moved:
        log.warning("Trash Folder: adopted %d retired entries from legacy Trash roots", len(moved))
    return {"ok": not failed, "moved": moved, "moved_count": len(moved), "failed": failed,
            "missing": missing, "cross_volume": cross_volume, "skipped": skipped,
            "manifest": str(manifest)}


# ── The owner's Trash Folder, from the Gateway (owner decision 4g, 2026-10-16) ─────────────────────
# Listed, moved to macOS Trash, or permanently deleted remotely so nobody has to visit a Mac. Only the
# entries `<trash_root>/<date>/<name>` are ever touched: never a managed root's own `.studiohub-trash`,
# never the user's own macOS Trash, never anything a link points at. Nothing here runs on its own.

_ITEM = re.compile(r"(\d{4}-\d{2}-\d{2})/([^/\x00]+)")
_RETIRED = re.compile(r"(.+)-[0-9a-f]{8}")          # `retire()`'s `<label>-<id>` leaf; anything else is output
_AGES = ((1, "today"), (7, "1-7 days"), (30, "7-30 days"), (None, "over 30 days"))
_JXA_TRASH = """function run(argv) {
  ObjC.import("Foundation");
  var fm = $.NSFileManager.defaultManager, out = [];
  for (var i = 0; i < argv.length; i++) {
    var error = $();
    var ok = fm.trashItemAtURLResultingItemURLError($.NSURL.fileURLWithPath(argv[i]), $(), error);
    out.push(ok ? "ok" : (error.isNil() ? "refused" : ObjC.unwrap(error.localizedDescription)));
  }
  return out.join("\\n");
}"""


class TrashRefused(Exception):
    def __init__(self, status: int, code: str, detail: str, **extra) -> None:
        super().__init__(detail)
        self.status, self.body = status, {"ok": False, "code": code, "detail": detail, **extra}


# ── macOS's own Trash preference (owner request 2026-10-19) ─────────────────────────────────────────
# Finder > Settings > Advanced > "Remove items from the Trash after 30 days" is the preference
# `com.apple.finder FXRemoveOldTrashItems`. Every retirement in this module ends in macOS Trash, so a
# Mac without it fills its disk until someone empties the Trash by hand - and the owner could not tell
# which of ~53 Macs had it. It is read here, published in the `startup` report the Gateway already
# reads, and turned **on** - never off - on request. Nothing here runs on its own.

FINDER_DOMAIN = "com.apple.finder"
FINDER_TRASH_KEY = "FXRemoveOldTrashItems"
#: `defaults` is a process, and this is read on every health, resource and roster poll.
FINDER_READ_TTL_S = 60.0
_finder_read: tuple[float, bool | None] = (0.0, None)


def _defaults(*arguments: str) -> subprocess.CompletedProcess | None:
    """`defaults` with a timeout, or `None` when it could not be run at all."""
    try:
        return subprocess.run(["/usr/bin/defaults", *arguments], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None


def finder_trash_auto_empty(*, now: float | None = None, fresh: bool = False) -> bool | None:
    """Whether this Mac's Finder removes Trash items after 30 days: `True`, `False`, or `None` unknown.

    The key is written only when the box is ticked, so **absent is off** - that is macOS's own default
    and a real answer, not a failed read. `None` is reserved for a `defaults` that could not be run or
    answered something unusable, and the Fleet page shows it as unknown rather than as off. Cached for
    `FINDER_READ_TTL_S`; `fresh=True` re-reads, which is what verifies a write.
    """
    global _finder_read
    moment = time.time() if now is None else float(now)
    stamped, value = _finder_read
    if not fresh and stamped and moment - stamped < FINDER_READ_TTL_S:
        return value
    read = _defaults("read", FINDER_DOMAIN, FINDER_TRASH_KEY)
    if read is None:
        value = None
    elif read.returncode == 0:
        value = read.stdout.strip().lower() in ("1", "true", "yes")
    elif read.returncode == 1:
        value = False                    # "does not exist": macOS's default, which is off
    else:
        value = None
    _finder_read = (moment, value)
    return value


def enable_finder_trash_auto_empty() -> dict:
    """Turn it on for the user this Hub runs as, then read it back. There is no way to turn it off.

    `defaults write` goes through cfprefsd in the same user session Finder reads its preferences from,
    so Finder sees the change without being restarted - its own Settings pane writes this same key the
    same way. **Nothing here kills Finder.** The answer is the value **read back**, not the write's exit
    code: a write that did not stick is a refusal, not a success.
    """
    written = _defaults("write", FINDER_DOMAIN, FINDER_TRASH_KEY, "-bool", "true")
    if written is None:
        raise TrashRefused(503, "DEFAULTS_UNAVAILABLE", "`defaults` could not be run on this Mac.")
    if written.returncode != 0:
        raise TrashRefused(500, "WRITE_FAILED",
                           (written.stderr or "`defaults write` failed").strip()[:300])
    state = finder_trash_auto_empty(fresh=True)
    if state is not True:
        raise TrashRefused(500, "NOT_APPLIED",
                           "The preference was written but does not read back as on.")
    return {"macos_trash_auto_empty": True, "finder_restart_required": False}


def _real_day(day: Path, real_root: Path) -> bool:
    """A date folder that is a real folder (not a link) whose real path is directly in the Trash Folder."""
    try:
        return stat.S_ISDIR(day.lstat().st_mode) and Path(os.path.realpath(day)) == real_root / day.name
    except OSError:
        return False


def _trash_items(wanted: object = "all") -> list[tuple[str, Path]]:
    """`(id, path)` for every `<date>/<name>` in the Hub's Trash Folder, or for the `wanted` ids.

    An id is exactly two levels (`<date>/<name>`, never `.` or `..`) under a date folder that passes
    `_real_day`. One id that is malformed or unsafe refuses the whole request (400); one that is no longer
    there refuses it as a changed Trash Folder (409). Links are listed as themselves, never followed."""
    root = trash_root()
    real_root = Path(os.path.realpath(root))
    root_ok = root.is_dir() and not root.is_symlink()
    if wanted == "all":
        found: list[tuple[str, Path]] = []
        try:
            days = sorted(p for p in root.iterdir() if _DAY.fullmatch(p.name)) if root_ok else []
        except OSError:
            days = []
        for day in days:
            if _real_day(day, real_root):
                try:
                    found += [(f"{day.name}/{entry.name}", entry) for entry in sorted(day.iterdir())]
                except OSError:
                    continue
        return found
    if not isinstance(wanted, list) or not wanted or not all(isinstance(item, str) for item in wanted):
        raise TrashRefused(400, "BAD_ITEMS", 'items must be "all" or a non-empty list of item ids')
    found = []
    for item in dict.fromkeys(wanted):
        match = _ITEM.fullmatch(item)
        if not root_ok or not match or match[2] in (".", "..") or not _real_day(root / match[1], real_root):
            raise TrashRefused(400, "BAD_ITEM", f"{item!r} is not an item in this Mac's Trash Folder")
        entry = root / match[1] / match[2]
        if not os.path.lexists(entry):
            raise TrashRefused(409, "TRASH_CHANGED", f"{item!r} is no longer in the Trash Folder; look again")
        found.append((item, entry))
    return found


def trash_listing(now: float | None = None) -> dict:
    """What the Hub's Trash Folder holds - every item with its physical bytes, kind and age - with
    totals by kind and age, and this disk's free space, so the Gateway can show who is filling up."""
    import calendar
    from . import disk_governor
    now = time.time() if now is None else float(now)
    items, kinds, ages = [], {}, {}
    for item, path in _trash_items():
        size = disk_governor.cached_physical(path, now)
        label = _RETIRED.fullmatch(path.name)
        kind = label[1] if label else "output"
        age = max(0.0, (now - calendar.timegm(time.strptime(item[:10], "%Y-%m-%d"))) / 86400)
        bucket = next(word for days, word in _AGES if days is None or age < days)
        for table, key in ((kinds, kind), (ages, bucket)):
            row = table.setdefault(key, {"count": 0, "bytes": 0})
            row["count"] += 1
            row["bytes"] += size
        items.append({"id": item, "kind": kind, "bytes": size, "age_days": round(age, 1)})
    try:
        usage = shutil.disk_usage(ledger.DATA_DIR)
        disk = {"free_bytes": usage.free, "total_bytes": usage.total}
    except OSError:
        disk = {"free_bytes": None, "total_bytes": None}
    return {"trash_folder": str(trash_root()), "count": len(items), "bytes": sum(i["bytes"] for i in items),
            "by_kind": kinds, "by_age": ages, "items": items, "disk": disk, "measured_at": now}


def _root_usage(base: Path, now: float) -> dict:
    """Items and physical bytes in one Trash Folder's dated subfolders."""
    from . import disk_governor
    count = total = 0
    try:
        days = sorted(p for p in base.iterdir() if _DAY.fullmatch(p.name) and p.is_dir() and not p.is_symlink())
    except OSError:
        days = []
    for day in days:
        try:
            for entry in day.iterdir():
                count += 1
                total += disk_governor.cached_physical(entry, now)
        except OSError:
            continue
    return {"path": str(base), "count": count, "bytes": total}


def trash_overview(now: float | None = None) -> dict:
    """The unified Trash Folder for the app: the same listing the Gateway reads, plus every root.

    `roots` names the Hub's own Trash Folder and each managed output root's `.studiohub-trash`
    (created when that root is on another volume), so the app can be honest about files the
    unified action cannot reach. A cross-volume root is left in place and only listed.
    """
    now = time.time() if now is None else float(now)
    result = trash_listing(now)
    primary = trash_root()
    try:
        primary_device = os.stat(primary).st_dev
    except OSError:
        primary_device = None
    roots = []
    for base in _trash_bases():
        row = _root_usage(base, now)
        try:
            device = os.stat(base).st_dev
        except OSError:
            device = None
        row["cross_volume"] = (primary_device is not None and device is not None
                               and device != primary_device)
        row["primary"] = base == primary
        roots.append(row)
    result["roots"] = roots
    # macOS's own Trash setting, beside the folder it feeds: the app shows it and offers to turn it on.
    result["macos_trash_auto_empty"] = finder_trash_auto_empty()
    return result


def _macos_trash(paths: list[str]) -> list[str]:
    """Move each path to the user's macOS Trash with NSFileManager.trashItem (Finder's Move to Trash),
    from a JavaScript-for-Automation child: no Finder automation prompt. One line per path: "ok" or why."""
    try:
        done = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", _JXA_TRASH, *paths],
                              capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as error:
        return [str(error)] * len(paths)
    lines = done.stdout.splitlines()
    return lines if len(lines) == len(paths) else [(done.stderr or "osascript failed").strip()[:300]] * len(paths)


def trash_to_macos(items: object) -> dict:
    """Move the chosen items (`"all"` or ids) to macOS Trash. Each move is atomic; whether an item moved
    is read back from the disk, so a partial failure reports exactly what is still in the Trash Folder.
    `_LOCK` is held only to choose the items: osascript can run for minutes and a move to macOS Trash can
    be undone, so the Hub's other requests are never made to wait for it."""
    global _TRASH_SIZE
    with _LOCK:
        found = _trash_items(items)
    reasons = _macos_trash([str(path) for _item, path in found]) if found else []
    moved = [item for item, path in found if not os.path.lexists(path)]
    failed = [{"id": item, "error": reason if reason != "ok" else "still in the Trash Folder"}
              for (item, path), reason in zip(found, reasons) if os.path.lexists(path)]
    _TRASH_SIZE = (0.0, 0)
    for item in failed:
        log.warning("could not move %s from the Trash Folder to macOS Trash: %s", item["id"], item["error"])
    return {"ok": not failed, "moved": moved, "moved_count": len(moved), "failed": failed}


def trash_handoff(days: int, max_bytes: int | None = None, now: float | None = None) -> dict:
    """Move Trash Folder items to macOS Trash: those older than `days`, and the oldest until the
    whole Trash Folder is under `max_bytes`.

    The owner's second half of the 2026-10-15 request, extended 2026-10-19 with the size cap. Age is
    the dated folder an item lives in (`<trash>/<UTC date>/...`, the same figure `trash_listing`
    shows). `max_bytes` is measured over the *unified* Trash Folder - every root `_trash_bases()`
    names, so a managed output root's own `.studiohub-trash` counts too - while only the Hub's own
    items can be handed over, so a Trash Folder left over the cap by another root reports
    `over_cap`. Items go oldest first. It uses the one `trash_to_macos` path, so every move is the
    normal recoverable macOS Trash one; the reusable Trash Folder stays, an item that fails stays
    listed, and nothing is ever permanently deleted here.
    """
    import calendar
    from . import disk_governor
    now = time.time() if now is None else float(now)
    cutoff = days * 86400 if isinstance(days, int) and not isinstance(days, bool) and days > 0 else None
    if cutoff is None and max_bytes is None:
        return {"ok": True, "moved_count": 0, "failed": [], "eligible_count": 0,
                "trash_bytes": 0, "over_cap": False}
    items = _trash_items()
    sizes = {item: disk_governor.cached_physical(path, now) for item, path in items}
    total = trash_bytes()                 # the unified Trash Folder, every root counted
    eligible: list[str] = []
    remaining = total
    for item, _path in items:             # `_trash_items()` is oldest date first, then by name
        old = False
        if cutoff is not None:
            try:
                old = now - calendar.timegm(time.strptime(item[:10], "%Y-%m-%d")) >= cutoff
            except ValueError:
                old = False
        if old or (max_bytes is not None and remaining > max_bytes):
            eligible.append(item)
            remaining -= sizes[item]
    answer = {"eligible_count": len(eligible), "trash_bytes": total}

    def over_cap(left: float) -> bool:
        return max_bytes is not None and left > max_bytes

    if not eligible:
        return {"ok": True, "moved_count": 0, "failed": []} | answer | {"over_cap": over_cap(total)}
    try:
        result = trash_to_macos(eligible)
    except TrashRefused as refusal:
        return {"ok": False, "moved_count": 0, "failed": [{"id": item, "error": refusal.body["detail"]}
                                                         for item in eligible]} | answer | {"over_cap": over_cap(total)}
    if result["failed"]:
        log.warning("Trash Folder: automatic handoff to macOS Trash left %d of %d items (%s)",
                    len(result["failed"]), len(eligible), result["failed"][0]["error"])
    # `over_cap` is measured after the moves, from what actually left: a move that failed leaves its
    # bytes in the Trash Folder, so the report cannot be the plan.
    left = total - sum(sizes[item] for item in result["moved"])
    return {"ok": result["ok"], "moved_count": result["moved_count"], "failed": result["failed"]} | answer | {
        "over_cap": over_cap(left)}


_OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_STAGING = ".deleting-"
_PURGE = threading.Lock()                           # one rmtree pass at a time; never held with _LOCK


def _open_day(day: Path, real_root: Path) -> int:
    """An fd on a date folder that `_real_day` accepted, checked again on the fd itself, so a swap for a
    link between the check and the open cannot redirect what is done through the fd."""
    if not _real_day(day, real_root):
        raise OSError("its folder is no longer inside the Trash Folder")
    before = day.lstat()
    fd = os.open(day, _OPEN_DIR)                    # O_NOFOLLOW: a link here fails
    after = os.fstat(fd)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        os.close(fd)
        raise OSError("its folder changed while it was being opened")
    return fd


def _purge_entry(name: str, dir_fd: int) -> None:
    """Delete `name` inside `dir_fd`: a link is removed itself; `rmtree` never follows one."""
    try:
        is_dir = stat.S_ISDIR(os.lstat(name, dir_fd=dir_fd).st_mode)
    except FileNotFoundError:
        return                                      # a concurrent sweep already removed it
    if is_dir:
        shutil.rmtree(name, dir_fd=dir_fd)
    else:
        os.unlink(name, dir_fd=dir_fd)


def _purge_staging(root: Path, mine: dict[str, str] | None = None, staged: str = "") -> list[dict]:
    """Delete every `.deleting-*` folder in the Trash Folder (this delete's `staged`, and any a crash left
    behind). `mine` maps entry name -> item id for `staged`; those that fail are returned as failed."""
    failed: list[dict] = []
    with _PURGE:
        try:
            root_fd = os.open(root, _OPEN_DIR)
        except OSError:
            return failed
        try:
            for leftover in sorted(n for n in os.listdir(root_fd) if n.startswith(_STAGING)):
                try:
                    stage_fd = os.open(leftover, _OPEN_DIR, dir_fd=root_fd)
                except OSError:
                    continue
                try:
                    for name in os.listdir(stage_fd):
                        try:
                            _purge_entry(name, stage_fd)
                        except OSError as error:
                            if leftover == staged and name in (mine or {}):
                                failed.append({"id": mine[name], "error": str(error)[:300]})
                            else:
                                log.warning("Trash Folder: could not finish deleting %s/%s (%s)", leftover, name, error)
                finally:
                    os.close(stage_fd)
                try:
                    os.rmdir(leftover, dir_fd=root_fd)      # only once it is empty; otherwise the next delete retries
                except OSError:
                    pass
        finally:
            os.close(root_fd)
    return failed


def trash_delete(*, items: object, confirm: object, expected_count: object, expected_bytes: object) -> dict:
    """Permanently delete the chosen items (`"all"` or ids) - only ever on the owner's explicit request.

    `confirm` must be true and `expected_count` / `expected_bytes` must equal what is measured here;
    otherwise 409 and nothing is deleted. Under `_LOCK` only the fast steps run: list, measure (cached
    physical bytes, the same figure the listing showed), compare, and rename each item into a
    `.deleting-<id>` folder beside the date folders (same volume, atomic). The lock is then released and
    the staging folder is removed, so a long delete never stalls the Hub. An item that cannot be renamed
    stays listed; one that cannot be removed afterwards is reported, is no longer listed, and its
    leftovers are removed by the next delete."""
    if confirm is not True:
        raise TrashRefused(400, "CONFIRM_REQUIRED", "Permanent delete needs confirm: true.")
    if any(type(value) is not int or value < 0 for value in (expected_count, expected_bytes)):
        raise TrashRefused(400, "EXPECTED_REQUIRED",
                           "expected_count and expected_bytes must be the count and bytes that were confirmed.")
    from . import disk_governor
    global _TRASH_SIZE
    now = time.time()
    root = trash_root()
    staged, mine, sizes, failed = "", {}, {}, []
    with _LOCK:
        found = _trash_items(items)
        measured = [disk_governor.cached_physical(path, now) for _item, path in found]
        count, total = len(found), sum(measured)
        if (count, total) != (expected_count, expected_bytes):
            raise TrashRefused(409, "TRASH_CHANGED",
                               f"The Trash Folder now holds {count} items ({total} bytes) of those chosen, not the "
                               f"{expected_count} items ({expected_bytes} bytes) confirmed. Nothing was deleted.",
                               count=count, bytes=total)
        real_root = Path(os.path.realpath(root))
        if found:
            staged = f"{_STAGING}{os.urandom(6).hex()}"
            os.mkdir(root / staged, 0o700)
            stage_fd = os.open(root / staged, _OPEN_DIR)
            try:
                for n, ((item, path), size) in enumerate(zip(found, measured)):
                    try:
                        day_fd = _open_day(path.parent, real_root)
                        try:
                            os.rename(path.name, str(n), src_dir_fd=day_fd, dst_dir_fd=stage_fd)
                        finally:
                            os.close(day_fd)
                    except OSError as error:
                        failed.append({"id": item, "error": str(error)[:300]})
                    else:
                        mine[str(n)], sizes[item] = item, size
            finally:
                os.close(stage_fd)
        _TRASH_SIZE = (0.0, 0)
    failed += _purge_staging(root, mine, staged)
    gone = {f["id"] for f in failed}
    deleted = [item for item in mine.values() if item not in gone]
    freed = sum(sizes[item] for item in deleted)
    log.warning("Trash Folder: permanently deleted %d items (%d bytes) on the owner's request; %d failed",
                len(deleted), freed, len(failed))
    return {"ok": not failed, "deleted": deleted, "deleted_count": len(deleted), "freed_bytes": freed,
            "failed": failed}


def _to_trash(path: Path, group, file_id: int, now: float, managed_root: object = None) -> bool:
    """Move one output into today's Trash Folder.  False (file untouched) if the rename fails.

    The Hub's Trash Folder is tried first (a same-volume rename). When that fails - a managed output
    root on another volume is the usual reason - the file goes to a Trash Folder inside its own root,
    which is the same volume by construction and is kept like the other. A move that fails both
    ways is counted and logged, never silent.
    """
    batch = re.sub(r"[^A-Za-z0-9._-]", "_", str(group["batch_id"] or group["job_id"]))
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    leaf = Path(day) / batch / f"{file_id}-{path.name}"
    try:
        target = trash_root() / leaf
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(path, target)                 # same volume or it fails; never a delete
        return True
    except OSError as primary:
        error = primary
    if managed_root:
        fallback_root = Path(str(managed_root)) / FALLBACK_TRASH_NAME
        try:
            target = fallback_root / leaf
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(path, target)
        except OSError as secondary:
            error = secondary
        else:
            _TRASH_STATS["fallback_moves"] += 1
            if str(managed_root) not in _FALLBACK_LOGGED:
                _FALLBACK_LOGGED.add(str(managed_root))
                log.warning("output cleanup: %s is not on the Hub's volume (%s), so its expired outputs go to "
                            "%s instead", managed_root, error, fallback_root)
            return True
    _TRASH_STATS["move_failures"] += 1
    _TRASH_STATS["last_failure"] = f"{path}: {error}"
    log.error("output cleanup could not move %s to a Trash Folder (%s); it stays in live output, "
              "counts against the limit and will be retried", path, error)
    return False


def _root_for(path: Path, roots: list[Path]) -> Path | None:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    for root in roots:
        try:
            if resolved.is_relative_to(root):
                return root
        except ValueError:
            pass
    return None


def _regular(path: Path, roots: list[Path]) -> tuple[os.stat_result, Path] | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return None
    root = _root_for(path, roots)
    return (path.stat(), root) if root else None


def _inspect(path: Path, roots: list[Path]) -> tuple[str, os.stat_result | None, Path | None]:
    """Distinguish a gone output from an inaccessible or unsafe one."""
    for root in roots:
        try:
            root_info = root.lstat()
        except OSError:
            return "uncertain", None, None  # a disconnected managed volume is not a deletion
        if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
            return "uncertain", None, None
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "missing", None, None
    except OSError:
        return "uncertain", None, None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return "uncertain", None, None
    root = _root_for(path, roots)
    if root is None:
        return "uncertain", None, None
    try:
        return "ok", path.stat(), root
    except OSError:
        return "uncertain", None, None


def _same_file(info: os.stat_result, row) -> bool:
    # Link-count changes update ctime on every remaining hard link.  mtime, size, inode and the
    # checksum recheck below identify the bytes.  `st_dev` is deliberately not compared: macOS
    # renumbers a volume's device between boots (measured here: three different values across
    # three reboots), so comparing it marked every output generated before the last restart
    # "uncertain" for ever - never moved to the Trash Folder, still counted against `max_bytes`.
    return (row["inode"], row["bytes"], row["modified_ns"]) == (
        info.st_ino, info.st_size, info.st_mtime_ns)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _roots(value: object) -> list[Path]:
    roots = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, str) or not item:
            continue
        path = Path(item).expanduser()
        try:
            info = path.lstat()
        except OSError:
            continue                         # a disconnected SSD is never recreated
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            continue
        roots.append(path.resolve())
    return roots


def _space_roots(value: object) -> list[Path]:
    """Use a fresh output leaf's existing direct parent, never an ancestor."""
    roots = _roots(value)
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, str) or not item:
            continue
        path = Path(item).expanduser()
        try:
            path.lstat()
        except FileNotFoundError:
            parent = path.parent
            try:
                info = parent.lstat()
            except OSError:
                continue
            if not stat.S_ISLNK(info.st_mode) and stat.S_ISDIR(info.st_mode):
                roots.append(parent.resolve())
        except OSError:
            continue
    return roots


def register_finalized_output(*, machine: str, studio: str, job_id: str,
                              managed_output_roots: object, output_path: object,
                              generated_at: object, batch_id: object = None,
                              item_index: object = None, attempt: object = 0,
                              model: object = None, runtime_identity: object = None,
                              bytes: object = None, sha256: object = None,
                              derivatives: object = None) -> dict | None:
    """Index one immutable finalized group, or safely decline untrusted paths."""
    _schema()
    roots = _roots(managed_output_roots)
    if not roots or not isinstance(output_path, str) or not output_path:
        return None
    try:
        generated = float(generated_at)
        attempt_value = int(attempt or 0)
    except (TypeError, ValueError):
        return None
    paths = [(output_path, bytes, sha256)]
    if isinstance(derivatives, list):
        for item in derivatives:
            if isinstance(item, str):
                paths.append((item, None, None))
            elif isinstance(item, dict) and isinstance(item.get("path"), str):
                paths.append((item["path"], item.get("bytes"), item.get("sha256")))
    records = []
    seen = set()
    for raw, expected_bytes, expected_sha256 in paths:
        if raw in seen:
            continue
        seen.add(raw)
        checked = _regular(Path(raw), roots)
        if checked is None:
            return None
        info, root = checked
        if expected_bytes is not None and (isinstance(expected_bytes, bool)
                                           or not isinstance(expected_bytes, int)
                                           or info.st_size != expected_bytes):
            return None
        actual_sha256 = _sha256(Path(raw))
        if expected_sha256 is not None and (not isinstance(expected_sha256, str) or len(expected_sha256) != 64
                                           or actual_sha256 != expected_sha256.lower()):
            return None
        records.append((str(Path(raw).resolve()), str(root), info, actual_sha256))
    if not records:
        return None
    group_id = f"{machine}:{studio}:{job_id}:{attempt_value}"
    with ledger._conn() as conn:
        conn.execute("""INSERT INTO output_groups
          (id,machine,studio,job_id,batch_id,item_index,attempt,model,runtime_identity,generated_at,created_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(machine,studio,job_id,attempt) DO UPDATE SET
            batch_id=COALESCE(excluded.batch_id,output_groups.batch_id),
            item_index=COALESCE(excluded.item_index,output_groups.item_index),
            model=COALESCE(excluded.model,output_groups.model),
            runtime_identity=COALESCE(excluded.runtime_identity,output_groups.runtime_identity)
          """, (group_id, machine, studio, job_id, batch_id,
                  item_index if isinstance(item_index, int) else None, attempt_value,
                  str(model) if model else None, str(runtime_identity) if runtime_identity else None,
                  generated, time.time()))
        for path, root, info, actual_sha256 in records:
            conn.execute("""INSERT INTO output_files
              (group_id,path,managed_root,device,inode,bytes,sha256,modified_ns,changed_ns,present)
              VALUES (?,?,?,?,?,?,?,?,?,1)
              ON CONFLICT(group_id,path) DO UPDATE SET device=excluded.device,inode=excluded.inode,
              bytes=excluded.bytes,sha256=COALESCE(excluded.sha256,output_files.sha256),
              modified_ns=excluded.modified_ns,changed_ns=excluded.changed_ns,present=1""",
                         (group_id, path, root, info.st_dev, info.st_ino, info.st_size,
                          actual_sha256,
                          info.st_mtime_ns, info.st_ctime_ns))
    return {"id": group_id, "paths": len(records), "generated_at": generated}


def mark_receipt_acked(*, batch_id: str, item_index: int) -> None:
    _schema()
    with ledger._conn() as conn:
        conn.execute("UPDATE output_groups SET receipt_acked_at=COALESCE(receipt_acked_at,?) "
                     "WHERE batch_id=? AND item_index=?", (time.time(), batch_id, item_index))


def _reconcile(conn, machine: str | None = None) -> None:
    where, args = ("", []) if machine is None else (" WHERE g.machine=?", [machine])
    rows = conn.execute("SELECT f.id,f.group_id,f.path,f.managed_root,f.device,f.inode,f.bytes,f.sha256,f.modified_ns,f.changed_ns FROM output_files f "
                        "JOIN output_groups g ON g.id=f.group_id" + where, args).fetchall()
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(row["group_id"], []).append(row)
    for group_id, files in groups.items():
        group = conn.execute("SELECT state,recoverable FROM output_groups WHERE id=?", (group_id,)).fetchone()
        was_uncertain = group is not None and group["state"] == "uncertain"
        recovered = was_uncertain and group["recoverable"]
        safe = True
        uncertain = False
        unavailable = False
        drifted = False
        for row in files:
            path, root = Path(row["path"]), Path(row["managed_root"])
            state, info, _ = _inspect(path, [root])
            if state == "missing":
                conn.execute("UPDATE output_files SET present=0 WHERE id=?", (row["id"],))
                safe = False
                continue
            if state != "ok" or info is None:
                uncertain = True
                safe = False
                unavailable = True
                continue
            try:
                # Identity unchanged (inode, size, mtime) is enough for a group that is already
                # trusted: hashing every output on every sweep read the whole live library
                # (20 GB by default, ~12 s at 1.6 GB/s) on the scheduler's event loop each time
                # admission asked, and pushed the models' pages out of a small Mac's file cache.
                # A group returning from `uncertain` still has to prove its bytes, and `enforce`
                # re-hashes each file immediately before it moves it.
                matches = _same_file(info, row) and (
                    not was_uncertain or _sha256(path) == row["sha256"])
            except OSError:
                matches = False
            if not matches:
                uncertain = True
                safe = False
                continue
            if info.st_dev != row["device"]:
                drifted = True
                conn.execute("UPDATE output_files SET device=? WHERE id=?", (info.st_dev, row["id"]))
            conn.execute("UPDATE output_files SET present=1 WHERE id=?", (row["id"],))
        if uncertain:
            conn.execute("UPDATE output_groups SET state='uncertain',recoverable=? WHERE id=?",
                         (1 if unavailable else 0, group_id))
        elif was_uncertain and safe and (recovered or drifted):
            # A temporarily missing/inaccessible root becomes eligible only when
            # every original path and checksum has returned unchanged.  A group parked as
            # "uncertain" before `st_dev` stopped being compared (same bytes, only the device
            # number renumbered by a reboot) is released the same way.
            conn.execute("UPDATE output_groups SET state='finalized',recoverable=0 WHERE id=?", (group_id,))


def _collectable(row, now: float) -> bool:
    """May the ordinary rules move this group? Acknowledged, or unacknowledged for UNACKED_DAYS; never leased."""
    return (row["state"] == "finalized" and row["reader_leases"] == 0 and row["transfer_leases"] == 0
            and (row["receipt_acked_at"] is not None or row["generated_at"] < now - UNACKED_DAYS * 86400))


def preview(*, machine: str, max_bytes: int, retention_days: int,
            enabled: bool = True, now: float | None = None,
            managed_output_roots: object = None, minimum_free_bytes: int = 0) -> dict:
    """Read the existing index without creating it, reconciling it, or deleting."""
    now = time.time() if now is None else float(now)
    if not ledger.DB_FILE.exists():
        rows = []
    else:
        try:
            with ledger._conn() as conn:
                rows = conn.execute("""SELECT g.*,f.device,f.inode,f.bytes,f.present,f.managed_root FROM output_groups g
                  JOIN output_files f ON f.group_id=g.id WHERE g.machine=? AND f.present=1""", (machine,)).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower() or "output_" not in str(exc).lower():
                raise
            rows = []
    physical = {}
    protected = {}
    removable: dict[str, list] = {}
    cutoff = now - retention_days * 86400
    for row in rows:
        key = (row["device"], row["inode"])
        physical[key] = max(physical.get(key, 0), row["bytes"])
        eligible = enabled and _collectable(row, now)
        if not eligible:
            protected[key] = max(protected.get(key, 0), row["bytes"])
        else:
            removable.setdefault(row["id"], [row["generated_at"], 0])[1] += row["bytes"]
    used = sum(physical.values())
    # What the next sweep would delete under these limits: `enforce`'s own rule (oldest eligible
    # group first, while it is past retention or the total is over the limit), replayed on the
    # index without touching a file. Hard links are counted per file, so it can overstate.
    would_delete, simulated = 0, used
    for generated_at, size in sorted(removable.values()):
        if generated_at >= cutoff and simulated <= max_bytes:
            continue
        would_delete += size
        simulated = max(0, simulated - size)
    free = []
    roots = {Path(row["managed_root"]) for row in rows}
    roots.update(_space_roots(managed_output_roots))
    for root in roots:
        try:
            free.append(shutil.disk_usage(root).free)
        except OSError:
            pass
    return {"machine": machine, "used_bytes": used, "protected_bytes": sum(protected.values()),
            "max_bytes": max_bytes, "retention_days": retention_days, "enabled": enabled,
            "minimum_free_bytes": minimum_free_bytes, "would_delete_bytes": would_delete,
            "over_limit": used > max_bytes, "free_bytes": min(free) if free else None,
            "expired_before": cutoff,
            "trash_move_failures": _TRASH_STATS["move_failures"],
            "trash_fallback_moves": _TRASH_STATS["fallback_moves"],
            "trash_last_failure": _TRASH_STATS["last_failure"]}


def enforce(*, machine: str, max_bytes: int, retention_days: int,
            enabled: bool = True, now: float | None = None,
            managed_output_roots: object = None, minimum_free_bytes: int = 0) -> dict:
    with _LOCK:
        _schema()
        now = time.time() if now is None else float(now)
        with ledger._conn() as conn:
            _reconcile(conn, machine)
        result = preview(machine=machine, max_bytes=max_bytes, retention_days=retention_days,
                         enabled=enabled, now=now, managed_output_roots=managed_output_roots,
                         minimum_free_bytes=minimum_free_bytes)
        if not enabled:
            return result | {"deleted": 0, "freed_bytes": 0}
        cutoff = now - retention_days * 86400
        with ledger._conn() as conn:
            rows = conn.execute("""SELECT * FROM output_groups WHERE machine=? AND state='finalized'
              AND (receipt_acked_at IS NOT NULL OR generated_at < ?)
              AND reader_leases=0 AND transfer_leases=0
              ORDER BY generated_at ASC""", (machine, now - UNACKED_DAYS * 86400)).fetchall()
            used = result["used_bytes"]
            deleted = freed = 0                 # `deleted` keeps its name: files taken out of live output
            for group in rows:
                current = conn.execute("SELECT * FROM output_groups WHERE id=?", (group["id"],)).fetchone()
                if current is None or not _collectable(current, now):
                    continue
                if group["generated_at"] >= cutoff and used <= max_bytes:
                    continue
                files = conn.execute("SELECT * FROM output_files WHERE group_id=? AND present=1",
                                     (group["id"],)).fetchall()
                for file in files:
                    state, info, _ = _inspect(Path(file["path"]), [Path(file["managed_root"])])
                    if state == "missing":
                        conn.execute("UPDATE output_files SET present=0 WHERE id=?", (file["id"],))
                        continue
                    if state != "ok" or info is None or not _same_file(info, file) or _sha256(Path(file["path"])) != file["sha256"]:
                        conn.execute("UPDATE output_groups SET state='uncertain' WHERE id=?", (group["id"],))
                        continue
                    if not _to_trash(Path(file["path"]), group, file["id"], now, file["managed_root"]):
                        continue
                    conn.execute("UPDATE output_files SET present=0 WHERE id=?", (file["id"],))
                    deleted += 1
                    # A moved file still holds its inode (in the Trash Folder), so `st_nlink` cannot say
                    # when the bytes leave live output: that is when no other indexed link remains.
                    if conn.execute("SELECT 1 FROM output_files WHERE device=? AND inode=? AND present=1",
                                    (file["device"], file["inode"])).fetchone() is None:
                        freed += info.st_size
                        used = max(0, used - info.st_size)
                remaining = conn.execute("SELECT 1 FROM output_files WHERE group_id=? AND present=1",
                                         (group["id"],)).fetchone()
                if remaining is None:
                    conn.execute("UPDATE output_groups SET state='deleted' WHERE id=?", (group["id"],))
        final = preview(machine=machine, max_bytes=max_bytes, retention_days=retention_days,
                        enabled=enabled, now=now, managed_output_roots=managed_output_roots,
                        minimum_free_bytes=minimum_free_bytes)
        return final | {"deleted": deleted, "freed_bytes": freed}


# What a Voice worker leaves in its output folder (`components/voice/app/backend/storage_policy.py`).
VOICE_OUTPUT_SUFFIXES = frozenset({".wav", ".mp3", ".ogg", ".flac", ".m4a"})


def trash_unregistered(roots: object, *, min_age_seconds: float,
                       now: float | None = None) -> tuple[int, int]:
    """Move generated audio the Hub never registered into the Trash Folder. Returns (bytes, files).

    The packaged Voice worker's own retention loop is off (the Hub owns the policy), but the Hub only
    cleans what `register_finalized_output` indexed, and that happens only when the Hub's own broker
    finishes a job. A job a controller ran on this Mac as a peer, or anything else that wrote to the
    worker's output folder without passing through the broker, was therefore kept for ever. Such a
    file has no acknowledgement to wait for, so it follows the unacknowledged rule: it moves once it
    is `min_age_seconds` old (the caller passes `UNACKED_DAYS`), which also leaves any job still
    being finished alone. Only top-level audio files of the given roots; hidden files (partials),
    links, other folders and everything the index already knows are never touched. A move that
    fails leaves the file where it is, to be retried.
    """
    now = time.time() if now is None else float(now)
    freed = files = 0
    with _LOCK:
        _schema()
        with ledger._conn() as conn:
            known_paths, known_inodes = set(), set()
            for row in conn.execute("SELECT path, inode FROM output_files"):
                known_paths.add(row["path"])
                known_inodes.add(row["inode"])
        for root in _roots([str(item) for item in (roots or [])]):
            try:
                entries = list(os.scandir(root))
            except OSError:
                continue
            for entry in entries:
                if entry.name.startswith(".") or Path(entry.name).suffix.lower() not in VOICE_OUTPUT_SUFFIXES:
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if (not stat.S_ISREG(info.st_mode) or now - info.st_mtime < min_age_seconds
                        or entry.path in known_paths or info.st_ino in known_inodes):
                    continue
                group = {"batch_id": "unregistered", "job_id": Path(entry.name).stem}
                if _to_trash(Path(entry.path), group, info.st_ino, now, root):
                    freed += info.st_size
                    files += 1
    return freed, files


def reclaim_acked(*, machine: str, min_age_seconds: float, done, now: float | None = None) -> tuple[int, int]:
    """Move acknowledged outputs into the Trash Folder ahead of retention, oldest first, until `done()`.

    Only the disk governor calls this, in amber and red. A same-volume move frees no disk space by
    itself: it gathers the delivered copies where the owner empties them (macOS Trash), and nothing
    is deleted.  Never an unacknowledged, leased or uncertain output, and never one younger than
    `min_age_seconds`; every file is re-checked against its recorded identity and checksum first,
    exactly as `enforce` does before it moves one.  Returns (bytes taken out of live output, files moved).
    """
    freed = files = 0
    with _LOCK:
        _schema()
        now = time.time() if now is None else float(now)
        with ledger._conn() as conn:
            _reconcile(conn, machine)
            rows = conn.execute("""SELECT id FROM output_groups WHERE machine=? AND state='finalized'
              AND receipt_acked_at IS NOT NULL AND reader_leases=0 AND transfer_leases=0
              AND generated_at<=? ORDER BY generated_at ASC""", (machine, now - min_age_seconds)).fetchall()
            for row in rows:
                if done():
                    break
                group = conn.execute("SELECT * FROM output_groups WHERE id=?", (row["id"],)).fetchone()
                if (group is None or group["state"] != "finalized" or group["receipt_acked_at"] is None
                        or group["reader_leases"] or group["transfer_leases"]):
                    continue
                for file in conn.execute("SELECT * FROM output_files WHERE group_id=? AND present=1",
                                         (group["id"],)).fetchall():
                    state, info, _ = _inspect(Path(file["path"]), [Path(file["managed_root"])])
                    if state == "missing":
                        conn.execute("UPDATE output_files SET present=0 WHERE id=?", (file["id"],))
                        continue
                    try:
                        sound = (state == "ok" and info is not None and _same_file(info, file)
                                 and _sha256(Path(file["path"])) == file["sha256"])
                    except OSError:
                        sound = False
                    if not sound:
                        conn.execute("UPDATE output_groups SET state='uncertain' WHERE id=?", (group["id"],))
                        continue
                    if not _to_trash(Path(file["path"]), group, file["id"], now, file["managed_root"]):
                        continue
                    conn.execute("UPDATE output_files SET present=0 WHERE id=?", (file["id"],))
                    files += 1
                    if conn.execute("SELECT 1 FROM output_files WHERE device=? AND inode=? AND present=1",
                                    (file["device"], file["inode"])).fetchone() is None:
                        freed += info.st_size
                if conn.execute("SELECT 1 FROM output_files WHERE group_id=? AND present=1",
                                (group["id"],)).fetchone() is None:
                    conn.execute("UPDATE output_groups SET state='deleted' WHERE id=?", (group["id"],))
    return freed, files


# How long an admission answer may be reused. The scheduler asks this question for every queued item
# on every pass - several times a second while work is waiting - and `enforce` walks and hashes the
# managed output roots to answer it, synchronously, on the event loop. The answer is therefore
# remembered for a few seconds per policy. Enforcement itself is unchanged: it still runs on the
# background timer (which moves files to the Trash Folder) and on every operator action; this only stops the
# scheduler re-sweeping the same roots in a tight loop, which is where a Mac with a full library
# stalled its own Hub.
_ADMISSION_TTL_SECONDS = 10.0
_ADMISSION_SNAPSHOT: dict[tuple, tuple[float, dict]] = {}


def admission(*, machine: str, max_bytes: int, retention_days: int, enabled: bool,
              managed_output_roots: object = None, minimum_free_bytes: int = 0) -> dict | None:
    key = (machine, max_bytes, retention_days, enabled, minimum_free_bytes,
           tuple(managed_output_roots) if isinstance(managed_output_roots, list) else managed_output_roots)
    cached = _ADMISSION_SNAPSHOT.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _ADMISSION_TTL_SECONDS:
        result = copy.deepcopy(cached[1])
    else:
        result = enforce(machine=machine, max_bytes=max_bytes, retention_days=retention_days,
                         enabled=enabled, managed_output_roots=managed_output_roots,
                         minimum_free_bytes=minimum_free_bytes)
        # One entry per policy: a fleet of nodes each with their own limits must not evict each other.
        _ADMISSION_SNAPSHOT[key] = (now, copy.deepcopy(result))
        if len(_ADMISSION_SNAPSHOT) > 64:
            oldest = min(_ADMISSION_SNAPSHOT, key=lambda item: _ADMISSION_SNAPSHOT[item][0])
            _ADMISSION_SNAPSHOT.pop(oldest, None)
    if result["used_bytes"] > max_bytes:
        return {"code": "OUTPUT_STORAGE_BLOCKED", "detail": "generated output cannot meet this device policy", "storage": result}
    has_managed_root = isinstance(managed_output_roots, list) and bool(managed_output_roots)
    # ponytail: the Trash Folder shares this disk and is never emptied here (the owner empties it), so
    # a full Trash Folder can be why work is refused; the disk warnings (`disk_governor`) name its size.
    if minimum_free_bytes and has_managed_root and (
            result["free_bytes"] is None or result["free_bytes"] < minimum_free_bytes):
        return {"code": "OUTPUT_STORAGE_BLOCKED", "detail": f"managed output storage has insufficient free space; empty the Trash Folder ({trash_root()}) to free space", "storage": result}
    return None


def _acquire_lease(*, batch_id: str, item_index: int, column: str):
    _schema()
    with _LOCK, ledger._conn() as conn:
        row = conn.execute("SELECT id FROM output_groups WHERE batch_id=? AND item_index=? AND state='finalized'",
                           (batch_id, item_index)).fetchone()
        if row:
            conn.execute(f"UPDATE output_groups SET {column}={column}+1 WHERE id=?", (row["id"],))
    released = False
    def release() -> None:
        nonlocal released
        if not row or released:
            return
        with _LOCK, ledger._conn() as conn:
            if released:
                return
            conn.execute(f"UPDATE output_groups SET {column}=MAX(0,{column}-1) WHERE id=?", (row["id"],))
            released = True
    return release


def acquire_reader(*, batch_id: str, item_index: int):
    return _acquire_lease(batch_id=batch_id, item_index=item_index, column="reader_leases")


def acquire_transfer(*, batch_id: str, item_index: int):
    return _acquire_lease(batch_id=batch_id, item_index=item_index, column="transfer_leases")


def removed_model_pass() -> None:
    """The one library read this timer may make, and only when it has something to do.

    `model_removal.removal_happened()` answers from memory plus a small marker file in the Hub's own
    data folder; it never reads the library. So an idle Mac reads nothing at all here, and a Mac
    that *has* removed something finishes its 30-day handoff - including one that restarted.
    """
    from .backend import model_removal
    if model_removal.removal_happened():
        model_removal.handoff()


def start_background(machine_provider, settings_provider, after_sweep=None,
                     removed_models=None) -> None:
    """Run the shared sweep inside the Hub process; no second service is created.

    `after_sweep(first)` is the disk governor's turn, once per pass (`first` is True for the start-up
    pass, which runs immediately): it reads free space and reclaims, so a Mac that is filling is
    handled on this timer and not only when a job is submitted.

    `removed_models()` returns the folders removed from the model library (`model_removal.listing`),
    and the same pass hands the ones older than `REMOVED_MODEL_DAYS` to **macOS Trash** through this
    module's own `_macos_trash` - one deleter, one place to empty, no second service (owner decision
    2026-10-28). A pass that fails leaves them where they are and tries again next time."""
    global _STARTED
    with _START_LOCK:
        if _STARTED:
            return
        _STARTED = True
    def loop() -> None:
        first = True
        while True:
            settings = settings_provider()["effective"]
            for machine in machine_provider():
                try:
                    enforce(machine=machine, max_bytes=settings["max_bytes"],
                            retention_days=settings["retention_days"],
                            enabled=settings["cleanup_enabled"])
                except Exception:
                    pass
            # The same timer hands Trash Folder items to macOS Trash (owner request 2026-10-15):
            # those older than `trash_handoff_days`, and the oldest until the unified Trash Folder is
            # back under `trash_handoff_gb` (owner rule 2026-10-19). No second service, and a failure
            # leaves the item in the Trash Folder for the next pass.
            days = settings.get("trash_handoff_days")
            gb = settings.get("trash_handoff_gb")
            cap = gb * 1_000_000_000 if isinstance(gb, int) and not isinstance(gb, bool) and gb > 0 else None
            if cap is not None or (isinstance(days, int) and not isinstance(days, bool) and days > 0):
                try:
                    trash_handoff(days, cap)
                except Exception:
                    log.exception("automatic Trash Folder handoff failed (continuing)")
            # Removed models reach macOS Trash on the same timer (owner decision 2026-10-28): the
            # library's `_trash` is where a removal puts a package, and this is the one thing that
            # moves it from there. **Only when a removal actually happened** (this process, or a
            # marker left by one): the model library is on a removable volume, and reading it on an
            # idle Mac is the protected access that froze the Hub in 0.6.206-0.6.209.
            if removed_models is not None:
                try:
                    removed_model_pass()
                except Exception:
                    log.exception("removed-model handoff failed (continuing)")
            if after_sweep is not None:
                try:
                    after_sweep(first)
                except Exception:
                    log.exception("disk governor pass failed (continuing)")
            first = False
            time.sleep(settings["cleanup_interval_seconds"])
    threading.Thread(target=loop, name="hub-output-cleanup", daemon=True).start()
