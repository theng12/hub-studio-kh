"""The one list of retired models.

Retiring a model is a one-line change: add its package id (and, if jobs name it differently, its
model id) to ``RETIRED_MODELS``. The Hub catalog, the model list published to the gateway, the
kit / SSD model listing and staging all read this list, so a retired model is never offered,
never staged into a kit, and refused by name - even if an old manifest for it turns up on a drive.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

RETIRED_MODELS = [
    "ace-step-1-5-turbo", "ACE-Step/Ace-Step1.5",   # ACE-Step 1.5, retired 0.6.116
    "lance-3b-video-bf16", "mlx-community/Lance-3B-Video-bf16",   # Lance 3B Video bf16, retired 0.6.120
    "lance-3b-processor-qwen2-5-vl", "Qwen/Qwen2.5-VL-3B-Instruct",   # its processor companion
    # LTX-2.3 22B video, retired 0.6.127. Its Gemma text encoder (`gemma-3-12b-it-4bit`) is NOT retired:
    # the owner may reuse it as a chat model, so it stays in the catalog.
    "ltx-2-3-22b-ram-12gb-mlx", "baa-ai/LTX-2.3-22B-RAM-12GB-MLX",
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


def retire_installed(library: str | os.PathLike[str], trash_root: str | os.PathLike[str],
                     now: float | None = None) -> list[str]:
    """Move each installed retired package's folder into the Trash Folder; return the package ids moved.

    The Trash Folder is the Hub's self-cleaning one (``<data dir>/trash/<UTC date>/``, purged after 15
    days). Nothing is deleted: a same-volume rename, and one that fails is skipped (tried again at the
    next start). Only entries that are a plain package id (no ``/``) name a folder in the library; a
    repo id is skipped. Called at Hub start-up, so a Mac sheds a model the day the app that retires it
    is installed instead of carrying 17 GB of weights nothing offers.
    """
    when = time.gmtime(time.time() if now is None else float(now))
    moved = []
    for package_id in RETIRED_MODELS:
        folder = Path(library) / package_id
        if ("/" in package_id or "\\" in package_id or Path(package_id).name != package_id
                or folder.is_symlink() or not folder.is_dir()):
            continue
        target = (Path(trash_root) / time.strftime("%Y-%m-%d", when) / "retired-models"
                  / f"{package_id}-{time.strftime('%H%M%S', when)}")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(folder, target)           # same volume or it fails; never a delete
        except OSError:
            continue
        moved.append(package_id)
    return moved
