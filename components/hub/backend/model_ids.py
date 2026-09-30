"""One canonical model identity shared by every Hub route.

A worker catalogue row's ``repo`` and a job batch's stored ``model`` are usually
the same string, but a packaged model may be named either by its manifest
``model_id`` (e.g. ``stabilityai/stable-audio-3-optimized``) or by its ``package_id`` (e.g.
``stable-audio-3-medium``).  Every route that carries a model publishes the
manifest's ``model_id`` under the key ``model_id`` so a peer can join rows
across routes on one string.

A name with no matching manifest is returned unchanged.  Absence is never
turned into a different value: if there is no name to resolve, the row simply
does not gain a ``model_id``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


# Cached (signature, index, manifests).  The signature is the manifest
# directory's mtime and file list, so adding or removing a manifest - which is
# how a packaged model appears or disappears - is noticed without re-reading
# every file on each call.
_CACHE: tuple[tuple, dict[str, str], dict[str, dict]] | None = None


def manifest_root() -> Path:
    value = os.environ.get("STUDIOHUB_MODEL_MANIFESTS", "").strip()
    if value:
        return Path(value).expanduser().resolve()
    return Path(__file__).resolve().parents[3] / "model-manifests"


def _signature(root: Path) -> tuple:
    try:
        info = root.stat()
        names = tuple(sorted(path.name for path in root.glob("*.json")))
    except OSError:
        return (str(root), None, ())
    return (str(root), info.st_mtime_ns, names)


def _load() -> tuple[dict[str, str], dict[str, dict]]:
    global _CACHE
    root = manifest_root()
    signature = _signature(root)
    if _CACHE is not None and _CACHE[0] == signature:
        return _CACHE[1], _CACHE[2]

    index: dict[str, str] = {}
    manifests: dict[str, dict] = {}
    for name in signature[2]:
        try:
            manifest = json.loads((root / name).read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        model_id = manifest.get("model_id")
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        model_id = model_id.strip()
        manifests.setdefault(model_id, manifest)
        for key in (model_id, manifest.get("package_id")):
            if isinstance(key, str) and key.strip():
                index.setdefault(key.strip().lower(), model_id)

    _CACHE = (signature, index, manifests)
    return index, manifests


def load_index() -> dict[str, str]:
    """A lowercase identifier (model_id or package_id) -> canonical model_id map."""
    return _load()[0]


def load_manifests() -> dict[str, dict]:
    """Canonical model_id -> manifest for every readable manifest."""
    return _load()[1]


def canonical(value: object, index: dict[str, str] | None = None) -> str:
    """Resolve ``value`` to its manifest ``model_id``, or return it unchanged."""
    text = str(value or "").strip()
    if not text:
        return text
    table = load_index() if index is None else index
    return table.get(text.lower(), text)


def annotate(payload: dict) -> dict:
    """Return a catalogue payload with ``model_id`` on every model row."""
    rows = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return payload
    index = load_index()
    annotated: list = []
    for row in rows:
        if not isinstance(row, dict):
            annotated.append(row)
            continue
        name = row.get("repo") or row.get("model_id")
        if not isinstance(name, str) or not name.strip():
            annotated.append(dict(row))
            continue
        # model_id: the manifest's stable identifier, so /api/hub/models and
        # /api/hub/jobs can be joined on one string.  The worker's own repo key
        # is preserved unchanged.
        annotated.append({**row, "model_id": canonical(name, index)})
    return {**payload, "models": annotated}
