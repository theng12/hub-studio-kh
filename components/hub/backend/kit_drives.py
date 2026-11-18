"""The KH-STUDIO kit drives plugged into this Mac: which models each one really holds.

**Why this is a separate module with its own reading path.** Any stat *inside* a removable volume
can make macOS ask for permission; an unanswered prompt once held Hub routes until the Mac looked
frozen (commit 26d086a7). So the Hub may list the volume root for free, but a drive's contents are
read only here, only when a person asked for it, in a child process under a hard timeout, and
cached. Nothing was written to a volume: reads only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from ..standalone_config import _atomic_write, data_dir

PREFIX = "KH-STUDIO-"
#: A kit keeps its models here on the drive (`copy_models_for_this_mac.py` writes this layout).
KIT_RELATIVE = ("1 Install Apps", "Hub Studio KH")
LIBRARY_NAME = "model-library"
TOTAL_SECONDS = 60.0
PER_DRIVE_SECONDS = 10.0


class DriveRefused(Exception):
    """A drive could not be read; ``code`` and ``detail`` are the answer the route returns."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def volumes_root(override: Path | str | None = None) -> Path:
    """Where the mounted volumes are. Injectable so a test never lists the real volume root."""
    if override is not None:
        return Path(override)
    value = os.environ.get("STUDIOHUB_VOLUMES_DIR", "").strip()
    # Built from the separator so no module here carries a literal volume path.
    return Path(value) if value else Path(os.sep) / "Volumes"


def name_of(drive: Path | str) -> str:
    base = Path(drive).name
    return base[len(PREFIX):] if base.startswith(PREFIX) else base


def mounted(root: Path | str | None = None) -> list[Path]:
    """The KH-STUDIO volumes mounted right now: `listdir` of the root and nothing else."""
    base = volumes_root(root)
    try:
        names = os.listdir(base)
    except OSError:
        return []
    return sorted(base / name for name in names if name.startswith(PREFIX))


def drive_library(drive: Path) -> Path:
    """The kit's model library on one drive - the folder a drive copy reads from."""
    return Path(drive).joinpath(*KIT_RELATIVE, LIBRARY_NAME)


def package_folder(drive: Path, manifest: dict) -> Path:
    return drive_library(drive) / str(manifest.get("package_id")) / str(manifest.get("revision") or "")


def _cache_path() -> Path:
    return data_dir() / "hub" / "kit-drives.json"


def _cache() -> dict[str, dict]:
    try:
        value = json.loads(_cache_path().read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {key: row for key, row in value.items() if isinstance(row, dict)}


def _write_cache(value: dict[str, dict]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, value)


#: The bounded reader. It runs as a child so a permission prompt blocks nothing that can outlive
#: the timeout: the parent kills it and answers instead of freezing the Hub.
_READER = (
    "import json, os, stat, sys\n"
    "library, path = sys.argv[1], sys.argv[2]\n"
    "manifests = json.load(open(path))\n"
    "out = {'exists': True, 'kit_mtime': None, 'has': []}\n"
    "try:\n"
    "    out['kit_mtime'] = os.stat(library).st_mtime\n"
    "except FileNotFoundError:\n"
    "    out['exists'] = False\n"
    "except OSError as error:\n"
    "    sys.exit('cannot read %s: %s' % (library, error))\n"
    "if out['exists']:\n"
    "    for package_id, manifest in sorted(manifests.items()):\n"
    "        base = os.path.join(library, package_id, str(manifest.get('revision') or ''))\n"
    "        ok = bool(manifest.get('files'))\n"
    "        for entry in manifest.get('files') or []:\n"
    "            try:\n"
    "                info = os.stat(os.path.join(base, entry['path']))\n"
    "            except OSError:\n"
    "                ok = False\n"
    "                break\n"
    "            if not stat.S_ISREG(info.st_mode) or (entry.get('size') is not None\n"
    "                    and info.st_size != int(entry['size'])):\n"
    "                ok = False\n"
    "                break\n"
    "        if ok:\n"
    "            out['has'].append(package_id)\n"
    "print(json.dumps(out))\n"
)


def _read(library: Path, manifests: dict[str, dict], seconds: float) -> dict:
    """Read one drive's model library, or refuse. Never stat a volume outside this call."""
    slim = {pid: {"package_id": pid, "revision": m.get("revision"),
                  "files": [{"path": f.get("path"), "size": f.get("size")}
                            for f in m.get("files") or []]}
            for pid, m in manifests.items() if not m.get("_manifest_error")}
    root = data_dir() / "hub"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f".drive-{os.getpid()}-{time.monotonic_ns()}.json"
    path.write_text(json.dumps(slim), encoding="utf-8")
    path.chmod(0o600)
    try:
        run = subprocess.run([sys.executable, "-I", "-B", "-c", _READER, str(library), str(path)],
                             capture_output=True, text=True, timeout=max(0.1, seconds))
    except subprocess.TimeoutExpired:
        raise DriveRefused("NEEDS_PERMISSION", "the read did not finish") from None
    finally:
        path.unlink(missing_ok=True)
    if run.returncode != 0:
        reason = (run.stderr or "").strip().splitlines()[-1:] or ["unreadable"]
        raise DriveRefused("NEEDS_PERMISSION", reason[0])
    try:
        value = json.loads(run.stdout)
    except ValueError:
        raise DriveRefused("NEEDS_PERMISSION", "the read gave no answer") from None
    if not isinstance(value, dict) or not isinstance(value.get("has"), list):
        raise DriveRefused("NEEDS_PERMISSION", "the read gave no answer")
    return value


def report(root: Path | str | None = None) -> tuple[list[dict], float | None]:
    """The `drives` block, from `listdir` and the cache alone: no stat inside any volume.

    `checked: false` means the contents are unknown, never that the drive lacks a package.
    """
    base = volumes_root(root)
    cache = _cache()
    entries: list[dict] = []
    seen: set[str] = set()
    for drive in mounted(base):
        key = str(drive)
        seen.add(key)
        row = {"name": name_of(drive), "path": key, "mounted": True}
        cached = cache.get(key)
        if cached and isinstance(cached.get("checked_at"), (int, float)):
            row.update(checked=True, checked_at=cached["checked_at"], has=list(cached.get("has") or []),
                       state="ok")
        else:
            row.update(checked=False, state="unknown")
        entries.append(row)
    for key, cached in sorted(cache.items()):
        if key in seen or not isinstance(cached.get("checked_at"), (int, float)):
            continue
        entries.append({"name": name_of(key), "path": key, "mounted": False, "checked": True,
                        "checked_at": cached["checked_at"], "has": list(cached.get("has") or []),
                        "state": "ok"})
    checked_at = max((float(row["checked_at"]) for row in cache.values()
                      if isinstance(row.get("checked_at"), (int, float))), default=None)
    return entries, checked_at


def check(manifests: dict[str, dict], root: Path | str | None = None) -> dict:
    """Read every mounted kit drive once, under a total bound, and cache what it holds."""
    base = volumes_root(root)
    cache = _cache()
    deadline = time.monotonic() + TOTAL_SECONDS
    mounted_now = mounted(base)
    refused: dict[str, str] = {}
    read_any = False
    for drive in mounted_now:
        name = name_of(drive)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            refused[name] = "the check ran out of time"
            print(f"[hub] drive: {name} could not be read: the check ran out of time", flush=True)
            continue
        library = drive_library(drive)
        try:
            value = _read(library, manifests, min(PER_DRIVE_SECONDS, remaining))
        except DriveRefused as exc:
            refused[name] = exc.detail
            print(f"[hub] drive: {name} could not be read: {exc.detail}", flush=True)
            continue
        read_any = True
        key = str(drive)
        cached = cache.get(key) or {}
        fresh = (isinstance(cached.get("checked_at"), (int, float))
                 and value.get("kit_mtime") is not None
                 and float(cached["checked_at"]) >= float(value["kit_mtime"]))
        if not fresh:
            cache[key] = {"checked_at": time.time(), "has": list(value["has"])}
            _write_cache(cache)
    entries, checked_at = report(base)
    # One drive that cannot be read never hides another drive's answer: the screen says which
    # drive needs permission and carries on with the rest of the ladder.
    for entry in entries:
        if not entry.get("mounted"):
            continue
        if entry["name"] in refused:
            entry.update(state="refused", detail=refused[entry["name"]])
    if mounted_now and not read_any:
        name, detail = next(iter(refused.items()))
        raise DriveRefused("NEEDS_PERMISSION", f"macOS needs your permission to read {name} once.")
    return {"drives": entries, "drives_checked_at": checked_at}


def resolve(drive_name: str, root: Path | str | None = None) -> Path:
    """A mounted drive by its display name (`ATLAS`) or volume name (`KH-STUDIO-ATLAS`)."""
    wanted = drive_name.strip()
    for entry in report(root)[0]:
        if not entry.get("mounted"):
            continue
        if wanted in (entry.get("name"), Path(entry["path"]).name, entry.get("path")):
            return Path(entry["path"])
    raise DriveRefused("DRIVE_LACKS_PACKAGE", f"No drive named {wanted} is plugged in.")


def has_package(drive: Path, manifest: dict, *, seconds: float = PER_DRIVE_SECONDS) -> bool:
    """Whether this drive holds every file of one package, read now under the same bound."""
    package_id = str(manifest.get("package_id"))
    try:
        value = _read(drive_library(drive), {package_id: manifest}, seconds)
    except DriveRefused as exc:
        print(f"[hub] drive: {name_of(drive)} could not be read: {exc.detail}", flush=True)
        raise DriveRefused("NEEDS_PERMISSION",
                           f"macOS needs your permission to read {name_of(drive)} once.") from None
    return package_id in value["has"]


def ssd_sources(entries: list[dict]) -> dict[str, str]:
    """Package id -> drive name, from the mounted drives whose contents are known."""
    found: dict[str, str] = {}
    for entry in entries:
        if not (entry.get("mounted") and entry.get("checked")):
            continue
        for package_id in entry.get("has") or []:
            found.setdefault(str(package_id), str(entry["name"]))
    return found
