"""What starts when this Mac starts, reported up the fleet and changeable from it.

**The two settings belong to the Swift app, not to this process.** "Start at login" is a launchd
agent the app writes; "Start services on startup" is a preference the app reads when it is opened
by that agent. This process only runs when the second one is on (or someone pressed Start), so it
cannot own them - it can only (a) *report* what the app tells it and (b) hand the app a request.
The exchange is one owner-only route, ``POST /api/hub/startup/sync``, that the app calls on its
ordinary poll: it brings its current settings, this side answers with whatever the fleet asked for
since the last call, and the app brings the outcome of each on the next one.

Nothing here is reachable without a credential: the sync route is the owner's (the Hub token, never a
fleet credential), and the request route is on the fleet allowlist only - a controller needs it to
act for the Gateway, and a node's own credential is deliberately not allowed to use it.

**When this process is not running, nothing here can help** - a Mac whose services are off is silent
to its controller. That is why a joined Mac turns the setting on by default (see ``ShellConfiguration``)
and why a request to *start* services can only be answered by a Mac that is already serving.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from .registry import DATA_DIR

log = logging.getLogger("studiohub.startup")

#: What a caller may ask for. ``start_services`` is answered here (nothing to do while this process
#: runs) or becomes a restart when a Studio is down; ``restart_services`` is always the app's work.
ACTIONS = ("set_start_services_on_launch", "set_open_at_login", "start_services", "restart_services")
_BOOL_ACTIONS = ("set_start_services_on_launch", "set_open_at_login")

#: The app polls about every ten seconds. A snapshot older than this means the app is not
#: reporting (an old app, or the Hub was started by something else) - said, not guessed.
REPORT_TTL_S = 120.0
#: How long a request waits for the app to take it before the caller is told it is queued.
DELIVERY_WAIT_S = 18.0
#: Requests nobody collected are dropped: an app that comes back an hour later must not apply an
#: instruction nobody remembers giving.
REQUEST_TTL_S = 300.0
AUDIT_FILE_NAME = "startup_audit.jsonl"
_AUDIT_KEEP = 500

_lock = threading.Lock()
_started_at = time.time()
_app: dict[str, Any] = {}
_reported_at = 0.0
_pending: list[dict[str, Any]] = []
_outcomes: dict[str, dict[str, Any]] = {}
_events: dict[str, threading.Event] = {}
_counter = 0


class StartupRefused(Exception):
    """A request this Mac will not take, with the status and code the caller sees."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    def body(self) -> dict[str, Any]:
        return {"ok": False, "code": self.code, "detail": self.message, **self.extra}


def _bool_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _clean_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = re.sub(r"[\x00-\x1f\x7f]", " ", value).strip()
    return text[:limit] or None


def reset_for_tests() -> None:
    global _reported_at, _counter
    with _lock:
        _app.clear()
        _pending.clear()
        _outcomes.clear()
        _events.clear()
        _reported_at = 0.0
        _counter = 0


# ── what the app reported ────────────────────────────────────────────────────────────────────────

def sync(report: dict[str, Any], *, role: str | None = None, now: float | None = None) -> dict[str, Any]:
    """The app's call: store what it says, settle the outcomes it brings, hand back what is waiting.

    Only booleans and short strings are kept; anything else in the body is ignored rather than
    trusted. The returned requests carry an id the app echoes back in ``applied``.
    """
    global _reported_at
    moment = time.time() if now is None else now
    delivered: list[dict[str, Any]] = []
    with _lock:
        _app.clear()
        _app.update({
            "open_at_login": _bool_or_none(report.get("open_at_login")),
            "start_services_on_launch": _bool_or_none(report.get("start_services_on_launch")),
            "open_at_login_saved": _bool_or_none(report.get("open_at_login_saved")),
            "app_version": _clean_text(report.get("app_version"), 40),
        })
        _reported_at = moment
        applied = report.get("applied")
        for outcome in applied if isinstance(applied, list) else []:
            if not isinstance(outcome, dict) or not isinstance(outcome.get("id"), str):
                continue
            row = {"ok": outcome.get("ok") is True, "detail": _clean_text(outcome.get("detail"), 300),
                   "at": moment}
            _outcomes[outcome["id"]] = row
            event = _events.get(outcome["id"])
            if event:
                event.set()
            _audit_locked("applied" if row["ok"] else "failed",
                          {"id": outcome["id"], "detail": row["detail"]})
        keep = []
        for request in _pending:
            if moment - request["created_at"] > REQUEST_TTL_S:
                _audit_locked("expired", {"id": request["id"], "action": request["action"]})
            elif not request.get("delivered"):
                request["delivered"] = True
                delivered.append({key: request[key] for key in ("id", "action", "value")})
                if request["action"] == "restart_services":
                    # The app restarts this process, so no outcome can come back through it. The
                    # handover is the answer.
                    _outcomes[request["id"]] = {"ok": True, "detail": "handed to the app", "at": moment}
                    event = _events.get(request["id"])
                    if event:
                        event.set()
                    _audit_locked("handed_to_app", {"id": request["id"], "action": request["action"]})
                else:
                    keep.append(request)
            else:
                keep.append(request)
        _pending[:] = keep
        for stale in [key for key, row in _outcomes.items() if moment - row["at"] > REQUEST_TTL_S]:
            _outcomes.pop(stale, None)
    return {"requests": delivered, "role": role or "standalone"}


def snapshot(studios: dict[str, str] | None = None, *, app_version: str | None = None,
             now: float | None = None) -> dict[str, Any]:
    """The ``startup`` object every Mac publishes (health, resources, node rows).

    ``open_at_login`` / ``start_services_on_launch`` are ``None`` when the app has not reported -
    unknown is a different fact from off, and the Fleet page shows it as such.
    """
    moment = time.time() if now is None else now
    with _lock:
        app = dict(_app)
        reported = _reported_at
    fresh = bool(reported) and moment - reported <= REPORT_TTL_S
    studios = studios or {}
    return {
        "open_at_login": app.get("open_at_login") if fresh else None,
        "start_services_on_launch": app.get("start_services_on_launch") if fresh else None,
        "open_at_login_saved": app.get("open_at_login_saved") if fresh else None,
        "services_running": {
            "up": sum(status == "up" for status in studios.values()),
            "total": len(studios),
            "studios": dict(sorted(studios.items())),
        },
        "app_version": app.get("app_version") or app_version,
        "last_start_at": round(_started_at, 3),
        "app_reporting": fresh,
        "reported_at": round(reported, 3) if reported else None,
    }


def studio_states(monitor: Any) -> dict[str, str]:
    """``{modality: status}`` for the Studios this Mac runs itself."""
    from . import peers
    states: dict[str, str] = {}
    for worker in getattr(monitor, "registry", []):
        if not peers.is_local_studio(worker):
            continue
        status = (getattr(monitor, "status", {}).get(worker.get("id")) or {}).get("status", "unknown")
        states[str(worker.get("modality") or worker.get("id"))] = str(status)
    return states


# ── work in flight ───────────────────────────────────────────────────────────────────────────────

def active_work() -> dict[str, int]:
    """What a restart would interrupt: items running or waiting on this Hub, by kind."""
    from . import broker, chat_jobs, transcription_jobs
    live = {"running": 0, "queued": 0}
    for module in (broker, transcription_jobs):
        for batch in list(getattr(module, "batches", {}).values()):
            if batch.get("cancelled"):
                continue
            for item in batch.get("items") or []:
                state = item.get("state")
                if state in ("running", "cancel_requested", "uncertain"):
                    live["running"] += 1
                elif state == "queued":
                    live["queued"] += 1
    for batch in list(chat_jobs.batches.values()):
        for pack in batch.get("packs") or []:
            if pack.get("state") == "running":
                live["running"] += 1
            elif pack.get("state") == "queued":
                live["queued"] += 1
    live["running"] += len(getattr(broker, "_busy", ()))  # a lease with no item is still work
    return live


# ── requests ─────────────────────────────────────────────────────────────────────────────────────

def _next_id() -> str:
    global _counter
    _counter += 1
    return f"su-{int(time.time())}-{_counter}"


async def request(action: str, value: object = None, *, force: bool = False, actor: str = "hub",
                  requested_by: str | None = None, studios: dict[str, str] | None = None,
                  wait: float | None = None) -> dict[str, Any]:
    """Take a request, validate it, queue it for the app and wait for the answer.

    Raises ``StartupRefused`` for anything this Mac will not do. A restart is **refused while work is
    running or waiting** unless ``force`` is true: nothing is stopped behind anyone's back, and the
    refusal says what is in flight so the caller can wait or force it.
    """
    who = _clean_text(requested_by, 80) or actor
    if action not in ACTIONS:
        raise StartupRefused(400, "UNKNOWN_ACTION", f"action must be one of {', '.join(ACTIONS)}")
    if action in _BOOL_ACTIONS and not isinstance(value, bool):
        raise StartupRefused(400, "VALUE_REQUIRED", f"{action} needs value: true or false")
    if not isinstance(force, bool):
        raise StartupRefused(400, "BAD_FORCE", "force must be true or false")
    if action in _BOOL_ACTIONS:
        _require_app_reporting(action)
    with _lock:
        have_app = bool(_reported_at) and time.time() - _reported_at <= REPORT_TTL_S
    effective = action
    if action == "start_services":
        states = studios or {}
        if states and all(status == "up" for status in states.values()):
            _audit("noop", {"action": action, "by": who, "actor": actor,
                            "detail": "services already running"})
            return {"ok": True, "state": "already_running", "action": action,
                    "services_running": {"up": len(states), "total": len(states)}}
        effective = "restart_services"  # a Studio is down: the app restarts the services
    if effective == "restart_services":
        if not have_app:
            raise StartupRefused(409, "APP_NOT_REPORTING",
                                 "the Studio Hub app on this Mac is not reporting, so it cannot be asked "
                                 "to restart the services")
        busy = active_work()
        if (busy["running"] or busy["queued"]) and not force:
            _audit("refused", {"action": action, "by": who, "actor": actor, "code": "WORK_IN_PROGRESS",
                               "work": busy})
            raise StartupRefused(
                409, "WORK_IN_PROGRESS",
                f"{busy['running']} job(s) running and {busy['queued']} waiting on this Mac; restarting "
                "would interrupt them. Retry when it is idle, or send force: true.", work=busy)
    with _lock:
        request_id = _next_id()
        _events[request_id] = threading.Event()
        _pending.append({"id": request_id, "action": effective, "value": value if action in _BOOL_ACTIONS else None,
                         "created_at": time.time(), "delivered": False})
    _audit("requested", {"id": request_id, "action": action, "effective": effective, "value": value,
                         "force": force, "by": who, "actor": actor})
    waited = await _wait_for(request_id, DELIVERY_WAIT_S if wait is None else wait)
    outcome = waited
    if outcome is None:
        return {"ok": True, "state": "queued", "id": request_id, "action": action,
                "detail": "the app has not collected it yet; it applies on the app's next check"}
    if not outcome["ok"]:
        return {"ok": False, "state": "failed", "id": request_id, "action": action,
                "detail": outcome.get("detail") or "the app could not apply it"}
    return {"ok": True, "state": "handed_to_app" if effective == "restart_services" else "applied",
            "id": request_id, "action": action, "detail": outcome.get("detail")}


def _require_app_reporting(action: str) -> None:
    with _lock:
        fresh = bool(_reported_at) and time.time() - _reported_at <= REPORT_TTL_S
    if not fresh:
        raise StartupRefused(409, "APP_NOT_REPORTING",
                             "the Studio Hub app on this Mac is not reporting, so it cannot apply "
                             f"{action}; open Studio Hub on that Mac once")


async def _wait_for(request_id: str, seconds: float) -> dict[str, Any] | None:
    event = _events.get(request_id)
    if event is None:
        return None
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if event.is_set():
            break
        await asyncio.sleep(0.05)
    with _lock:
        outcome = _outcomes.get(request_id)
        _events.pop(request_id, None)
    return outcome


# ── audit ────────────────────────────────────────────────────────────────────────────────────────

def audit_path() -> Path:
    return DATA_DIR / AUDIT_FILE_NAME


def audit(event: str, fields: dict[str, Any]) -> None:
    with _lock:
        _audit_locked(event, fields)


_audit = audit


def _audit_locked(event: str, fields: dict[str, Any]) -> None:
    """One JSON line per event, owner-only, kept to the last ``_AUDIT_KEEP``. Never raises."""
    row = {"at": round(time.time(), 3), "event": event, **fields}
    log.info("startup %s %s", event, json.dumps(fields, sort_keys=True, default=str))
    try:
        path = audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            pass
        lines.append(json.dumps(row, sort_keys=True, default=str))
        temporary = path.with_name(path.name + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines[-_AUDIT_KEEP:]) + "\n")
        os.replace(temporary, path)
    except OSError as exc:  # an audit that cannot be written is logged, never a reason to refuse
        log.warning("startup audit could not be written: %s", exc)


def recent_audit(limit: int = 30) -> list[dict[str, Any]]:
    try:
        lines = audit_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows
