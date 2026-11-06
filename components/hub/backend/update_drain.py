"""Drain for update: a Mac with an update to install stops taking NEW work, finishes what runs, installs.

Every Mac installs on its own schedule (owner, 2026-11-06: fan out - a Mac installs as soon as its own
running work finishes, never waiting behind another Mac's install). The drain is one in-memory lease,
so nothing can wedge - an app that dies, a Mac that restarts or a network that drops simply lets it
lapse, and the Mac takes work again:

* **draining** (this Mac): while it holds, ``broker.busy_machines()`` names this Mac's own machine, so
  the scheduler starts nothing here, a peer request is refused ``409 MACHINE_BUSY`` and a job submitted
  here is refused the same way (``standalone._submission_readiness``). ``MACHINE_BUSY`` is the code the
  gateway already re-routes as a location refusal (``LOCATION_NOT_READY``) and the broker already treats
  as "not available right now", never as a failure. Running work is never touched, and a controller's
  joined nodes keep taking work.

A node does not ask its controller: every Mac that wants a drain gets one, so an update never waits on
another Mac or on a controller that cannot answer. The authenticated
``/api/hub/fleet/update-slot`` route stays for older apps and grants every holder.

The native app owns the timing (grace period, install, resume); it renews the lease while it waits.
"""
from __future__ import annotations

import time
from typing import Any

LEASE_S = 120.0
_drain_until = 0.0


def draining() -> bool:
    return time.monotonic() < _drain_until


def _set_draining(want: bool) -> None:
    global _drain_until
    _drain_until = time.monotonic() + LEASE_S if want else 0.0


def slot(holder: str, want: bool) -> bool:
    """Grant an older app's group-slot request. There is no shared slot any more - every Mac drains
    and installs on its own - so two holders are always granted and `holder`/`want` are unused."""
    return True


async def request(want: bool, controller: tuple[str, dict[str, str]] | None = None) -> dict[str, Any]:
    """This Mac's answer to the app: ``draining`` (it now refuses new work) or ``resumed``.

    `controller` is accepted from older callers and ignored: a node drains here without asking its
    controller, so a slow or unreachable one cannot hold its update back."""
    _set_draining(want)
    return {"state": "draining" if want else "resumed"}
