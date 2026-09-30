"""The one list of retired models.

Retiring a model is a one-line change: add its package id (and, if jobs name it differently, its
model id) to ``RETIRED_MODELS``. The Hub catalog, the model list published to the gateway, the
kit / SSD model listing and staging all read this list, so a retired model is never offered,
never staged into a kit, and refused by name - even if an old manifest for it turns up on a drive.
"""
from __future__ import annotations

RETIRED_MODELS = [
    "ace-step-1-5-turbo", "ACE-Step/Ace-Step1.5",   # ACE-Step 1.5, retired 0.6.116
    "lance-3b-video-bf16", "mlx-community/Lance-3B-Video-bf16",   # Lance 3B Video bf16, retired 0.6.120
    "lance-3b-processor-qwen2-5-vl", "Qwen/Qwen2.5-VL-3B-Instruct",   # its processor companion
]


def is_retired(*names: object) -> bool:
    """True when any of these names (package id, model id, alias, repo) is retired."""
    retired = {item.lower() for item in RETIRED_MODELS}
    return any(isinstance(name, str) and name.strip().lower() in retired for name in names)


def manifest_retired(manifest: object, stem: str | None = None) -> bool:
    """True when a manifest (or its file name) names a retired model."""
    if not isinstance(manifest, dict):
        return is_retired(stem)
    return is_retired(stem, manifest.get("package_id"), manifest.get("model_id"),
                      *(manifest.get("aliases") or []))
