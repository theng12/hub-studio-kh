"""Bounded, transient demand for synchronous Chat; never stores customer content."""
from __future__ import annotations

import asyncio
import re
import time

from fastapi import HTTPException

MAX_WAIT_SECONDS = 3 * 3600
MAX_REQUESTS = 256
MAX_MACHINE_REQUESTS = 2
requests: dict[str, dict] = {}


def _identifier(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise HTTPException(400, "request id must be 32 lowercase hexadecimal characters")
    return value


def _prune() -> None:
    now = time.monotonic()
    for key, row in list(requests.items()):
        active = row["state"] in {"queued", "running"} or (row["state"] == "unknown" and row.get("lease_held"))
        if row["expires"] <= now and not active:
            requests.pop(key, None)
    # Completed receipts are bounded evidence, not a throughput limit. Cancellation
    # tombstones remain until expiry because a delayed POST must still be fenced.
    if len(requests) >= MAX_REQUESTS:
        oldest = next((key for key, row in requests.items() if row["state"] in {"done", "expired", "refused"}), None)
        if oldest is not None:
            requests.pop(oldest, None)


def register(request_id: str, machine: str, studio_id: str, model: str) -> dict:
    request_id = _identifier(request_id)
    _prune()
    previous = requests.get(request_id)
    if previous and previous["state"] in {"refused", "expired"}:
        requests.pop(request_id)
        previous = None
    if previous:
        code = "CHAT_REQUEST_CANCELLED" if previous["state"] == "cancelled" else "CHAT_REQUEST_DUPLICATE"
        raise HTTPException(409, {"code": code, "detail": "this request id has already been used"})
    active = sum(row.get("machine") == machine and (
        row["state"] in {"queued", "running"} or (row["state"] == "unknown" and row.get("lease_held")))
                 for row in requests.values())
    if active >= MAX_MACHINE_REQUESTS or len(requests) >= MAX_REQUESTS:
        raise HTTPException(409, {"code": "MACHINE_BUSY", "detail": "the bounded Chat queue is full"})
    row = {"id": request_id, "machine": machine, "studio": studio_id, "model": model,
           "state": "queued", "ready": False, "expires": time.monotonic() + MAX_WAIT_SECONDS}
    requests[request_id] = row
    return row


def snapshot(request_id: str) -> dict:
    _identifier(request_id)
    _prune()
    row = requests.get(request_id)
    if row is None:
        raise HTTPException(404, "unknown Chat request")
    return {"id": request_id, "state": row["state"]}


def cancel(request_id: str) -> dict:
    _identifier(request_id)
    _prune()
    row = requests.get(request_id)
    if row is not None and row["state"] in {"running", "unknown"}:
        raise HTTPException(409, "Chat has already started and cannot be interrupted")
    if row is None:
        if len(requests) >= MAX_REQUESTS:
            raise HTTPException(409, "the bounded Chat request registry is full")
        row = {"id": request_id, "state": "cancelled", "expires": time.monotonic() + MAX_WAIT_SECONDS}
        requests[request_id] = row
    elif row["state"] in {"queued", "refused", "expired"}:
        row.update(state="cancelled", ready=False)
    return snapshot(request_id)


def first(row: dict) -> bool:
    return next((r for r in requests.values()
                 if r.get("machine") == row["machine"] and r["state"] == "queued" and r.get("ready")), None) is row


def pending(machine: str) -> bool:
    return any(row.get("machine") == machine and row["state"] == "queued" and row.get("ready")
               for row in requests.values())


def mark_running(row: dict) -> None:
    row.update(state="running", ready=False)


def finish(row: dict) -> None:
    if row["state"] in {"queued", "running", "unknown"}:
        row.update(state="done", ready=False, expires=time.monotonic() + MAX_WAIT_SECONDS)


async def wait(row, request, ready, reserve, timeout_seconds: float) -> str:
    """Revalidate a waiting client immediately before the non-awaiting physical lease."""
    deadline = time.monotonic() + min(MAX_WAIT_SECONDS, max(0, timeout_seconds))
    try:
        while True:
            if await request.is_disconnected() or row["state"] == "cancelled":
                row.update(state="cancelled", ready=False)
                raise HTTPException(409, {"code": "CHAT_REQUEST_CANCELLED", "detail": "Chat request cancelled before starting"})
            if time.monotonic() >= deadline:
                row.update(state="expired", ready=False)
                raise HTTPException(409, {"code": "CHAT_QUEUE_EXPIRED", "detail": "Chat request exceeded its queue wait ceiling"})
            refusal, dispatchable = await ready()
            row["ready"] = dispatchable
            if refusal:
                raise HTTPException(409, refusal)
            if first(row) and dispatchable:
                # Memory and readiness probes can await: a cancel or disconnect during those
                # probes must be observed before taking the lease, not on the next poll.
                if await request.is_disconnected() or row["state"] == "cancelled":
                    row.update(state="cancelled", ready=False)
                    continue
                if time.monotonic() >= deadline:
                    continue
                owner = reserve()
                if owner is not None:
                    mark_running(row)
                    return owner
            await asyncio.sleep(0.25)
    except asyncio.CancelledError:
        if row["state"] == "queued":
            row.update(state="cancelled", ready=False)
        raise
    finally:
        if row["state"] == "queued":
            row.update(state="refused", ready=False)
