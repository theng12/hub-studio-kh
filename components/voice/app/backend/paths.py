"""External writable paths for the packaged standalone Voice worker.

The adopted source tree and app bundle are immutable.  Every path returned by
this module is either an explicit Studio Hub location or a read-only bundle
location used for shipped resources.
"""
from __future__ import annotations

import os
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = APP_ROOT.parent
_DEFAULT_DATA_DIR = Path.home() / "Library" / "Application Support" / "StudioHub Standalone" / "voice"


def _configured(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return (Path(raw).expanduser() if raw else default).resolve()


def data_dir() -> Path:
    raw = os.environ.get("STUDIOHUB_DATA_DIR", "").strip()
    # The worker receives the app-wide node-state root; keep each studio's
    # writable files in its own namespace beneath that root.
    return (_configured("STUDIOHUB_DATA_DIR", _DEFAULT_DATA_DIR.parent) / "voice").resolve() if raw else _DEFAULT_DATA_DIR.resolve()


def models_dir() -> Path:
    return _configured("STUDIOHUB_MODELS_DIR", data_dir() / "models")


def tools_dir() -> Path:
    return _configured("STUDIOHUB_TOOLS_DIR", BUNDLE_ROOT / "tools")


def data_path(*parts: str) -> Path:
    return data_dir().joinpath(*parts)


def models_path(*parts: str) -> Path:
    return models_dir().joinpath(*parts)


def release_manifest_path() -> Path | None:
    raw = os.environ.get("STUDIOHUB_RELEASE_MANIFEST", "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def legacy_source_root() -> Path:
    """Read-only source root, retained only for bundle resource resolution."""
    return BUNDLE_ROOT
