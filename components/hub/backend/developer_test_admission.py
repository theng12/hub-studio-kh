"""Atomic physical-machine admission for Developer Mode tests.

The frozen contract (``studio.developer-test.v1``, candidate 2) requires one test at a time to
hold the same physical machine lease production uses, and to start only when production is not
queued, running or reserved there. This module owns that decision; it deliberately reuses
``broker``'s lease vocabulary (``_external_machine_leases`` / ``acquire_external_machine``)
instead of inventing a second one.

The runner calls ``reserve`` immediately before any child spawn or install callback, and
``release`` only after the exact owned process tree is verified gone. Production paths call
nothing: ``broker.acquire_external_machine`` sees the test lease and refuses, and the queue
gates call ``production_arrived`` so the runner's cancel event is set before its next stage.

No developer-test state means every check here is a no-op: production behaves exactly as before.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

_lock = threading.RLock()
_active: dict[str, Any] | None = None
# Set only when the persisted ledger exists but cannot be read: production stays off this Mac
# rather than assuming the test's resources were released.
_recovery_unknown = False


class AdmissionRefused(Exception):
    """A test may not start. ``code`` is the contract reason code."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def owner_for(attempt_id: str) -> str:
    """The broker lease owner for one test attempt (same shape as ``render:``/``chat:``)."""
    return f"developer-test:{attempt_id}"


def _setting() -> tuple[bool, str | None]:
    """(allowed, refusal code) from the persisted Developer Mode setting.

    Uses the released reader (``_load_setting()``, no arguments); absence is the verified OFF
    default the Hub creates at start, so it reads as ``paused``, never as unverified.
    """
    from .. import developer_tests
    try:
        path = developer_tests._setting_path()
        if path is None or not path.exists():
            return False, "paused"          # absent means OFF
        record, error = developer_tests._load_setting()
    except Exception:
        logging.getLogger("studiohub.developer_test").exception(
            "could not read the Developer Mode setting")
        return False, "unverified"
    if error is not None or not isinstance(record, dict):
        return False, "unverified"
    if not record.get("verified"):
        return False, "unverified"
    if not record.get("enabled"):
        return False, "paused"
    return True, None


def _machine_paused(machine: str) -> bool:
    """Whether the owner's existing controls say this Mac takes no image work right now.

    The test runner performs image generation, so it obeys the same switches production does:
    the Mac's enabled flag, its image function switch, Lite Mode standing by/yielded/quiet
    hours, and the image Studio rows (only when at least one is registered - a node with no
    Studio row has no owner switch to read, so absent stays ON as everywhere else). Unknown
    authority refuses.
    """
    from . import broker, peers, registry
    try:
        if not registry.machine_enabled(machine):
            return True
        if not registry.function_enabled(machine, "image"):
            return True
        if peers.standing_by(machine):
            return True
        studios = [studio for studio in broker._monitor().registry
                   if studio.get("modality") == "image"
                   and studio.get("machine", "local") == machine]
        if studios and not any(registry.studio_enabled(machine, studio["id"])
                               for studio in studios):
            return True
    except Exception:
        return True   # unknown authority is not permission
    return False


def _queued_production(machine: str) -> bool:
    """Whether any production queue has work that could run on ``machine`` now.

    Conservative for Chat and transcription (their queues do not name a machine until dispatch),
    exact for broker batches through the same eligibility the dispatcher uses.
    """
    from . import broker, chat_jobs, transcription_jobs
    from . import routing as routing_rule
    for batch in list(broker.batches.values()):
        if batch.get("cancelled"):
            continue
        if not any(item.get("state") == "queued" for item in batch.get("items") or []):
            continue
        try:
            studios = broker._eligible_studios(
                str(batch.get("modality") or ""),
                batch.get("routing") or routing_rule.POOL,
                batch.get("model"))
        except Exception:
            return True
        if any(studio.get("machine", "local") == machine for studio in studios):
            return True
    for batch in list(chat_jobs.batches.values()):
        if not batch.get("cancelled") and any(
                pack.get("state") == "queued" for pack in batch.get("packs") or []):
            return True
    for batch in list(transcription_jobs.batches.values()):
        if not batch.get("cancelled") and any(
                item.get("state") == "queued" for item in batch.get("items") or []):
            return True
    return False


def _production_pending(machine: str) -> bool:
    from . import broker
    if broker.update_drain.draining():
        return True
    if machine in broker.busy_machines() or machine in broker.preparing_machines():
        return True
    executor = broker._render_executor
    if executor is not None and executor.pending_for_machine(machine):
        return True
    return _queued_production(machine)


def reserve(attempt_id: str, *, machine: str = "local", resource_safe: bool = True) -> str:
    """Atomically claim ``machine`` for one test attempt; return the broker owner.

    ``resource_safe`` is the runner's own pre-spawn memory check: unknown resource safety must
    refuse, never start. Raises :class:`AdmissionRefused` with one of ``paused``, ``unverified``,
    ``resource_unknown``, ``production_busy``, ``busy`` or ``invalid``.
    """
    global _active
    text = str(attempt_id or "")
    if not text or len(text) > 128:
        raise AdmissionRefused("invalid", "attempt_id is not a usable identity")
    from . import broker
    # Broker lock outside, admission lock inside: busy_machines() takes the broker lock (from
    # acquire) and then reads held_machines(), so the reverse order would deadlock.
    with broker.machine_lease_lock(), _lock:
        if _active is not None:
            if _active["attempt_id"] == text:
                return _active["owner"]          # idempotent replay, never a second claim
            raise AdmissionRefused("busy", "another Developer Mode test holds this Mac")
        allowed, code = _setting()
        if not allowed:
            raise AdmissionRefused(str(code), "Developer Mode is not enabled and verified")
        if _machine_paused(machine):
            raise AdmissionRefused("paused", "this Mac is paused for work")
        if not resource_safe:
            raise AdmissionRefused("resource_unknown",
                                   "resource safety could not be established before starting")
        if _production_pending(machine):
            raise AdmissionRefused("production_busy",
                                   "production is queued, running or reserved on this Mac")
        owner = owner_for(text)
        if not broker.acquire_external_machine(machine, owner):
            raise AdmissionRefused("production_busy",
                                   "the physical machine is reserved for production")
        _active = {
            "attempt_id": text,
            "owner": owner,
            "machine": machine,
            "cancel": threading.Event(),
            "release_unknown": False,
        }
        broker._wakeup.set()
        return owner


def release(attempt_id: str, *, confirmed: bool) -> bool:
    """Release the physical hold. ``confirmed=False`` keeps it and returns False.

    A release that cannot prove the exact process tree is gone retains the lease (and therefore
    production's wait) until a later confirmed release; the ledger owns ``recovery_hold``.
    """
    global _active
    from . import broker
    with broker.machine_lease_lock(), _lock:
        if _active is None or _active["attempt_id"] != str(attempt_id or ""):
            return False
        if not confirmed:
            _active["release_unknown"] = True
            return False
        broker.release_external_machine(_active["machine"], _active["owner"])
        _active = None
        return True


def cancel_event(attempt_id: str) -> threading.Event | None:
    """The event the runner polls between stages; set by production arrival, OFF or pause."""
    with _lock:
        if _active is None or _active["attempt_id"] != str(attempt_id or ""):
            return None
        return _active["cancel"]


def cancel_requested(attempt_id: str) -> bool:
    """Whether the runner must stop before its next stage.

    Live setting/machine state is read every call, so switching Developer Mode off or pausing
    the Mac stops an active test without waiting for another event.
    """
    with _lock:
        if _active is None or _active["attempt_id"] != str(attempt_id or ""):
            return False
        event = _active["cancel"]
        machine = _active["machine"]
    if event.is_set():
        return True
    allowed, _code = _setting()
    return not allowed or _machine_paused(machine)


def stage_allowed(attempt_id: str) -> bool:
    """Inverse of :func:`cancel_requested`, for a runner that prefers a positive check."""
    return not cancel_requested(attempt_id)


def production_arrived(machine: str) -> None:
    """Production is queued for ``machine``: stop the active test before its next stage."""
    from . import broker
    with _lock:
        if _active is not None and _active["machine"] == machine:
            _active["cancel"].set()
            broker._wakeup.set()


def active_attempt() -> str | None:
    with _lock:
        return None if _active is None else _active["attempt_id"]


def release_unknown(attempt_id: str) -> bool:
    """Whether this attempt's last release could not be proved (status stays recovery_hold)."""
    with _lock:
        return bool(_active is not None and _active["attempt_id"] == str(attempt_id or "")
                    and _active["release_unknown"])


def held(machine: str) -> str | None:
    """The owner holding ``machine`` for a test, or None."""
    with _lock:
        if _active is not None and _active["machine"] == machine:
            return _active["owner"]
        return None


def held_machines() -> set[str]:
    """Machines a test holds; also the conservative block after an unreadable recovery ledger."""
    with _lock:
        if _active is not None:
            return {_active["machine"]}
    if _recovery_unknown:
        return {"local"}
    return set()


def restore_before_production() -> list[str]:
    """Restore a test hold after restart, before production queues are restored.

    Reads the same durable attempt ledger the runner persists; ``installing``, ``running`` and
    ``recovery_hold`` rows may still own a child process. The owner string is deterministic, so
    no second durable store is introduced. Called from ``broker.restore_batches``.
    """
    global _active, _recovery_unknown
    from .. import developer_tests
    from . import broker
    try:
        # No provisioned root or no ledger file means no test ever ran here; that is not a
        # corrupt ledger and must not keep this Mac out of production.
        path = developer_tests._ledger_path()
        if path is None or not path.exists():
            return []
        ledger = developer_tests._load_ledger()
        rows = [row for row in (ledger.get("attempts") or {}).values()
                if isinstance(row, dict)
                and row.get("state") in {"installing", "running", "recovery_hold"}
                and row.get("accepted")]
    except Exception:
        logging.getLogger("studiohub.developer_test").exception(
            "Developer Mode ledger unreadable after restart; keeping this Mac out of production")
        with _lock:
            _recovery_unknown = True
        return []
    restored: list[str] = []
    rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    for row in rows:
        attempt_id = str(row.get("attempt_id") or "")
        if not attempt_id:
            continue
        owner = owner_for(attempt_id)
        try:
            broker.restore_external_machine("local", owner)
        except RuntimeError:
            logging.getLogger("studiohub.developer_test").error(
                "could not restore Developer Mode hold for %s: production already holds this Mac",
                attempt_id)
            continue
        with _lock:
            _active = {"attempt_id": attempt_id, "owner": owner, "machine": "local",
                       "cancel": threading.Event(), "release_unknown": True}
        restored.append(attempt_id)
        break   # one test at a time; any other non-terminal row is stale and stays loud
    for row in rows[len(restored):]:
        logging.getLogger("studiohub.developer_test").error(
            "stale non-terminal Developer Mode attempt %s was not restored",
            row.get("attempt_id"))
    return restored


def _reset() -> None:
    """Test-only: forget in-memory state (the broker lease is cleared by the test)."""
    global _active, _recovery_unknown
    with _lock:
        _active = None
        _recovery_unknown = False
