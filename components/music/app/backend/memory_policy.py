"""Idle model-release policy for the standalone Music worker.

Music is the last Studio that keeps a model resident in a long-lived child and had no way
to let go of it.  ``generation.EngineExecutor`` is documented in its own words as *"One
spawned engine child, reused until a cancel forces a restart"*: the ACE-Step 1.5 Turbo
2B DiT plus its 0.6B planner stay in unified memory between jobs, and until this module
existed the only thing that ever stopped that child was a cancel or the worker exiting.
On a Mac where three to five Studios share unified memory, that residency is exactly the
memory the next Studio's job needs.

This mirrors the Voice worker's policy (``components/voice/app/backend/memory_policy.py``)
and the Subtitle worker's, under the same fleet contract the Hub's
:class:`~components.hub.backend.memory_control.FleetMemoryControl` reads from every
Studio: ``GET /api/memory-policy``, ``PUT /api/memory-policy``,
``POST /api/memory/release``.  The status payload deliberately uses the same field names
Voice publishes (``loaded_models``, ``model_idle_seconds``, ``active_work``, ``busy``,
``next_release_at``, ``last_release_*``, ``release_count``, ``counters_since``), because
the controller decides whether a release freed anything by reading ``loaded_models`` back
out of the response.  A Studio that publishes a different vocabulary is silently counted
as released while still holding its model.

Two things are Music's own and are stated here rather than left implicit:

* **Releasing means discarding the engine child through the existing
  ``EngineExecutor._discard()`` path.**  There is no second lifecycle mechanism and no
  model unload that leaves a half-initialised child behind: the next submit's
  ``_ensure_child`` spawns a fresh child that loads the model again.  Stable Audio 3 has
  nothing to release at all - it runs a fresh MLX subprocess per job and exits - so only
  an ACE-Step child is ever resident here.
* **The release is never reported unless it happened.**  A discard whose child exit was
  not verified leaves the child resident and is reported as not released, with
  ``loaded_models`` still naming it, so the Hub classifies the release as ``still_loaded``
  instead of telling the operator memory was freed.

Active work is never preempted: ``release()`` refuses while a job is queued or running,
and the idle timer asks the same question before it fires.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

# The same modes, ids, labels and thresholds as the Voice worker, so one per-device choice
# means the same thing in Settings on every Studio row. `tests/test_memory_policy_contract.py`
# asserts this table equals Voice's entry for entry, instead of trusting a copy.
MODES = {
    "performance": {"idle_seconds": None, "label": "Performance"},
    "balanced": {"idle_seconds": 600, "label": "Balanced"},
    "memory_saver": {"idle_seconds": 120, "label": "Memory Saver"},
    "immediate": {"idle_seconds": 0, "label": "Immediate"},
}
# "performance" pins a loaded model in unified memory forever. That is a fine default for
# an app that owns its machine - and a bad one for the deployment that actually exists,
# where 3-5 Studios share a single 8 GB Mac and none of them knows the others are resident.
# Each Studio ships this same skeleton with this same default, so every one of them
# independently concluded that holding its model forever was free.
#
# Measured on the fleet 2026-08-07: 16 of 19 machines sat below the memory guard's 3.2 GB
# floor with 1.5-4.4 GB of swap burned and could not start a job at all. The idle-release
# thread was running the whole time - it just had nothing to do, because idle_seconds is
# None in this mode.
#
# A five-machine production pressure run on 2026-08-09 showed that waiting even two minutes
# is too long for a Mac that must switch between sibling Studios. Image throughput was
# unchanged, while the following Voice job on an M4 16 GB fell from 52.7 s to 4.0 s once
# the prior Studio released immediately. An operator's explicit choice, persisted in
# memory_policy.json, always wins.
#
# Music keeps that default, and the cost is real enough to state in the same place: the
# catalogue's own words for ACE-Step 1.5 Turbo are "First generation on a cold worker pays
# a one-time ~3 minute model load" (components/music/app/backend/catalog.py, family
# "ace-step-1.5"), and the repository's own warm-timing evidence measured three consecutive
# 30 s renders at 106.25 s / 32.93 s / 42.76 s
# (qualification/evidence/r07-2026-09-21/ace-step-warm-timing.json) - so on that host the
# first job carried roughly 63-73 s of load that the next two did not. `immediate` therefore
# costs one model load per job, while `balanced` (600 s) would pay it once for a session of
# takes. That is a trade the owner should decide, not this module: the shipped default stays
# identical to every other Studio, and the recommendation (Balanced on the 16 GB and larger
# machines, Immediate on the 8 GB tier) is recorded in the task report rather than silently
# applied here.
DEFAULT_MODE = "immediate"


def default_mode() -> str:
    """Mode to use when the operator has not chosen one."""
    return DEFAULT_MODE


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
        print(f"[memory] released Music engine memory ({reason}): {details}", flush=True)
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
    under the inference lock.
    """
    current = time.time() if now is None else float(now)
    with _LOCK:
        mode = _mode()
        threshold = MODES[mode]["idle_seconds"]
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
        threshold = MODES[mode]["idle_seconds"]
        loaded = _loaded_models()
        activity = _idle_since()
        idle = max(0.0, time.time() - activity) if loaded and activity is not None else None
        due_at = None
        if threshold is not None and idle is not None:
            due_at = time.time() + max(0, threshold - idle)
        active = _active()
        return {
            "mode": mode,
            "default_mode": default_mode(),
            "idle_seconds": threshold,
            # The four modes and their labels, in the order every Studio publishes them.
            "options": [{"mode": key, **value} for key, value in MODES.items()],
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
