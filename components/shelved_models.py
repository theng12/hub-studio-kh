"""The one list of shelved models (beside ``retired_models.py``).

A shelved model is still in the catalog - offered, and usable wherever its files are - but a Mac does
not carry it by default:

* the kit's copy step (``qualification/copy_models_for_this_mac.py``) skips it, so it stays on the
  SSD (in the kit's model library, which that step reads) and is never copied to a fleet Mac;
* at start-up the Hub moves an installed one into its Trash Folder (``trash/<UTC date>/shelved-models/``,
  purged after 15 days), so a fleet Mac sheds it;
* the Hub refuses to download or import one onto such a Mac, so nothing brings it back by itself.

A Mac marked **keep all models** (``"keep_all_models": true`` in the Hub's ``device_settings.json``,
default false) is exempt from all three (so is a Mac whose settings file is damaged: see ``keep_all_state``): it copies, keeps and downloads shelved models like any other.

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
    "chatterbox-4bit", "mlx-community/chatterbox-4bit",          # Chatterbox 4-bit (disqualified 2026-10-01)
    "chatterbox-8bit", "mlx-community/chatterbox-8bit",          # Chatterbox 8-bit (disqualified 2026-10-01)
    "s3tokenizerv2", "mlx-community/S3TokenizerV2",              # Chatterbox's companion; nothing else requires it
    "gemma-3-12b-it-4bit", "mlx-community/gemma-3-12b-it-4bit",  # Gemma 3 12B 4-bit (owner 2026-10-02; kept for a possible chat model)
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


def keep_all_state(settings_file: str | os.PathLike[str]) -> tuple[bool, str | None]:
    """(keep_all, problem) for a Mac's settings file.

    Fails safe: **a file that exists but cannot be read, is not JSON, is not an object or holds a
    non-boolean ``keep_all_models`` reads as keep-all**, with the problem named, because the
    alternative - reading damage as "false" - moves a keep-all Mac's shelved models into the Trash
    Folder at the next start. Only an absent file, or a file that does not mention the key, is the
    default (false).
    """
    try:
        text = Path(settings_file).read_text(encoding="utf-8")
    except FileNotFoundError:
        return False, None
    except OSError as exc:
        return True, f"the settings file could not be read ({exc})"
    try:
        value = json.loads(text)
    except ValueError:
        return True, "the settings file is not valid JSON"
    if not isinstance(value, dict):
        return True, "the settings file is not a JSON object"
    if "keep_all_models" not in value:
        return False, None
    if isinstance(value["keep_all_models"], bool):
        return value["keep_all_models"], None
    return True, "keep_all_models in the settings file is neither true nor false"


def keep_all_models(settings_file: str | os.PathLike[str]) -> bool:
    """Is this Mac marked keep-all? See `keep_all_state` for what a damaged file means."""
    return keep_all_state(settings_file)[0]


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
    moved, shelved_ids = [], []
    for manifest in manifests:
        package_id = str(manifest.get("package_id") or "")
        if package_id and manifest_shelved(manifest) and Path(package_id).name == package_id:
            shelved_ids.append(package_id)
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
    from components import retired_models       # lazy: the kit's copy script imports this file by bare name
    retired_models.forget_receipts(library, shelved_ids)
    return moved
