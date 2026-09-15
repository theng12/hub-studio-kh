"""Idle model-release policy for the standalone Subtitle worker.

The adopted Voice worker keeps one model resident and releases it when idle
(``components/voice/app/backend/memory_policy.py``). The Subtitle worker had
``engine.unload()`` but nothing called it, so a Whisper model could sit in unified memory
for the lifetime of the process — the leak that matters most on an 8 GB machine.

This mirrors the Voice policy: one resident model, an idle timer, an operator-chosen mode
persisted per machine, and a hard rule that **active work is never preempted**.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

# Same modes and labels as the Voice worker so one per-device choice means the same thing
# across the app.
MODES = {
    'performance': {'idle_seconds': None, 'label': 'Performance'},
    'balanced': {'idle_seconds': 600, 'label': 'Balanced'},
    'memory_saver': {'idle_seconds': 120, 'label': 'Memory Saver'},
    'immediate': {'idle_seconds': 0, 'label': 'Immediate'},
}
CHECK_INTERVAL_SECONDS = 5

_LOCK = threading.RLock()
_MANAGER = None
_LAST_ACTIVITY: float | None = None
_LAST_RELEASE: dict | None = None
_RELEASE_COUNT = 0
_STARTED = False


def settings_file() -> Path:
    data = (os.environ.get('STUDIOHUB_SUBTITLE_DATA_DIR')
            or os.environ.get('STUDIOHUB_DATA_DIR'))
    return Path(data or '.') / 'memory_policy.json'


def machine_bytes() -> int:
    try:
        return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
    except (ValueError, OSError, AttributeError):
        return 0


def default_mode() -> str:
    """Mode used when the operator has not chosen one.

    Small machines must not hold a model between jobs; large ones can afford the reload
    saving. An explicit operator choice always wins.
    """
    total = machine_bytes()
    if total and total < 16 * 1024**3:
        return 'immediate'
    return 'balanced'


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
    return {
        'mode': mode,
        'idle_seconds': MODES[mode]['idle_seconds'],
        'modes': {key: value['label'] for key, value in MODES.items()},
        'default_mode': default_mode(),
        'machine_bytes': machine_bytes(),
        'resident': _resident(),
        'release_count': _RELEASE_COUNT,
        'last_release': _LAST_RELEASE,
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
    global _LAST_RELEASE, _RELEASE_COUNT
    with _LOCK:
        if _active():
            raise RuntimeError('subtitle work is active; memory was not released')
        released = False
        if _MANAGER is not None:
            released = bool(_MANAGER.engine.unload(reason).get('released'))
        _RELEASE_COUNT += 1
        _LAST_RELEASE = {'at': time.time(), 'reason': reason, 'released': released}
        return {'released': released, 'reason': reason}


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
            idle_seconds = MODES[_mode()]['idle_seconds']
            if idle_seconds is None or not _resident() or _active():
                continue
            last = _idle_since()
            if last is None or (time.time() - last) < idle_seconds:
                continue
            release_now('idle')
        except Exception:
            # A policy failure must never take down the worker.
            continue


def start_background(manager) -> None:
    global _MANAGER, _STARTED, _LAST_ACTIVITY
    with _LOCK:
        _MANAGER = manager
        if _LAST_ACTIVITY is None:
            _LAST_ACTIVITY = time.time()
        if _STARTED:
            return
        _STARTED = True
    threading.Thread(target=_loop, daemon=True, name='subtitle-memory-policy').start()
