"""The Image worker's per-machine loading-size setting: ``auto``, ``low`` or ``full``.

This is deliberately **not** ``/api/memory-policy``. That route is the fleet's *release*
policy - when a resident model is dropped (``memory_policy``). This module is the other axis:
which mflux memory *configuration* the next job loads with, on this Mac.

It is also not the per-model generation defaults (``model_defaults``,
``GET/PUT/DELETE /api/model-defaults``), the third thing a job is shaped by. Those are the numbers
the request asks the model *for* - resolution, aspect ratio, steps, guidance, seed - stored per
model; this is which memory configuration loads to answer it, chosen per machine. The two are
stored in two files and neither changes the other: setting ``steps`` for Z-Image-Turbo must not
load a different configuration, and forcing low-RAM must not change a number in the request.

Nothing about a model lives here. Whether a model has a bounded configuration at all is the
model's own ``generation.MemoryPolicy``, and what this setting does to each model is
published per model in ``/api/catalog``'s ``loading_modes`` (derived in
:func:`generation.loading_modes`). A model that declares no bounded configuration loads the
one way it always has however this setting is set, and its catalog entry says so rather than
offering a control the lane would discard.

The choice is persisted beside the worker's own job ledger (``loading_mode.json`` in the Image
data directory) the way the Music worker persists ``memory_policy.json``: written to a
``.tmp`` sibling and moved into place with ``os.replace``. An unknown value is refused by
name on the way in; on the way out an unrecognised stored value falls back to ``auto`` -
today's behaviour - rather than to a configuration nobody asked for.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# The three values the setting may hold, in the order every surface publishes them, with the
# operator-facing label and the meaning that holds for *every* model. The per-model trade is
# not here: it is built from each model's own measurements in `generation.loading_modes`,
# because a number a model did not state must never be published.
MODE_AUTO = 'auto'
MODE_LOW = 'low'
MODE_FULL = 'full'
MODES = {
    MODE_AUTO: {
        'label': 'Auto',
        'detail': ("Each model decides from this Mac's unified memory: its bounded low-RAM "
                   "configuration below the model's own threshold, its default configuration "
                   "at or above it."),
    },
    MODE_LOW: {
        'label': 'Low RAM',
        'detail': ("Force the bounded configuration on every model that declares one. A model "
                   "that declares none has a single loading mode: it loads unchanged, and its "
                   "catalog entry says this setting does not apply."),
    },
    MODE_FULL: {
        'label': 'Full',
        'detail': ("Force each model's default configuration whatever this Mac's memory is. A "
                   "default measured on a larger Mac may exceed a smaller one's memory; the "
                   "job's own recorded mode shows which configuration ran."),
    },
}
DEFAULT_MODE = MODE_AUTO
SETTINGS_NAME = 'loading_mode.json'


def settings_file(data) -> Path:
    """Where this machine's choice is stored: the worker's own data directory.

    ``data`` is the ``GenerationManager``'s state directory, so the setting sits beside the
    job ledger rather than beside whatever directory the process happened to start in.
    """
    return Path(data) / SETTINGS_NAME


def current(data) -> str:
    """The operator's choice, falling back to ``auto`` for anything unreadable or unknown.

    A stored value this worker does not recognise must never resolve to a configuration
    nobody asked for: an older or hand-edited file falls back to today's behaviour instead.
    """
    try:
        raw = json.loads(settings_file(data).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return DEFAULT_MODE
    mode = raw.get('mode') if isinstance(raw, dict) else None
    return mode if mode in MODES else DEFAULT_MODE


def save(data, mode: object) -> str:
    """Persist one operator choice, refusing an unknown mode by name."""
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError(
            f'unknown memory mode {mode!r}; must be one of: {", ".join(MODES)}')
    path = settings_file(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + '.tmp')
    partial.write_text(json.dumps({'mode': mode}, indent=2) + '\n', encoding='utf-8')
    os.replace(partial, path)
    return mode


def status(data, machine_bytes: int, models=()) -> dict:
    """The machine-wide setting, and what it resolves to for each model on this Mac.

    ``models`` is an iterable of ``(repo, loading_modes_row)`` as
    :func:`generation.loading_modes` publishes them, so the per-model half of this payload is a
    projection of the same derivation the catalog publishes rather than a second answer.
    """
    rows = []
    for repo, row in models:
        rows.append({
            'repo': repo,
            'supported': row['supported'],
            'selected': row['selected'],
            'effective': row['effective'],
            'threshold_bytes': row['threshold_bytes'],
            'reason': row['reason'],
        })
    return {
        'mode': current(data),
        'default_mode': DEFAULT_MODE,
        'options': [{'id': key, **value} for key, value in MODES.items()],
        'machine_bytes': machine_bytes,
        'models': sorted(rows, key=lambda row: row['repo']),
    }
