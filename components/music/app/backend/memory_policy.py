"""Idle model-release policy for the standalone Music worker.

Music publishes the same fleet contract every Studio does, so the Hub's
:class:`~components.hub.backend.memory_control.FleetMemoryControl` reads one shape from all of
them: ``GET /api/memory-policy``, ``PUT /api/memory-policy``, ``POST /api/memory/release``.  The
status payload uses the field names Voice publishes (``loaded_models``, ``model_idle_seconds``,
``active_work``, ``busy``, ``next_release_at``, ``last_release_*``, ``release_count``,
``counters_since``), because the controller decides whether a release freed anything by reading
``loaded_models`` back out of the response.

Neither Music engine keeps a model resident: Stable Audio 3 runs a fresh MLX subprocess per job
and YuE2 a bridge per job, and both exit with the job.  ``loaded_models`` is therefore always
empty and a release has nothing to free; the mode is still stored and published so the Hub's
picker behaves the same on every Studio.  Active work is never preempted: ``release()`` refuses
while a job is queued or running, and the idle timer asks the same question before it fires.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from components import memory_modes

# The same modes, ids, labels and thresholds as the Voice worker, from the one table every
# Studio imports (`components/memory_modes.py`): `tests/test_memory_policy_contract.py`
# proves the agreement rather than trusting a copy, and there is no copy to drift.
MODES = memory_modes.MODES
# `auto` is the fresh-install default: `balanced` at 16 GiB of unified memory and above,
# `immediate` below it. The old default was `immediate` everywhere, chosen for a deployment
# where 3-5 Studios share a single 8 GB Mac and none of them knows the others are resident.
#
# Measured on the fleet 2026-08-07: 16 of 19 machines sat below the memory guard's 3.2 GB
# floor with 1.5-4.4 GB of swap burned and could not start a job at all. The idle-release
# thread was running the whole time - it just had nothing to do, because idle_seconds is
# None in the mode that never releases.
#
# A five-machine production pressure run on 2026-08-09 showed that waiting even two minutes
# is too long for a Mac that must switch between sibling Studios: the following Voice job on
# an M4 16 GB fell from 52.7 s to 4.0 s once the prior Studio released immediately. That is
# the 8 GB tier's answer, and `auto` gives it there. `auto` picks per machine, and a machine
# whose memory cannot be measured gets `immediate`: a Mac that cannot prove it has room must
# not hold a model.
#
# An operator's explicit choice, persisted in memory_policy.json, always wins over all of
# this.
DEFAULT_MODE = memory_modes.DEFAULT_MODE


def default_mode() -> str:
    """The mode selected when the operator has not chosen one: ``auto``."""
    return DEFAULT_MODE


def machine_bytes() -> int:
    """This Mac's unified memory in bytes, through the one reader every Studio shares."""
    return memory_modes.machine_bytes()


def machine_mode() -> str:
    """What ``auto`` resolves to on this Mac: ``balanced`` or ``immediate``."""
    return memory_modes.machine_mode(machine_bytes())


def resolved_mode(mode: str) -> str:
    """The mode actually in force, with ``auto`` answered by this machine."""
    return memory_modes.resolved_mode(mode, machine_mode())


def idle_seconds(mode: str) -> int | None:
    """The threshold ``mode`` runs with; never ``None`` for ``auto``."""
    return memory_modes.idle_seconds(mode, machine_mode())


def options() -> list[dict]:
    """The five modes, each with its own resolved threshold and a sentence to act on."""
    return memory_modes.options(machine_mode())


CHECK_INTERVAL_SECONDS = 5

# Where a read lands before the app is built: the same location the packaged worker's
# manager is given by default. Never the process's working directory, so a probe can never
# drop state beside whatever folder the worker happened to start in.
_DEFAULT_DATA_DIR = (Path.home() / "Library" / "Application Support"
                     / "StudioHub Standalone" / "music")

_LOCK = threading.RLock()
_START_LOCK = threading.Lock()
_STARTED = False
_MANAGER = None
_STATE_DIR: Path | None = None
# Every counter below lives in this process and starts at zero on each start. A restart
# therefore erases them while job history survives on disk, so `release_count: 0` alone
# cannot distinguish "the idle-release thread never fired" from "it fired, then the service
# was upgraded an hour ago". status() publishes the process start these counters are
# measured from (`counters_since`).
_PROCESS_STARTED_AT = time.time()
_LAST_ACTIVITY: float | None = None
_LAST_RELEASE_AT: float | None = None
_LAST_RELEASE_REASON: str | None = None
_LAST_RELEASE_DETAILS: dict | None = None
_LAST_ERROR: str | None = None
_RELEASE_COUNT = 0
_RELEASING = False


def settings_file() -> Path:
    """Where the operator's choice is stored.

    The worker's own state directory wins, so the saved mode sits beside the job ledger
    and the artifacts rather than beside whatever directory the process happened to start
    in.  The manager arrives through :func:`start_background`; the environment fallback
    covers a read before the app is built (the packaged worker sets
    ``STUDIOHUB_MUSIC_DATA_DIR`` to exactly that directory).
    """
    if _STATE_DIR is not None:
        return _STATE_DIR / "memory_policy.json"
    data = os.environ.get("STUDIOHUB_MUSIC_DATA_DIR", "").strip()
    base = Path(data).expanduser() if data else _DEFAULT_DATA_DIR
    return base / "memory_policy.json"


def _mode() -> str:
    try:
        raw = json.loads(settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raw = {}
    mode = raw.get("mode") if isinstance(raw, dict) else None
    return mode if mode in MODES else default_mode()


def save(mode: object) -> dict:
    """Persist one operator choice, refusing an unknown mode by name."""
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError(
            f"unknown memory mode {mode!r}; must be one of: {', '.join(MODES)}")
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".tmp")
    partial.write_text(json.dumps({"mode": mode}, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, path)
    return status()


def _active() -> bool:
    """Is a Music job queued or running right now?

    Unknown activity counts as active: a release that cannot prove the worker is idle must
    not fire, because discarding the engine child out from under a running job kills the
    job, not the model.
    """
    if _MANAGER is None:
        return False
    try:
        return bool(_MANAGER.has_active_jobs())
    except Exception:  # noqa: BLE001 - never release on an unreadable activity state
        return True


def _loaded_models() -> list[list[str]]:
    """Resident models in the fleet controller's vocabulary.

    ``loaded_models`` is the field the Hub reads back after a release to check that the
    model actually went away; reporting residency only as a private boolean would let a
    failed discard look like a successful release.
    """
    if _MANAGER is None:
        return []
    return [list(key) for key in _MANAGER.loaded_model_keys()]


def _latest_job_time() -> float | None:
    """Newest finish/progress timestamp across this worker's jobs.

    Deriving activity from the job records means the idle clock starts when work actually
    ended, without every endpoint having to remember to report it.
    """
    if _MANAGER is None:
        return None
    newest: float | None = None
    try:
        jobs = _MANAGER.list()
    except Exception:  # noqa: BLE001 - an unreadable ledger is not evidence of idleness
        return None
    for job in jobs:
        for key in ("finished_at", "last_progress_at", "created_at"):
            stamp = job.get(key)
            if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
                continue
            if newest is None or float(stamp) > newest:
                newest = float(stamp)
    return newest


def _idle_since() -> float | None:
    candidates = [value for value in (_LAST_ACTIVITY, _latest_job_time())
                  if value is not None]
    return max(candidates) if candidates else None


def _release(reason: str) -> dict:
    global _LAST_RELEASE_AT, _LAST_RELEASE_REASON, _LAST_RELEASE_DETAILS
    global _LAST_ERROR, _RELEASE_COUNT, _RELEASING
    with _LOCK:
        if _RELEASING:
            raise RuntimeError("a memory release is already running")
        if _MANAGER is None:
            raise RuntimeError("Music memory policy is not ready")
        if _active():
            raise RuntimeError("music generation is active; memory was not released")
        _RELEASING = True
    try:
        # The same lock a job holds for its whole run. Re-checking under it closes the
        # window between the refusal above and the discard: a job that started in that
        # window makes the release refuse instead of killing it.
        lock = getattr(_MANAGER, "inference_lock", None)
        if lock is not None:
            with lock:
                if _active():
                    raise RuntimeError(
                        "music work started before memory could be released")
                outcome = _MANAGER.release_memory_locked(reason=reason)
        else:
            outcome = _MANAGER.release_memory_locked(reason=reason)
        details = {
            "released": bool(outcome.get("released")),
            "models": [list(model) for model in outcome.get("models") or []],
            "actions": list(outcome.get("actions") or []),
            "reason": reason,
        }
        # An engine child whose exit was not verified is still holding the model. That is
        # not a release, and `loaded_models` below still names it so the Hub reports
        # `still_loaded` rather than success.
        error = None
        if details["models"] and not details["released"]:
            error = "engine child exit was not verified; the model is still resident"
        with _LOCK:
            _LAST_RELEASE_AT = time.time()
            _LAST_RELEASE_REASON = reason
            _LAST_RELEASE_DETAILS = details
            _LAST_ERROR = error
            _RELEASE_COUNT += 1
        print(f"[memory] Music memory release ({reason}): {details}", flush=True)
        return {**status(), **details}
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - report the engine's own text
        with _LOCK:
            _LAST_ERROR = f"{type(exc).__name__}: {exc}"
        raise RuntimeError(f"Music memory release deferred: {exc}") from exc
    finally:
        with _LOCK:
            _RELEASING = False


def release_now() -> dict:
    return _release("manual")


def run_due_release(now: float | None = None) -> dict | None:
    """Release the engine child once the chosen idle window has passed.

    ``None`` means never (Performance).  Nothing here can preempt a job: a queued or
    running job returns before the threshold is even read, and `_release` refuses again
    under the inference lock.  The threshold is the one actually in force, so `auto` idles
    out exactly where `status()` says it will.
    """
    current = time.time() if now is None else float(now)
    with _LOCK:
        mode = _mode()
        threshold = MODES[resolved_mode(mode)]["idle_seconds"]
        loaded = _loaded_models()
        activity = _idle_since()
        if threshold is None or not loaded or _active() or _RELEASING or activity is None:
            return None
        idle = max(0.0, current - activity)
        if idle < threshold:
            return None
        # A child that survived a release (exit unverified) must not make the timer fire
        # on every tick; the same activity timestamp cannot justify a second release.
        if _LAST_RELEASE_AT is not None and _LAST_RELEASE_AT >= activity:
            return None
    return _release(f"idle:{mode}")


def status() -> dict:
    with _LOCK:
        mode = _mode()
        machine = machine_mode()
        resolved = memory_modes.resolved_mode(mode, machine)
        threshold = MODES[resolved]["idle_seconds"]
        loaded = _loaded_models()
        activity = _idle_since()
        idle = max(0.0, time.time() - activity) if loaded and activity is not None else None
        due_at = None
        if threshold is not None and idle is not None:
            due_at = time.time() + max(0, threshold - idle)
        active = _active()
        return {
            "mode": mode,
            # The resolution, not just the name: `mode` may be "auto", `resolved_mode` is
            # what is actually in force here, and `idle_seconds` is its threshold — never
            # None for auto, which a UI would otherwise render as "keeps the model loaded
            # until you release it".
            "resolved_mode": resolved,
            "default_mode": default_mode(),
            "idle_seconds": threshold,
            "machine_mode": machine,
            "machine_bytes": machine_bytes(),
            # The five modes and their labels, in the order every Studio publishes them.
            # Each option carries its own resolved threshold and an actionable sentence.
            "options": memory_modes.options(machine),
            "loaded_models": loaded,
            "model_idle_seconds": idle,
            "active_work": active,
            "busy": bool(active or _RELEASING),
            "next_release_at": due_at,
            "last_release_at": _LAST_RELEASE_AT,
            "last_release_reason": _LAST_RELEASE_REASON,
            "last_release_details": _LAST_RELEASE_DETAILS,
            "last_error": _LAST_ERROR,
            "release_count": _RELEASE_COUNT,
            # The provenance of every counter above: this process started here, and the
            # counters are zero since then rather than for all time.
            "counters_since": _PROCESS_STARTED_AT,
            "resident": bool(loaded),
        }


def start_background(manager) -> None:
    """Bind this policy to one Music manager and start the idle timer once."""
    global _MANAGER, _STATE_DIR, _LAST_ACTIVITY, _STARTED
    with _START_LOCK:
        _MANAGER = manager
        state = getattr(manager, "data", None)
        if state is not None:
            _STATE_DIR = Path(state)
        # Binding a manager starts the idle clock now. The packaged worker calls this once;
        # a test that builds a second app gets its own clock instead of the first one's.
        _LAST_ACTIVITY = time.time()
        if _STARTED:
            return
        _STARTED = True

    def loop() -> None:
        while True:
            time.sleep(CHECK_INTERVAL_SECONDS)
            try:
                run_due_release()
            except Exception as exc:  # noqa: BLE001 - a policy failure never stops the worker
                print(f"[memory] automatic release deferred: {exc}", flush=True)

    threading.Thread(target=loop, name="music-memory-policy", daemon=True).start()
