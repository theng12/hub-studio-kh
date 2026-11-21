#!/usr/bin/env python3
"""Build-time audits and deterministic manifests for the Voice runtime."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
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


# --- Test-only content -----------------------------------------------------------------------
#
# A Voice runtime is a whole Python installation, so it carries every wheel's `tests/` directory:
# numpy, scipy, networkx, torch and mlx_audio alone contribute hundreds of files that no product
# code runs. 0.6.200 shipped them *and* pytest 9.1.1 with pluggy and iniconfig, because someone
# installed pytest into `.build/voice-runtime` after it was built and `stage()` copied the tree
# wholesale. That is what `strip_test_only` closes, at both points a runtime can enter a release -
# the build stage and the copy into an app bundle: it removes the files, rewrites the runtime's own
# file manifest so it does not describe files that are gone, and refuses a tree it could not clean.
#
# **`tests` is not a synonym for test-only**, which is why the decision is made by scanning the
# staged sources rather than by a list of exceptions somebody must remember to extend:
# `jinja2/tests.py` is Jinja's runtime test vocabulary (`from .tests import TESTS`), `mpmath`'s
# `runtests()` imports `mpmath.tests`, and `scipy/stats/_page_trend_test.py` *is* `page_trend_test`.
TEST_DIRECTORY_NAMES = frozenset({"tests", "test"})
TEST_SCAFFOLDING_NAMES = frozenset({"conftest.py", "pytest.ini", "tox.ini"})
# Console scripts that belong only to a test framework; the distributions are matched by name.
TEST_FRAMEWORK_DISTRIBUTIONS = frozenset(
    {"pytest", "_pytest", "pluggy", "iniconfig", "coverage", "hypothesis"}
)
TEST_FRAMEWORK_SCRIPTS = frozenset({"py.test", "coverage3"})
_IMPORT_LINE = re.compile(rb"^[ \t]*(?:from|import)[ \t]+([^\n#]*)", re.MULTILINE)
_IDENTIFIER = re.compile(rb"[A-Za-z_][A-Za-z0-9_]*")


def is_test_file_name(name: str) -> bool:
    """True for a file that exists only to be collected by a test runner."""
    if name in TEST_SCAFFOLDING_NAMES:
        return True
    if name.endswith(".py") and name.startswith("test_"):
        return True
    # A leading underscore keeps scipy's `_page_trend_test.py` and `_bws_test.py`: those are the
    # implementations of `scipy.stats.page_trend_test` and `scipy.stats.bws_test`.
    return name.endswith("_test.py") and not name.startswith("_")


def is_test_framework_entry(name: str) -> bool:
    """True for a test framework's package, `.dist-info` directory or console script."""
    return (
        name in TEST_FRAMEWORK_SCRIPTS
        or name.split("-", 1)[0].lower() in TEST_FRAMEWORK_DISTRIBUTIONS
    )


def _test_directory_targets(root: Path, source: Path, token: str) -> list[Path]:
    """Where an import of a test-named module points, as absolute candidate directories."""
    dots = len(token) - len(token.lstrip("."))
    components = token.lstrip(".").split(".")
    named = [index for index, part in enumerate(components) if part in TEST_DIRECTORY_NAMES]
    if not named:
        return []
    components = components[: named[-1] + 1]
    base = source.parent
    for _ in range(dots - 1):
        base = base.parent
    if dots:
        return [base.joinpath(*components)]
    return [
        search_root.joinpath(*components)
        for search_root in (root, *sorted(root.glob("lib/python*/site-packages")))
    ]


def referenced_test_content(root: Path) -> tuple[set[Path], set[str]]:
    """Test-named packages and modules that the staged sources still import.

    A relative import resolves against the importing file's own directory, an absolute one against
    the runtime root and every `lib/python*/site-packages` in it. Both halves matter: `mpmath`'s
    `from .tests import runtests` names a *directory*, while torch's Dynamo **imports
    `torch._inductor.test_operators` from `trace_rules.py` at run time** - a `test_`-named module
    that removing by filename alone broke `AutoTokenizer`, and therefore Whisper transcription.
    """
    directories: set[Path] = set()
    modules: set[str] = set()
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if is_test_file_name(path.name) or TEST_DIRECTORY_NAMES & set(relative.parts[:-1]):
            continue  # the importer is test-only too and is being removed
        try:
            contents = path.read_bytes()
        except OSError:
            continue
        if b".test" not in contents and b"test_" not in contents:
            continue
        for statement in _IMPORT_LINE.findall(contents):
            text = statement.decode("ascii", "ignore")
            modules.update(
                match.decode("ascii", "ignore") for match in _IDENTIFIER.findall(statement)
            )
            module = text.split(" import ", 1)[0].split(",", 1)[0]
            directories.update(_test_directory_targets(root, path, module.strip()))
    return directories, modules


def test_only_hits(root: Path) -> list[Path]:
    """Every staged path that exists only to run tests, in bundle order."""
    references, imported = referenced_test_content(root)
    hits: list[Path] = []
    for directory, subdirectories, files in os.walk(root):
        current = Path(directory)
        retained = []
        for name in sorted(subdirectories):
            candidate = current / name
            if name in TEST_DIRECTORY_NAMES:
                if candidate not in references:
                    hits.append(candidate)
                    continue
            elif is_test_framework_entry(name) and name.endswith(".dist-info"):
                hits.append(candidate)
                continue
            elif name.lower() in TEST_FRAMEWORK_DISTRIBUTIONS:
                hits.append(candidate)
                continue
            retained.append(name)
        subdirectories[:] = retained
        for name in sorted(files):
            path = current / name
            if is_test_file_name(name):
                # A test package or module a package's own code imports stays whole:
                # `mpmath.runtests()` runs the very modules inside `mpmath/tests`, and torch's
                # Dynamo imports `torch._inductor.test_operators`. Keeping the directory and
                # deleting its tests would be the same lie in a smaller size.
                if path.stem not in imported and not references.intersection(path.parents):
                    hits.append(path)
            elif current == root / "bin" and is_test_framework_entry(name):
                hits.append(path)
    return hits


def _remove(path: Path) -> int:
    """Delete one staged path, returning the bytes it held. Staged runtimes are read-only."""
    parent_mode = stat.S_IMODE(path.parent.stat().st_mode)
    path.parent.chmod(parent_mode | stat.S_IWUSR)
    try:
        if path.is_dir() and not path.is_symlink():
            freed = sum(
                entry.lstat().st_size
                for entry in path.rglob("*")
                if entry.is_file() and not entry.is_symlink()
            )
            for entry in (path, *path.rglob("*")):
                if entry.is_symlink():
                    continue
                mode = stat.S_IMODE(entry.lstat().st_mode)
                entry.chmod(mode | stat.S_IWUSR | stat.S_IRUSR | stat.S_IXUSR)
            shutil.rmtree(path)
            return freed
        freed = path.lstat().st_size
        path.unlink()
        return freed
    finally:
        path.parent.chmod(parent_mode)


def strip_test_only(root: Path) -> dict[str, object]:
    """Remove every test-only file from a staged runtime and keep its manifest honest.

    Called at the build stage and again when a runtime is copied into an app bundle, because
    `.build/voice-runtime` is reused between releases: a plain build-time sweep cannot clean a
    tree that was already built.
    """
    hits = test_only_hits(root)
    freed = sum(_remove(path) for path in hits)
    manifest = root / MANIFEST_PATH
    if hits and manifest.is_file():
        directory_mode = stat.S_IMODE(manifest.parent.stat().st_mode)
        manifest_mode = stat.S_IMODE(manifest.stat().st_mode)
        manifest.parent.chmod(directory_mode | stat.S_IWUSR)
        manifest.chmod(manifest_mode | stat.S_IWUSR)
        write_file_manifest(root)
        manifest.chmod(manifest_mode)
        manifest.parent.chmod(directory_mode)
    remaining = test_only_hits(root)
    if remaining:
        raise RuntimeError(
            "test-only files survive in the runtime:\n"
            + json.dumps([path.relative_to(root).as_posix() for path in remaining], indent=2)
        )
    return {
        "removed": [path.relative_to(root).as_posix() for path in hits],
        "bytes": freed,
    }


def _strip_absolute_python_header(contents: bytes) -> bytes | None:
    """Return the script body after an absolute Python shebang or uv polyglot header."""
    first_line, separator, body = contents.partition(b"\n")
    if not separator:
        return None
    if first_line.startswith(b"#!") and b"python" in first_line.lower():
        return body
    # When the build directory path contains spaces (e.g. `My Own Apps`), uv writes a 3-line
    # `#!/bin/sh` polyglot wrapper whose second line hardcodes the absolute python3 path.
    lines = contents.splitlines(keepends=True)
    if len(lines) < 3:
        return None
    line0 = lines[0].rstrip(b"\r\n")
    line1 = lines[1].rstrip(b"\r\n")
    line2 = lines[2].rstrip(b"\r\n")
    prefix = b"'''exec' "
    suffix = b' "$0" "$@"'
    if line0 != b"#!/bin/sh" or line2 != b"' '''":
        return None
    if not (line1.startswith(prefix) and line1.endswith(suffix)):
        return None
    target = line1[len(prefix) : len(line1) - len(suffix)]
    if (target.startswith(b"'/") or target.startswith(b'"/')) and b"python" in target.lower():
        return b"".join(lines[3:])
    return None


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
        body = _strip_absolute_python_header(path.read_bytes())
        if body is None:
            continue
        path.write_bytes(header + body)
        changed.append(path.relative_to(root).as_posix())
    return changed


def prune_build_junk(root: Path) -> list[str]:
    """Remove files that describe the build machine or its caches, not the runtime.

    `.DS_Store` and `__pycache__`/`.pyc` are Finder and interpreter leftovers; `uv_cache.json`
    and `BUILD` are markers written by the installer. None of them is code, and any of them
    makes the digest depend on how the tree was produced.
    """
    remove_names = {".DS_Store", "uv_cache.json", "BUILD"}
    removed: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink() and (
                path.name in remove_names or path.suffix in {".pyc", ".pyo"}):
            path.unlink()
            removed.append(path.relative_to(root).as_posix())
    for path in sorted(root.rglob("__pycache__")):
        if path.is_dir() and not path.is_symlink() and not any(path.iterdir()):
            path.rmdir()
            removed.append(path.relative_to(root).as_posix())
    return sorted(removed)


def normalize_record_files(root: Path) -> list[str]:
    """Recompute every `.dist-info/RECORD` from the tree as it is now.

    The installer writes a RECORD entry for each console script **before** the shebang rewrite,
    so the recorded size and hash carry the build directory's path length. That made the tree
    digest a build-path identity: two checkouts of the same content could never agree. Running
    this after `normalize-shebangs` makes the tree a content identity again.
    """
    # Resolved, because every entry below is: on macOS /var and /tmp are themselves links, so an
    # unresolved root would not be a parent of any resolved path and every row would be dropped.
    root = root.resolve()
    changed: list[str] = []
    for record in sorted(root.rglob("*.dist-info/RECORD")):
        base = record.parent.parent
        rows: list[str] = []
        for line in record.read_text().splitlines():
            if not line:
                continue
            parts = line.rsplit(",", 2)
            relative = parts[0]
            target = base / relative
            resolved = target.resolve()
            if resolved == record.resolve():
                # The RECORD cannot hash itself: writing the hash would change the bytes it
                # describes. An empty hash and size is the standard self-entry, and it is also
                # what makes the file identical at every build path.
                rows.append(f"{relative},,")
                continue
            if root not in resolved.parents and resolved != root:
                continue  # a path outside the runtime is not this tree's to describe
            if target.is_symlink() or not target.is_file():
                continue  # build junk was pruned, or the entry is a link: RECORD lists files only
            digest = base64.urlsafe_b64encode(
                hashlib.sha256(target.read_bytes()).digest()).rstrip(b"=").decode()
            rows.append(f"{relative},sha256={digest},{target.stat().st_size}")
        text = "\n".join(rows) + "\n"
        if text != record.read_text():
            record.write_text(text)
            changed.append(record.relative_to(root).as_posix())
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

    strip_parser = subparsers.add_parser("strip-tests")
    strip_parser.add_argument("root", type=Path)

    prune_parser = subparsers.add_parser("prune-build-junk")
    prune_parser.add_argument("root", type=Path)

    record_parser = subparsers.add_parser("normalize-records")
    record_parser.add_argument("root", type=Path)

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
    elif args.command == "prune-build-junk":
        print(json.dumps(prune_build_junk(root), indent=2))
        return 0
    elif args.command == "normalize-records":
        print(json.dumps(normalize_record_files(root), indent=2))
        return 0
    elif args.command == "strip-tests":
        print(json.dumps(strip_test_only(root), indent=2))
        return 0
    else:
        changed = sanitize_macho_rpaths(root)
        print(json.dumps(changed, indent=2))
        return 0
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
