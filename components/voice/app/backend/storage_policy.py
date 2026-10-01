"""Automatic retention and hard-cap cleanup for generated audio backups.

Only completed audio files in the external ``output`` directory are eligible. Shared voice
masters, cloning references, uploads, model caches, credentials, and active
jobs live elsewhere or are explicitly protected.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path

from fastapi import HTTPException

from . import paths


SETTINGS_FILE = paths.data_path("storage_policy.json")
POLICY_VERSION = 3
# The output policy of every store on a Mac: 3 days, 20 GB or 8% of the disk if that is smaller
# (the old 30 days / 80 GB was a third of a 256 GB node). `LEGACY_DEFAULTS` are what version 2 wrote.
DEFAULTS = {"enabled": True, "retention_days": 3, "max_gb": 20.0}
LEGACY_DEFAULTS = {"retention_days": 30, "max_gb": 80.0}
DEFAULT_DISK_FRACTION = 0.08


def default_max_gb() -> float:
    try:
        total = shutil.disk_usage(SETTINGS_FILE.parent if SETTINGS_FILE.parent.exists() else Path.home()).total
    except OSError:
        return DEFAULTS["max_gb"]
    return max(1.0, min(DEFAULTS["max_gb"], round(total * DEFAULT_DISK_FRACTION / 1024 ** 3, 1)))
OUTPUT_SUFFIXES = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}
_LOCK = threading.RLock()
_START_LOCK = threading.Lock()
_STARTED = False


def _write(value: dict) -> None:
    payload = {**value, "policy_version": POLICY_VERSION}
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    partial = SETTINGS_FILE.with_suffix(".json.tmp")
    partial.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, SETTINGS_FILE)


def _read() -> dict:
    try:
        value = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        value = {}
    saved = value if isinstance(value, dict) else {}
    try:
        policy_version = int(saved.get("policy_version", 1))
    except (TypeError, ValueError):
        policy_version = 1
    # A file still holding the old defaults was never chosen: it reads as the new ones.
    migrate = (policy_version < POLICY_VERSION and bool(saved)
               and saved.get("retention_days", 30) == LEGACY_DEFAULTS["retention_days"]
               and saved.get("max_gb", 80.0) == LEGACY_DEFAULTS["max_gb"])
    if migrate:
        saved = {"enabled": saved.get("enabled", True)}
    out = {**DEFAULTS, "max_gb": default_max_gb(), **saved}
    if not isinstance(out["enabled"], bool):
        out["enabled"] = True
    try:
        out["retention_days"] = int(out["retention_days"])
        out["max_gb"] = float(out["max_gb"])
    except (TypeError, ValueError):
        out.update(
            retention_days=DEFAULTS["retention_days"],
            max_gb=default_max_gb(),
        )
    if not 1 <= out["retention_days"] <= 3650:
        out["retention_days"] = DEFAULTS["retention_days"]
    if not 1 <= out["max_gb"] <= 1000:
        out["max_gb"] = default_max_gb()
    if migrate:
        _write({key: out[key] for key in DEFAULTS})
    return {key: out[key] for key in DEFAULTS}


def save(enabled: object, retention_days: object, max_gb: object) -> dict:
    if not isinstance(enabled, bool):
        raise HTTPException(400, "enabled must be true or false")
    try:
        days = int(retention_days)
        maximum = float(max_gb)
    except (TypeError, ValueError):
        raise HTTPException(400, "retention_days and max_gb must be numbers")
    if not 1 <= days <= 3650:
        raise HTTPException(400, "retention_days must be between 1 and 3650")
    if not 1 <= maximum <= 1000:
        raise HTTPException(400, "max_gb must be between 1 and 1000")
    value = {"enabled": enabled, "retention_days": days, "max_gb": maximum}
    _write(value)
    return value


def _snapshot(output_dir: Path) -> dict:
    rows = []
    if output_dir.exists():
        for path in output_dir.iterdir():
            try:
                if (path.name.startswith(".") or path.is_symlink()
                        or not path.is_file()
                        or path.suffix.lower() not in OUTPUT_SUFFIXES):
                    continue
                stat = path.stat()
                rows.append((path, stat.st_size, stat.st_mtime))
            except OSError:
                continue
    rows.sort(key=lambda row: row[2])
    return {
        "used_bytes": sum(row[1] for row in rows),
        "count": len(rows),
        "oldest_at": rows[0][2] if rows else None,
        "newest_at": rows[-1][2] if rows else None,
        "rows": rows,
    }


def status(manager, output_dir: Path) -> dict:
    policy = _read()
    snap = _snapshot(output_dir)
    maximum = round(policy["max_gb"] * 1024 ** 3)
    return {
        **policy,
        **{key: value for key, value in snap.items() if key != "rows"},
        "max_bytes": maximum,
        "over_limit": policy["enabled"] and snap["used_bytes"] > maximum,
        "scope": "generated voice outputs only",
    }


def _remove(manager, path: Path) -> int:
    job = manager.get(path.stem)
    if job is not None and str(getattr(job, "state", "")) not in {
        "done", "error", "cancelled",
    }:
        return 0
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    if job is not None:
        if not manager.delete_job(path.stem):
            return 0
    else:
        try:
            path.unlink()
        except OSError:
            return 0
    return size


def enforce(manager, output_dir: Path, target_bytes: int | None = None) -> dict:
    """Apply age expiry, then delete oldest completed outputs to fit the cap."""
    with _LOCK:
        policy = _read()
        before = _snapshot(output_dir)
        result = {
            "enabled": policy["enabled"],
            "retention_days": policy["retention_days"],
            "max_gb": policy["max_gb"],
            "used_before_bytes": before["used_bytes"],
            "used_bytes": before["used_bytes"],
            "deleted": 0,
            "freed_bytes": 0,
        }
        if not policy["enabled"] and target_bytes is None:
            return result
        maximum = (max(0, int(target_bytes)) if target_bytes is not None
                   else round(policy["max_gb"] * 1024 ** 3))
        cutoff = time.time() - policy["retention_days"] * 86400
        for path, _size, modified in list(before["rows"]):
            if modified >= cutoff:
                continue
            freed = _remove(manager, path)
            if freed:
                result["deleted"] += 1
                result["freed_bytes"] += freed
        snap = _snapshot(output_dir)
        for path, _size, _modified in list(snap["rows"]):
            if snap["used_bytes"] <= maximum:
                break
            freed = _remove(manager, path)
            if freed:
                result["deleted"] += 1
                result["freed_bytes"] += freed
                snap["used_bytes"] = max(0, snap["used_bytes"] - freed)
        final = _snapshot(output_dir)
        result.update(
            used_bytes=final["used_bytes"], count=final["count"],
            max_bytes=maximum, over_limit=final["used_bytes"] > maximum,
        )
        return result


def start_background(manager, output_dir: Path) -> None:
    """Enforce independently of the UI after a safe startup delay, then hourly."""
    if os.environ.get("STUDIOHUB_OUTPUT_POLICY_OWNER") == "hub":
        return
    global _STARTED
    with _START_LOCK:
        if _STARTED:
            return
        _STARTED = True

    def loop() -> None:
        time.sleep(60)
        while True:
            try:
                enforce(manager, output_dir)
            except Exception as exc:
                print(f"[storage] automatic cleanup failed: {exc}", flush=True)
            time.sleep(3600)

    threading.Thread(target=loop, name="output-storage-cleanup", daemon=True).start()
