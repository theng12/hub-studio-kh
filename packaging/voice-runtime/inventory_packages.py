#!/usr/bin/env python3
"""Write installed Python distribution and declared-license metadata."""

from __future__ import annotations

from importlib import metadata
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._+-]+", "-", value).strip("-") or "unknown"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _license_source(distribution: metadata.Distribution, declared: str) -> Path | None:
    declared_path = Path(declared)
    if declared_path.is_absolute() or ".." in declared_path.parts:
        return None
    metadata_root = Path(distribution._path)
    candidates = (
        metadata_root / declared,
        metadata_root / "licenses" / declared,
        Path(distribution.locate_file(declared)),
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def main() -> int:
    output = Path(sys.argv[1])
    licenses_root = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    records = []
    for distribution in metadata.distributions():
        package = distribution.metadata
        name = package.get("Name") or distribution.name
        declared_license_files = package.get_all("License-File") or []
        copied_license_files = []
        if licenses_root is not None:
            destination_root = licenses_root / (
                f"{_safe_component(name)}-{_safe_component(distribution.version)}"
            )
            for declared in declared_license_files:
                source = _license_source(distribution, declared)
                if source is None:
                    copied_license_files.append(
                        {"declared": declared, "status": "missing-from-installation"}
                    )
                    continue
                safe_parts = [
                    _safe_component(part)
                    for part in Path(declared).parts
                    if part not in ("", ".", "..", "/")
                ]
                destination = destination_root.joinpath(*safe_parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                copied_license_files.append(
                    {
                        "declared": declared,
                        "path": destination.relative_to(licenses_root.parent).as_posix(),
                        "sha256": _sha256(destination),
                        "status": "copied",
                    }
                )
        direct_url_path = Path(distribution._path) / "direct_url.json"
        direct_url = None
        if direct_url_path.is_file():
            direct_url = json.loads(direct_url_path.read_text(encoding="utf-8"))
        records.append(
            {
                "name": name,
                "version": distribution.version,
                "license_expression": package.get("License-Expression"),
                "license": package.get("License"),
                "license_files": declared_license_files,
                "copied_license_files": copied_license_files,
                "license_classifiers": [
                    value.removeprefix("License :: ")
                    for value in (package.get_all("Classifier") or [])
                    if value.startswith("License :: ")
                ],
                "home_page": package.get("Home-page") or package.get("Project-URL"),
                "direct_url": direct_url,
            }
        )
    records.sort(key=lambda record: record["name"].lower())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {"schema": "studiohub.python-distributions.v1", "distributions": records},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
