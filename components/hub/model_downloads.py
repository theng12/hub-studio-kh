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
        run.update(state="done" if result == 0 else "error", exit_code=result,
                   finished_at=time.time())
        if result == 0 and (used := _source_used(run_id)):
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


def start(*, package_id: str, manifest: dict, catalog: dict, library: Path,
          sources: dict | None, peer: str | None = None) -> dict:
    """Start (or rejoin) the download. ``peer`` is another Mac's LAN share URL
    (`model_packages.peer_base`), tried before the internet file by file."""
    from components import model_packages
    with _lock:
        value = reconcile()
        for existing in value["runs"].values():
            if existing.get("package_id") == package_id and existing.get("state") in {"running", "recovery_pending"}:
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
        output = root / "output.log"
        stream = output.open("w", encoding="utf-8")
        output.chmod(0o600)
        try:
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", "-c", _PARENT_WATCH, str(os.getpid()), *child_command],
                stdout=stream, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        finally:
            stream.close()
        run = {"id": run_id, "package_id": package_id, "state": "running",
               "started_at": time.time(), "pid": process.pid,
               "total_bytes": total_bytes, "staging": staging,
               "source": "lan" if peer else "internet"}
        value["runs"][run_id] = run
        _processes[run_id] = process
        _write(value)
        return _public(run)


def status(run_id: str) -> dict | None:
    with _lock:
        value = reconcile()
        return _public(value["runs"][run_id]) if run_id in value["runs"] else None


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
        if run is None or run.get("state") != "running" or process is None:
            return _public(run) if run else None
        exit_code = _stop_owned(run_id, process)
        run.update(state="cancelled", exit_code=exit_code, finished_at=time.time())
        _write(value)
        return _public(run)


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
