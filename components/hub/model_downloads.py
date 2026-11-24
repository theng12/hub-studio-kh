"""Durable, cancellable wrapper around the audited model-packages CLI."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

from .standalone_config import _atomic_write, data_dir


class InsufficientDiskSpace(Exception):
    """The volume cannot hold this download and still leave the Mac its working room."""


_processes: dict[str, subprocess.Popen] = {}
_lock = threading.RLock()
_PARENT_WATCH = (
    "import os,subprocess,sys,time\n"
    "parent=int(sys.argv[1]); child=subprocess.Popen(sys.argv[2:])\n"
    "while child.poll() is None:\n"
    " if os.getppid()!=parent:\n"
    "  child.terminate()\n"
    "  try: child.wait(timeout=5)\n"
    "  except subprocess.TimeoutExpired: child.kill(); child.wait()\n"
    "  sys.exit(128)\n"
    " time.sleep(.2)\n"
    "sys.exit(child.returncode)\n"
)


def _path() -> Path:
    return data_dir() / "model_downloads.json"


def _read() -> dict:
    try:
        value = json.loads(_path().read_text())
        return value if isinstance(value, dict) and isinstance(value.get("runs", {}), dict) else {"runs": {}}
    except (OSError, ValueError):
        return {"runs": {}}


def _write(value: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, value)


def _dir_bytes(path: str) -> int:
    """Bytes on disk under `path`, `.part` files included: what a resumable download has so far."""
    total = 0
    for item in Path(path).rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            pass
    return total


def _error_tail(run_id: str, limit: int = 300) -> str | None:
    """The end of the downloader's own output: the reason a run failed, in its words."""
    try:
        text = (data_dir() / "model-download-runs" / run_id / "output.log").read_text(errors="replace")
    except OSError:
        return None
    return text.strip()[-limit:] or None


def _public(run: dict) -> dict:
    """The run as the API publishes it: progress while it is active, the failure text once it
    has failed. The downloader prints nothing while it works, so progress is measured from the
    staging folder it writes into rather than read from its log."""
    out = {key: value for key, value in run.items() if key != "staging"}
    if run.get("staging") and run.get("state") in {"running", "recovery_pending"}:
        out["bytes_done"] = _dir_bytes(run["staging"])
    if run.get("state") in {"error", "interrupted"}:
        out["error_tail"] = _error_tail(run["id"])
    if run.get("source") == "lan" and (fell := _lan_fallback(run["id"])):
        out.setdefault("source_used", fell[0])   # a finished run keeps what its closing JSON said
        out["lan_error"] = fell[1]
    return out


_LAN_FAILED = re.compile(r"^LAN copy failed, using the internet \((mixed|internet)\): (.*)$", re.M)


def _lan_fallback(run_id: str) -> tuple[str, str] | None:
    """`(source_used, reason)` once a LAN copy has dropped its peer for the internet: the downloader
    prints that line the moment it happens, so a running copy already shows it."""
    try:
        text = (data_dir() / "model-download-runs" / run_id / "output.log").read_text(errors="replace")
    except OSError:
        return None
    match = _LAN_FAILED.search(text)
    return (match[1], match[2]) if match else None


def _source_used(run_id: str) -> str | None:
    """`lan`, `internet` or `mixed`: where a finished run's bytes came from, as the downloader's
    closing JSON says. None when it fetched nothing (already installed) or said nothing readable."""
    try:
        text = (data_dir() / "model-download-runs" / run_id / "output.log").read_text(errors="replace")
        return json.loads(text[text.find("\n{") + 1:]).get("source_used")   # after any LAN-fallback line
    except (OSError, ValueError, AttributeError):
        return None


def _mark_exited(value: dict, run_id: str, process: subprocess.Popen) -> bool:
    run = value["runs"].get(run_id)
    result = process.poll()
    changed = False
    if run is not None and run.get("state") == "running" and result is not None:
        tail = _error_tail(run_id) or ""
        if result != 0 and "Another model import is active" in tail \
                and int(run.get("waited") or 0) < 3:
            # The library lock is a reason to wait, never a failure: requeue behind whatever holds
            # it rather than writing that sentence into a row the person sees (owner 2026-11-24).
            # Keep its original queued_at: it was first in line and does not lose its place.
            run.update(state="queued", waited=int(run.get("waited") or 0) + 1)
            run.pop("pid", None)
            changed = True
            _processes.pop(run_id, None)
            return changed
        run.update(state="done" if result == 0 else "error", exit_code=result,
                   finished_at=time.time())
        if result == 0 and not str(run.get("source") or "").startswith("ssd:") \
                and (used := _source_used(run_id)):
            run["source_used"] = used
        changed = True
    if result is not None:
        _processes.pop(run_id, None)
    return changed


def reconcile() -> dict:
    """Reap children owned by this Hub; never guess about a persisted PID."""
    with _lock:
        value = _read()
        changed = False
        for run_id, process in list(_processes.items()):
            changed |= _mark_exited(value, run_id, process)
        # A prior process has a parent-watch wrapper that exits its downloader.
        # Leave it a bounded grace period, then permit an explicit retry.
        for run in value["runs"].values():
            if run.get("state") == "running" and run.get("id") not in _processes:
                run["state"] = "recovery_pending"
                run["recovery_pending_at"] = time.time()
                run["recovery_note"] = "waiting for the prior Hub-owned downloader to exit"
                changed = True
            if (run.get("state") == "recovery_pending"
                    and time.time() - float(run.get("recovery_pending_at") or 0) >= 6):
                run.update(state="interrupted", finished_at=time.time())
                changed = True
        changed |= _pump(value)
        if changed:
            _write(value)
        return value


def running_staging() -> set[str]:
    """Staging folders a live download is writing into: the disk governor must leave them alone."""
    with _lock:
        return {run["staging"] for run in _read()["runs"].values()
                if run.get("staging") and run.get("state") in {"running", "recovery_pending"}}


def _free_space_check(package_id: str, needed: int, library: Path) -> None:
    """Refuse a download that would not fit. `needed` is what is still to fetch (what a resumable
    run already staged is not counted twice); the Mac keeps `STOP_BYTES` free afterwards, the point
    where it would stop accepting work."""
    from . import disk_governor
    try:
        free = disk_governor.disk_usage(library).free
    except OSError:
        return                                  # cannot tell: the downloader fails by itself if it must
    margin = disk_governor.STOP_BYTES
    if free < needed + margin:
        raise InsufficientDiskSpace(
            f"Not enough free disk space to download {package_id}: it needs about {needed / 1e9:.1f} GB "
            f"and this Mac keeps {margin / 1e9:.0f} GB free for working room, but only {free / 1e9:.1f} GB is free. "
            f"Free some space (or choose a model library on a larger disk) and try again. Nothing was downloaded.")


def _drive_name(drive: Path) -> str:
    """`ATLAS` from `<volume>/1 Install Apps/Hub Studio KH/model-library`, for the run's source."""
    for parent in Path(drive).parents:
        if parent.name.startswith("KH-STUDIO-"):
            return parent.name[len("KH-STUDIO-"):]
    return Path(drive).parent.name


ACTIVE_STATES = {"running", "recovery_pending"}


def _library_of(run: dict) -> str:
    return str((run.get("request") or {}).get("library") or "")


def _active_run(value: dict, library: str | None = None) -> dict | None:
    return next((run for run in value["runs"].values()
                 if run.get("state") in ACTIVE_STATES
                 and (library is None or _library_of(run) == library)), None)


def _queued(value: dict) -> list[dict]:
    return sorted((run for run in value["runs"].values() if run.get("state") == "queued"),
                  key=lambda run: run.get("queued_at") or run.get("started_at") or 0)


def _launch(run: dict) -> None:
    """Start one queued run. The caller holds the lock and writes the store."""
    request = run.get("request") or {}
    if request.get("kind") == "import":
        run.update(state="running", started_at=time.time(), pid=None)
        threading.Thread(target=_run_import, args=(run["id"],), daemon=True,
                         name=f"model-import-{run['id'][:8]}").start()
        return
    output = Path(request["root"]) / "output.log"
    stream = output.open("a", encoding="utf-8")
    output.chmod(0o600)
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", _PARENT_WATCH, str(os.getpid()),
             *request["child_command"]],
            stdout=stream, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True)
    finally:
        stream.close()
    run.update(state="running", started_at=time.time(), pid=process.pid)
    _processes[run["id"]] = process


def _run_import(run_id: str) -> None:
    """Import one folder in the queue's thread, then settle its row like any other run.

    A folder import is the other door the owner could hit ("copying or installing models"): it waits
    behind a download in the same library instead of taking the library lock's refusal.
    """
    from components import model_packages
    with _lock:
        value = _read()
        run = value["runs"].get(run_id)
        if run is None:
            return
        request = run.get("request") or {}
        manifest, source = request.get("manifest"), Path(request.get("source") or "")
        library = Path(_library_of(run))
    state, reason = "done", None
    try:
        model_packages.import_folder(manifest, source, library,
                                     catalog=request.get("catalog"))
    except model_packages.PackageError as exc:
        text = str(exc)
        if "Another model import is active" in text and int(run.get("waited") or 0) < 3:
            with _lock:
                store = _read()
                row = store["runs"].get(run_id)
                if row is not None:
                    row.update(state="queued", waited=int(row.get("waited") or 0) + 1)
                    _pump(store)
                    _write(store)
            return
        state, reason = "error", text
    except (OSError, ValueError) as exc:
        state, reason = "error", str(exc)
    with _lock:
        store = _read()
        row = store["runs"].get(run_id)
        if row is not None and row.get("state") not in {"cancelled"}:
            row.update(state=state, finished_at=time.time())
            if reason:
                row["reason"] = reason
        _pump(store)
        _write(store)


def enqueue_import(*, package_id: str, manifest: dict, library: Path, source_folder: Path,
                   catalog: dict | None = None) -> dict:
    """Queue a folder import. Returns the run, running now or waiting with its position."""
    with _lock:
        value = reconcile()
        for existing in value["runs"].values():
            if (existing.get("state") in {"queued", "running"}
                    and existing.get("kind") == "import"
                    and existing.get("package_id") == package_id
                    and (existing.get("request") or {}).get("source") == str(source_folder)):
                return _with_position(existing, value)
        run_id = uuid.uuid4().hex
        root = data_dir() / "model-download-runs" / run_id
        root.mkdir(parents=True, mode=0o700)
        run = {"id": run_id, "package_id": package_id, "kind": "import", "state": "queued",
               "queued_at": time.time(), "total_bytes": 0, "source": "folder",
               "request": {"kind": "import", "root": str(root), "library": str(library),
                           "source": str(source_folder), "manifest": manifest,
                           "catalog": catalog}}
        value["runs"][run_id] = run
        _pump(value)
        _write(value)
        return _with_position(value["runs"][run_id], value)


def _pump(value: dict) -> bool:
    """One import at a time: start the oldest queued run when nothing is active. A run whose drive
    is gone pauses and says so instead of failing — the queue waits for a Resume (owner 2026-11-24,
    "it's not queuing properly")."""
    # Per library, because the lock this queues behind is per library: one import at a time in each
    # library, and two libraries never wait for each other.
    changed = False
    for library in {_library_of(run) for run in _queued(value)}:
        if _active_run(value, library) is not None:
            continue
        for run in _queued(value):
            if _library_of(run) != library:
                continue
            drive = (run.get("request") or {}).get("drive")
            if drive and not Path(drive).exists():
                run.update(state="paused", reason=f"the drive at {drive} is not mounted",
                           paused_at=time.time())
                changed = True
                continue
            _launch(run)
            changed = True
            break
    return changed


def _with_position(run: dict, value: dict) -> dict:
    public = _public(run)
    if run.get("state") == "queued":
        public["position"] = _queued(value).index(run) + 1
    return public


def start(*, package_id: str, manifest: dict, catalog: dict, library: Path,
          sources: dict | None, peer: str | None = None, peer_only: bool = False,
          drive: Path | None = None) -> dict:
    """Start (or rejoin) the download. ``peer`` is another Mac's LAN share URL
    (`model_packages.peer_base`), tried before the internet file by file. ``peer_only`` makes
    failure of that share terminal without changing an already accepted non-strict run.
    ``drive`` is a mounted kit drive's model library: the run copies from it and never uses the
    internet."""
    from components import model_packages
    if drive is not None:
        # A drive run is one source and no fallthrough: the SSD either has the files or the run
        # fails with the file named.
        sources, peer, peer_only = None, None, False
    if not isinstance(peer_only, bool):
        raise ValueError("peer_only must be true or false")
    if peer_only and not peer:
        raise ValueError("LAN-only copy requires a peer source")
    with _lock:
        value = reconcile()
        for existing in value["runs"].values():
            if existing.get("package_id") == package_id and existing.get("state") in {"running", "recovery_pending"}:
                if peer_only and existing.get("peer_only") is not True:
                    raise ValueError(
                        f"{package_id} already has an accepted download without a LAN-only policy; "
                        "wait for it to finish before requesting a LAN-only copy")
                return {**_public(existing), "reused": True}
        # Worked out before the child starts: a manifest that cannot be digested must fail here, not
        # after a downloader is already running with no record of it.
        total_bytes = sum(int(f.get("size") or 0) for f in manifest.get("files", []))
        staging = str(Path(library).expanduser().resolve() / ".studiohub" / "staging"
                      / f"{package_id}-{model_packages.manifest_digest(manifest)}")
        _free_space_check(package_id, max(0, total_bytes - _dir_bytes(staging)), Path(library))
        run_id = uuid.uuid4().hex
        root = data_dir() / "model-download-runs" / run_id
        root.mkdir(parents=True, mode=0o700)
        # The CLI discovers trusted companion manifests by globbing sibling
        # *.json.  Keep the arbitrary operator source map outside that set.
        manifest_path, sources_path = root / "manifest.json", root / "sources.map"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        manifest_path.chmod(0o600)
        # model_packages discovers trusted companions from sibling JSON files.
        for dependency in catalog.values():
            path = root / f"catalog-{dependency['package_id']}.json"
            path.write_text(json.dumps(dependency), encoding="utf-8")
            path.chmod(0o600)
        if sources is not None:
            sources_path.write_text(json.dumps(sources), encoding="utf-8")
            sources_path.chmod(0o600)
        # Not *.json either (same companion glob), and owner-only: the URL carries the share token.
        peer_path = root / "peer.map"
        if peer:
            peer_path.write_text(json.dumps(peer), encoding="utf-8")
            peer_path.chmod(0o600)
        tool = Path(__file__).resolve().parents[1] / "model_packages.py"
        child_command = [sys.executable, "-I", "-B", str(tool), "download", "--manifest",
                         str(manifest_path), "--library", str(library)]
        if sources is not None:
            child_command.extend(("--sources", str(sources_path)))
        if peer:
            child_command.extend(("--peer", str(peer_path)))
        if peer_only:
            child_command.append("--peer-only")
        if drive is not None:
            child_command.extend(("--drive", str(drive)))
        # Every request becomes a run; `_pump` starts it or leaves it queued behind the one in
        # flight. A second request never fails because another is running (owner 2026-11-24).
        run = {"id": run_id, "package_id": package_id, "state": "queued",
               "queued_at": time.time(), "total_bytes": total_bytes, "staging": staging,
               "source": (f"ssd:{_drive_name(drive)}" if drive is not None
                          else "lan" if peer else "internet"), "peer_only": peer_only,
               "request": {"root": str(root), "child_command": child_command,
                           "library": str(library),
                           "drive": str(drive) if drive is not None else None}}
        value["runs"][run_id] = run
        _pump(value)
        _write(value)
        return _with_position(value["runs"][run_id], value)


def status(run_id: str) -> dict | None:
    with _lock:
        value = reconcile()
        return _with_position(value["runs"][run_id], value) if run_id in value["runs"] else None


def list_runs() -> list[dict]:
    """Every run this Hub knows, newest first, whoever started it: the app, the Gateway's fleet
    install, or a LAN copy. The app lists only the ones it started itself without this."""
    # ponytail: the whole history every read; trim old finished runs if this file ever grows large.
    with _lock:
        value = reconcile()
        runs = value["runs"].values()
        return [_with_position(run, value)
                for run in sorted(runs, key=lambda r: r.get("started_at") or 0, reverse=True)]


def _stop_owned(run_id: str, process: subprocess.Popen) -> int | None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        return process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return process.wait(timeout=5)
    finally:
        # The wrapper can exit on TERM while its downloader ignores it. The
        # child still shares this owner-created session, so reap the group. macOS answers EPERM when
        # the group holds only a zombie (the downloader died with the wrapper and awaits its reaper).
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        _processes.pop(run_id, None)


def cancel(run_id: str) -> dict | None:
    with _lock:
        value = reconcile()
        run = value["runs"].get(run_id)
        process = _processes.get(run_id)
        if run is None:
            return None
        if run.get("state") in {"queued", "paused"}:
            # A waiting run is cancelled where it waits; the next one starts at once.
            run.update(state="cancelled", finished_at=time.time())
            _pump(value)
            _write(value)
            return _public(run)
        if run.get("state") != "running" or process is None:
            return _public(run)
        exit_code = _stop_owned(run_id, process)
        run.update(state="cancelled", exit_code=exit_code, finished_at=time.time())
        _write(value)
        return _public(run)


def resume(run_id: str) -> dict | None:
    """Put a paused run back in the queue. The next read (or this call) starts it when the queue is
    free; the drive is checked again at that moment, so Resume after remounting the drive works."""
    with _lock:
        value = reconcile()
        run = value["runs"].get(run_id)
        if run is None:
            return None
        if run.get("state") == "paused":
            run.update(state="queued", queued_at=time.time())
            run.pop("reason", None)
            _pump(value)
            _write(value)
        return _with_position(run, value)


def shutdown() -> None:
    """Terminate and reap only children created by this Hub process."""
    with _lock:
        value = _read()
        changed = False
        for run_id, process in list(_processes.items()):
            if process.poll() is not None:   # it finished before the stop: keep what it did (done/error)
                changed |= _mark_exited(value, run_id, process)
                continue
            exit_code = _stop_owned(run_id, process)
            run = value["runs"].get(run_id)
            if run and run.get("state") == "running":
                run.update(state="interrupted", exit_code=exit_code, finished_at=time.time())
                changed = True
        if changed:
            _write(value)
