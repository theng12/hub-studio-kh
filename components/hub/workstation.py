"""Lite Mode (the name people see; "workstation" inside the code, its API, file and error codes): a
shop's cashier Mac lends the fleet its idle time, never its user's time.

Owner, 2026-10-03 ("Shops"): 7-8 cashier iMacs (M1/M3, 8 GB) run Chrome's cloud POS, Telegram, a PDF
viewer and the label printer all day and are idle most of it. A Mac with ``enabled`` on:

* runs every worker process at **background priority** (``PRIO_DARWIN_BG``: lowest CPU band, throttled
  disk I/O, low-priority GPU work - what ``taskpolicy -b`` sets), re-applied every few seconds so a
  restarted worker or a child it spawns is caught too;
* takes only the **workstation models** (``components/workstation_models.py``);
* starts a job only when the model's own free-before-start floor is free (about 2.8 GB for the
  8 GB-class models), or this Mac's own ``start_free_gb`` when its owner set one - the controller's
  memory gate applies it. There is no extra reserve: an idle 8 GB Mac has about 3 GB free;
* **finishes every job it starts** (owner: no generation wasted). The one exception is macOS memory
  pressure at **critical** for ``PRESSURE_SUSTAIN_S`` - very rare - when the job **yields**: it is
  cancelled, its model unloaded, and the controller told at its next poll so it moves the job at
  once (``WORKSTATION_YIELDED``), logged plainly;
* optionally takes work **only when idle** (``idle_only``: ``idle_minutes`` without keyboard/mouse
  input, default 3, or the screen locked) and optionally **steps aside** (``step_aside``): on input
  it finishes the job it has, unloads its model so the memory comes back, and takes no new work until
  ``resume_after_minutes`` pass without input;
* takes no work in **quiet hours** (``"HH:MM-HH:MM"``, may wrap midnight);
* is **quiet**: the app shows no pop-ups or desktop alerts and may hide its menu-bar icon.

It works the same on **every role** (owner, 2026-10-04): a "work Mac" is set up like any other Mac -
standalone, controller or node - and Lite Mode is the one extra tick. Every generation job this Mac runs
locally is governed, wherever it came from: a controller's work through the peer surface
(``admission_refusal`` at the door), and this Mac's own queues - a standalone's, a node's, a
controller's own-Mac share of its group's work - through the scheduler, which treats this Mac like any
other Lite Mode Mac (``peers.dispatch_ready`` / ``peers.lite_mode_allows`` read this file for
``"local"``). A controller standing by keeps routing and serving; only its own generation waits, and its
group's work goes to its nodes. On a Mac with nowhere else to send it, work waits. A synchronous
request (Subtitle's ``/api/transcribe``) is not cancelled mid-way; it is short and ends by itself.

The settings live in ``workstation.json`` in the Hub's data folder. The Mac's own Studio Hub app changes
them in Settings > Lite Mode (``POST /api/hub/workstation``), the Gateway through the controller
(``POST /api/hub/fleet/nodes/{id}/workstation``), and that is the only place it is switched on: a Lite Mode
Mac is installed like any other Mac (no kit step since the Lite-only copy launcher was retired
2026-10-30) and takes the Essentials 8 GB preset, while ``workstation_models.py`` stays what it is -
a runtime allowlist. Every decision re-reads the file, so a change applies without a restart. ``status()`` is what every fleet row publishes as
``workstation``.
"""
from __future__ import annotations

import asyncio
import ctypes
import datetime as _dt
import json
import logging
import os
import re
import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from components import workstation_models

from .backend.registry import DATA_DIR

log = logging.getLogger("studiohub.workstation")

SETTINGS_FILE = DATA_DIR / "workstation.json"
DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "idle_only": False, "idle_minutes": 3,
    "step_aside": False, "resume_after_minutes": 2,
    "start_free_gb": None,   # None: each model's own free-before-start floor
    "quiet_hours": [],
    "hide_menu_bar_icon": False,
}
STATES = ("off", "working", "standing_by", "yielded", "quiet_hours")
YIELDED_CODE = "WORKSTATION_YIELDED"
STANDING_BY_CODE = "WORKSTATION_STANDING_BY"
NOT_ALLOWED_CODE = "WORKSTATION_MODEL_NOT_ALLOWED"
POLL_S = 0.25            # one HID read and one sysctl cost microseconds
IDLE_POLL_S = 2.0        # mode off: only notice it being turned on
PRESSURE_SUSTAIN_S = 20.0  # critical memory pressure must last this long before a job yields
PRESSURE_CRITICAL = 4      # kern.memorystatus_vm_pressure_level: 1 normal, 2 warning, 4 critical
STOP_GRACE_S = 2.0       # a cancelled job not stopped by then has its worker stopped (the Hub restarts it)
YIELDED_SHOW_S = 60.0    # how long "yielded" is the reported state after a yield
YIELD_MEMORY_S = 6 * 3600.0
PRIORITY_EVERY_S = 5.0
_PRIO_DARWIN_PROCESS, _PRIO_DARWIN_BG = 4, 0x1000
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")

_lock = threading.Lock()
_counters = {"day": "", "jobs": 0, "yields": 0}
_last_yield: tuple[float, str] | None = None          # (monotonic, reason)
_yielded: dict[tuple[str, str], dict[str, Any]] = {}  # (worker_id, job_id) -> {reason, at, payload}
_backgrounded: set[int] = set()


# ── settings ────────────────────────────────────────────────────────────────

def _minutes(key: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 60:
        raise ValueError(f"{key} must be a whole number of minutes from 1 to 60")
    return value


def validate(changes: object, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """``base`` with ``changes`` applied, or ``ValueError`` naming the first bad setting."""
    if not isinstance(changes, dict):
        raise ValueError("Lite Mode settings must be an object")
    merged = dict(base or DEFAULTS)
    for key, value in changes.items():
        if key not in DEFAULTS:
            raise ValueError(f"{key!r} is not a Lite Mode setting")
        if isinstance(DEFAULTS[key], bool):
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
        elif key in ("idle_minutes", "resume_after_minutes"):
            value = _minutes(key, value)
        elif key == "start_free_gb":
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not 0.5 <= value <= 8):
                raise ValueError("start_free_gb must be empty (each model's own) or GB from 0.5 to 8")
            value = None if value is None else round(float(value), 1)
        elif key == "quiet_hours":
            if (not isinstance(value, list) or len(value) > 6
                    or not all(isinstance(span, str) and _HHMM.match(span.strip()) for span in value)):
                raise ValueError('quiet_hours must be up to 6 spans written "HH:MM-HH:MM", e.g. "22:00-07:00"')
            value = [span.strip() for span in value]
        merged[key] = value
    return merged


def load() -> dict[str, Any]:
    """This Mac's settings. A file that exists but cannot be read keeps the mode **on** with its
    defaults: a damaged file must not turn a cashier's Mac into a full-speed server."""
    try:
        raw = json.loads(SETTINGS_FILE.read_text())
    except FileNotFoundError:
        return dict(DEFAULTS)
    except (OSError, ValueError):
        log.warning("workstation.json is unreadable; keeping Lite Mode on with its defaults")
        return {**DEFAULTS, "enabled": True}
    try:
        return validate({key: value for key, value in raw.items() if key in DEFAULTS})
    except (ValueError, AttributeError) as exc:
        log.warning("workstation.json is invalid (%s); keeping Lite Mode on with its defaults", exc)
        return {**DEFAULTS, "enabled": True}


def save(changes: object) -> dict[str, Any]:
    """Apply and persist ``changes`` (atomic replace); returns ``status()``."""
    with _lock:
        merged = validate(changes, load())
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = SETTINGS_FILE.with_name(f".workstation-{os.getpid()}.tmp")
        temporary.write_text(json.dumps(merged, indent=2) + "\n")
        os.replace(temporary, SETTINGS_FILE)
    return status()


# ── what the Mac is doing ───────────────────────────────────────────────────

def hid_idle_seconds() -> float | None:
    """Seconds since the last keyboard/mouse/trackpad input (the HID system's idle time), or None."""
    try:
        cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        since = cg.CGEventSourceSecondsSinceLastEventType
        since.restype, since.argtypes = ctypes.c_double, [ctypes.c_int32, ctypes.c_uint32]
        return float(since(1, 0xFFFFFFFF))   # kCGEventSourceStateHIDSystemState, any input event
    except (OSError, AttributeError):
        return None


def screen_locked() -> bool:
    """Whether the console session's screen is locked (``CGSSessionScreenIsLocked``)."""
    try:
        cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        cg.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFDictionaryGetValue.restype = ctypes.c_void_p
        cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        session = cg.CGSessionCopyCurrentDictionary()
        if not session:
            return False
        key = cf.CFStringCreateWithCString(None, b"CGSSessionScreenIsLocked", 0x08000100)
        try:
            value = cf.CFDictionaryGetValue(session, key)
            return bool(value) and bool(cf.CFBooleanGetValue(value))
        finally:
            cf.CFRelease(key)
            cf.CFRelease(session)
    except (OSError, AttributeError):
        return False


def pressure_level() -> int | None:
    """macOS's own memory-pressure level (1 normal, 2 warning, 4 critical), or None."""
    from components import host_memory
    return host_memory._sysctl("kern.memorystatus_vm_pressure_level")


def in_quiet_hours(spans: list[str], now: _dt.datetime) -> bool:
    minute = now.hour * 60 + now.minute
    for span in spans:
        match = _HHMM.match(span)
        if not match:
            continue
        h1, m1, h2, m2 = (int(part) for part in match.groups())
        start, end = h1 * 60 + m1, h2 * 60 + m2
        if (start <= minute < end) if start <= end else (minute >= start or minute < end):
            return True
    return False


def decide(settings: dict[str, Any], *, idle_s: float | None, locked: bool, now: _dt.datetime, since_yield: float | None = None,
           yield_reason: str = "") -> tuple[str, str | None]:
    """``(state, reason)`` for one moment. Pure: every input is passed in."""
    if not settings["enabled"]:
        return "off", None
    if in_quiet_hours(settings["quiet_hours"], now):
        return "quiet_hours", "inside its quiet hours"
    if since_yield is not None and since_yield < YIELDED_SHOW_S:
        return "yielded", yield_reason or "it stopped a job and handed it back"
    # A locked screen is nobody at the keyboard. An unreadable idle time is someone at it: the
    # owner turned these toggles on to protect the person using the Mac.
    idle = float("inf") if locked else (idle_s if idle_s is not None else 0.0)
    if settings["step_aside"] and idle < settings["resume_after_minutes"] * 60:
        return "standing_by", "someone is using this Mac"
    if settings["idle_only"] and idle < settings["idle_minutes"] * 60:
        return "standing_by", (f"waiting for {settings['idle_minutes']} minutes without keyboard "
                               "or mouse use")
    return "working", None


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _roll_day() -> None:
    if _counters["day"] != _today():
        _counters.update(day=_today(), jobs=0, yields=0)


def note_job_accepted() -> None:
    """A job started on this Mac - from a controller or from its own queue - while the mode is on."""
    if load()["enabled"]:
        with _lock:
            _roll_day()
            _counters["jobs"] += 1


def current() -> tuple[dict[str, Any], str, str | None]:
    settings = load()
    if not settings["enabled"]:
        return settings, "off", None
    since = reason = None
    if _last_yield is not None:
        since, reason = time.monotonic() - _last_yield[0], _last_yield[1]
    state, why = decide(settings, idle_s=hid_idle_seconds(), locked=screen_locked(),
                        now=_dt.datetime.now(), since_yield=since, yield_reason=reason or "")
    return settings, state, why


def status() -> dict[str, Any]:
    """The ``workstation`` object every fleet row publishes for this Mac."""
    settings, state, why = current()
    with _lock:
        _roll_day()
        counters = dict(_counters)
    return {**settings, "state": state, "reason": why,
            "yields_today": counters["yields"], "jobs_today": counters["jobs"]}


def admission_refusal(model: str) -> dict[str, Any] | None:
    """This Mac's workstation answer to a new job - a controller's, or its own - or None to let it in."""
    settings, state, why = current()
    if not settings["enabled"]:
        return None
    if model and not workstation_models.is_workstation_model(model):
        return {"code": NOT_ALLOWED_CODE,
                "detail": f"{model} is not one of the models this Mac runs in Lite Mode"}
    if state != "working":
        return {"code": STANDING_BY_CODE, "detail": f"this Mac is standing by: {why}"}
    return None


# ── yielding ────────────────────────────────────────────────────────────────

def yielded(worker_id: str, job_id: str) -> dict[str, Any] | None:
    return _yielded.get((worker_id, job_id))


def rewrite_status(worker_id: str, job_id: str, status_code: int,
                   payload: object) -> tuple[int, dict[str, Any]] | None:
    """A yielded job's status as the controller must read it, or None to pass the worker's on.

    The worker's own receipt is kept (the controller checks its identity fields) with the state set
    to ``error`` and the code ``WORKSTATION_YIELDED``, so the controller moves it at once instead of
    waiting out a recovery window. A job that finished before the cancel landed is passed through:
    the first result wins. A worker that is down or no longer knows the job gets the receipt saved
    when the yield began.
    """
    record = yielded(worker_id, job_id)
    if record is None:
        return None
    job = payload.get("job") if isinstance(payload, dict) else None
    if status_code < 400 and isinstance(job, dict) and job.get("state") == "done" and not job.get("error"):
        return None
    if status_code >= 400 or not isinstance(job, dict):
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {"job": {"id": job_id}}
        job = payload.get("job") if isinstance(payload.get("job"), dict) else {"id": job_id}
    sentence = f"{YIELDED_CODE}: this Mac stepped aside ({record['reason']}); nothing was kept"
    return 200, {**payload, "job": {**job, "id": job_id, "state": "error",
                                     "error": sentence, "error_code": YIELDED_CODE}}


def _family(worker_id: str) -> str:
    from .backend import vocabulary
    return ("generate/transcribe/jobs"
            if worker_id == vocabulary.WORKER_FOR_MODALITY.get("transcription") else "generate/jobs")


async def yield_jobs(monitor: Any, held: list[tuple[str, str]], reason: str) -> None:
    """Stop every held job, hand it back, and unload its model. Never raises."""
    global _last_yield
    if not held:
        return
    now = time.monotonic()
    with _lock:
        _roll_day()
        _counters["yields"] += 1
        _last_yield = (now, reason)
        for key in [key for key, row in _yielded.items() if now - row["at"] > YIELD_MEMORY_S]:
            _yielded.pop(key, None)
    log.warning("workstation: stepping aside (%s); handing back %d job(s)", reason, len(held))
    workers = {row["id"]: row for row in monitor.registry}
    for worker_id, job_id in held:
        worker = workers.get(worker_id)
        _yielded[(worker_id, job_id)] = {"reason": reason, "at": now, "payload": None}
        if worker is None:
            continue
        url = f"{worker['base_url']}/api/{_family(worker_id)}/{job_id}"
        headers = {"X-Hub-Token": worker["hub_token"]}
        try:
            receipt = await monitor._client.get(url, headers=headers, timeout=2.0)
            if receipt.status_code < 400:
                _yielded[(worker_id, job_id)]["payload"] = receipt.json()
        except Exception:
            pass
        try:
            await monitor._client.delete(url, headers=headers, timeout=2.0)
        except Exception:
            pass
    asyncio.get_running_loop().create_task(_unload(monitor, held))


async def _unload(monitor: Any, held: list[tuple[str, str]]) -> None:
    """Free the memory: a job that has not stopped within STOP_GRACE_S has its worker stopped (the
    Hub restarts an exited worker by itself, empty); one that stopped has its model released."""
    from .backend.memory_control import FleetMemoryControl
    await asyncio.sleep(STOP_GRACE_S)
    workers = {row["id"]: row for row in monitor.registry}
    release = []
    for worker_id, job_id in held:
        worker = workers.get(worker_id)
        if worker is None:
            continue
        try:
            answer = await monitor._client.get(
                f"{worker['base_url']}/api/{_family(worker_id)}/{job_id}",
                headers={"X-Hub-Token": worker["hub_token"]}, timeout=2.0)
            job = answer.json().get("job") if answer.status_code < 400 else None
            running = isinstance(job, dict) and job.get("state") in ("queued", "running", "cancel_requested")
        except Exception:
            running = False
        if running and isinstance(worker.get("pid"), int):
            try:
                os.kill(worker["pid"], signal.SIGTERM)
                log.warning("workstation: %s did not stop within %.0f s; its worker was stopped",
                            worker_id, STOP_GRACE_S)
            except OSError:
                pass
        else:
            release.append(worker_id)
    if release:
        try:
            await FleetMemoryControl(monitor, monitor._client).release(release)
        except Exception:
            log.exception("workstation: could not release models after a yield")


def apply_priority(pids: list[int], background: bool) -> None:
    """Put these worker processes (and everything they spawned) at - or back from - background."""
    try:
        import psutil
    except ImportError:
        return
    every = set()
    for pid in pids:
        every.add(pid)
        try:
            every.update(child.pid for child in psutil.Process(pid).children(recursive=True))
        except Exception:
            pass
    targets = every if background else set(_backgrounded)
    for pid in targets:
        try:
            os.setpriority(_PRIO_DARWIN_PROCESS, pid, _PRIO_DARWIN_BG if background else 0)
        except (OSError, AttributeError):
            continue
        (_backgrounded.add if background else _backgrounded.discard)(pid)


async def tick(monitor: Any, held: Callable[[], list[tuple[str, str]]],
               memo: dict[str, Any]) -> float:
    """One pass of the workstation loop; returns how long to sleep. ``memo`` carries its memory."""
    settings = load()
    if not settings["enabled"]:
        if _backgrounded:
            apply_priority([], background=False)
        return IDLE_POLL_S
    now = time.monotonic()
    if now - memo.get("priority_at", -PRIORITY_EVERY_S) >= PRIORITY_EVERY_S:
        memo["priority_at"] = now
        apply_priority([row["pid"] for row in monitor.registry
                        if row.get("local") and isinstance(row.get("pid"), int)], background=True)
    running = [key for key in held() if key not in _yielded]
    if running and pressure_level() == PRESSURE_CRITICAL:
        memo.setdefault("critical_since", now)
        if now - memo["critical_since"] >= PRESSURE_SUSTAIN_S:
            memo.pop("critical_since", None)
            await yield_jobs(monitor, running, f"macOS reported critical memory pressure for "
                                               f"{PRESSURE_SUSTAIN_S:.0f} seconds")
    else:
        memo.pop("critical_since", None)
    # Someone is using this Mac: the job it has finishes, then its model is unloaded once so the
    # memory goes back to that person's apps. It is unloaded again only after work resumed.
    if not running and (settings["step_aside"] or settings["idle_only"]):
        _settings, state, _why = current()
        if state == "standing_by" and not memo.get("released"):
            memo["released"] = True
            await release_models(monitor)
        elif state == "working":
            memo.pop("released", None)
    return POLL_S if running else IDLE_POLL_S / 4


async def release_models(monitor: Any) -> None:
    """Unload every local worker's model (a worker with work answers busy and keeps it)."""
    from .backend.memory_control import FleetMemoryControl
    ids = [row["id"] for row in monitor.registry if row.get("local")]
    if not ids:
        return
    try:
        await FleetMemoryControl(monitor, monitor._client).release(ids)
        log.info("workstation: someone is using this Mac; models unloaded")
    except Exception:
        log.exception("workstation: could not unload models")


async def watch(monitor: Any, held: Callable[[], list[tuple[str, str]]]) -> None:
    """The workstation loop: background priority, and the two mid-job yield triggers."""
    memo: dict[str, Any] = {}
    try:
        while True:
            try:
                delay = await tick(monitor, held, memo)
            except Exception:
                log.exception("workstation pass failed (continuing)")
                delay = IDLE_POLL_S
            await asyncio.sleep(delay)
    except asyncio.CancelledError:
        apply_priority([], background=False)
        return


def reset_for_tests() -> None:
    global _last_yield
    _last_yield = None
    _yielded.clear()
    _backgrounded.clear()
    _counters.update(day="", jobs=0, yields=0)
