#!/usr/bin/env python3
"""Stage the KH Updater agent kit into a Studio Hub app bundle.

Two inputs, deliberately kept apart:

* the **credential-free kit** from kh-updater's own
  `packaging/build-agent-kit.sh <version> <new-dir>` — `bin/khu`, `bin/khu-stub`,
  `install-agent.sh`, `com.khupdater.agent.plist`, `agent-config.example.json`, `VERSION`;
* the **publisher descriptor** from the owner's `~/Library/KHUpdater/publisher.json` —
  the feed and enrolment URLs and the pinned public key. It is the one file the kit
  build must never produce, because it is the trust anchor and it carries no secret.

Neither input carries a per-Mac token, so the shipped bundle never does either: the
token is minted per Mac at enrolment and written 0600 under that Mac's own home.

Every staged byte is recorded in `MANIFEST.json` (size + sha256) and re-read to prove
the copy. `components/hub/backend/khu_agent.verify_kit` refuses a kit whose bytes do
not match that manifest, so a tampered bundle cannot install an agent.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

#: The credential-free files kh-updater's kit builder produces.
_KIT_FILES = (
    "VERSION",
    "bin/khu",
    "bin/khu-stub",
    "install-agent.sh",
    "com.khupdater.agent.plist",
    "agent-config.example.json",
)
#: Added here from the publisher descriptor; never produced by the kit builder.
PUBLISHER_FILE = "publisher.json"
#: Everything that lands in the bundle, and what `khu_agent.KIT_FILES` checks.
STAGED_FILES = tuple(sorted(_KIT_FILES + (PUBLISHER_FILE,)))
#: Executable on the destination Mac; everything else is data.
_EXECUTABLE = frozenset(("bin/khu", "bin/khu-stub", "install-agent.sh"))
_MANIFEST_FORMAT = "studiohub.khu-agent.v1"
_PUBLISHER_KEYS = frozenset(("feedURL", "enrolURL", "publicKey", "testAuthority"))
_READ_SIZE = 1024 * 1024


@dataclass(frozen=True)
class AgentKitStageResult:
    staged_path: Path
    manifest_path: Path
    agent_version: str


def _digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_READ_SIZE), b""):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _regular_file(path: Path, what: str) -> None:
    """Refuse anything that is not a plain, non-empty regular file.

    A symlink would let the bundle's manifest describe one file while the Mac runs
    another (the same trap the render tools pin guards against).
    """
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"agent kit {what} is not a regular file: {path}")
    if info.st_size <= 0:
        raise ValueError(f"agent kit {what} is empty: {path}")


def validate_source_kit(kit: Path) -> Path:
    """The credential-free kit: exactly those files, no extras, no aliases."""
    kit = Path(kit).expanduser()
    if kit.is_symlink() or not kit.is_dir():
        raise ValueError(f"agent kit is not a directory: {kit}")
    found = set()
    for path in kit.rglob("*"):
        relative = path.relative_to(kit).as_posix()
        if path.is_dir() and not path.is_symlink():
            continue
        found.add(relative)
    if found != set(_KIT_FILES):
        missing = sorted(set(_KIT_FILES) - found)
        extra = sorted(found - set(_KIT_FILES))
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unexpected " + ", ".join(extra))
        raise ValueError("agent kit contents are wrong: " + "; ".join(detail))
    for name in _KIT_FILES:
        _regular_file(kit / name, name)
    return kit


def validate_publisher_descriptor(path: Path, *, allow_test: bool = False) -> dict:
    """The owner's publisher.json: the pinned key and the two URLs, and nothing else."""
    path = Path(path).expanduser()
    _regular_file(path, PUBLISHER_FILE)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"publisher descriptor is not JSON: {path}") from exc
    if not isinstance(value, dict) or set(value) != _PUBLISHER_KEYS:
        raise ValueError("publisher descriptor must be exactly feedURL, enrolURL, publicKey, testAuthority")
    if value["testAuthority"] is not False and not allow_test:
        # A published DMG must never pin a test key: the kit's own reader refuses it,
        # and a bundle that shipped one could not enrol at all.
        raise ValueError("refusing to stage a testAuthority publisher descriptor")
    if not isinstance(value["publicKey"], str):
        raise ValueError("publisher descriptor has no public key")
    return value


def build_manifest(kit_version: str, files: dict[str, tuple[int, str]]) -> dict:
    return {
        "format": _MANIFEST_FORMAT,
        "agent_version": kit_version,
        # Stated here because it is what the Hub does with the kit: install and enrol.
        # It never updates an app - that stays KH Updater's own signed-feed job.
        "stage": "bootstrap-only",
        "files": {name: {"bytes": size, "sha256": digest}
                  for name, (size, digest) in sorted(files.items())},
    }


def stage_agent_kit(resources: Path, kit: Path, publisher_json: Path, *,
                    allow_test: bool = False) -> AgentKitStageResult:
    """Stage a verified agent kit into ``<resources>/khu-agent``.

    Built in a private sibling directory and moved into place, so a failure leaves no
    half-written kit for the Hub to install, and a second call cannot merge into one.
    """
    kit = validate_source_kit(kit)
    descriptor = validate_publisher_descriptor(publisher_json, allow_test=allow_test)
    resources = Path(resources).expanduser().absolute()
    if resources.is_symlink() or not resources.is_dir():
        raise ValueError(f"app Resources is not a directory: {resources}")
    destination = resources / "khu-agent"
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"refusing to replace an existing agent kit: {destination}")

    version = (kit / "VERSION").read_text(encoding="utf-8").strip()
    if not version or any(character not in "0123456789." for character in version):
        raise ValueError(f"agent kit version is not a dotted number: {version!r}")

    scratch = Path(tempfile.mkdtemp(prefix=".khu-agent-", dir=resources))
    scratch.chmod(0o700)
    staged = scratch / "khu-agent"
    try:
        (staged / "bin").mkdir(parents=True)
        files: dict[str, tuple[int, str]] = {}
        for name in _KIT_FILES:
            target = staged / name
            shutil.copyfile(kit / name, target)
            target.chmod(0o755 if name in _EXECUTABLE else 0o644)
            files[name] = _digest(target)
        # Written from the descriptor's own bytes, so the pinned key in the bundle is
        # exactly the one the publisher printed and the per-Mac config will trust.
        publisher_target = staged / PUBLISHER_FILE
        publisher_target.write_text(json.dumps(descriptor, indent=2, sort_keys=True) + "\n",
                                    encoding="utf-8")
        publisher_target.chmod(0o644)
        files[PUBLISHER_FILE] = _digest(publisher_target)

        manifest = build_manifest(version, files)
        if tuple(manifest["files"]) != STAGED_FILES:
            raise ValueError("agent kit manifest is missing a file")
        manifest_path = staged / "MANIFEST.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                 encoding="utf-8")
        manifest_path.chmod(0o644)

        # Prove the copy before anything can use it: size and hash of every staged byte.
        for name, pin in manifest["files"].items():
            size, digest = _digest(staged / name)
            if size != pin["bytes"] or digest != pin["sha256"]:
                raise ValueError(f"staged agent kit file does not match its manifest: {name}")

        os.rename(staged, destination)
        return AgentKitStageResult(staged_path=destination,
                                   manifest_path=destination / "MANIFEST.json",
                                   agent_version=version)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
