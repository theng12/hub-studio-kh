"""The one list of models a Mac in workstation mode runs.

A workstation is a shop's cashier Mac (8 GB, Chrome, Telegram, a PDF viewer) that lends its idle
time to the fleet. It carries and runs only the 8 GB-class lanes the owner chose (2026-10-03), so a
job for any other model is refused at its door with a plain reason and the controller sends it to
another Mac. The kit's "2 - Copy the Lite Mode models" copies exactly these packages (~15 GB).

Adding a model to the workstation lanes is one line here: its package id and the repo id its jobs
name it by.
"""
from __future__ import annotations

WORKSTATION_MODELS = [
    "flux2-klein-4b-mlx-4bit", "AITRADER/FLUX2-klein-4B-mlx-4bit",                 # FLUX.2 Klein 4B 4-bit
    "z-image-turbo-mflux-4bit", "filipstrand/Z-Image-Turbo-mflux-4bit",           # Z-Image-Turbo 4-bit
    "qwen3-tts-12hz-0-6b-base-4bit", "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit",  # Qwen3-TTS 0.6B 4-bit
    "voxcpm2-4bit", "mlx-community/VoxCPM2-4bit",                                 # VoxCPM2 4-bit
    "whisper-turbo", "mlx-community/whisper-large-v3-turbo",                      # Whisper large-v3 turbo
    "stable-audio-3-medium", "stabilityai/stable-audio-3-optimized",              # Stable Audio 3 Medium
]


def is_workstation_model(*names: object) -> bool:
    """True when any of these names (package id, model id, alias, repo) is a workstation lane."""
    allowed = {item.lower() for item in WORKSTATION_MODELS}
    return any(isinstance(name, str) and name.strip().lower() in allowed for name in names)


def manifest_allowed(manifest: object, stem: str | None = None) -> bool:
    """True when a manifest (or its file name) names a workstation model."""
    if not isinstance(manifest, dict):
        return is_workstation_model(stem)
    return is_workstation_model(stem, manifest.get("package_id"), manifest.get("model_id"),
                                *(manifest.get("aliases") or []))
