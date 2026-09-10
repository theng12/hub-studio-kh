#!/usr/bin/env python3
"""Model-free import smoke test for the packaged Voice dependency surface."""

from __future__ import annotations

import importlib
from importlib import metadata
import json
import platform
import site
import sys


MODULES = (
    "fastapi",
    "mlx.core",
    "mlx_audio",
    "pyopenjtalk",
    "torch",
    "torchaudio",
    "transformers",
)


for module in MODULES:
    importlib.import_module(module)

import torch
import torchaudio

probe = torch.tensor([[0.0, 1.0, 0.0, -1.0]], dtype=torch.float32)
resampled = torchaudio.functional.resample(probe, 4, 8)
if tuple(resampled.shape) != (1, 8):
    raise RuntimeError(f"unexpected TorchAudio resample shape: {resampled.shape}")

# pyopenjtalk is source-built for this artifact. Exercise its bundled Japanese
# dictionary so a missing resource or first-run download cannot hide behind a
# successful module import.
import pyopenjtalk

if not pyopenjtalk.g2p("こんにちは").strip():
    raise RuntimeError("pyopenjtalk bundled dictionary returned no phonemes")

payload = {
    "executable": sys.executable,
    "isolated": sys.flags.isolated,
    "dont_write_bytecode": sys.dont_write_bytecode,
    "user_site_enabled": site.ENABLE_USER_SITE,
    "machine": platform.machine(),
    "python": platform.python_version(),
    "packages": {
        name: metadata.version(name)
        for name in (
            "fastapi",
            "mlx",
            "mlx-audio",
            "pyopenjtalk",
            "torch",
            "torchaudio",
            "transformers",
        )
    },
}
print(json.dumps(payload, sort_keys=True))
