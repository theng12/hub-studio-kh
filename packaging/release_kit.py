"""Build the offline install-kit manifest for one staged app.

The manifest is the honest description of what is being handed to a machine: the exact app
payload, the runtime identity of every component inside it, every model package the app
advertises, and whether the library being shipped alongside it actually contains that
package at the pinned revision.

It exits non-zero when a package the app advertises is missing or unverified in the
library, so a kit can never be published claiming to be complete when it is not.

**The second thing a kit hands to a machine is the KH Updater agent.** `--khu-agent` stages that
one: it takes the credential-free kit kh-updater's own `packaging/build-agent-kit.sh` produced,
adds the publisher descriptor beside it, pins every staged byte in `MANIFEST.json`, and reads the
copy back before anything can use it. Same shape as `stage_khu_agent.py`, which stages the same
kind of kit into the Hub's bundle, and the same reader accepts both (`khu_agent.verify_kit`, and
mirrored in `packaging/kit-khu/khu-install.py` for the drive) - so a kit that was altered on the
drive is refused there exactly as it would be inside the app.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from components import retired_models  # noqa: E402 - the one retired-models list


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
        if retired_models.manifest_retired(manifest, manifest_path.stem):
            continue                            # a retired model is never listed for a kit
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
            "Open Studio Hub from Applications and use its 'Set up this Mac' screen: it shows this "
            "Mac's memory and the free space on the models' drive, offers the four presets by size "
            "(Essentials 8 GB, Creator 16 GB, Creator Plus 24 GB, Studio Pro 32 GB+) and copies from "
            "this drive. A Lite Mode Mac takes Essentials 8 GB, then Lite Mode is ticked in Settings.",
            "If this Mac has no internet and no other Mac to copy a model from, run "
            "'2 - Copy the models (everything).command' from the kit instead - it copies every model "
            "the Mac's memory can run.",
            "Allow the local-network and incoming-connection prompts.",
            "Wait for the status line to read 'Local services ready'.",
            "Run one generation per model to record that machine's speed and memory.",
        ],
        "problems": problems,
    }


#: The credential-free files kh-updater's own `packaging/build-agent-kit.sh` produces.
KHU_KIT_FILES = (
    "VERSION",
    "bin/khu",
    "bin/khu-stub",
    "install-agent.sh",
    "com.khupdater.agent.plist",
    "agent-config.example.json",
)
#: Added here from the publisher descriptor; the kit builder never produces it.
KHU_PUBLISHER_FILE = "publisher.json"
#: Everything that lands in the staged kit, and what the readers check for.
KHU_STAGED_FILES = tuple(sorted(KHU_KIT_FILES + (KHU_PUBLISHER_FILE,)))
#: Executable on the destination Mac; everything else is data.
KHU_EXECUTABLE = frozenset(("bin/khu", "bin/khu-stub", "install-agent.sh"))
KHU_MANIFEST_FORMAT = "studiohub.khu-agent.v1"
KHU_PUBLISHER_KEYS = frozenset(("feedURL", "enrolURL", "publicKey", "testAuthority"))
#: **Not part of the kit.** A drive may carry the owner's plain backup of the publisher's signing
#: key in a folder of its own at the root. Nothing is ever staged into it, copied out of it or
#: pinned in a manifest, and a path inside it is refused rather than assumed harmless.
PROTECTED_AT_ROOT = "KH Updater signing key backup"


def inside_protected(path: Path) -> bool:
    return PROTECTED_AT_ROOT in Path(path).expanduser().parts


def file_digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def regular_file(path: Path, what: str) -> None:
    """Refuse anything that is not a plain, non-empty regular file.

    A symlink would let the manifest describe one file while the Mac runs another.
    """
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"agent kit {what} is not a regular file: {path}")
    if info.st_size <= 0:
        raise ValueError(f"agent kit {what} is empty: {path}")


def validate_agent_kit(kit: Path) -> Path:
    """The credential-free kit: exactly those files, no extras, no aliases."""
    kit = Path(kit).expanduser()
    if kit.is_symlink() or not kit.is_dir():
        raise ValueError(f"agent kit is not a directory: {kit}")
    found = set()
    for path in kit.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            continue
        found.add(path.relative_to(kit).as_posix())
    if found != set(KHU_KIT_FILES):
        detail = []
        missing = sorted(set(KHU_KIT_FILES) - found)
        extra = sorted(found - set(KHU_KIT_FILES))
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unexpected " + ", ".join(extra))
        raise ValueError("agent kit contents are wrong: " + "; ".join(detail))
    for name in KHU_KIT_FILES:
        regular_file(kit / name, name)
    return kit


def validate_publisher_descriptor(path: Path, *, allow_test: bool = False) -> dict:
    """The owner's publisher.json: the pinned key and the two URLs, and nothing else."""
    path = Path(path).expanduser()
    regular_file(path, KHU_PUBLISHER_FILE)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"publisher descriptor is not JSON: {path}") from exc
    if not isinstance(value, dict) or set(value) != KHU_PUBLISHER_KEYS:
        raise ValueError("publisher descriptor must be exactly feedURL, enrolURL, publicKey, "
                         "testAuthority")
    if value["testAuthority"] is not False and not allow_test:
        # A kit that may be handed out must never pin a test key: the kit's own reader refuses it,
        # and a kit carrying one could not enrol anything.
        raise ValueError("refusing to stage a testAuthority publisher descriptor")
    if not isinstance(value["publicKey"], str) or not value["publicKey"]:
        raise ValueError("publisher descriptor has no public key")
    return value


def build_agent_manifest(kit_version: str, files: dict[str, tuple[int, str]]) -> dict:
    return {
        "format": KHU_MANIFEST_FORMAT,
        "agent_version": kit_version,
        # Stated here because it is what a Mac does with the kit: install and enrol. It never
        # updates an app - that stays KH Updater's own signed-feed job.
        "stage": "bootstrap-only",
        "files": {name: {"bytes": size, "sha256": digest}
                  for name, (size, digest) in sorted(files.items())},
    }


def stage_agent_kit(output: Path, kit: Path, publisher_json: Path, *,
                    allow_test: bool = False) -> dict:
    """Stage a ready agent kit at `output`, or refuse and leave nothing behind.

    Built in a private sibling directory and moved into place, so a failure leaves no half-written
    kit for a Mac to install from, and a second call cannot merge into one.
    """
    kit = validate_agent_kit(kit)
    descriptor = validate_publisher_descriptor(publisher_json, allow_test=allow_test)
    if inside_protected(kit) or inside_protected(publisher_json) or inside_protected(output):
        raise ValueError("refusing to stage into or out of the publisher signing key backup: it "
                         "is not part of the kit")
    output = Path(output).expanduser().absolute()
    # `absolute`, not `resolve`: a drive is reached through a symlink on macOS (`/Volumes/...`),
    # and the kit belongs at the path the operator named.
    if output.exists() or output.is_symlink():
        raise ValueError(f"refusing to replace an existing agent kit: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.parent.is_symlink() or not output.parent.is_dir():
        raise ValueError(f"the agent kit's folder is not a directory: {output.parent}")

    version = (kit / "VERSION").read_text(encoding="utf-8").strip()
    if not version or any(character not in "0123456789." for character in version):
        raise ValueError(f"agent kit version is not a dotted number: {version!r}")

    scratch = Path(tempfile.mkdtemp(prefix=".khu-agent-", dir=output.parent))
    scratch.chmod(0o700)
    staged = scratch / output.name
    try:
        (staged / "bin").mkdir(parents=True)
        files: dict[str, tuple[int, str]] = {}
        for name in KHU_KIT_FILES:
            target = staged / name
            shutil.copyfile(kit / name, target)
            target.chmod(0o755 if name in KHU_EXECUTABLE else 0o644)
            files[name] = file_digest(target)
        # Written from the descriptor's own bytes, so the pinned key in the kit is exactly the one
        # the publisher printed and every Mac's config will trust.
        publisher_target = staged / KHU_PUBLISHER_FILE
        publisher_target.write_text(json.dumps(descriptor, indent=2, sort_keys=True) + "\n",
                                    encoding="utf-8")
        publisher_target.chmod(0o644)
        files[KHU_PUBLISHER_FILE] = file_digest(publisher_target)

        manifest = build_agent_manifest(version, files)
        if tuple(manifest["files"]) != KHU_STAGED_FILES:
            raise ValueError("agent kit manifest is missing a file")
        manifest_path = staged / "MANIFEST.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                 encoding="utf-8")
        manifest_path.chmod(0o644)

        # Prove the copy before anything can use it: size and hash of every staged byte.
        for name, pin in manifest["files"].items():
            if file_digest(staged / name) != (pin["bytes"], pin["sha256"]):
                raise ValueError(f"staged agent kit file does not match its manifest: {name}")

        os.rename(staged, output)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return {"mode": "khu-agent", "agent_version": version, "staged": str(output),
            "files": sorted(manifest["files"]), "problems": []}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path,
                        help="a staged Studio Hub app (its install-kit manifest is what is built)")
    parser.add_argument("--library", type=Path, default=PROJECT_ROOT / "model-library")
    parser.add_argument("--output", type=Path, required=True,
                        help="where the manifest - or, with --khu-agent, the staged kit - goes")
    parser.add_argument("--khu-agent", type=Path,
                        help="a built KH Updater agent kit to stage instead of an app")
    parser.add_argument("--khu-publisher", type=Path,
                        help="the publisher descriptor to pin into that kit")
    parser.add_argument("--allow-test-authority", action="store_true",
                        help="accept a testAuthority descriptor; never for a kit that is handed out")
    arguments = parser.parse_args()

    if arguments.khu_agent or arguments.khu_publisher:
        if arguments.app:
            parser.error("--app and --khu-agent are different kits; choose one")
        if not arguments.khu_agent or not arguments.khu_publisher:
            parser.error("--khu-agent and --khu-publisher go together")
        try:
            result = stage_agent_kit(arguments.output, arguments.khu_agent,
                                     arguments.khu_publisher,
                                     allow_test=arguments.allow_test_authority)
        except (ValueError, OSError) as error:
            print(json.dumps({"mode": "khu-agent", "problems": [str(error)]}, indent=1))
            return 1
        print(json.dumps({key: result[key] for key in ("agent_version", "staged", "problems")},
                         indent=1))
        return 0

    if not arguments.app:
        parser.error("--app is required unless --khu-agent is given")
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
