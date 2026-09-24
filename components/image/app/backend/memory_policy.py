"""Memory policy for the standalone Image worker.

Image is the one Studio with nothing to configure. FLUX is constructed inside
:meth:`GenerationManager._run` and dropped when that call returns, so there is no
cross-job residency and no idle timer that could ever fire — the model is released after
every job by construction.

It still publishes the fleet contract (``GET /api/memory-policy``,
``PUT /api/memory-policy``, ``POST /api/memory/release``) that the Hub's
:class:`~components.hub.backend.memory_control.FleetMemoryControl` reads from every Studio.
Without it the Hub's per-machine memory inventory reported Image as *update required*, and
the machine-wide release pass counted it as a failed release, which is what a user would
see in Settings and in a fleet handoff receipt.

The policy is honest about the one thing it does own: while a generation is running the
model **is** resident, and a release must be refused rather than reporting success.
"""
from __future__ import annotations

import time

from .generation import MODEL_ID

MODES = {
    'immediate': {'idle_seconds': 0, 'label': 'After every job'},
}
# Why only one mode is offered: every other Studio's choice is *when* to drop a model it
# would otherwise keep. Image has no such choice to make, so offering Performance/Balanced
# here would be a control that does nothing. `fixed` tells the app to explain rather than
# render a picker.
FIXED = True

_PROCESS_STARTED_AT = time.time()


def machine_bytes() -> int:
    """This Mac's physical memory, through the worker's one implementation."""
    from .generation import host_memory_bytes
    return host_memory_bytes()


def status(manager) -> dict:
    """Residency right now, derived from the jobs the manager is actually running."""
    running = [job for job in manager.list() if job.get('state') == 'running']
    # The resident model is the one the running job asked for; a job receipt with no recorded
    # params (an older shape) falls back to the worker's default model.
    loaded = [[(job.get('params') or {}).get('repo') or MODEL_ID] for job in running[:1]]
    return {
        'mode': 'immediate',
        'default_mode': 'immediate',
        'idle_seconds': 0,
        'options': [{'mode': key, **value} for key, value in MODES.items()],
        'loaded_models': loaded,
        'model_idle_seconds': None,
        'active_work': bool(running),
        'busy': bool(running),
        'next_release_at': None,
        'last_release_at': None,
        'last_release_reason': None,
        'last_release_details': None,
        'last_error': None,
        'release_count': 0,
        'counters_since': _PROCESS_STARTED_AT,
        'resident': bool(loaded),
        'machine_bytes': machine_bytes(),
        # Not part of the shared shape: what the app reads to explain why there is no picker.
        'fixed': FIXED,
        'release_policy': 'The model is released when each job finishes.',
    }


def release(manager) -> dict:
    """Never preempt a running generation; otherwise there is nothing to release."""
    if any(job.get('state') == 'running' for job in manager.list()):
        raise RuntimeError('image generation is active; memory was not released')
    return {**status(manager), 'released': False, 'reason': 'manual', 'error': None,
            'models': []}
