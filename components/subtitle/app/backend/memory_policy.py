"""Idle model-release policy for the standalone Subtitle worker.

The adopted Voice worker keeps one model resident and releases it when idle
(``components/voice/app/backend/memory_policy.py``). The Subtitle worker had
``engine.unload()`` but nothing called it, so a Whisper model could sit in unified memory
for the lifetime of the process — the leak that matters most on an 8 GB machine.

This mirrors the Voice policy: one resident model, an idle timer, an operator-chosen mode
persisted per machine, and a hard rule that **active work is never preempted**.

The status payload deliberately publishes the same field names the Voice worker uses
(``loaded_models``, ``model_idle_seconds``, ``active_work``, ``busy``, ``next_release_at``,
``last_release_*``, ``release_count``, ``counters_since``). The Hub's fleet controller
(:mod:`components.hub.backend.memory_control`) reads every Studio through one shape, and it
decides whether a release actually freed anything by reading ``loaded_model``/
``loaded_models``/``loaded_pipeline`` back out of the response. A Studio that publishes a
different vocabulary is silently counted as released while still holding its model.

The mode table and the ``auto`` rule come from :mod:`components.memory_modes`, the one
place they are written down. ``auto`` is the fresh-install default and this module is where
the machine's answer was first spelled out — it used to be ``default_mode()``; it is now
:func:`machine_mode`, published as ``machine_mode``/``resolved_mode`` beside the selected
``mode`` so a reader never has to re-derive it.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from components import memory_modes

# Same modes, labels and thresholds as every other Studio, from the one table they share, so
# one per-device choice means the same thing across the app and a sixth mode cannot exist in
# one worker and not another.
MODES = memory_modes.MODES
# `auto`: `balanced` at 16 GiB of unified memory and above, `immediate` below it. Unchanged
# behaviour for an operator who has already chosen — this is only what a machine that has
# never chosen now selects.
DEFAULT_MODE = memory_modes.DEFAULT_MODE
CHECK_INTERVAL_SECONDS = 5

# Provenance for every counter below. Each starts at zero on process start, while job
# history survives on disk, so `release_count: 0` alone cannot distinguish "the idle thread
# never fired" from "it fired, then the service was restarted". Publishing the process start
# these counters are measured from lets a remote reader tell the two apart without SSH.
_PROCESS_STARTED_AT = time.time()

_LOCK = threading.RLock()
_MANAGER = None
_STATE_DIR: Path | None = None
_LAST_ACTIVITY: float | None = None
_LAST_RELEASE_AT: float | None = None
_LAST_RELEASE_REASON: str | None = None
_LAST_RELEASE_DETAILS: dict | None = None
_LAST_ERROR: str | None = None
_RELEASE_COUNT = 0
_STARTED = False


def settings_file() -> Path:
    """Where the operator's choice is stored.

    The worker's own state directory wins, so the saved mode sits beside the job ledger and
    artifacts rather than beside whatever directory the process happened to start in.
    """
    if _STATE_DIR is not None:
        return _STATE_DIR / 'memory_policy.json'
    data = (os.environ.get('STUDIOHUB_SUBTITLE_DATA_DIR')
            or os.environ.get('STUDIOHUB_DATA_DIR'))
    return Path(data or '.') / 'memory_policy.json'


def machine_bytes() -> int:
    """This Mac's unified memory in bytes, through the one reader every Studio shares."""
    return memory_modes.machine_bytes()


def machine_mode() -> str:
    """What ``auto`` resolves to on this Mac.

    Small machines must not hold a model between jobs; large ones can afford the reload
    saving. This is the rule that used to live in :func:`default_mode`, and it is what
    ``auto`` answers with. A Mac whose memory cannot be measured resolves to ``immediate``:
    a machine that cannot prove it has room must not hold a model.
    """
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


def default_mode() -> str:
    """The mode selected when the operator has not chosen one: ``auto``.

    An explicit operator choice always wins; this is only what a machine that has never
    chosen selects.
    """
    return DEFAULT_MODE


def _mode() -> str:
    try:
        raw = json.loads(settings_file().read_text(encoding='utf-8'))
        mode = raw.get('mode') if isinstance(raw, dict) else None
    except (OSError, ValueError):
        mode = None
    return mode if mode in MODES else default_mode()


def _resident() -> bool:
    return bool(_MANAGER is not None
                and getattr(_MANAGER.engine, '_model', None) is not None)


def _loaded_models() -> list[list[str]]:
    """Resident models in the Hub fleet controller's vocabulary.

    ``loaded_models`` is the field the controller reads back after a release to check that
    the model actually went away. Reporting residency only as a private boolean would let a
    failed unload look like a successful release.
    """
    package = getattr(_MANAGER.engine, 'loaded_package', None) if _MANAGER is not None else None
    return [[str(package)]] if package else []


def _active() -> bool:
    if _MANAGER is None:
        return False
    try:
        return any(job.get('state') == 'running' for job in _MANAGER.list())
    except Exception:
        # Unknown activity must never cause a release.
        return True


def note_activity() -> None:
    """Mark the worker busy; called when work is accepted or finished."""
    global _LAST_ACTIVITY
    with _LOCK:
        _LAST_ACTIVITY = time.time()


def status() -> dict:
    mode = _mode()
    machine = machine_mode()
    resolved = memory_modes.resolved_mode(mode, machine)
    threshold = MODES[resolved]['idle_seconds']
    loaded = _loaded_models()
    active = _active()
    last = _idle_since()
    idle = max(0.0, time.time() - last) if loaded and last is not None else None
    due_at = None
    if threshold is not None and idle is not None:
        due_at = time.time() + max(0, threshold - idle)
    with _LOCK:
        release_count = _RELEASE_COUNT
        last_release_at = _LAST_RELEASE_AT
        last_release_reason = _LAST_RELEASE_REASON
        last_release_details = _LAST_RELEASE_DETAILS
        last_error = _LAST_ERROR
    return {
        'mode': mode,
        # The resolution, not just the name: `mode` may be "auto", `resolved_mode` is what
        # is actually in force here, and `idle_seconds` is its threshold — never None for
        # auto, which a UI would otherwise render as "keeps the model loaded until you
        # release it".
        'resolved_mode': resolved,
        'default_mode': default_mode(),
        'idle_seconds': threshold,
        'machine_mode': machine,
        'machine_bytes': machine_bytes(),
        # Both spellings are published on purpose: `options` is the fleet contract the Hub
        # and the native Settings screen read, `modes` is the older label map. Each option
        # carries its own resolved threshold and an actionable sentence.
        'options': memory_modes.options(machine),
        'modes': {key: value['label'] for key, value in MODES.items()},
        'loaded_models': loaded,
        'model_idle_seconds': idle,
        'active_work': active,
        'busy': active,
        'next_release_at': due_at,
        'last_release_at': last_release_at,
        'last_release_reason': last_release_reason,
        'last_release_details': last_release_details,
        'last_error': last_error,
        'release_count': release_count,
        'counters_since': _PROCESS_STARTED_AT,
        # Kept for the Subtitle screen and the pre-existing policy tests.
        'resident': bool(loaded),
        'machine_bytes': machine_bytes(),
    }


def save(mode: str) -> dict:
    if mode not in MODES:
        raise ValueError(f'unknown memory mode {mode!r}')
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps({'mode': mode}), encoding='utf-8')
    os.replace(temporary, path)
    return status()


def release_now(reason: str = 'manual') -> dict:
    global _LAST_RELEASE_AT, _LAST_RELEASE_REASON, _LAST_RELEASE_DETAILS
    global _LAST_ERROR, _RELEASE_COUNT
    with _LOCK:
        if _active():
            raise RuntimeError('subtitle work is active; memory was not released')
        released_models = [row[0] for row in _loaded_models()]
        released = False
        error = None
        try:
            if _MANAGER is not None:
                released = bool(_MANAGER.engine.unload(reason).get('released'))
        except Exception as exc:
            # A failed unload must be visible in the payload rather than reported as a
            # successful release; the residency below still shows what stayed loaded.
            error = f'{type(exc).__name__}: {exc}'
        _RELEASE_COUNT += 1
        _LAST_RELEASE_AT = time.time()
        _LAST_RELEASE_REASON = reason
        _LAST_RELEASE_DETAILS = {'released': released, 'reason': reason,
                                 'models': released_models}
        _LAST_ERROR = error
        details = dict(_LAST_RELEASE_DETAILS)
        count = _RELEASE_COUNT
    return {**status(), 'released': released, 'reason': reason, 'error': error,
            'release_count': count, **details}


def _latest_job_time() -> float | None:
    """Newest finish/update timestamp across this worker's jobs.

    Deriving activity from the job records means the idle timer starts when work actually
    ends, without every endpoint having to remember to report it.
    """
    if _MANAGER is None:
        return None
    newest = None
    try:
        for job in _MANAGER.list():
            stamp = job.get('finished_at') or job.get('updated_at') or job.get('created_at')
            if isinstance(stamp, (int, float)) and (newest is None or stamp > newest):
                newest = float(stamp)
    except Exception:
        return None
    return newest


def _idle_since() -> float | None:
    candidates = [value for value in (_LAST_ACTIVITY, _latest_job_time()) if value is not None]
    return max(candidates) if candidates else None


def _loop() -> None:
    while True:
        time.sleep(CHECK_INTERVAL_SECONDS)
        try:
            mode = _mode()
            # The threshold actually in force: `auto` answers with this Mac's mode, so the
            # timer idles out exactly where `status()` says it will.
            threshold = MODES[resolved_mode(mode)]['idle_seconds']
            if threshold is None or not _resident() or _active():
                continue
            last = _idle_since()
            if last is None or (time.time() - last) < threshold:
                continue
            release_now('idle')
        except Exception:
            # A policy failure must never take down the worker.
            continue


def start_background(manager) -> None:
    global _MANAGER, _STARTED, _LAST_ACTIVITY, _STATE_DIR
    with _LOCK:
        _MANAGER = manager
        state = getattr(manager, 'data', None)
        if state is not None and _STATE_DIR is None:
            _STATE_DIR = Path(state)
        if _LAST_ACTIVITY is None:
            _LAST_ACTIVITY = time.time()
        if _STARTED:
            return
        _STARTED = True
    threading.Thread(target=_loop, daemon=True, name='subtitle-memory-policy').start()
