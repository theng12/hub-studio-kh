"""Durable, cancellable wrapper around the audited model-packages CLI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

from .standalone_config import _atomic_write, data_dir


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


def _mark_exited(value: dict, run_id: str, process: subprocess.Popen) -> bool:
    run = value["runs"].get(run_id)
    result = process.poll()
    changed = False
    if run is not None and run.get("state") == "running" and result is not None:
        run.update(state="done" if result == 0 else "error", exit_code=result,
                   finished_at=time.time())
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


def start(*, package_id: str, manifest: dict, catalog: dict, library: Path,
          sources: dict | None) -> dict:
    with _lock:
        value = reconcile()
        for existing in value["runs"].values():
            if existing.get("package_id") == package_id and existing.get("state") in {"running", "recovery_pending"}:
                return {**existing, "reused": True}
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
        tool = Path(__file__).resolve().parents[1] / "model_packages.py"
        child_command = [sys.executable, "-I", "-B", str(tool), "download", "--manifest",
                         str(manifest_path), "--library", str(library)]
        if sources is not None:
            child_command.extend(("--sources", str(sources_path)))
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
               "started_at": time.time(), "pid": process.pid}
        value["runs"][run_id] = run
        _processes[run_id] = process
        _write(value)
        return dict(run)


def status(run_id: str) -> dict | None:
    with _lock:
        value = reconcile()
        return dict(value["runs"][run_id]) if run_id in value["runs"] else None


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
        # child still shares this owner-created session, so reap the group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _processes.pop(run_id, None)


def cancel(run_id: str) -> dict | None:
    with _lock:
        value = reconcile()
        run = value["runs"].get(run_id)
        process = _processes.get(run_id)
        if run is None or run.get("state") != "running" or process is None:
            return dict(run) if run else None
        exit_code = _stop_owned(run_id, process)
        run.update(state="cancelled", exit_code=exit_code, finished_at=time.time())
        _write(value)
        return dict(run)


def shutdown() -> None:
    """Terminate and reap only children created by this Hub process."""
    with _lock:
        value = _read()
        changed = False
        for run_id, process in list(_processes.items()):
            exit_code = _stop_owned(run_id, process)
            run = value["runs"].get(run_id)
            if run and run.get("state") == "running":
                run.update(state="interrupted", exit_code=exit_code, finished_at=time.time())
                changed = True
        if changed:
            _write(value)
