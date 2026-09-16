"""Build the offline install-kit manifest for one staged app.

The manifest is the honest description of what is being handed to a machine: the exact app
payload, the runtime identity of every component inside it, every model package the app
advertises, and whether the library being shipped alongside it actually contains that
package at the pinned revision.

It exits non-zero when a package the app advertises is missing or unverified in the
library, so a kit can never be published claiming to be complete when it is not.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                digest.update(block)
        digest.update(b"\0")
    return digest.hexdigest()


def folder_bytes(root: Path) -> tuple[int, int]:
    files = [path for path in root.rglob("*") if path.is_file()]
    return len(files), sum(path.stat().st_size for path in files)


def build(app: Path, library: Path) -> dict:
    resources = app / "Contents" / "Resources"
    release = json.loads((resources / "release-manifest.json").read_text())
    index_path = library / ".studiohub" / "installed.json"
    installed = json.loads(index_path.read_text()) if index_path.is_file() else {}

    problems: list[str] = []
    models = []
    for manifest_path in sorted((resources / "model-manifests").glob("*.json")):
        manifest = json.loads(manifest_path.read_text())
        package_id = manifest["package_id"]
        row = installed.get(package_id)
        files, size = folder_bytes(library / row["folder"]) if row else (0, 0)
        entry = {
            "package_id": package_id,
            "model_id": manifest.get("model_id"),
            "engine": manifest.get("engine"),
            "revision": manifest.get("revision"),
            "minimum_macos": manifest.get("minimum_macos"),
            "manifest_files": len(manifest.get("files") or []),
            "installed": bool(row),
            "installed_revision": (row or {}).get("revision"),
            "installed_folder": (row or {}).get("folder"),
            "installed_files": files,
            "installed_bytes": size,
            "digest": manifest.get("digest") or {},
        }
        if not row:
            problems.append(f"{package_id} is advertised but not installed in the library")
        elif row.get("revision") != manifest.get("revision"):
            problems.append(
                f"{package_id} is installed at {row.get('revision')} but the app pins "
                f"{manifest.get('revision')}")
        models.append(entry)

    if not models:
        problems.append("the staged app advertises no model packages")

    bytes_total = sum(entry["installed_bytes"] for entry in models)
    tree_file = app.with_suffix(".app.tree-sha256")
    payload = hashlib.sha256()
    for path in sorted(p for p in resources.rglob("*") if p.is_file()):
        payload.update(path.relative_to(resources).as_posix().encode())
        payload.update(b"\0")
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                payload.update(block)

    return {
        "schema": "studiohub.standalone-install-kit",
        "schema_version": 1,
        "generated_at": time.time(),
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "implementation_commit": release.get("implementation_commit"),
        "version": release.get("version"),
        "qualification": release.get("qualification"),
        "distribution": {
            "method": "copy the app to the Mac and open it with right-click → Open",
            "signed": False,
            "notarized": False,
            "gatekeeper_note": "Unsigned. The first launch must use right-click → Open; "
                               "a plain double-click is blocked once by macOS.",
        },
        "app": {
            "name": app.name,
            "bytes": sum(path.stat().st_size for path in app.rglob("*") if path.is_file()),
            "resource_payload_sha256": payload.hexdigest(),
            "recorded_tree_sha256": tree_file.read_text().strip() if tree_file.is_file() else None,
        },
        "components": release.get("components") or {},
        "models": models,
        "library": {
            "path_as_shipped": library.name,
            "installed_bytes": bytes_total,
            "installed_bytes_human": f"{bytes_total / 1024**3:.1f} GiB",
            "index": str(index_path.relative_to(library)),
        },
        "operator_steps": [
            "Copy the app and the model library folder to the target Mac.",
            "Right-click the app and choose Open, then confirm.",
            "Choose the copied model library when the app asks for one.",
            "Wait for the status line to read 'Local services ready'.",
            "Run one generation per model to record that machine's speed and memory.",
        ],
        "problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--library", type=Path, default=PROJECT_ROOT / "model-library")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    app = arguments.app.expanduser().resolve()
    if not (app / "Contents" / "Resources" / "release-manifest.json").is_file():
        parser.error(f"{app} is not a staged Studio Hub app")
    manifest = build(app, arguments.library.expanduser().resolve())
    manifest["app"]["tree_sha256_recomputed"] = tree_sha256(app)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    print(json.dumps({key: manifest[key] for key in
                      ("version", "implementation_commit", "library", "problems")}, indent=1))
    return 0 if not manifest["problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
