#!/usr/bin/env python3
"""Append the pinned mlx-serve leaf to an already verified Image runtime.

The Python/mflux runtime remains a macOS 14 runtime.  The native leaf has its own
audited macOS floor and is used only by manifests that declare that floor.  This
helper never retargets a Mach-O deployment header and never replaces an existing
native tree or its audit records.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import time
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
PIN_PATH = SCRIPT_DIR / "mlx-serve-pin.json"
NATIVE_DIR = "mlx-serve"
NATIVE_MANIFESTS = (
    "mlx-serve.json",
    "mlx-serve-files.json",
    "mlx-serve-macho-audit.json",
    "mlx-serve-relocations.json",
)


def _runtime_packaging():
    path = SCRIPT_DIR.parent / "voice-runtime" / "runtime_packaging.py"
    spec = importlib.util.spec_from_file_location("studiohub_runtime_packaging", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_pin(path: Path = PIN_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema", "version", "archive", "archive_sha256", "archive_root",
        "source_url", "base_runtime_minimum_macos", "minimum_macos", "files",
    }
    if value.get("schema") != "studiohub.mlx-serve-pin.v1" or set(value) != required:
        raise ValueError(f"invalid mlx-serve pin: {path}")
    if not re.fullmatch(r"[0-9a-f]{64}", str(value["archive_sha256"])):
        raise ValueError(f"invalid mlx-serve archive digest: {path}")
    files = value["files"]
    if (not isinstance(files, list) or not files
            or any(not isinstance(item, str) or not item for item in files)
            or len({item.casefold() for item in files}) != len(files)):
        raise ValueError(f"invalid mlx-serve file inventory: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _archive_files(bundle: tarfile.TarFile, pin: dict[str, Any]) -> list[tarfile.TarInfo]:
    root = str(pin["archive_root"])
    expected = set(pin["files"])
    found: dict[str, tarfile.TarInfo] = {}
    folded: set[str] = set()
    for member in bundle.getmembers():
        raw = member.name
        path = PurePosixPath(raw)
        if (not raw or raw.startswith("/") or ".." in path.parts or "." in path.parts
                or not path.parts or path.parts[0] != root):
            raise ValueError(f"unsafe archive path: {raw!r}")
        relative = PurePosixPath(*path.parts[1:]).as_posix()
        if member.isdir():
            continue
        if not member.isfile():
            raise ValueError(f"mlx-serve archive contains a non-file entry: {raw!r}")
        if not relative or relative not in expected:
            raise ValueError(f"mlx-serve archive has an unexpected file: {raw!r}")
        key = relative.casefold()
        if key in folded:
            raise ValueError(f"mlx-serve archive has an overwrite collision: {raw!r}")
        folded.add(key)
        found[relative] = member
    missing = sorted(expected.difference(found))
    if missing:
        raise ValueError(f"mlx-serve archive is missing: {', '.join(missing)}")
    return [found[path] for path in pin["files"]]


def extract_archive(archive: Path, destination: Path, pin: dict[str, Any]) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive, "r:gz") as bundle:
        members = _archive_files(bundle, pin)
        for member in members:
            relative = PurePosixPath(*PurePosixPath(member.name).parts[1:])
            output = destination.joinpath(*relative.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise ValueError(f"mlx-serve archive could not read {member.name!r}")
            with source, output.open("xb") as handle:
                shutil.copyfileobj(source, handle, length=1024 * 1024)
            output.chmod(stat.S_IMODE(member.mode))


def _macho_files(root: Path) -> list[Path]:
    packaging = _runtime_packaging()
    return [path for path in sorted(root.rglob("*"))
            if path.is_file() and not path.is_symlink() and packaging.is_macho(path)]


def relocate_native_tree(root: Path) -> list[dict[str, Any]]:
    """Remove build-host identities and stale rpaths, then ad-hoc sign changes."""
    packaging = _runtime_packaging()
    records = list(packaging.sanitize_macho_rpaths(root))
    for path in _macho_files(root):
        commands = subprocess.run(
            ["otool", "-arch", "arm64", "-l", str(path)], check=True,
            capture_output=True, text=True,
        ).stdout
        removed: list[str] = []
        for rpath in packaging.parse_rpaths(commands):
            if rpath.startswith("@loader_path"):
                suffix = rpath.removeprefix("@loader_path").lstrip("/")
                target = path.parent / suffix
            elif rpath.startswith("@executable_path"):
                suffix = rpath.removeprefix("@executable_path").lstrip("/")
                target = root / suffix
            else:
                continue
            if _inside(root, target) and target.resolve(strict=False).exists():
                continue
            subprocess.run(
                ["install_name_tool", "-delete_rpath", rpath, str(path)], check=True,
                capture_output=True, text=True,
            )
            removed.append(rpath)
        if removed:
            subprocess.run(
                ["codesign", "--force", "--sign", "-", str(path)], check=True,
                capture_output=True, text=True,
            )
            records.append({
                "path": path.relative_to(root).as_posix(),
                "removed": removed,
                "added": [],
                "install_id": None,
            })
    for path in _macho_files(root):
        subprocess.run(
            ["codesign", "--verify", "--strict", str(path)], check=True,
            capture_output=True, text=True,
        )
    return sorted(records, key=lambda item: str(item.get("path")))


def _expand(root: Path, loader: Path, value: str) -> Path | None:
    if value == "@loader_path":
        return loader.parent
    if value.startswith("@loader_path/"):
        return loader.parent / value.removeprefix("@loader_path/")
    if value == "@executable_path":
        return root
    if value.startswith("@executable_path/"):
        return root / value.removeprefix("@executable_path/")
    if value.startswith("/"):
        return Path(value)
    return None


def audit_native_tree(root: Path, target_macos: str) -> list[dict[str, Any]]:
    """Audit the conditional native subtree with its real executable directory."""
    packaging = _runtime_packaging()
    executable = root / "mlx-serve"
    executable_commands = subprocess.run(
        ["otool", "-arch", "arm64", "-l", str(executable)], check=True,
        capture_output=True, text=True,
    ).stdout
    executable_rpaths = packaging.parse_rpaths(executable_commands)
    records: list[dict[str, Any]] = []
    failures: list[str] = []
    observed_minimums: list[str] = []
    for path in _macho_files(root):
        relative = path.relative_to(root).as_posix()
        architectures = subprocess.run(
            ["lipo", "-archs", str(path)], check=True, capture_output=True, text=True,
        ).stdout.strip().split()
        links = subprocess.run(
            ["otool", "-arch", "arm64", "-L", str(path)], check=True,
            capture_output=True, text=True,
        ).stdout
        dependencies = [
            line.strip().split(" (compatibility version", 1)[0]
            for line in links.splitlines()[1:] if line.strip()
        ]
        install_id = packaging.macho_install_id(path)
        if install_id is not None and dependencies and dependencies[0] == install_id:
            dependencies = dependencies[1:]
        commands = subprocess.run(
            ["otool", "-arch", "arm64", "-l", str(path)], check=True,
            capture_output=True, text=True,
        ).stdout
        rpaths = packaging.parse_rpaths(commands)
        minimum = packaging.parse_macos_minimum(commands)
        if minimum:
            observed_minimums.append(minimum)
        record = {
            "path": relative,
            "architectures": architectures,
            "minimum_macos": minimum,
            "install_id": install_id,
            "dependencies": dependencies,
            "rpaths": rpaths,
            "inherited_executable_rpaths": executable_rpaths,
        }
        records.append(record)
        if not packaging.has_arm64_slice(architectures):
            failures.append(f"{relative}: no arm64 slice; found {architectures}")
        if minimum is None:
            failures.append(f"{relative}: no macOS deployment target")
        elif not packaging.minimum_supports_target(minimum, target_macos):
            failures.append(f"{relative}: requires macOS {minimum}, conditional target is {target_macos}")
        invalid = [item for item in dependencies if packaging.invalid_dependency(item)]
        if invalid:
            failures.append(f"{relative}: external dependencies: {', '.join(invalid)}")
        if packaging.invalid_install_id(install_id):
            failures.append(f"{relative}: external install id: {install_id}")
        for rpath in rpaths:
            expanded = _expand(root, path, rpath)
            if expanded is None or not _inside(root, expanded) or not expanded.resolve(strict=False).exists():
                failures.append(f"{relative}: unresolved or escaping rpath: {rpath}")
        for dependency in dependencies:
            if dependency.startswith("@loader_path") or dependency.startswith("@executable_path"):
                expanded = _expand(root, path, dependency)
                if expanded is None or not _inside(root, expanded) or not expanded.resolve(strict=False).exists():
                    failures.append(f"{relative}: unresolved dependency: {dependency}")
            elif dependency.startswith("@rpath/"):
                suffix = dependency.removeprefix("@rpath/")
                candidates = [
                    expanded / suffix
                    for base, owner in ((rpaths, path), (executable_rpaths, executable))
                    for value in base
                    if (expanded := _expand(root, owner, value)) is not None
                ]
                if not any(_inside(root, item) and item.resolve(strict=False).exists()
                           for item in candidates):
                    failures.append(f"{relative}: unresolved dependency: {dependency}")
    if not observed_minimums or max(observed_minimums, key=packaging._version_tuple) != target_macos:
        failures.append(
            f"native tree does not carry the pinned conditional macOS floor {target_macos}"
        )
    if failures:
        raise RuntimeError("mlx-serve Mach-O audit failed:\n" + "\n".join(failures))
    return records


def native_version(binary: Path) -> str:
    result = subprocess.run(
        [str(binary), "--version"], check=True, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    match = re.search(
        r"^mlx-serve\s+(\d+(?:\.\d+){2})$",
        result.stdout + "\n" + result.stderr,
        re.MULTILINE,
    )
    if match is None:
        raise ValueError("mlx-serve --version did not report a semantic version")
    return match.group(1)


def _file_records(root: Path, *, installed_read_only: bool = False) -> list[dict[str, Any]]:
    return [{
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
        "mode": f"{stat.S_IMODE(path.stat().st_mode) & (~0o222 if installed_read_only else ~0):04o}",
    } for path in sorted(root.rglob("*")) if path.is_file() and not path.is_symlink()]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_runtime_manifest(runtime: Path) -> Path:
    return _runtime_packaging().write_file_manifest(runtime)


def _backup_session(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    stem = f"mlx-serve-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{os.getpid()}"
    session = root / stem
    suffix = 0
    while session.exists():
        suffix += 1
        session = root / f"{stem}-{suffix}"
    session.mkdir()
    return session


def _validate_base_runtime(runtime: Path, pin: dict[str, Any]) -> None:
    if not runtime.is_dir():
        raise ValueError(f"Image runtime is not a directory: {runtime}")
    base_audit_path = runtime / "manifest" / "macho-audit.json"
    runtime_manifest = runtime / "manifest" / "runtime-files.json"
    if not base_audit_path.is_file() or not runtime_manifest.is_file():
        raise ValueError(f"Image runtime has no verified base manifests: {runtime}")
    base_audit = json.loads(base_audit_path.read_text(encoding="utf-8"))
    expected = pin["base_runtime_minimum_macos"]
    if base_audit.get("status") != "passed" or base_audit.get("target_macos") != expected:
        raise ValueError(f"Image runtime base audit is not a passing macOS {expected} audit")
    mismatches = _runtime_packaging().verify_file_manifest(runtime)
    if mismatches:
        raise ValueError(f"Image runtime file manifest does not verify: {mismatches[:3]}")


def append_runtime(runtime: Path, archive: Path, pin: dict[str, Any], *,
                   backup_root: Path | None = None) -> None:
    runtime = runtime.expanduser().resolve()
    archive = archive.expanduser().resolve()
    _validate_base_runtime(runtime, pin)
    if not archive.is_file():
        raise FileNotFoundError(f"Pinned mlx-serve archive is unavailable: {archive}")
    if _inside(runtime, archive):
        raise ValueError("mlx-serve archive path must stay outside the runtime")
    actual_digest = sha256_file(archive)
    if actual_digest != pin["archive_sha256"]:
        raise ValueError(
            f"mlx-serve archive SHA-256 is {actual_digest}, expected {pin['archive_sha256']}"
        )
    destination = runtime / NATIVE_DIR
    manifest_dir = runtime / "manifest"
    collisions = [destination, *(manifest_dir / name for name in NATIVE_MANIFESTS)]
    existing = [path for path in collisions if path.exists()]
    if existing:
        raise FileExistsError(
            "mlx-serve append collision; refusing to overwrite: "
            + ", ".join(str(path) for path in existing)
        )

    if backup_root is None:
        backup_root = PROJECT_ROOT / "_trash" / "image-runtime-native"
    session = _backup_session(backup_root.expanduser().resolve())
    staged = session / "staging" / NATIVE_DIR
    extract_archive(archive, staged, pin)
    relocations = relocate_native_tree(staged)
    version = native_version(staged / "mlx-serve")
    if version != pin["version"]:
        raise ValueError(f"mlx-serve version is {version}, expected {pin['version']}")
    audit = audit_native_tree(staged, str(pin["minimum_macos"]))
    staged_manifests = session / "staging-manifests"
    staged_manifests.mkdir()
    _write_json(staged_manifests / "mlx-serve.json", {
        "schema": "studiohub.image-runtime-native.v1",
        "name": "mlx-serve",
        "version": version,
        "archive": pin["archive"],
        "archive_sha256": pin["archive_sha256"],
        "source_url": pin["source_url"],
        "path": NATIVE_DIR,
        "base_runtime_minimum_macos": pin["base_runtime_minimum_macos"],
        "minimum_macos": pin["minimum_macos"],
        "conditional": True,
    })
    _write_json(staged_manifests / "mlx-serve-files.json", {
        "schema": "studiohub.image-runtime-native-files.v1",
        # macOS refuses to rename a non-writable directory, so the move happens while staged is
        # writable and this inventory records the modes installed immediately afterwards.
        "files": _file_records(staged, installed_read_only=True),
    })
    _write_json(staged_manifests / "mlx-serve-macho-audit.json", {
        "schema": "studiohub.image-runtime-native-macho.v1",
        "target_macos": pin["minimum_macos"],
        "conditional": True,
        "status": "passed",
        "failures": [],
        "binaries": audit,
    })
    _write_json(staged_manifests / "mlx-serve-relocations.json", {
        "schema": "studiohub.image-runtime-native-relocations.v1",
        "records": relocations,
    })

    before = session / "before" / "manifest"
    shutil.copytree(manifest_dir, before)
    root_mode = stat.S_IMODE(runtime.stat().st_mode)
    manifest_mode = stat.S_IMODE(manifest_dir.stat().st_mode)
    runtime_manifest = manifest_dir / "runtime-files.json"
    runtime_manifest_mode = stat.S_IMODE(runtime_manifest.stat().st_mode)
    runtime.chmod(root_mode | stat.S_IWUSR)
    manifest_dir.chmod(manifest_mode | stat.S_IWUSR)
    runtime_manifest.chmod(runtime_manifest_mode | stat.S_IWUSR)
    installed_manifests: list[Path] = []
    installed_tree = False
    try:
        os.replace(staged, destination)
        installed_tree = True
        for name in NATIVE_MANIFESTS:
            target = manifest_dir / name
            os.replace(staged_manifests / name, target)
            installed_manifests.append(target)
        for path in destination.rglob("*"):
            path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
        destination.chmod(stat.S_IMODE(destination.stat().st_mode) & ~0o222)
        for path in installed_manifests:
            path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
        write_runtime_manifest(runtime)
        mismatches = _runtime_packaging().verify_file_manifest(runtime)
        if mismatches:
            raise RuntimeError(f"updated Image runtime manifest does not verify: {mismatches[:3]}")
    except Exception:
        failed = session / "failed-installed"
        failed.mkdir(exist_ok=True)
        for path in installed_manifests:
            if path.exists():
                os.replace(path, failed / path.name)
        if installed_tree and destination.exists():
            os.replace(destination, failed / NATIVE_DIR)
        shutil.copy2(before / "runtime-files.json", manifest_dir / "runtime-files.json")
        raise
    finally:
        if runtime_manifest.exists():
            runtime_manifest.chmod(runtime_manifest_mode)
        manifest_dir.chmod(manifest_mode)
        runtime.chmod(root_mode)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path, help="existing verified Image runtime")
    parser.add_argument("archive", type=Path, help="pinned mlx-serve release archive")
    parser.add_argument("--pin", type=Path, default=PIN_PATH)
    parser.add_argument("--backup-root", type=Path)
    arguments = parser.parse_args()
    append_runtime(
        arguments.runtime,
        arguments.archive,
        load_pin(arguments.pin),
        backup_root=arguments.backup_root,
    )
    print(f"mlx-serve appended to {arguments.runtime.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
