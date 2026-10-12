"""Keep qualification probes away from a real model library.

A Hub started with a throwaway data directory over a REAL library (``~/StudioHubModels``, a kit's
``model-library``) used to shelve and retire the real weights into the throwaway directory's Trash
Folder; deleting that directory destroyed them (2026-10-01/02). An app older than
``components/library_ownership.py`` still does. So a script that starts a Hub with a temporary data
directory gives it a library under a temp folder: an empty one, or a copy-on-write clone of just the
packages it needs (``clone``; APFS clones share the bytes, so it costs no space and no time).

    python3 qualification/library_guard.py clone ~/StudioHubModels /tmp/probe-library whisper-turbo
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_TEMP = ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders", "/var/tmp", "/private/var/tmp")


def _under_temp(path: Path) -> bool:
    roots = [Path(p).resolve() for p in (*_TEMP, tempfile.gettempdir(), os.environ.get("TMPDIR") or "/tmp")]
    path = path.expanduser().resolve()
    return any(path == root or root in path.parents for root in roots)


def require_disposable(models: Path) -> Path:
    """Return `models` if it is under a temp folder; otherwise stop with how to make a safe one."""
    models = Path(models).expanduser().resolve()
    if not _under_temp(models):
        raise SystemExit(
            f"refusing to start a Hub on the real model library {models}: an app that predates the "
            f"library-ownership check shelves and retires its models into the throwaway data directory.\n"
            f"Make a disposable copy-on-write library of just what you need, and pass that:\n"
            f"  python3 {Path(__file__).resolve()} clone {models} <temp-dir>/library <package-id>...")
    return models


def clone(source: Path, destination: Path, packages: list[str]) -> Path:
    """Copy-on-write clone of `packages` from `source` into a new library at `destination` (in temp)."""
    source, destination = Path(source).expanduser().resolve(), Path(destination).expanduser().resolve()
    require_disposable(destination)
    destination.mkdir(parents=True, exist_ok=True)
    state = destination / ".studiohub"
    state.mkdir(mode=0o700, exist_ok=True)
    index = {}
    try:
        index = json.loads((source / ".studiohub" / "installed.json").read_text())
    except (OSError, ValueError):
        pass
    kept = {}
    for package in packages:
        if Path(package).name != package or not (source / package).is_dir():
            raise SystemExit(f"{package} is not a package folder in {source}")
        subprocess.run(["/bin/cp", "-Rcp", str(source / package), str(destination / package)], check=True)
        if package in index:
            kept[package] = index[package]
    (state / "installed.json").write_text(json.dumps(kept, sort_keys=True))
    os.chmod(state / "installed.json", 0o600)
    return destination


if __name__ == "__main__":
    if len(sys.argv) < 4 or sys.argv[1] != "clone":
        raise SystemExit(__doc__)
    print(clone(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4:]))


def foreign_model_processes(listing: str | None = None,
                            *, current_pid: int | None = None) -> list[str]:
    rows = []
    if listing is None:
        listing = subprocess.run(["/bin/ps", "-axo", "pid=,command="],
                                 capture_output=True, text=True, timeout=15, check=True).stdout
    current_pid = os.getpid() if current_pid is None else current_pid
    for line in listing.splitlines():
        match = re.match(r"\s*(\d+)\s+(.*)", line)
        if not match or int(match[1]) == current_pid:
            continue
        command = match[2]
        if (any(worker in command for worker in
                ("hub_worker.py", "image_worker.py", "voice_worker.py", "chat_worker.py"))
                or "mlx_lm.generate" in command
                or re.search(
                    r"(?:^|/)(?:mlx-serve|mflux-generate(?:-[A-Za-z0-9][A-Za-z0-9._-]*)?)(?:\s|$)",
                    command,
                )):
            rows.append(f"pid {match[1]}: {command[:180]}")
    return rows
