"""Drain for update: a Mac with an update to install stops taking NEW work, finishes what runs, installs.

Two in-memory leases, so nothing can wedge - an app that dies, a Mac that restarts or a network that
drops simply lets them lapse, and the Mac takes work again:

* **draining** (this Mac): while it holds, ``broker.busy_machines()`` names every machine, so the
  scheduler starts nothing, a peer request is refused ``409 MACHINE_BUSY`` and a job submitted here is
  refused the same way (``standalone._submission_readiness``). ``MACHINE_BUSY`` is the code the gateway
  already re-routes as a location refusal (``LOCATION_NOT_READY``) and the broker already treats as
  "not available right now", never as a failure. Running work is never touched.
* **the slot** (a controller's group): one Mac drains or installs at a time, the controller included,
  so capacity is never lost wholesale. A node asks its controller for it; a controller or standalone
  Mac holds its own. A controller too old to know the route, or unreachable, does not block a node:
  an update must not depend on a Mac that cannot answer.

The native app owns the timing (grace period, install, resume); it renews both leases while it waits.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import httpx

LEASE_S = 120.0
_lock = threading.Lock()
_drain_until = 0.0
_slot: tuple[str, float] | None = None   # (holder, monotonic expiry)


def draining() -> bool:
    return time.monotonic() < _drain_until


def _set_draining(want: bool) -> None:
    global _drain_until
    _drain_until = time.monotonic() + LEASE_S if want else 0.0


def slot(holder: str, want: bool) -> bool:
    """Take, renew or give up the group's one update slot. True when `holder` has it (or, for
    `want=False`, no longer holds it)."""
    global _slot
    with _lock:
        now = time.monotonic()
        held = _slot if _slot and _slot[1] > now else None
        if not want:
            if held and held[0] == holder:
                _slot = None
            return True
        if held and held[0] != holder:
            return False
        _slot = (holder, now + LEASE_S)
        return True


async def request(want: bool, controller: tuple[str, dict[str, str]] | None) -> dict[str, Any]:
    """This Mac's answer to the app: ``draining`` (it now refuses new work) or ``waiting`` (another
    Mac in the group has the slot). `controller` is ``(url, headers)`` on a node, None otherwise."""
    if controller is None:
        granted = slot("controller", want)
    else:
        granted = True
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                answer = await client.post(f"{controller[0]}/api/hub/fleet/update-slot",
                                           json={"want": want}, headers=controller[1])
            if answer.status_code == 200:
                granted = bool(answer.json().get("granted"))
        except (httpx.HTTPError, ValueError):
            pass
    with _lock:
        _set_draining(want and granted)
    return {"state": "draining" if want and granted else "waiting" if want else "resumed"}
