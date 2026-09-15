#!/usr/bin/env python3
"""Model-free imports required by the selected FLUX.2 Klein mflux worker."""

from __future__ import annotations

from importlib import import_module, metadata
import json
import platform
import sys


for module in (
    "mflux",
    "mlx.core",
    "PIL",
    "numpy",
    "safetensors",
    "transformers",
    "mflux.models.flux2.variants.txt2img.flux2_klein",
    "mflux.models.common.config.model_config",
):
    import_module(module)

print(
    json.dumps(
        {
            "machine": platform.machine(),
            "python": platform.python_version(),
            "packages": {
                name: metadata.version(name)
                for name in ("mflux", "mlx", "mlx-metal", "Pillow", "numpy", "safetensors", "transformers")
            },
            "executable": sys.executable,
        },
        sort_keys=True,
    )
)
