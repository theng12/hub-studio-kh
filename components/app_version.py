"""The release version a running worker was staged from.

`GET /api/health` answers with this, and the Hub republishes it verbatim as
`workers[].studio_version` (`components/hub/backend/monitor.py` → `capabilities.py`). It is the one
field an operator reads to answer *"which build is that Mac running?"*, so a value that is not a
version is worse than an empty one: it reads as data.

Four of the five packaged workers answered the literal string `"standalone"` there — Image, Music,
Subtitle and Chat — while only Voice read a real version. Measured by Gateway Studio: three of four
workers reported `studio_version: "standalone"`, which is indistinguishable from a version that
failed to load and makes every Mac look like the same build.

The version comes from the **release manifest the bundle was staged with**, never from a copied
upstream `VERSION` file: a vendored component's own version is provenance, not this application's
identity. Outside a bundle — a development run from the checkout — the repository's own `VERSION`
is reported and flagged as a development build, so the number is still true and still labelled.
Where neither exists the answer is `"unknown"`, which cannot be mistaken for a version.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

SCHEMA = "studiohub.standalone-release"
SCHEMA_VERSION = 1
_VERSION = re.compile(r"\d+\.\d+\.\d+")

# `components/app_version.py` -> `components/` -> the resource root of a staged bundle, which is
# also the repository root in a checkout. One level, so it is right in both layouts.
RESOURCE_ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT_VARIABLE = "STUDIOHUB_RELEASE_MANIFEST"


def _valid(value) -> dict | None:
    """A release manifest this application can trust, or ``None``.

    The same shape the Hub's own worker validates, so a worker and the Hub that spawned it never
    disagree about which build is running.
    """
    if (
        not isinstance(value, dict)
        or value.get("schema") != SCHEMA
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != SCHEMA_VERSION
        or not _VERSION.fullmatch(str(value.get("version") or ""))
    ):
        return None
    return value


def release_manifest() -> dict | None:
    """The staged release manifest, or ``None`` when this is not a packaged run."""
    candidates = []
    configured = os.environ.get(ENVIRONMENT_VARIABLE)
    if configured:
        candidates.append(Path(configured))
    candidates.append(RESOURCE_ROOT / "release-manifest.json")
    for path in candidates:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        manifest = _valid(value)
        if manifest is not None:
            return manifest
    return None


def _development_version() -> str | None:
    """The checkout's own version, for a run that is not a staged bundle."""
    try:
        value = (RESOURCE_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value if _VERSION.fullmatch(value) else None


def release_version() -> str:
    """The version an operator should see: the staged release, else the checkout, else `unknown`.

    `unknown` is deliberate and is not a placeholder to be filled in later. It says "this process
    was not started from a release bundle", which is exactly what a reader needs to know before
    trusting any other version-shaped answer.
    """
    manifest = release_manifest()
    if manifest is not None:
        return str(manifest["version"])
    return _development_version() or "unknown"


def release_commit() -> str:
    """The implementation commit of the staged release, or `""` when unknown."""
    manifest = release_manifest()
    commit = str((manifest or {}).get("implementation_commit") or "")
    return commit if re.fullmatch(r"[0-9a-f]{40}", commit) else ""


def development_build() -> bool:
    """True when this process is a checkout run rather than a staged release."""
    manifest = release_manifest()
    if manifest is not None:
        return bool(manifest.get("development_build"))
    return _development_version() is not None


def version_fields() -> dict:
    """The three fields every worker's `/api/health` should answer with."""
    return {
        "app_version": release_version(),
        "app_commit": release_commit(),
        "development_build": development_build(),
    }
