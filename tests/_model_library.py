"""Where a test looks for shipped model checkpoints.

An explicit ``STUDIOHUB_MODELS_DIR`` wins: the release gate points it at an empty temp library,
so a fixture that reads the operator's own library anyway would run the shipped-model tests
against real checkpoints there (R7: one ERROR at import instead of the gate's skip). With
nothing configured the operator's library is still read in place, read-only, as before.
"""
from __future__ import annotations

import os
from pathlib import Path


def shipped_model_library() -> Path:
    configured = os.environ.get('STUDIOHUB_MODELS_DIR')
    return Path(configured) if configured else Path.home() / 'StudioHubModels'
