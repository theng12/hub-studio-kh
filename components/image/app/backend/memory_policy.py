"""Memory policy for the standalone Image worker: how long a loaded model stays loaded.

Image used to be a one-mode ("fixed") exception: mflux was built inside each job and dropped when
it returned, so there was nothing to configure.  That is still the right answer on a small Mac and
a wasteful one on a large one - measured on a 32 GB M2 Max, a freshly loaded FLUX.2 Klein 4-bit
spends 15 s of a ~100 s image reading its weights from disk, and Z-Image-Turbo 4-bit about 9 s - so
Image now answers the same five-mode contract every other resident Studio does, from the one shared
table (:mod:`components.memory_modes`): ``auto`` is ``balanced`` (600 s idle) from 16 GiB and
``immediate`` below it, so an 8 GB Mac behaves exactly as before.

What can stay resident is decided by the job's own memory plan (``generation.memory_plan``):

* the bounded (staged) configuration frees its components as the job goes and so can never serve a
  second job: it is released after every job whatever the mode says (``resident_ok`` is False);
* the default and lean configurations keep the whole model, and keep it for the mode's idle window.

A resident model is dropped when (1) the idle window passes, (2) the Hub asks every Studio to release
before another needs the RAM (``POST /api/memory/release``), (3) macOS reports memory pressure while
it sits idle, or (4) the next job needs another model or another configuration.  A release never
preempts a running generation.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from components import memory_modes

# The shared table and rule, imported - never restated (see `tests/test_memory_policy_contract.py`).
MODES = memory_modes.MODES
DEFAULT_MODE = memory_modes.DEFAULT_MODE
# Image has a real residency choice now; the Hub renders a picker for it like for Voice or Music.
FIXED = False
CHECK_INTERVAL_SECONDS = 5

_PROCESS_STARTED_AT = time.time()
_LOCK = threading.RLock()
_MANAGER = None
_LAST_RELEASE_AT: float | None = None
_LAST_RELEASE_REASON: str | None = None
_LAST_RELEASE_DETAILS: dict | None = None
_LAST_ERROR: str | None = None
_RELEASE_COUNT = 0
_STARTED = False


def settings_file() -> Path:
    """Where the operator's choice is stored: beside the job ledger, in the worker's data directory."""
    data = getattr(_MANAGER, 'data', None) or os.environ.get('STUDIOHUB_IMAGE_DATA_DIR') or '.'
    return Path(data) / 'memory_policy.json'


def machine_bytes() -> int:
    """This Mac's unified memory: the manager's own reading, so the policy and the memory plan agree."""
    total = getattr(_MANAGER, 'total_memory_bytes', None)
    return int(total) if total else memory_modes.machine_bytes()


def machine_mode() -> str:
    """What ``auto`` resolves to here: ``balanced`` from 16 GiB, ``immediate`` below (or unknown)."""
    return memory_modes.machine_mode(machine_bytes())


def resolved_mode(mode: str) -> str:
    return memory_modes.resolved_mode(mode, machine_mode())


def idle_seconds(mode: str) -> int | None:
    """The idle window ``mode`` runs with; never ``None`` for ``auto``."""
    return memory_modes.idle_seconds(mode, machine_mode())


def options() -> list[dict]:
    return memory_modes.options(machine_mode())


def default_mode() -> str:
    return DEFAULT_MODE


def _stored_mode(path: Path) -> str:
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
        mode = raw.get('mode') if isinstance(raw, dict) else None
    except (OSError, ValueError):
        mode = None
    return mode if mode in MODES else default_mode()


def _mode() -> str:
    """The operator's choice, ``auto`` when nothing (or nothing valid) is stored."""
    return _stored_mode(settings_file())


def keep_resident(manager) -> bool:
    """Whether a model that *can* stay loaded should, after the job that just finished.

    Reads *this* manager's own data directory and memory, not the module's: the module serves the
    one manager the worker runs, but a job must never be decided by another manager's setting.
    """
    mode = _stored_mode(Path(manager.data) / 'memory_policy.json')
    machine = memory_modes.machine_mode(manager.total_memory_bytes)
    return memory_modes.idle_seconds(mode, machine) != 0


def _jobs() -> list[dict]:
    return _MANAGER.list() if _MANAGER is not None else []


def _busy() -> bool:
    try:
        return any(job.get('state') == 'running' for job in _jobs())
    except Exception:  # noqa: BLE001 - unknown activity must never cause a release
        return True


def _default_model() -> str:
    from .generation import MODEL_ID
    return MODEL_ID


def status(manager=None) -> dict:
    """The fleet contract. ``manager`` is accepted for the older call shape; the module's own wins."""
    source = _MANAGER if _MANAGER is not None else manager
    mode = _mode()
    machine = machine_mode()
    resolved = memory_modes.resolved_mode(mode, machine)
    threshold = MODES[resolved]['idle_seconds']
    resident = source.resident_models() if source is not None else []
    jobs = source.list() if source is not None else []
    running = [job for job in jobs if job.get('state') == 'running']
    # While a job runs its model is resident even though it is not (yet) kept: the Hub reads this
    # to decide whether a release freed anything, and must not be told "nothing loaded" mid-job.
    loaded = ([[(job.get('params') or {}).get('repo') or _default_model()] for job in running[:1]]
              or [[repo] for repo in resident])
    idle = source.resident_idle_seconds() if source is not None else None
    due_at = (time.time() + max(0.0, threshold - idle)
              if threshold is not None and idle is not None else None)
    with _LOCK:
        count, at, reason, details, error = (_RELEASE_COUNT, _LAST_RELEASE_AT, _LAST_RELEASE_REASON,
                                             _LAST_RELEASE_DETAILS, _LAST_ERROR)
    return {
        'mode': mode,
        'resolved_mode': resolved,
        'default_mode': default_mode(),
        'idle_seconds': threshold,
        'machine_mode': machine,
        'machine_bytes': machine_bytes(),
        'options': memory_modes.options(machine),
        'modes': {key: value['label'] for key, value in MODES.items()},
        'loaded_models': loaded,
        'model_idle_seconds': idle,
        'active_work': bool(running),
        'busy': bool(running),
        'next_release_at': due_at,
        'last_release_at': at,
        'last_release_reason': reason,
        'last_release_details': details,
        'last_error': error,
        'release_count': count,
        'counters_since': _PROCESS_STARTED_AT,
        'resident': bool(loaded),
        # Not part of the shared shape: what the app reads to explain a model that cannot stay loaded.
        'fixed': FIXED,
        'release_policy': ('A model that keeps every component loaded stays resident for the idle '
                           'window of the selected mode; the low-memory configuration is released '
                           'as each job finishes.'),
    }


def save(mode: str) -> dict:
    if mode not in MODES:
        raise ValueError(f'unknown memory mode {mode!r}')
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps({'mode': mode}), encoding='utf-8')
    os.replace(temporary, path)
    if idle_seconds(mode) == 0:
        # A mode that keeps nothing between jobs applies now, not after the next one.
        release_now('mode', raise_if_busy=False)
    return status()


def release_now(reason: str = 'manual', raise_if_busy: bool = True) -> dict:
    """Drop the resident model. Never preempts a running generation (``RuntimeError``)."""
    global _LAST_RELEASE_AT, _LAST_RELEASE_REASON, _LAST_RELEASE_DETAILS, _LAST_ERROR, _RELEASE_COUNT
    error = None
    models: list[str] = []
    try:
        if _MANAGER is not None:
            models = _MANAGER.release_resident(reason)
    except RuntimeError:
        if raise_if_busy:
            raise
        return {**status(), 'released': False, 'reason': reason, 'error': None, 'models': []}
    except Exception as exc:  # noqa: BLE001 - a failed release is reported, not hidden
        error = f'{type(exc).__name__}: {exc}'
    with _LOCK:
        _RELEASE_COUNT += 1
        _LAST_RELEASE_AT, _LAST_RELEASE_REASON = time.time(), reason
        _LAST_RELEASE_DETAILS = {'released': bool(models), 'reason': reason, 'models': models}
        _LAST_ERROR = error
        details, count = dict(_LAST_RELEASE_DETAILS), _RELEASE_COUNT
    return {**status(), 'released': bool(models), 'reason': reason, 'error': error,
            'release_count': count, **details}


def _pressure_is_high() -> bool:
    from .generation import _under_memory_pressure
    return _under_memory_pressure()


def _loop() -> None:
    while True:
        time.sleep(CHECK_INTERVAL_SECONDS)
        try:
            idle = _MANAGER.resident_idle_seconds() if _MANAGER is not None else None
            if idle is None or _busy():
                continue
            threshold = MODES[resolved_mode(_mode())]['idle_seconds']
            if threshold is not None and idle >= threshold:
                release_now('idle', raise_if_busy=False)
            elif _pressure_is_high():
                release_now('memory-pressure', raise_if_busy=False)
        except Exception:  # noqa: BLE001 - a policy failure must never take down the worker
            continue


def start_background(manager) -> None:
    global _MANAGER, _STARTED
    with _LOCK:
        _MANAGER = manager
        if _STARTED:
            return
        _STARTED = True
    threading.Thread(target=_loop, daemon=True, name='image-memory-policy').start()
