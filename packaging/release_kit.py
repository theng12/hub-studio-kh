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
import subprocess
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def git_commit() -> str | None:
    """The commit building this kit, or None when it is not built from a checkout.

    Never fatal: the manifest describes an artifact that must remain valid even when built from
    an exported tree with no git metadata at all.
    """
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and len(commit) == 40 else None


def tree_sha256(root: Path) -> str:
    """The same digest the staging tool recorded, so the two can actually be compared.

    This used to hash only regular files and their contents, while `stage_voice.tree_digest`
    also folds in file modes and symlink targets. The two never matched on an intact bundle,
    which made the integrity field worthless: a tampered app and a pristine one both looked
    like a mismatch.
    """
    import sys

    packaging = str(PROJECT_ROOT / "packaging")
    if packaging not in sys.path:
        sys.path.insert(0, packaging)
    from stage_voice import tree_digest

    return tree_digest(root)


def folder_bytes(root: Path) -> tuple[int, int]:
    files = [path for path in root.rglob("*") if path.is_file()]
    return len(files), sum(path.stat().st_size for path in files)


def build(app: Path, library: Path) -> dict:
    resources = app / "Contents" / "Resources"
    release = json.loads((resources / "release-manifest.json").read_text())
    index_path = library / ".studiohub" / "installed.json"
    installed = json.loads(index_path.read_text()) if index_path.is_file() else {}

    problems: list[str] = []
    not_shipped: list[str] = []
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
            # Expected: a kit may carry a subset of the catalogue. The app still offers
            # the rest as downloads, so this is recorded rather than treated as a defect.
            not_shipped.append(package_id)
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
        # The commit that produced the *distribution* files - the launchers, this manifest and the
        # copy helper. The app itself may have been built earlier and left byte-identical, so
        # without this the kit's own revision is not recorded anywhere on the drive.
        "kit_commit": git_commit(),
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
        "models_not_shipped": not_shipped,
        "library": {
            "path_as_shipped": library.name,
            "installed_bytes": bytes_total,
            "installed_bytes_human": f"{bytes_total / 1024**3:.1f} GiB",
            "index": str(index_path.relative_to(library)),
        },
        "operator_steps": [
            "Double-click '1 - Install the app.command' in the kit folder.",
            "Double-click '2 - Copy the models.command' - it copies the models this Mac "
            "can run into ~/StudioHubModels.",
            "Open Studio Hub from Applications; it offers that library as 'Use the Models Already "
            "on This Mac'.",
            "Allow the local-network and incoming-connection prompts.",
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
    recorded = manifest["app"]["recorded_tree_sha256"]
    recomputed = manifest["app"]["tree_sha256_recomputed"]
    if not recorded:
        manifest["problems"].append(
            "the staged app has no .app.tree-sha256 beside it, so its integrity cannot be checked")
    elif recorded != recomputed:
        manifest["problems"].append(
            f"the app does not match the hash recorded at staging time "
            f"(recorded {recorded}, recomputed {recomputed})")
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    print(json.dumps({key: manifest[key] for key in
                      ("version", "implementation_commit", "library", "problems")}, indent=1))
    return 0 if not manifest["problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
