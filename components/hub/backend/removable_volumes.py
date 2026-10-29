"""The removable-volume check, done **once per process** and never inside a request.

**The freeze this module exists for (0.6.206 to 0.6.209).** 0.6.206 added the first walk of
``/Volumes`` so the install screen could say which SSD kit is mounted. It ran inside
``GET /api/hub/model-presets``, and the app read that route from ``refresh()`` on every connect
and every poll. macOS asks for permission to read removable volumes, and an **ad-hoc signed**
build loses that permission on every update - so ``open()`` sat waiting on an unanswered prompt,
holding a threadpool slot. Enough polls filled the pool, and ``/api/health`` and
``/api/hub/resources`` stopped answering while the Mac looked frozen. Clicking **Allow** on the
prompt revived it, which is how the owner found the cause at the Macs.

**The rule that follows, and this whole module is it:** the walk runs in a **child process**
with a hard timeout, at most **once per process**, and every request reads only the cached
answer. A child that cannot get permission is killed at the timeout; the Hub keeps serving and
reports an honest ``needs_permission`` state instead of a hung slot.

The probe is started at Hub start-up (`standalone.create_app`'s lifespan). Nothing on a request
path may call `probe()`; `status()` and `kits()` never block and never touch ``/Volumes``.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

#: Where removable volumes mount, and the folder a kit drive carries its models under. Kept here
#: because this is the one place that walks the folder.
VOLUME_ROOT = Path("/Volumes")
KIT_FOLDER = "model-library"

#: How long the child may take before it is killed. The owner's cause is an **unanswered** macOS
#: permission prompt, which never returns on its own; two seconds is long enough for a real walk
#: and short enough that nobody notices it.
PROBE_TIMEOUT_SECONDS = 2.0

#: The child prints one line beginning with this, so a warning printed by an interpreter or an
#: import cannot be mistaken for the answer.
_PROBE_PREFIX = "studiohub-volume-probe:"

#: The child's whole job: list the kit libraries and answer in JSON. It is a separate process on
#: purpose - a blocked `os.scandir('/Volumes')` must be something the parent can kill, not a thread
#: this Hub then waits on for ever.
_CHILD_SOURCE = r"""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
folder = sys.argv[2]
found = []
try:
    volumes = sorted(p for p in root.iterdir() if p.is_dir())
except OSError as exc:
    print("__PREFIX__" + json.dumps({"ok": False,
                                      "reason": f"{root} could not be read: {exc}"}))
    raise SystemExit(0)
for volume in volumes:
    try:
        # A kit drive keeps its library three levels down: <drive>/Video Generation Kit/
        # Studio Hub KH/model-library. The first two patterns are the older layouts, kept so an
        # existing drive is still found; the third is the one the 2026-10-28 write produced.
        candidates = (volume / folder, *sorted(volume.glob(f"*/{folder}")),
                      *sorted(volume.glob(f"*/*/{folder}")))
    except OSError:
        continue
    for candidate in candidates:
        try:
            if candidate.is_dir() and str(candidate) not in found:
                found.append(str(candidate))
        except OSError:
            continue
print("__PREFIX__" + json.dumps({"ok": True, "kits": found}))
""".replace("__PREFIX__", _PROBE_PREFIX)

_LOCK = threading.Lock()
_THREAD: threading.Thread | None = None
_STATE: dict | None = None
#: Only tests replace this; production always runs the child above.
_PROBE_COMMAND: list[str] | None = None


def _child_command(root: Path | str = VOLUME_ROOT, folder: str = KIT_FOLDER) -> list[str]:
    """The child that does the only ``/Volumes`` walk in this process tree."""
    return [sys.executable, "-I", "-c", _CHILD_SOURCE, str(root), folder]


def _command() -> list[str]:
    if _PROBE_COMMAND is not None:
        return list(_PROBE_COMMAND)
    return _child_command()


def _answer(state: str, *, reason: str | None, kits: list[str] | None = None) -> dict:
    return {"state": state, "path": str(VOLUME_ROOT), "reason": reason,
            "kits": list(kits or []), "checked_at": time.time()}


def probe(*, timeout: float | None = None, command: list[str] | None = None) -> dict:
    """Run the child once and answer ``ready`` or ``needs_permission``.

    Never raises: a timeout, a refused read, a child that dies and an unreadable answer are all
    ``needs_permission`` with the reason stated. That is the honest state - "I could not read the
    volumes" must not be reported as "no kit is mounted", which is what an empty list would say.
    """
    limit = PROBE_TIMEOUT_SECONDS if timeout is None else float(timeout)
    try:
        done = subprocess.run(command or _command(), capture_output=True, text=True, timeout=limit)
    except subprocess.TimeoutExpired:
        return _answer("needs_permission", reason=(
            f"The check for removable volumes needs permission: macOS did not answer within "
            f"{limit:g} s. Click Allow on the macOS prompt for Removable Volumes, then restart "
            f"this Mac's Hub services so it can look again."))
    except (OSError, ValueError) as exc:
        return _answer("needs_permission", reason=(
            f"The check for removable volumes could not run: {exc}"))
    lines = [line for line in (done.stdout or "").splitlines() if line.startswith(_PROBE_PREFIX)]
    if done.returncode != 0 or not lines:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        return _answer("needs_permission", reason=(
            f"The check for removable volumes ended without an answer"
            f"{': ' + detail[-1] if detail else ''}."))
    try:
        payload = json.loads(lines[-1][len(_PROBE_PREFIX):])
    except ValueError as exc:
        return _answer("needs_permission", reason=(
            f"The check for removable volumes answered something unreadable: {exc}"))
    if not payload.get("ok"):
        return _answer("needs_permission", reason=str(payload.get("reason") or "the volumes could "
                                                       "not be read"))
    kits = [str(item) for item in payload.get("kits") or [] if isinstance(item, str)]
    return _answer("ready", reason=None, kits=kits)


def _store(result: dict) -> dict:
    global _STATE
    with _LOCK:
        _STATE = result
        return _copy(_STATE)


def _copy(state: dict) -> dict:
    return {**state, "kits": list(state.get("kits") or [])}


def _run() -> None:
    try:
        result = probe()
    except Exception as exc:  # noqa: BLE001 - a probe thread must never die silently
        log.exception("the removable-volume check failed")
        result = _answer("needs_permission", reason=f"The check for removable volumes failed: {exc}")
    _store(result)


def start() -> None:
    """Start the one probe this process will run. Safe to call more than once.

    Called at Hub start-up. It returns immediately; the child is waited for on a daemon thread.
    """
    global _THREAD
    with _LOCK:
        if _STATE is not None:
            return
        if _THREAD is not None and _THREAD.is_alive():
            return
        _THREAD = threading.Thread(target=_run, name="removable-volumes", daemon=True)
        _THREAD.start()


def status() -> dict:
    """The cached answer. **Never probes and never blocks.**

    ``not_checked`` and ``checking`` are their own states, kept apart from ``needs_permission`` and
    from ``ready``: "I have not looked" must not read as "there is nothing there".
    """
    with _LOCK:
        if _STATE is not None:
            return _copy(_STATE)
        if _THREAD is not None and _THREAD.is_alive():
            return _answer("checking", reason="the removable-volume check is running")
        return _answer("not_checked", reason="the removable-volume check has not run")


def kits() -> list[Path]:
    """Every mounted kit library from the cached answer. Empty until the probe has run."""
    return [Path(item) for item in status().get("kits") or []]


# ── Test seams ────────────────────────────────────────────────────────────────────────────────
# The owner's failure is an unanswered macOS prompt, which cannot be reproduced with `tccutil`
# (and must not be tried on a working Mac). A test substitutes a child that blocks - the same
# shape as the prompt - and proves the routes keep answering while it hangs.

def _set_probe_command_for_testing(command: list[str] | None) -> None:
    global _PROBE_COMMAND
    _PROBE_COMMAND = list(command) if command else None


def _reset_for_testing() -> None:
    """Forget the cached answer and any thread, so a test starts from ``not_checked``."""
    global _THREAD, _STATE
    with _LOCK:
        _THREAD = None
        _STATE = None
