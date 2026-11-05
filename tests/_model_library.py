"""Resolve shipped checkpoints without escaping an explicitly configured test library."""
from __future__ import annotations

import os
from pathlib import Path


def shipped_model_library() -> Path:
    configured = os.environ.get('STUDIOHUB_MODELS_DIR')
    return Path(configured) if configured else Path.home() / 'StudioHubModels'
