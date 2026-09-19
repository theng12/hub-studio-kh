"""Hub-owned retention for generated worker output.

Workers retain their files.  This index only accepts paths under roots supplied
by the packaged owner descriptor, so a worker result can never turn an
arbitrary local pathname into a deletion candidate.
"""
from __future__ import annotations

import os
import copy
import hashlib
import stat
import threading
import time
import shutil
import sqlite3
from pathlib import Path

from .backend import ledger

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
    # Link-count changes update ctime on every remaining hard link.  mtime, size,
    # device/inode and the checksum recheck below still identify the bytes.
    return (row["device"], row["inode"], row["bytes"], row["modified_ns"]) == (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


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
        recovered = group is not None and group["state"] == "uncertain" and group["recoverable"]
        safe = True
        uncertain = False
        unavailable = False
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
                matches = _same_file(info, row) and _sha256(path) == row["sha256"]
            except OSError:
                matches = False
            if not matches:
                uncertain = True
                safe = False
                continue
            conn.execute("UPDATE output_files SET present=1 WHERE id=?", (row["id"],))
        if uncertain:
            conn.execute("UPDATE output_groups SET state='uncertain',recoverable=? WHERE id=?",
                         (1 if unavailable else 0, group_id))
        elif recovered and safe:
            # A temporarily missing/inaccessible root becomes eligible only when
            # every original path and checksum has returned unchanged.
            conn.execute("UPDATE output_groups SET state='finalized',recoverable=0 WHERE id=?", (group_id,))


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
    cutoff = now - retention_days * 86400
    for row in rows:
        key = (row["device"], row["inode"])
        physical[key] = max(physical.get(key, 0), row["bytes"])
        eligible = (enabled and row["state"] == "finalized" and row["receipt_acked_at"] is not None
                    and row["reader_leases"] == 0 and row["transfer_leases"] == 0)
        if not eligible:
            protected[key] = max(protected.get(key, 0), row["bytes"])
    used = sum(physical.values())
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
            "minimum_free_bytes": minimum_free_bytes,
            "over_limit": used > max_bytes, "free_bytes": min(free) if free else None,
            "expired_before": cutoff}


def enforce(*, machine: str, max_bytes: int, retention_days: int,
            enabled: bool = True, now: float | None = None,
            managed_output_roots: object = None, minimum_free_bytes: int = 0) -> dict:
    with _LOCK:
        _schema()
        with ledger._conn() as conn:
            _reconcile(conn, machine)
        result = preview(machine=machine, max_bytes=max_bytes, retention_days=retention_days,
                         enabled=enabled, now=now, managed_output_roots=managed_output_roots,
                         minimum_free_bytes=minimum_free_bytes)
        if not enabled:
            return result | {"deleted": 0, "freed_bytes": 0}
        now = time.time() if now is None else float(now)
        cutoff = now - retention_days * 86400
        with ledger._conn() as conn:
            rows = conn.execute("""SELECT * FROM output_groups WHERE machine=? AND state='finalized'
              AND receipt_acked_at IS NOT NULL AND reader_leases=0 AND transfer_leases=0
              ORDER BY generated_at ASC""", (machine,)).fetchall()
            used = result["used_bytes"]
            deleted = freed = 0
            for group in rows:
                current = conn.execute("SELECT * FROM output_groups WHERE id=?", (group["id"],)).fetchone()
                if (current is None or current["state"] != "finalized" or current["receipt_acked_at"] is None
                        or current["reader_leases"] or current["transfer_leases"]):
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
                    try:
                        Path(file["path"]).unlink()
                    except OSError:
                        continue
                    conn.execute("UPDATE output_files SET present=0 WHERE id=?", (file["id"],))
                    deleted += 1
                    if info.st_nlink == 1:
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


# How long an admission answer may be reused. The scheduler asks this question for every queued item
# on every pass - several times a second while work is waiting - and `enforce` walks and hashes the
# managed output roots to answer it, synchronously, on the event loop. The answer is therefore
# remembered for a few seconds per policy. Enforcement itself is unchanged: it still runs on the
# background timer (which is what deletes files) and on every operator action; this only stops the
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
    if minimum_free_bytes and has_managed_root and (result["free_bytes"] is None or result["free_bytes"] < minimum_free_bytes):
        return {"code": "OUTPUT_STORAGE_BLOCKED", "detail": "managed output storage has insufficient free space", "storage": result}
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


def start_background(machine_provider, settings_provider) -> None:
    """Run the shared sweep inside the Hub process; no second service is created."""
    global _STARTED
    with _START_LOCK:
        if _STARTED:
            return
        _STARTED = True
    def loop() -> None:
        while True:
            settings = settings_provider()["effective"]
            for machine in machine_provider():
                try:
                    enforce(machine=machine, max_bytes=settings["max_bytes"],
                            retention_days=settings["retention_days"],
                            enabled=settings["cleanup_enabled"])
                except Exception:
                    pass
            time.sleep(settings["cleanup_interval_seconds"])
    threading.Thread(target=loop, name="hub-output-cleanup", daemon=True).start()
