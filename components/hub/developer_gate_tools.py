"""Bounded preparation of official developer-gate tool archives.

Consumes only a trusted packaged manifest (``packaging/developer-gates/official-tools.json``).
The caller owns the shared lease and supplies three required callbacks:

* ``checkpoint(stage)`` - called at every bounded chunk/member and before promotion.
* ``open_stream(url, timeout_seconds)`` - a bounded stream provider; it must honour the
  timeout and follow only HTTPS redirects on the pinned/trusted hosts.
* ``probe(executable, timeout_seconds)`` - the caller's owned executor version readback.

This module never spawns a process, reads wire input, serves HTTP, or claims that an
installation is supported in production.  It verifies the pinned archive hash and size
before any executable is probed, extracts one archive in a single bounded pass, defers
all link creation until every regular file and directory is validated, and promotes a
content-addressed tree with one atomic rename.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import platform as platform_module
import posixpath
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping
from urllib.parse import unquote, urlsplit

from components.hub import developer_gate_contract as contract

MANIFEST_FORMAT = "studio.developer-gate.official-tools.v1"
RESULT_FORMAT = "studio.developer-gate.official-tool-preparation.v1"
RECEIPT_FORMAT = "studio.developer-gate.official-tool-receipt.v1"
DEFAULT_MANIFEST_PATH = (
    Path(__file__).resolve().parents[2] / "packaging/developer-gates/official-tools.json"
)

MIN_FREE_BYTES = 10 * 1024 ** 3
ABSOLUTE_MAX_MEMBERS = 200_000
ABSOLUTE_MAX_EXPANDED_BYTES = 4 * 1024 ** 3
ABSOLUTE_MAX_MEMBER_BYTES = 1024 ** 3
ABSOLUTE_MAX_ARCHIVE_BYTES = 1024 ** 3
ABSOLUTE_MAX_CACHE_BYTES = 4 * 1024 ** 3
ABSOLUTE_MAX_TOTAL_SECONDS = 3600
MANIFEST_MAX_BYTES = 1024 * 1024
TRUSTED_HTTPS_HOSTS = frozenset({"go.dev", "nodejs.org", "github.com"})
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
TOOL_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
VERSION_OUTPUT = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][0-9A-Za-z.-]+)?$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

_ACTIVE_ROOTS: set[str] = set()
_ACTIVE_ROOTS_LOCK = threading.Lock()

# One indirection so a test can state the free-space answer without a real disk.
_disk_usage = shutil.disk_usage

_LIMIT_KEYS = (
    "max_archive_bytes",
    "max_expanded_bytes",
    "max_member_bytes",
    "max_members",
    "max_path_length",
    "max_total_seconds",
    "min_free_bytes",
    "download_cache_bytes",
)
_ARTIFACT_REQUIRED = {
    "id",
    "version",
    "platform",
    "filename",
    "url",
    "size_bytes",
    "sha256",
    "executable",
}
_ARTIFACT_OPTIONAL = {"expanded_bytes"}
_RECEIPT_KEYS = {
    "format",
    "scope",
    "production_installer_qualified",
    "id",
    "version",
    "platform",
    "sha256",
    "size_bytes",
    "executable",
    "version_line",
    "tree_digest",
}


class OfficialToolError(contract.GateContractError):
    """A refused or failed bounded official-tool preparation."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 400,
        code: str = "malformed",
        detail: str | None = None,
        retained_staging: str | None = None,
    ) -> None:
        super().__init__(message, status=status, code=code, detail=detail)
        self.retained_staging = retained_staging


def _refuse(message: str, *, code: str = "invalid_request", status: int = 400) -> None:
    raise OfficialToolError(message, status=status, code=code)


def _rethrow(exc: contract.GateContractError) -> None:
    raise OfficialToolError(exc.message, status=exc.status, code=exc.code) from exc


def _check_keys(value: Any, required: set[str], optional: set[str], where: str) -> None:
    if not isinstance(value, Mapping):
        _refuse(f"{where} must be an object")
    missing = required - set(value)
    if missing:
        _refuse(f"{where} is missing keys: {', '.join(sorted(missing))}")
    unknown = set(value) - required - optional
    if unknown:
        _refuse(f"{where} has unknown keys: {', '.join(sorted(unknown))}")


def _int_field(value: Any, where: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _refuse(f"{where} must be an integer in [{minimum}, {maximum}]")
    return value


def _safe_relative(raw: Any, where: str, max_path_length: int) -> str:
    """Validate a manifest-supplied relative path with the shared gate path rules."""
    if not isinstance(raw, str) or not raw or len(raw) > max_path_length:
        _refuse(f"{where} must be a non-empty relative path within {max_path_length} characters")
    if "\x00" in raw or "\\" in raw:
        _refuse(f"{where} contains a forbidden character")
    try:
        norm = contract._validate_safe_member_path(raw, set(), set())
    except contract.GateContractError as exc:
        _rethrow(exc)
    if norm == ".":
        _refuse(f"{where} must name a path below the tool root")
    return norm


def _validate_url(url: Any, filename: str, where: str) -> str:
    if not isinstance(url, str) or not url or len(url) > 2048:
        _refuse(f"{where} must be an https URL")
    parts = urlsplit(url)
    if parts.scheme != "https":
        _refuse(f"{where} must use https")
    if parts.username or parts.password:
        _refuse(f"{where} must not contain credentials")
    try:
        port = parts.port
    except ValueError:
        _refuse(f"{where} has an invalid port")
    if port not in (None, 443):
        _refuse(f"{where} must use the default https port")
    if (parts.hostname or "").lower() not in TRUSTED_HTTPS_HOSTS:
        _refuse(f"{where} host is not in the trusted official set")
    if unquote(posixpath.basename(parts.path)) != filename:
        _refuse(f"{where} filename does not match the URL path")
    return url


def _runtime_platform() -> str:
    return f"{sys.platform}-{platform_module.machine()}"


def load_official_tools_manifest(
    manifest_path: Path | str | None = None,
) -> dict[str, Any]:
    """Load and strictly validate the trusted packaged official-tools manifest."""
    path = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST_PATH
    try:
        manifest_stat = os.lstat(path)
    except OSError as exc:
        _refuse(f"Official tools manifest is unavailable: {exc}")
    if not stat.S_ISREG(manifest_stat.st_mode):
        _refuse("Official tools manifest must be a regular file")
    if manifest_stat.st_size > MANIFEST_MAX_BYTES:
        _refuse("Official tools manifest exceeds the metadata ceiling", code="bounds", status=413)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _refuse(f"Official tools manifest is unreadable: {exc}")
    try:
        data = contract.parse_strict_json(raw, max_bytes=MANIFEST_MAX_BYTES)
    except contract.GateContractError as exc:
        _rethrow(exc)

    _check_keys(
        data,
        {"format", "scope", "production_installer_qualified", "platform", "limits", "artifacts"},
        {"notes"},
        "manifest",
    )
    if data["format"] != MANIFEST_FORMAT:
        _refuse(f"Unsupported official tools manifest format: {data['format']!r}")
    if data["scope"] != "archive-preparation-only":
        _refuse("Manifest scope must be archive-preparation-only")
    if data["production_installer_qualified"] is not False:
        _refuse("Manifest must not claim a qualified production installer")
    if data["platform"] != _runtime_platform():
        _refuse(
            f"Manifest platform {data['platform']!r} does not match this runtime "
            f"{_runtime_platform()!r}"
        )

    _check_keys(data["limits"], set(_LIMIT_KEYS), set(), "manifest.limits")
    limits = data["limits"]
    _int_field(limits["max_archive_bytes"], "max_archive_bytes", 1, ABSOLUTE_MAX_ARCHIVE_BYTES)
    _int_field(limits["max_expanded_bytes"], "max_expanded_bytes", 1, ABSOLUTE_MAX_EXPANDED_BYTES)
    _int_field(limits["max_member_bytes"], "max_member_bytes", 1, ABSOLUTE_MAX_MEMBER_BYTES)
    _int_field(limits["max_members"], "max_members", 1, ABSOLUTE_MAX_MEMBERS)
    _int_field(limits["max_path_length"], "max_path_length", 1, contract.ABSOLUTE_MAX_PATH_LENGTH)
    _int_field(limits["max_total_seconds"], "max_total_seconds", 1, ABSOLUTE_MAX_TOTAL_SECONDS)
    _int_field(limits["min_free_bytes"], "min_free_bytes", MIN_FREE_BYTES, 1 << 62)
    _int_field(limits["download_cache_bytes"], "download_cache_bytes", 1, ABSOLUTE_MAX_CACHE_BYTES)
    if limits["max_expanded_bytes"] < limits["max_member_bytes"]:
        _refuse("max_expanded_bytes must be at least max_member_bytes")

    artifacts = data["artifacts"]
    if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 16:
        _refuse("manifest.artifacts must contain between 1 and 16 pins")
    seen_ids: set[str] = set()
    for index, pin in enumerate(artifacts):
        where = f"manifest.artifacts[{index}]"
        _check_keys(pin, _ARTIFACT_REQUIRED, _ARTIFACT_OPTIONAL, where)
        if not isinstance(pin["id"], str) or not TOOL_ID.fullmatch(pin["id"]):
            _refuse(f"{where}.id is invalid")
        if pin["id"] in seen_ids:
            _refuse(f"{where}.id is duplicated")
        seen_ids.add(pin["id"])
        if not isinstance(pin["version"], str) or not VERSION.fullmatch(pin["version"]):
            _refuse(f"{where}.version is invalid")
        if pin["platform"] != data["platform"]:
            _refuse(f"{where}.platform does not match the manifest platform")
        filename = pin["filename"]
        if (
            not isinstance(filename, str)
            or not 0 < len(filename) <= 255
            or filename in (".", "..")
            or any(ch in filename for ch in "/\\:\x00")
            or unicodedata.normalize("NFC", filename) != filename
        ):
            _refuse(f"{where}.filename is invalid")
        _validate_url(pin["url"], filename, f"{where}.url")
        _int_field(pin["size_bytes"], f"{where}.size_bytes", 1, limits["max_archive_bytes"])
        if not isinstance(pin["sha256"], str) or not SHA256_HEX.fullmatch(pin["sha256"]):
            _refuse(f"{where}.sha256 must be 64 lowercase hex characters")
        _safe_relative(pin["executable"], f"{where}.executable", limits["max_path_length"])
        if "expanded_bytes" in pin:
            _int_field(
                pin["expanded_bytes"],
                f"{where}.expanded_bytes",
                0,
                limits["max_expanded_bytes"],
            )
    return data


def _validate_tools_root(raw_root: Path | str) -> Path:
    root = Path(raw_root)
    if not root.is_absolute():
        _refuse("tools_root must be an absolute owned path")
    if root.is_symlink():
        _refuse("tools_root must not be a symlink")
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        _refuse(f"tools_root is unavailable: {exc}")
    st = os.lstat(resolved)
    if not stat.S_ISDIR(st.st_mode):
        _refuse("tools_root must be a directory")
    if st.st_uid != os.geteuid():
        _refuse("tools_root must be owned by the current user")
    if st.st_mode & 0o022:
        _refuse("tools_root must not be group- or world-writable")
    return resolved


def _require_private_owned(path: Path) -> None:
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        _refuse(f"{path.name} must be a real directory")
    if st.st_uid != os.geteuid() or st.st_mode & 0o077:
        _refuse(f"{path.name} must be private and owned by the current user")


def _validate_attempt_root(raw_root: Path | str, tools_root: Path) -> Path:
    """Validate the caller-owned scratch root: private, owned, outside the store, same volume."""
    root = Path(raw_root)
    if not root.is_absolute():
        _refuse("attempt_root must be an absolute caller-owned scratch path")
    if root.is_symlink():
        _refuse("attempt_root must not be a symlink")
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        _refuse(f"attempt_root is unavailable: {exc}")
    st = os.lstat(resolved)
    if not stat.S_ISDIR(st.st_mode):
        _refuse("attempt_root must be a directory")
    if st.st_uid != os.geteuid() or st.st_mode & 0o077:
        _refuse("attempt_root must be private and owned by the current user")
    if _within(str(resolved), tools_root) or _within(str(tools_root), resolved):
        _refuse("attempt_root must be outside the persistent tools_root store")
    if st.st_dev != os.stat(tools_root).st_dev:
        _refuse("attempt_root must be on the same proven volume as tools_root")
    return resolved


def _private_subdir(root: Path, name: str) -> Path:
    path = root / name
    if os.path.lexists(path):
        _require_private_owned(path)
    else:
        os.mkdir(path, 0o700)
        _require_private_owned(path)
    return path


def _within(path: str, root: Path) -> bool:
    try:
        real_root = os.path.realpath(str(root))
        return os.path.commonpath([real_root, os.path.realpath(path)]) == real_root
    except ValueError:
        return False


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _make_guard(
    root: Path,
    limits: Mapping[str, int],
    deadline: float,
    checkpoint: Callable[[str], None],
) -> Callable[[str, int], None]:
    def guard(stage: str, reserve_bytes: int = 0) -> None:
        checkpoint(stage)
        if time.monotonic() > deadline:
            _refuse("Preparation deadline exceeded", code="bounds", status=413)
        free = _disk_usage(root).free
        if free - reserve_bytes < limits["min_free_bytes"]:
            _refuse(
                f"Free space {free} bytes cannot cover the next {reserve_bytes} bytes "
                f"above the required {limits['min_free_bytes']} bytes",
                code="bounds",
                status=507,
            )

    return guard


def _verify_archive(path: Path, pin: Mapping[str, Any], guard: Callable[[str], None]) -> bool:
    """Hash and size check a cached archive without following links."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return False
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size != pin["size_bytes"]:
            return False
        hasher = hashlib.sha256()
        total = 0
        while True:
            guard("verify")
            chunk = os.read(fd, DOWNLOAD_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > pin["size_bytes"]:
                _refuse("Cached archive exceeds its pinned size", code="bounds", status=413)
            hasher.update(chunk)
        return total == pin["size_bytes"] and hasher.hexdigest() == pin["sha256"]
    finally:
        os.close(fd)


def _remove_cache_entry(cache_path: Path) -> None:
    try:
        os.unlink(cache_path)
    except FileNotFoundError:
        pass
    try:
        os.rmdir(cache_path.parent)
    except OSError:
        pass


def _cache_bytes(cache_root: Path, guard: Callable[[str], None]) -> int:
    total = 0
    for entry in cache_root.iterdir():
        guard("cache")
        if entry.is_symlink() or not entry.is_dir():
            _refuse("Unexpected non-directory entry in the download cache")
        for item in entry.iterdir():
            if item.is_symlink() or not item.is_file():
                _refuse("Unexpected non-regular file in the download cache")
            total += item.stat(follow_symlinks=False).st_size
    return total


def _make_cache_room(
    cache_root: Path,
    needed: int,
    cap: int,
    guard: Callable[[str], None],
) -> None:
    if needed > cap:
        _refuse("Pinned archive cannot fit in the download cache", code="bounds", status=413)
    current = _cache_bytes(cache_root, guard)
    if current + needed <= cap:
        return
    entries = sorted(
        (entry for entry in cache_root.iterdir()),
        key=lambda entry: entry.stat(follow_symlinks=False).st_mtime,
    )
    for entry in entries:
        guard("cache")
        if current + needed <= cap:
            break
        if entry.is_symlink() or not entry.is_dir():
            _refuse("Unexpected entry in the download cache")
        size = sum(
            item.stat(follow_symlinks=False).st_size
            for item in entry.iterdir()
            if item.is_file() and not item.is_symlink()
        )
        shutil.rmtree(entry)
        current -= size
    if current + needed > cap:
        _refuse("Download cache cap cannot be met", code="bounds", status=413)


def _download_archive(
    url: str,
    destination: Path,
    pin: Mapping[str, Any],
    guard: Callable[[str], None],
    open_stream: Callable[[str, float], BinaryIO],
    deadline: float,
) -> None:
    """Stream one pinned URL into the attempt directory under read/size/time caps.

    The injected provider owns redirect policy: it must follow only HTTPS redirects on
    trusted official hosts and must honour the timeout it is given.  This module only
    accepts the pinned manifest URL and never accepts a caller-supplied URL.
    """
    stream = open_stream(url, _remaining(deadline))
    if not hasattr(stream, "read"):
        _refuse("Stream provider did not return a readable stream")
    hasher = hashlib.sha256()
    total = 0
    fd = os.open(
        destination,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        while True:
            remaining = pin["size_bytes"] - total
            chunk = stream.read(min(DOWNLOAD_CHUNK_BYTES, remaining + 1))
            if not chunk:
                break
            if not isinstance(chunk, (bytes, bytearray)):
                _refuse("Stream provider returned non-bytes data")
            guard("download", len(chunk))
            total += len(chunk)
            if total > pin["size_bytes"]:
                _refuse("Downloaded archive exceeds its pinned size", code="bounds", status=413)
            hasher.update(chunk)
            contract._write_all(fd, chunk)
        os.fsync(fd)
    finally:
        os.close(fd)
        close = getattr(stream, "close", None)
        if close is not None:
            close()
    guard("download")
    if total != pin["size_bytes"] or hasher.hexdigest() != pin["sha256"]:
        _refuse("Downloaded archive does not match its pinned size and SHA-256", code="integrity_error")


def _claim_path(
    paths: dict[str, str],
    casefold: dict[str, str],
    norm: str,
    kind: str,
) -> None:
    """Claim a member path and every implicit parent, rejecting aliases and conflicts."""
    parts = norm.split("/")
    for index in range(1, len(parts) + 1):
        prefix = "/".join(parts[:index])
        folded = prefix.casefold()
        previous = casefold.get(folded)
        if previous is not None and previous != prefix:
            _refuse(f"Case-colliding member paths: {previous!r} and {prefix!r}")
        casefold[folded] = prefix
        existing = paths.get(prefix)
        if index < len(parts):
            if existing is not None and existing != "dir":
                _refuse(f"Path component is not a directory: {prefix!r}")
            paths[prefix] = "dir"
            continue
        if existing is not None and not (existing == "dir" and kind == "dir"):
            _refuse(f"Duplicate or conflicting member path: {norm!r}")
        paths[prefix] = kind


def _reject_member_type(member: Any) -> None:
    if member.islnk():
        _refuse(f"Hardlinks are forbidden: {member.name!r}")
    if member.issparse() or getattr(member, "sparse", None):
        _refuse(f"Sparse members are forbidden: {member.name!r}")
    if member.ischr() or member.isblk() or member.isfifo():
        _refuse(f"Special members are forbidden: {member.name!r}")
    if not (member.isreg() or member.isdir() or member.issym()):
        _refuse(f"Unsupported member type {member.type!r}: {member.name!r}")
    if "\x00" in member.name:
        _refuse("Member name contains a NUL byte")


def _ensure_dirs(root: Path, parts: list[str]) -> None:
    current = root
    for part in parts:
        current = current / part
        if os.path.lexists(current):
            st = os.lstat(current)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                _refuse(f"Extraction path component is not a real directory: {current}")
        else:
            os.mkdir(current, 0o755)


def _write_member(
    tar: Any,
    member: Any,
    target: Path,
    guard: Callable[[str], None],
) -> None:
    if os.path.lexists(target):
        _refuse(f"Extraction target already exists: {target.name!r}")
    fd = os.open(
        target,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        source = tar.extractfile(member)
        if source is None:
            _refuse(f"Unable to read archive member: {member.name!r}")
        written = 0
        while True:
            chunk = source.read(contract.STREAM_CHUNK_BYTES)
            if not chunk:
                break
            guard("extract", len(chunk))
            written += len(chunk)
            if written > member.size:
                _refuse(f"Member stream exceeded its declared size: {member.name!r}")
            contract._write_all(fd, chunk)
        if written != member.size:
            _refuse(f"Incomplete member write: {member.name!r}")
        os.fchmod(fd, 0o755 if member.mode & 0o111 else 0o644)
    finally:
        os.close(fd)


def _create_links(
    root: Path,
    links: list[tuple[str, str]],
    paths: Mapping[str, str],
) -> None:
    """Create links only after every regular file and directory is validated and written."""
    for norm, linkname in links:
        if not linkname or "\x00" in linkname:
            _refuse(f"Invalid symlink target for {norm!r}")
        if linkname.startswith(("/", "\\")) or ":" in linkname:
            _refuse(f"Absolute symlink target is forbidden: {norm!r}")
        target = posixpath.normpath(posixpath.join(posixpath.dirname(norm), linkname))
        if target in ("", ".", "..") or target.startswith(("../", "/")):
            _refuse(f"Symlink target escapes the tool root: {norm!r}")
        if unicodedata.normalize("NFC", target) != target:
            _refuse(f"Symlink target is not NFC normalized: {norm!r}")
        kind = paths.get(target)
        if kind not in ("file", "dir"):
            _refuse(
                f"Symlink target is missing, loops, or is another link: {norm!r} -> {linkname!r}"
            )
        link_path = root.joinpath(*norm.split("/"))
        if os.path.lexists(link_path):
            _refuse(f"Symlink path already exists: {norm!r}")
        os.symlink(linkname, str(link_path))
        if not _within(os.path.realpath(link_path), root):
            _refuse(f"Symlink resolves outside the tool root: {norm!r}")


def _extract_archive(
    archive: Path,
    root: Path,
    pin: Mapping[str, Any],
    limits: Mapping[str, int],
    guard: Callable[[str], None],
) -> dict[str, int]:
    """Extract one pinned archive in a single bounded pass, then create validated links."""
    paths: dict[str, str] = {}
    casefold: dict[str, str] = {}
    links: list[tuple[str, str]] = []
    seen_norm: set[str] = set()
    seen_casefold: set[str] = set()
    total = regular = directories = symlinks = expanded = largest = 0

    try:
        with contract.open_bounded_tar(name=archive, mode="r:gz") as tar:
            while True:
                member = tar.next()
                if member is None:
                    break
                guard("extract")
                total += 1
                if total > limits["max_members"]:
                    _refuse("Archive member count exceeds its ceiling", code="bounds", status=413)
                _reject_member_type(member)
                norm = contract._validate_safe_member_path(member.name, seen_norm, seen_casefold)
                if norm == ".":
                    if member.isdir():
                        continue
                    _refuse("Archive root entry must be a directory")
                if len(norm) > limits["max_path_length"]:
                    _refuse("Member path exceeds its ceiling", code="bounds", status=413)
                parts = norm.split("/")
                kind = "dir" if member.isdir() else "link" if member.issym() else "file"
                _claim_path(paths, casefold, norm, kind)
                if member.isdir():
                    _ensure_dirs(root, parts)
                    directories += 1
                elif member.issym():
                    if not member.linkname or len(member.linkname) > limits["max_path_length"]:
                        _refuse(f"Symlink target exceeds its ceiling: {norm!r}", code="bounds", status=413)
                    links.append((norm, member.linkname))
                    symlinks += 1
                else:
                    if member.size < 0 or member.size > limits["max_member_bytes"]:
                        _refuse(f"Member exceeds the per-file ceiling: {norm!r}", code="bounds", status=413)
                    if expanded + member.size > limits["max_expanded_bytes"]:
                        _refuse("Expanded bytes exceed their ceiling", code="bounds", status=413)
                    guard("extract", member.size)
                    _ensure_dirs(root, parts[:-1])
                    _write_member(tar, member, root.joinpath(*parts), guard)
                    expanded += member.size
                    regular += 1
                    largest = max(largest, member.size)
    except contract.GateContractError as exc:
        if isinstance(exc, OfficialToolError):
            raise
        _rethrow(exc)

    _create_links(root, links, paths)
    return {
        "members": total,
        "regular_files": regular,
        "directories": directories,
        "symlinks": symlinks,
        "logical_bytes": expanded,
        "largest_member_bytes": largest,
    }


def _verify_contained_executable(root: Path, executable: str, max_path_length: int) -> Path:
    norm = _safe_relative(executable, "executable", max_path_length)
    target = root.joinpath(*norm.split("/"))
    if not os.path.lexists(target):
        _refuse(f"Pinned executable is missing: {executable!r}")
    st = os.lstat(target)
    if stat.S_ISLNK(st.st_mode):
        resolved = Path(os.path.realpath(target))
        if not _within(str(resolved), root):
            _refuse("Pinned executable symlink resolves outside the tool root")
        try:
            st = os.lstat(resolved)
        except OSError:
            _refuse("Pinned executable symlink target is missing")
    else:
        resolved = target
    if not stat.S_ISREG(st.st_mode):
        _refuse(f"Pinned executable is not a regular file: {executable!r}")
    if not st.st_mode & 0o111:
        _refuse(f"Pinned executable is not executable: {executable!r}")
    real = os.path.realpath(resolved)
    if not _within(real, root):
        _refuse("Pinned executable resolves outside the tool root")
    return Path(real)


def _probe_version(
    probe: Callable[[Path, float], Mapping[str, Any]],
    executable: Path,
    pin: Mapping[str, Any],
    deadline: float,
) -> str:
    remaining = _remaining(deadline)
    if remaining <= 0:
        _refuse("Preparation deadline exceeded before the version probe", code="bounds", status=413)
    readback = probe(executable, remaining)
    if not isinstance(readback, Mapping):
        _refuse("Version probe did not return a mapping")
    exit_code = readback.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code != 0:
        _refuse("Version probe did not exit successfully", code="integrity_error")
    if readback.get("process_release") is not True:
        _refuse("Version probe did not confirm exact process release", code="integrity_error")
    observed = readback.get("version")
    if not isinstance(observed, str) or not VERSION_OUTPUT.fullmatch(observed):
        _refuse("Version probe did not return a parsed exact version", code="integrity_error")
    if observed != pin["version"]:
        _refuse(
            f"Version probe parsed {observed!r}, expected exact pin {pin['version']!r}",
            code="integrity_error",
        )
    stdout = readback.get("stdout")
    if stdout is not None and (not isinstance(stdout, str) or len(stdout) > 64 * 1024):
        _refuse("Version probe returned invalid stdout", code="integrity_error")
    line = readback.get("version_line")
    if not isinstance(line, str) or not line.strip():
        line = next(
            (item for item in (stdout or "").splitlines() if observed in item),
            observed,
        )
    line = line.strip()[:512]
    if not line:
        _refuse("Version probe returned an empty version line", code="integrity_error")
    return line


def _write_receipt(
    root: Path,
    pin: Mapping[str, Any],
    version_line: str,
    tree_digest: str,
    guard: Callable[[str, int], None],
) -> None:
    payload = {
        "format": RECEIPT_FORMAT,
        "scope": "archive-preparation-only",
        "production_installer_qualified": False,
        "id": pin["id"],
        "version": pin["version"],
        "platform": pin["platform"],
        "sha256": pin["sha256"],
        "size_bytes": pin["size_bytes"],
        "executable": pin["executable"],
        "version_line": version_line,
        "tree_digest": tree_digest,
    }
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    path = root / ".prepared.json"
    guard("receipt", len(data))
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        contract._write_all(fd, data)
        os.fchmod(fd, 0o444)
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_receipt(root: Path, pin: Mapping[str, Any]) -> dict[str, Any]:
    """Read the retained proof that this content-addressed tree came from a verified pin.

    The receipt is written only after the archive hash/size check and the contained
    executable version readback, so a reused tree does not need the archive again.
    """
    path = root / ".prepared.json"
    try:
        st = os.lstat(path)
    except OSError:
        _refuse("Existing tool tree has no preparation receipt")
    if not stat.S_ISREG(st.st_mode) or st.st_size > 16 * 1024:
        _refuse("Existing tool tree receipt is invalid")
    try:
        raw = path.read_bytes()
        receipt = contract.parse_strict_json(raw, max_bytes=16 * 1024)
    except contract.GateContractError as exc:
        _rethrow(exc)
    except OSError as exc:
        _refuse(f"Existing tool tree receipt is unreadable: {exc}")
    _check_keys(receipt, _RECEIPT_KEYS, set(), "receipt")
    expected = {
        "format": RECEIPT_FORMAT,
        "scope": "archive-preparation-only",
        "production_installer_qualified": False,
        "id": pin["id"],
        "version": pin["version"],
        "platform": pin["platform"],
        "sha256": pin["sha256"],
        "size_bytes": pin["size_bytes"],
        "executable": pin["executable"],
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            _refuse(f"Existing tool tree receipt does not match the pin field {key!r}")
    tree_digest = receipt.get("tree_digest")
    if not isinstance(tree_digest, str) or not SHA256_HEX.fullmatch(tree_digest):
        _refuse("Existing tool tree receipt has no valid content digest")
    return receipt


def _measure_tree(
    root: Path,
    limits: Mapping[str, int],
    guard: Callable[[str], None],
) -> dict[str, int]:
    stack = [root]
    files = directories = symlinks = members = 0
    logical = allocated = largest = 0
    while stack:
        guard("measure")
        directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                guard("measure")
                members += 1
                if members > limits["max_members"]:
                    _refuse("Prepared tree member count exceeds its ceiling", code="bounds", status=413)
                st = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    directories += 1
                    stack.append(Path(entry.path))
                elif stat.S_ISREG(st.st_mode):
                    files += 1
                    logical += st.st_size
                    allocated += getattr(st, "st_blocks", 0) * 512
                    largest = max(largest, st.st_size)
                    if st.st_size > limits["max_member_bytes"]:
                        _refuse("Prepared tree file exceeds the per-file ceiling", code="bounds", status=413)
                    if logical > limits["max_expanded_bytes"]:
                        _refuse("Prepared tree exceeds the expanded ceiling", code="bounds", status=413)
                elif stat.S_ISLNK(st.st_mode):
                    symlinks += 1
                    resolved = os.path.realpath(entry.path)
                    if not _within(resolved, root):
                        _refuse(f"Prepared symlink escapes the tool root: {entry.name!r}")
                    if not os.path.exists(resolved):
                        _refuse(f"Prepared symlink target is missing: {entry.name!r}")
                    target_st = os.lstat(resolved)
                    if not (stat.S_ISREG(target_st.st_mode) or stat.S_ISDIR(target_st.st_mode)):
                        _refuse(f"Prepared symlink target is unsupported: {entry.name!r}")
                else:
                    _refuse(f"Prepared tree contains a special file: {entry.name!r}")
    return {
        "regular_files": files,
        "directories": directories,
        "symlinks": symlinks,
        "members": members,
        "logical_bytes": logical,
        "allocated_bytes": allocated,
        "largest_member_bytes": largest,
    }


def _hash_regular(
    path: Path,
    guard: Callable[[str, int], None],
    limits: Mapping[str, int],
) -> tuple[int, str]:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            _refuse(f"Digest input is not a regular file: {path.name!r}")
        if st.st_size > limits["max_member_bytes"]:
            _refuse("Digest input exceeds the per-file ceiling", code="bounds", status=413)
        hasher = hashlib.sha256()
        total = 0
        while True:
            guard("digest")
            chunk = os.read(fd, DOWNLOAD_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > st.st_size:
                _refuse(f"Digest input grew while reading: {path.name!r}", code="integrity_error")
            hasher.update(chunk)
        if total != st.st_size:
            _refuse(f"Digest input changed size while reading: {path.name!r}", code="integrity_error")
        return total, hasher.hexdigest()
    finally:
        os.close(fd)


def _tree_digest(
    root: Path,
    limits: Mapping[str, int],
    guard: Callable[[str, int], None],
) -> str:
    """Hash paths, modes, link targets and file contents, excluding the receipt."""
    entries: list[tuple[str, str, int, int, str]] = []
    stack = [(root, "")]
    members = 0
    logical = 0
    while stack:
        guard("digest")
        directory, relative = stack.pop()
        with os.scandir(directory) as scanned:
            for entry in scanned:
                guard("digest")
                members += 1
                if members > limits["max_members"]:
                    _refuse("Digest member count exceeds its ceiling", code="bounds", status=413)
                relpath = f"{relative}/{entry.name}" if relative else entry.name
                if relpath == ".prepared.json":
                    continue
                st = entry.stat(follow_symlinks=False)
                mode = stat.S_IMODE(st.st_mode)
                if stat.S_ISDIR(st.st_mode):
                    entries.append(("d", relpath, mode, 0, ""))
                    stack.append((Path(entry.path), relpath))
                elif stat.S_ISREG(st.st_mode):
                    size, digest = _hash_regular(Path(entry.path), guard, limits)
                    logical += size
                    if logical > limits["max_expanded_bytes"]:
                        _refuse("Digest bytes exceed the expanded ceiling", code="bounds", status=413)
                    entries.append(("f", relpath, mode, size, digest))
                elif stat.S_ISLNK(st.st_mode):
                    linkname = os.readlink(entry.path)
                    resolved = os.path.realpath(entry.path)
                    if not _within(resolved, root) or not os.path.exists(resolved):
                        _refuse(f"Digest symlink target is missing or escaping: {relpath!r}")
                    target_st = os.lstat(resolved)
                    if not (stat.S_ISREG(target_st.st_mode) or stat.S_ISDIR(target_st.st_mode)):
                        _refuse(f"Digest symlink target is unsupported: {relpath!r}")
                    entries.append(("l", relpath, mode, 0, linkname))
                else:
                    _refuse(f"Digest tree contains a special file: {relpath!r}")
    entries.sort(key=lambda item: item[1])
    hasher = hashlib.sha256()
    for kind, relpath, mode, size, value in entries:
        if kind == "d":
            hasher.update(f"d\0{relpath}\0{mode:o}\n".encode("utf-8"))
        elif kind == "f":
            hasher.update(f"f\0{relpath}\0{mode:o}\0{size}\0{value}\n".encode("utf-8"))
        else:
            hasher.update(f"l\0{relpath}\0{mode:o}\0{value}\n".encode("utf-8"))
    return hasher.hexdigest()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _acquire_preparation_lock(root: Path) -> tuple[int, str]:
    """Take an exclusive in-process + flock lease for the whole preparation."""
    key = os.path.realpath(str(root))
    with _ACTIVE_ROOTS_LOCK:
        if key in _ACTIVE_ROOTS:
            _refuse("Another preparation is active for this tools root", code="busy", status=409)
        _ACTIVE_ROOTS.add(key)
    fd = -1
    try:
        fd = os.open(
            root / ".preparation.lock",
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid():
            _refuse("Preparation lock file is not a private owned regular file")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if fd >= 0:
            os.close(fd)
        with _ACTIVE_ROOTS_LOCK:
            _ACTIVE_ROOTS.discard(key)
        _refuse(f"Another preparation holds the tools-root lock: {exc}", code="busy", status=409)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        with _ACTIVE_ROOTS_LOCK:
            _ACTIVE_ROOTS.discard(key)
        raise
    return fd, key


def _release_preparation_lock(fd: int, key: str) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
        with _ACTIVE_ROOTS_LOCK:
            _ACTIVE_ROOTS.discard(key)


def _cleanup_attempt(
    attempt: Path,
    *,
    probe_started: bool,
    probe_confirmed: bool,
    active_exception: bool,
) -> None:
    if probe_started and not probe_confirmed:
        return
    try:
        shutil.rmtree(attempt)
    except FileNotFoundError:
        return
    except OSError as exc:
        if not active_exception:
            raise OfficialToolError(
                f"Staging cleanup failed: {exc}", status=500, code="cleanup_error"
            ) from exc


def _result(
    pin: Mapping[str, Any],
    tools_root: Path,
    tool_root: Path,
    executable: Path,
    version_line: str,
    footprint: Mapping[str, int],
    *,
    archive_path: Path | None,
    archive_verified: bool,
    reused: bool,
    extraction: Mapping[str, int] | None,
) -> dict[str, Any]:
    return {
        "format": RESULT_FORMAT,
        "scope": "archive-preparation-only",
        "production_installer_qualified": False,
        "authority": "trusted packaged manifest only; no supported-production claim",
        "tool": {
            "id": pin["id"],
            "version": pin["version"],
            "platform": pin["platform"],
            "sha256": pin["sha256"],
            "size_bytes": pin["size_bytes"],
            "expanded_bytes": pin.get("expanded_bytes"),
        },
        "paths": {
            "tools_root": str(tools_root),
            "tool_root": str(tool_root),
            "executable": str(executable),
        },
        "version_line": version_line,
        "footprint": dict(footprint),
        "archive": {
            "path": str(archive_path) if archive_path is not None else None,
            "sha256_verified_this_call": archive_verified,
        },
        "reused_existing_tree": reused,
        "extraction": dict(extraction) if extraction is not None else None,
    }


def _prepare_locked(
    root: Path,
    attempt_root: Path,
    pin: Mapping[str, Any],
    limits: Mapping[str, int],
    deadline: float,
    checkpoint: Callable[[str], None],
    open_stream: Callable[[str, float], BinaryIO],
    probe: Callable[[Path, float], Mapping[str, Any]],
) -> dict[str, Any]:
    cache_root = _private_subdir(root, "cache")
    tools_dir = _private_subdir(root, "tools")
    guard = _make_guard(root, limits, deadline, checkpoint)
    guard("start")

    final = tools_dir / f"{pin['id']}-{pin['version']}-{pin['sha256']}"
    if os.path.lexists(final):
        if final.is_symlink() or not final.is_dir():
            _refuse("Content-addressed tool tree path is not a real directory")
        receipt = _read_receipt(final, pin)
        guard("digest")
        digest = _tree_digest(final, limits, guard)
        if digest != receipt["tree_digest"]:
            _refuse("Retained tool tree content digest changed", code="integrity_error")
        executable = _verify_contained_executable(final, pin["executable"], limits["max_path_length"])
        guard("probe")
        version_line = _probe_version(probe, executable, pin, deadline)
        footprint = _measure_tree(final, limits, guard)
        return _result(
            pin,
            root,
            final,
            executable,
            version_line,
            footprint,
            archive_path=None,
            archive_verified=False,
            reused=True,
            extraction=None,
        )

    install_estimate = pin.get("expanded_bytes", limits["max_expanded_bytes"])
    guard("preflight", pin["size_bytes"] + install_estimate)
    attempt = Path(
        tempfile.mkdtemp(prefix=f"official-tool-{pin['id']}-", dir=attempt_root)
    )
    probe_started = False
    probe_confirmed = False
    try:
        _require_private_owned(attempt)
        cache_dir = _private_subdir(cache_root, pin["sha256"])
        cache_path = cache_dir / pin["filename"]
        archive_path: Path | None = None
        archive_verified = False
        if os.path.lexists(cache_path):
            if _verify_archive(cache_path, pin, guard):
                archive_path = cache_path
                archive_verified = True
            else:
                _remove_cache_entry(cache_path)
        if archive_path is None:
            # The exclusive preparation lock means no other call can be using the cache.
            _make_cache_room(cache_root, pin["size_bytes"], limits["download_cache_bytes"], guard)
            downloaded = attempt / "archive"
            _download_archive(pin["url"], downloaded, pin, guard, open_stream, deadline)
            guard("cache")
            os.replace(downloaded, cache_path)
            archive_path = cache_path
            archive_verified = True

        guard("extract")
        extract_root = attempt / "tree"
        os.mkdir(extract_root, 0o700)
        extraction = _extract_archive(archive_path, extract_root, pin, limits, guard)
        if "expanded_bytes" in pin and extraction["logical_bytes"] != pin["expanded_bytes"]:
            _refuse(
                f"Expanded bytes {extraction['logical_bytes']} do not match the pin "
                f"{pin['expanded_bytes']}",
                code="integrity_error",
            )
        executable = _verify_contained_executable(
            extract_root, pin["executable"], limits["max_path_length"]
        )
        guard("probe")
        probe_started = True
        try:
            version_line = _probe_version(probe, executable, pin, deadline)
        except Exception as exc:
            raise OfficialToolError(
                f"Version probe did not confirm exact process release; staging retained at {attempt}",
                status=500,
                code="probe_release_unknown",
                retained_staging=str(attempt),
            ) from exc
        probe_confirmed = True
        executable = _verify_contained_executable(
            extract_root, pin["executable"], limits["max_path_length"]
        )
        guard("digest")
        tree_digest = _tree_digest(extract_root, limits, guard)
        _write_receipt(extract_root, pin, version_line, tree_digest, guard)
        _fsync_dir(extract_root)
        footprint = _measure_tree(extract_root, limits, guard)
        guard("promote")
        if os.path.lexists(final):
            _refuse("Content-addressed tool tree appeared during preparation")
        os.rename(extract_root, final)
        _fsync_dir(tools_dir)
        final_executable = _verify_contained_executable(
            final, pin["executable"], limits["max_path_length"]
        )
        return _result(
            pin,
            root,
            final,
            final_executable,
            version_line,
            footprint,
            archive_path=archive_path,
            archive_verified=archive_verified,
            reused=False,
            extraction=extraction,
        )
    finally:
        _cleanup_attempt(
            attempt,
            probe_started=probe_started,
            probe_confirmed=probe_confirmed,
            active_exception=sys.exc_info()[0] is not None,
        )


def prepare_official_tool(
    tool_id: str,
    *,
    tools_root: Path | str,
    attempt_root: Path | str,
    checkpoint: Callable[[str], None] | None,
    open_stream: Callable[[str, float], BinaryIO] | None,
    probe: Callable[[Path, float], Mapping[str, Any]] | None,
    manifest_path: Path | str | None = None,
    deadline_monotonic: float | None = None,
    max_total_seconds: float | None = None,
) -> dict[str, Any]:
    """Prepare one pinned official tool tree under ``tools_root`` or refuse.

    ``attempt_root`` is the caller-owned scratch workspace on the same volume; per-tool
    staging is created there and never under the persistent tools/cache store.  The
    three callbacks are mandatory.  There is no default downloader, process runner,
    URL input, argv input, or generic package path: the only authority is the trusted
    packaged manifest and the caller's lease-aware executor.
    """
    if not callable(checkpoint) or not callable(open_stream) or not callable(probe):
        _refuse("checkpoint, open_stream and probe callbacks are required")
    if not isinstance(tool_id, str) or not TOOL_ID.fullmatch(tool_id):
        _refuse("tool_id is invalid")

    manifest = load_official_tools_manifest(manifest_path)
    pin = next((item for item in manifest["artifacts"] if item["id"] == tool_id), None)
    if pin is None:
        _refuse(f"Tool {tool_id!r} is not in the trusted packaged manifest")

    limits = manifest["limits"]
    seconds = float(limits["max_total_seconds"])
    if max_total_seconds is not None:
        if not isinstance(max_total_seconds, (int, float)) or not math.isfinite(max_total_seconds):
            _refuse("max_total_seconds must be finite")
        seconds = min(seconds, float(max_total_seconds))
    if seconds <= 0:
        _refuse("max_total_seconds must be positive")
    deadline = time.monotonic() + seconds
    if deadline_monotonic is not None:
        if not isinstance(deadline_monotonic, (int, float)) or not math.isfinite(deadline_monotonic):
            _refuse("deadline_monotonic must be finite")
        deadline = min(deadline, float(deadline_monotonic))
    if deadline <= time.monotonic():
        _refuse("Preparation deadline is already expired", code="bounds", status=413)

    root = _validate_tools_root(tools_root)
    scratch = _validate_attempt_root(attempt_root, root)
    lock_fd, lock_key = _acquire_preparation_lock(root)
    try:
        return _prepare_locked(
            root, scratch, pin, limits, deadline, checkpoint, open_stream, probe
        )
    finally:
        _release_preparation_lock(lock_fd, lock_key)
