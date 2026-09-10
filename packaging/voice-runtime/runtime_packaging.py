#!/usr/bin/env python3
"""Build-time audits and deterministic manifests for the Voice runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Iterable


MANIFEST_PATH = Path("manifest/runtime-files.json")
SYSTEM_DEPENDENCY_PREFIXES = ("/usr/lib/", "/System/Library/")
RELOCATABLE_DEPENDENCY_PREFIXES = ("@rpath/", "@loader_path/", "@executable_path/")
MACHO_MAGICS = {
    bytes.fromhex("feedface"),
    bytes.fromhex("cefaedfe"),
    bytes.fromhex("feedfacf"),
    bytes.fromhex("cffaedfe"),
    bytes.fromhex("cafebabe"),
    bytes.fromhex("bebafeca"),
    bytes.fromhex("cafebabf"),
    bytes.fromhex("bfbafeca"),
}


def forbidden_prefix_hits(root: Path, prefixes: Iterable[str]) -> list[dict[str, str]]:
    """Return bundle entries that retain any build-machine absolute prefix."""
    encoded = [(prefix, os.fsencode(prefix)) for prefix in prefixes if prefix]
    hits: list[dict[str, str]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            target = os.readlink(path)
            for prefix, _ in encoded:
                if prefix in target:
                    hits.append({"path": relative, "prefix": prefix, "source": "symlink"})
            continue
        if not path.is_file():
            continue
        remaining = dict(encoded)
        overlap = max((len(value) for value in remaining.values()), default=1) - 1
        carry = b""
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                contents = carry + block
                for prefix, prefix_bytes in list(remaining.items()):
                    if prefix_bytes in contents:
                        hits.append(
                            {"path": relative, "prefix": prefix, "source": "contents"}
                        )
                        del remaining[prefix]
                if not remaining:
                    break
                carry = contents[-overlap:] if overlap else b""
    return hits


def invalid_dependency(dependency: str) -> str | None:
    """Return a Mach-O dependency that is neither system-owned nor relocatable."""
    if dependency.startswith(SYSTEM_DEPENDENCY_PREFIXES):
        return None
    if dependency.startswith(RELOCATABLE_DEPENDENCY_PREFIXES):
        return None
    if dependency == "":
        return None
    return dependency


def unsafe_symlink_hits(root: Path) -> list[dict[str, str]]:
    """Return symlinks that are absolute or escape the bundle."""
    resolved_root = root.resolve()
    hits: list[dict[str, str]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_symlink():
            continue
        target = os.readlink(path)
        resolved = (path.parent / target).resolve(strict=False)
        if os.path.isabs(target) or not _inside(resolved_root, resolved):
            hits.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "target": target,
                    "reason": "absolute" if os.path.isabs(target) else "escapes-bundle",
                }
            )
    return hits


def is_macho(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(4) in MACHO_MAGICS


def has_arm64_slice(architectures: Iterable[str]) -> bool:
    return "arm64" in architectures


def parse_rpaths(load_commands: str) -> list[str]:
    return re.findall(
        r"LC_RPATH(?:(?!\n\s*cmd\s).)*?^\s*path\s+(\S+)\s+\(offset\s+\d+\)\s*$",
        load_commands,
        re.MULTILINE | re.DOTALL,
    )


def macho_install_id(path: Path) -> str | None:
    output = subprocess.run(
        ["otool", "-arch", "arm64", "-D", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    return output[1].strip() if len(output) > 1 and output[1].strip() else None


def invalid_rpath(rpath: str) -> str | None:
    """Return an absolute search path outside Apple's system locations."""
    if not rpath.startswith("/"):
        return None
    if rpath == "/usr/lib" or rpath.startswith(SYSTEM_DEPENDENCY_PREFIXES):
        return None
    return rpath


def invalid_install_id(install_id: str | None) -> str | None:
    """Return a dylib identity that embeds a non-system absolute path."""
    if install_id is None or not install_id.startswith("/"):
        return None
    if install_id.startswith(SYSTEM_DEPENDENCY_PREFIXES):
        return None
    return install_id


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _expand_loader_token(root: Path, binary: Path, value: str) -> Path | None:
    if value == "@loader_path":
        return binary.parent
    if value.startswith("@loader_path/"):
        return binary.parent / value.removeprefix("@loader_path/")
    if value == "@executable_path":
        return root / "bin"
    if value.startswith("@executable_path/"):
        return root / "bin" / value.removeprefix("@executable_path/")
    if value.startswith("/"):
        return Path(value)
    return None


def unresolved_relocatable_dependency(
    root: Path, binary: Path, dependency: str, rpaths: Iterable[str]
) -> str | None:
    """Reject a loader-relative dependency unless it resolves within the bundle."""
    candidates: list[Path] = []
    if dependency.startswith("@rpath/"):
        suffix = dependency.removeprefix("@rpath/")
        for rpath in rpaths:
            expanded = _expand_loader_token(root, binary, rpath)
            if expanded is not None:
                candidates.append(expanded / suffix)
    else:
        expanded = _expand_loader_token(root, binary, dependency)
        if expanded is not None:
            candidates.append(expanded)
    resolved_root = root.resolve()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if _inside(resolved_root, resolved) and resolved.exists():
            return None
    return dependency


def parse_macos_minimum(load_commands: str) -> str | None:
    """Read a deployment target from otool -l output."""
    modern = re.search(r"^\s*minos\s+(\d+(?:\.\d+){1,2})\s*$", load_commands, re.MULTILINE)
    if modern:
        return modern.group(1)
    legacy_block = re.search(
        r"LC_VERSION_MIN_MACOSX(?:(?!\n\s*cmd\s).)*?^\s*version\s+(\d+(?:\.\d+){1,2})\s*$",
        load_commands,
        re.MULTILINE | re.DOTALL,
    )
    return legacy_block.group(1) if legacy_block else None


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = tuple(int(part) for part in version.split("."))
    return parts + (0,) * (3 - len(parts))


def minimum_supports_target(binary_minimum: str, target: str) -> bool:
    return _version_tuple(binary_minimum) <= _version_tuple(target)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_file_manifest(root: Path) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if relative == MANIFEST_PATH:
            continue
        if path.is_symlink():
            files.append(
                {
                    "path": relative.as_posix(),
                    "type": "symlink",
                    "target": os.readlink(path),
                    "mode": f"{stat.S_IMODE(path.lstat().st_mode):04o}",
                }
            )
            continue
        if not path.is_file():
            continue
        file_mode = stat.S_IMODE(path.stat().st_mode)
        files.append(
            {
                "path": relative.as_posix(),
                "type": "file",
                "sha256": _sha256(path),
                "size": path.stat().st_size,
                "mode": f"{file_mode:04o}",
            }
        )
    return files


def write_file_manifest(root: Path) -> Path:
    output = root / MANIFEST_PATH
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": "studiohub.voice-runtime-files.v1", "files": build_file_manifest(root)}
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def verify_file_manifest(root: Path) -> list[dict[str, str]]:
    manifest_path = root / MANIFEST_PATH
    expected_records = json.loads(manifest_path.read_text(encoding="utf-8"))["files"]
    expected = {record["path"]: record for record in expected_records}
    actual = {record["path"]: record for record in build_file_manifest(root)}
    mismatches: list[dict[str, str]] = []
    for relative in sorted(expected.keys() | actual.keys()):
        if relative not in actual:
            mismatches.append({"path": relative, "reason": "missing"})
            continue
        if relative not in expected:
            mismatches.append({"path": relative, "reason": "unexpected"})
            continue
        for field in sorted(expected[relative].keys() | actual[relative].keys()):
            if expected[relative].get(field) != actual[relative].get(field):
                mismatches.append({"path": relative, "reason": field})
                break
    return mismatches


def normalize_python_shebangs(root: Path) -> list[str]:
    """Replace absolute Python shebangs with a script-relative polyglot header."""
    bin_dir = root / "bin"
    changed: list[str] = []
    if not bin_dir.is_dir():
        return changed
    header = b'''#!/bin/sh\n\'\'\'exec\' "$(dirname "$0")/python3" "$0" "$@"\n\' \'\'\'\n'''
    for path in sorted(bin_dir.iterdir()):
        if not path.is_file() or path.is_symlink() or is_macho(path):
            continue
        contents = path.read_bytes()
        first_line, separator, body = contents.partition(b"\n")
        if not separator or not first_line.startswith(b"#!") or b"python" not in first_line.lower():
            continue
        path.write_bytes(header + body)
        changed.append(path.relative_to(root).as_posix())
    return changed


def collect_macho_audit(
    root: Path, target: str
) -> tuple[list[dict[str, object]], list[str]]:
    records: list[dict[str, object]] = []
    failures: list[str] = []
    executable_rpaths: list[str] = []
    executable = root / "bin" / "python3"
    if executable.is_file() and is_macho(executable):
        executable_architectures = subprocess.run(
            ["lipo", "-archs", str(executable)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip().split()
        if has_arm64_slice(executable_architectures):
            executable_commands = subprocess.run(
                ["otool", "-arch", "arm64", "-l", str(executable)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            executable_rpaths = parse_rpaths(executable_commands)
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path.is_symlink() or not is_macho(path):
            continue
        relative = path.relative_to(root).as_posix()
        architectures = subprocess.run(
            ["lipo", "-archs", str(path)], check=True, capture_output=True, text=True
        ).stdout.strip().split()
        if not has_arm64_slice(architectures):
            records.append(
                {
                    "path": relative,
                    "architectures": architectures,
                    "minimum_macos": None,
                    "dependencies": [],
                    "rpaths": [],
                }
            )
            failures.append(f"{relative}: no arm64 slice; found {architectures}")
            continue
        links_output = subprocess.run(
            ["otool", "-arch", "arm64", "-L", str(path)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        dependencies = [
            line.strip().split(" (compatibility version", 1)[0]
            for line in links_output.splitlines()[1:]
            if line.strip()
        ]
        install_id = macho_install_id(path)
        if install_id is not None and dependencies and dependencies[0] == install_id:
            dependencies = dependencies[1:]
        commands_output = subprocess.run(
            ["otool", "-arch", "arm64", "-l", str(path)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        rpaths = parse_rpaths(commands_output)
        invalid_search_paths = [item for item in rpaths if invalid_rpath(item)]
        invalid = [item for item in dependencies if invalid_dependency(item)]
        unresolved = [
            item
            for item in dependencies
            if item.startswith(RELOCATABLE_DEPENDENCY_PREFIXES)
            and unresolved_relocatable_dependency(
                root, path, item, [*rpaths, *executable_rpaths]
            )
        ]
        minimum = parse_macos_minimum(commands_output)
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
        if minimum is None:
            failures.append(f"{relative}: no macOS deployment target")
        elif not minimum_supports_target(minimum, target):
            failures.append(f"{relative}: requires macOS {minimum}, target is {target}")
        if invalid:
            failures.append(f"{relative}: external dependencies: {', '.join(invalid)}")
        if invalid_search_paths:
            failures.append(
                f"{relative}: external rpaths: {', '.join(invalid_search_paths)}"
            )
        if invalid_install_id(install_id):
            failures.append(f"{relative}: external install id: {install_id}")
        if unresolved:
            failures.append(f"{relative}: unresolved loader dependencies: {', '.join(unresolved)}")
    return records, failures


def audit_macho(root: Path, target: str) -> list[dict[str, object]]:
    records, failures = collect_macho_audit(root, target)
    if failures:
        raise RuntimeError("Mach-O audit failed:\n" + "\n".join(failures))
    return records


def sanitize_macho_rpaths(root: Path) -> list[dict[str, object]]:
    """Remove wheel build-host rpaths and retain sibling @rpath resolution."""
    records: list[dict[str, object]] = []
    files_by_name: dict[str, list[Path]] = {}
    for candidate in root.rglob("*"):
        if candidate.is_file() and not candidate.is_symlink():
            files_by_name.setdefault(candidate.name, []).append(candidate)
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path.is_symlink() or not is_macho(path):
            continue
        architectures = subprocess.run(
            ["lipo", "-archs", str(path)], check=True, capture_output=True, text=True
        ).stdout.strip().split()
        if not has_arm64_slice(architectures):
            continue
        commands_output = subprocess.run(
            ["otool", "-arch", "arm64", "-l", str(path)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        removed = [item for item in parse_rpaths(commands_output) if invalid_rpath(item)]
        for rpath in removed:
            subprocess.run(
                ["install_name_tool", "-delete_rpath", rpath, str(path)],
                check=True,
                capture_output=True,
                text=True,
            )

        links_output = subprocess.run(
            ["otool", "-arch", "arm64", "-L", str(path)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        dependencies = [
            line.strip().split(" (compatibility version", 1)[0]
            for line in links_output.splitlines()[1:]
            if line.strip()
        ]
        install_id = macho_install_id(path)
        rewritten_install_id = None
        if invalid_install_id(install_id):
            rewritten_install_id = f"@rpath/{path.name}"
            subprocess.run(
                ["install_name_tool", "-id", rewritten_install_id, str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
        if install_id is not None and dependencies and dependencies[0] == install_id:
            dependencies = dependencies[1:]
        refreshed_commands = subprocess.run(
            ["otool", "-arch", "arm64", "-l", str(path)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        rpaths = parse_rpaths(refreshed_commands)
        added: list[str] = []
        for dependency in dependencies:
            if not dependency.startswith("@rpath/"):
                continue
            suffix = dependency.removeprefix("@rpath/")
            candidates = files_by_name.get(Path(suffix).name, [])
            if len(candidates) != 1:
                continue
            suffix_parts = Path(suffix).parts
            rpath_root = candidates[0].parents[len(suffix_parts) - 1]
            relative_parent = os.path.relpath(rpath_root, path.parent)
            candidate_rpath = (
                "@loader_path"
                if relative_parent == "."
                else f"@loader_path/{relative_parent}"
            )
            if candidate_rpath in rpaths or candidate_rpath in added:
                continue
            subprocess.run(
                ["install_name_tool", "-add_rpath", candidate_rpath, str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
            added.append(candidate_rpath)
        if removed or added or rewritten_install_id:
            subprocess.run(
                ["codesign", "--force", "--sign", "-", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["codesign", "--verify", "--strict", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
            records.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "removed": removed,
                    "added": added,
                    "install_id": rewritten_install_id,
                }
            )
    return records


def write_macho_audit(root: Path, target: str) -> Path:
    output = root / "manifest" / "macho-audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    binaries, failures = collect_macho_audit(root, target)
    payload = {
        "schema": "studiohub.voice-runtime-macho.v1",
        "target_macos": target,
        "status": "failed" if failures else "passed",
        "failures": failures,
        "binaries": binaries,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if failures:
        raise RuntimeError("Mach-O audit failed:\n" + "\n".join(failures))
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("root", type=Path)
    audit_parser.add_argument("--target-macos", default="14.0")
    audit_parser.add_argument("--forbid-prefix", action="append", default=[])

    manifest_parser = subparsers.add_parser("manifest")
    manifest_parser.add_argument("root", type=Path)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("root", type=Path)

    normalize_parser = subparsers.add_parser("normalize-shebangs")
    normalize_parser.add_argument("root", type=Path)

    sanitize_parser = subparsers.add_parser("sanitize-rpaths")
    sanitize_parser.add_argument("root", type=Path)

    args = parser.parse_args()
    root = args.root.resolve()
    if args.command == "audit":
        output = write_macho_audit(root, args.target_macos)
        prefix_hits = forbidden_prefix_hits(root, args.forbid_prefix)
        if prefix_hits:
            raise RuntimeError("build prefixes remain:\n" + json.dumps(prefix_hits, indent=2))
        symlink_hits = unsafe_symlink_hits(root)
        if symlink_hits:
            raise RuntimeError("unsafe symlinks remain:\n" + json.dumps(symlink_hits, indent=2))
    elif args.command == "manifest":
        output = write_file_manifest(root)
    elif args.command == "verify":
        mismatches = verify_file_manifest(root)
        if mismatches:
            raise RuntimeError("runtime changed:\n" + json.dumps(mismatches, indent=2))
        output = root / MANIFEST_PATH
    elif args.command == "normalize-shebangs":
        changed = normalize_python_shebangs(root)
        print(json.dumps(changed, indent=2))
        return 0
    else:
        changed = sanitize_macho_rpaths(root)
        print(json.dumps(changed, indent=2))
        return 0
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
