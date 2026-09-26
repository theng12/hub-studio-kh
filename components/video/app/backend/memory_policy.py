"""Memory policy for the standalone Video worker — the release half of it.

Video has no idle release policy to configure. A registered model's pipeline is constructed
inside :meth:`GenerationManager._run` and dropped in that call's own ``finally``, so there is
no cross-job residency and no idle timer that could ever fire: the weights are released after
every job by construction. That matters more here than in Image — the 22B pack's measured
process footprint is 16.38 GB, and a resident pipeline would hold a third of a 32 GB Mac
between jobs.

It still publishes the fleet contract (``GET /api/memory-policy``, ``PUT /api/memory-policy``,
``POST /api/memory/release``) that the Hub's ``FleetMemoryControl`` reads from every Studio;
without it the Hub's per-machine memory inventory reports Video as *update required* and the
machine-wide release pass counts it as a failed release.

The policy is honest about the one thing it does own: while a generation is running the
pipeline **is** resident, and a release must be refused rather than reporting success.
"""
from __future__ import annotations

import time

from .generation import MODEL_ID

MODES = {
    'immediate': {'idle_seconds': 0, 'label': 'After every job'},
}
# Why only one mode is offered: every other Studio's choice is *when* to drop a model it would
# otherwise keep. Video has no such choice to make, so offering Performance/Balanced here would
# be a control that does nothing. `fixed` tells the app to explain rather than render a picker.
FIXED = True

_PROCESS_STARTED_AT = time.time()


def machine_bytes() -> int:
    from .generation import host_memory_bytes
    return host_memory_bytes()


def status(manager) -> dict:
    """Residency right now, derived from the jobs the manager is actually running."""
    running = [job for job in manager.list()
               if job.get('state') in ('running', 'cancel_requested')]
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
        'release_policy': 'The pipeline is released when each job finishes.',
    }


def release(manager) -> dict:
    """Never preempt a running generation; otherwise there is nothing to release."""
    if any(job.get('state') in ('running', 'cancel_requested') for job in manager.list()):
        raise RuntimeError('video generation is active; memory was not released')
    return {**status(manager), 'released': False, 'reason': 'manual', 'error': None,
            'models': []}
