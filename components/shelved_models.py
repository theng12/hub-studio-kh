"""The one list of shelved models (beside ``retired_models.py``).

A shelved model is still in the catalog - offered, and usable wherever its files are - but a Mac does
not carry it by default:

* the kit's copy step (``qualification/copy_models_for_this_mac.py``) skips it, so it stays on the
  SSD (in the kit's model library, which that step reads) and is never copied to a fleet Mac;
* at start-up the Hub moves an installed one into its Trash Folder (``trash/<UTC date>/shelved-models/``,
  purged after 15 days), so a fleet Mac sheds it;
* the Hub refuses to download or import one onto such a Mac, so nothing brings it back by itself.

A Mac marked **keep all models** (``"keep_all_models": true`` in the Hub's ``device_settings.json``,
default false) is exempt from all three: it copies, keeps and downloads shelved models like any other.

Shelving a model is one line here: its package id (and the repo id its jobs name it by).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

SHELVED_MODELS = [
    "whisper-base", "mlx-community/whisper-base-mlx",            # Whisper base
    "whisper-small", "mlx-community/whisper-small-mlx",          # Whisper small
    "voxcpm2-bf16", "mlx-community/VoxCPM2-bf16",                # VoxCPM2 bf16
    "fish-audio-s2-pro-bf16", "mlx-community/fish-audio-s2-pro-bf16",   # Fish S2 Pro bf16
]


def is_shelved(*names: object) -> bool:
    """True when any of these names (package id, model id, alias, repo) is shelved."""
    shelved = {item.lower() for item in SHELVED_MODELS}
    return any(isinstance(name, str) and name.strip().lower() in shelved for name in names)


def manifest_shelved(manifest: object, stem: str | None = None) -> bool:
    """True when a manifest (or its file name) names a shelved model."""
    if not isinstance(manifest, dict):
        return is_shelved(stem)
    return is_shelved(stem, manifest.get("package_id"), manifest.get("model_id"),
                      *(manifest.get("aliases") or []))


def keep_all_models(settings_file: str | os.PathLike[str]) -> bool:
    """Is this Mac marked keep-all? Only a literal ``true`` in the settings file says so."""
    try:
        return json.loads(Path(settings_file).read_text(encoding="utf-8")).get("keep_all_models") is True
    except (OSError, ValueError, AttributeError):
        return False


def shelve_installed(library: str | os.PathLike[str], manifests, trash_root: str | os.PathLike[str],
                     keep_all: bool, now: float | None = None) -> list[str]:
    """Move each installed shelved package's folder into the Trash Folder; return the package ids moved.

    Nothing is deleted: a same-volume rename, and one that fails is skipped (tried again at the next
    start). The receipt in ``installed.json`` is left alone - with the folder gone the Hub already
    reads the package as "not installed". A keep-all Mac moves nothing.
    """
    if keep_all:
        return []
    when = time.gmtime(time.time() if now is None else float(now))
    moved = []
    for manifest in manifests:
        package_id = str(manifest.get("package_id") or "")
        folder = Path(library) / package_id
        if (not package_id or not manifest_shelved(manifest) or Path(package_id).name != package_id
                or folder.is_symlink() or not folder.is_dir()):
            continue
        target = Path(trash_root) / time.strftime("%Y-%m-%d", when) / "shelved-models" / f"{package_id}-{time.strftime('%H%M%S', when)}"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(folder, target)           # same volume or it fails; never a delete
        except OSError:
            continue
        moved.append(package_id)
    return moved
