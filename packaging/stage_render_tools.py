#!/usr/bin/env python3
"""Copy the two pinned universal render tools into a fresh app staging tree."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Literal, Sequence


TOOL_VERSION = "8.1"
PAYLOAD_FORMAT = "macos-universal"
STAGING_LEAF = "render-tools-stage"
SCRATCH_LEAF = "render-tools-manifest-scratch"
SCRATCH_MANIFEST = "MANIFEST.json.complete"
AUTHORITY_SCRATCH_LEAF = "render-authority-scratch"
_MANIFEST_FORMAT = "studiohub.render-tools.payload.v1"
_READ_SIZE = 1024 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


@dataclass(frozen=True)
class _ToolPin:
    name: str
    source: Path
    size_bytes: int
    sha256: str


RenderAuthorityMode = Literal["tool-only", "descriptor-only", "sealed-candidate"]
RENDER_AUTHORITY_MODES: tuple[RenderAuthorityMode, ...] = (
    "tool-only",
    "descriptor-only",
    "sealed-candidate",
)


@dataclass(frozen=True)
class _AuthorityPin:
    relative_path: str
    source: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class RenderAuthorityStageResult:
    staged_paths: tuple[Path, ...]


# The identities are fixed; only the directory holding the binaries is supplied by the build
# (`stage_voice.py --render-tools-dir`), so any other binary fails the size/hash check.
_PRODUCTION_IDENTITIES = (
    ("ffmpeg", 132141880,
     "ba3a9d9d20b43492a85649d7e03122144905e3f07b713279fb5a0e659cf73e84"),
    ("ffprobe", 131770248,
     "f9827709561c11162a9fe93380fe6ecd92648461faf53c5bf5eb3a037442621e"),
)


def production_pins(tools_dir: Path) -> tuple[_ToolPin, _ToolPin]:
    """The two fixed pins, sourced from ``tools_dir`` (absolute, no default, no fallback)."""
    tools_dir = Path(tools_dir).expanduser()
    if not tools_dir.is_absolute() or not tools_dir.is_dir():
        raise ValueError(f"render-tools directory must be an existing absolute directory: {tools_dir}")
    return tuple(_ToolPin(name, tools_dir / name, size, sha)
                 for name, size, sha in _PRODUCTION_IDENTITIES)


# The packaged descriptors, frozen by exact byte identity. Descriptor-only stages these three
# and no qualification vector, so a Hub build can carry the recipe without claiming a run it
# has not made: production authority still comes only from a node's own enabled/ record.
_AUTHORITY_DIRECTORY = Path(__file__).resolve().parent / "render-authority"
PRODUCTION_AUTHORITY_PINS: tuple[_AuthorityPin, ...] = (
    _AuthorityPin(
        "PROFILE.json", _AUTHORITY_DIRECTORY / "PROFILE.json", 970,
        "ad3f1c9d47d5f59400b1b1d03596a59fba6dc2f26817ac5cc2c77f8837f24863",
    ),
    _AuthorityPin(
        "TOOLS.json", _AUTHORITY_DIRECTORY / "TOOLS.json", 796,
        "98c25ae582d4ee43ee1cf665192365149f8d9f860c229a0be9da131df43eba8a",
    ),
    _AuthorityPin(
        "COMPILER.json", _AUTHORITY_DIRECTORY / "COMPILER.json", 447,
        "1239374f6cf60d025ee897c4c6ea394892d2bc146b0f1b2df94e5eaecc86b4a7",
    ),
)


_LATE_LABEL_DIRECTORY = Path(__file__).resolve().parent / "render-authority-late-label"
LATE_LABEL_AUTHORITY_PINS: tuple[_AuthorityPin, ...] = (
    _AuthorityPin(
        "PROFILE.json", _LATE_LABEL_DIRECTORY / "PROFILE.json", 976,
        "b39f239092d8854be82b4a99df0cd591f9cdef05553d7741765b35ac72ca2b3c",
    ),
    _AuthorityPin(
        "TOOLS.json", _LATE_LABEL_DIRECTORY / "TOOLS.json", 796,
        "98c25ae582d4ee43ee1cf665192365149f8d9f860c229a0be9da131df43eba8a",
    ),
    _AuthorityPin(
        "COMPILER.json", _LATE_LABEL_DIRECTORY / "COMPILER.json", 458,
        "8eb1f962735692144ef8f3627ee8080d0d338c6295781bcde3550d6eef7834e3",
    ),
)


@dataclass(frozen=True)
class RenderToolsStageResult:
    state: str
    staging_root: Path
    manifest_path: Path
    manifest_sha256: str


class PublishedButDurabilityUnconfirmed(RuntimeError):
    """The complete manifest is visible, but its directory fsync failed."""

    state = "published_but_durability_unconfirmed"

    def __init__(self, manifest_path: Path, manifest_sha256: str) -> None:
        self.manifest_path = manifest_path
        self.manifest_sha256 = manifest_sha256
        super().__init__(
            f"render-tool manifest was published at {manifest_path}, but directory durability "
            "could not be confirmed"
        )


def _open_private_root(path: Path) -> int:
    fd = os.open(os.fspath(path), os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"staging parent is not a directory: {path}")
        if stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError(f"staging parent must have mode 0700: {path}")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _mkdir_open(parent_fd: int, name: str) -> int:
    os.mkdir(name, 0o700, dir_fd=parent_fd)
    fd = os.open(name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=parent_fd)
    try:
        os.fchmod(fd, 0o700)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _stat_identity(info: os.stat_result) -> tuple:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
    )


def _write_all(fd: int, data: bytes) -> None:
    if os.write(fd, data) != len(data):
        raise OSError("short write while staging render tools")


def _validate_pins(pins: Sequence[_ToolPin]) -> tuple[_ToolPin, _ToolPin]:
    fixed = tuple(pins)
    if tuple(pin.name for pin in fixed) != ("ffmpeg", "ffprobe"):
        raise ValueError("render-tool pins must be ordered ffmpeg, ffprobe")
    for pin in fixed:
        if type(pin.size_bytes) is not int or pin.size_bytes <= 0:
            raise ValueError(f"invalid pinned size for {pin.name}")
        if len(pin.sha256) != 64 or any(character not in "0123456789abcdef" for character in pin.sha256):
            raise ValueError(f"invalid pinned sha256 for {pin.name}")
    return fixed


def _copy_payload(directory_fd: int, pin: _ToolPin) -> None:
    source_fd = os.open(
        os.fspath(pin.source), os.O_RDONLY | os.O_NONBLOCK | _NOFOLLOW
    )
    output_fd = None
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"render-tool source is not a regular file: {pin.source}")
        if before.st_size != pin.size_bytes:
            raise ValueError(
                f"render-tool source size mismatch for {pin.name}: "
                f"expected {pin.size_bytes}, found {before.st_size}"
            )
        output_fd = os.open(
            pin.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o755,
            dir_fd=directory_fd,
        )
        os.fchmod(output_fd, 0o755)
        digest = hashlib.sha256()
        copied = 0
        while True:
            chunk = os.read(source_fd, _READ_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            copied += len(chunk)
            _write_all(output_fd, chunk)
        after = os.fstat(source_fd)
        if _stat_identity(before) != _stat_identity(after):
            raise ValueError(f"render-tool source changed while being copied: {pin.name}")
        if copied != pin.size_bytes or digest.hexdigest() != pin.sha256:
            raise ValueError(f"render-tool source content mismatch: {pin.name}")
        os.fsync(output_fd)
    finally:
        if output_fd is not None:
            os.close(output_fd)
        os.close(source_fd)


def _hash_fd(fd: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = os.read(fd, _READ_SIZE)
        if not chunk:
            return size, digest.hexdigest()
        size += len(chunk)
        digest.update(chunk)


def _verify_output(directory_fd: int, pin: _ToolPin) -> None:
    fd = os.open(pin.name, os.O_RDONLY | os.O_NONBLOCK | _NOFOLLOW, dir_fd=directory_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"staged render tool is not a regular file: {pin.name}")
        if stat.S_IMODE(info.st_mode) != 0o755:
            raise ValueError(f"staged render tool has wrong mode: {pin.name}")
        size, digest = _hash_fd(fd)
        if size != pin.size_bytes or digest != pin.sha256:
            raise ValueError(f"staged render tool verification failed: {pin.name}")
    finally:
        os.close(fd)


def _manifest_document(pins: Sequence[_ToolPin]) -> dict:
    document = {
        "format": _MANIFEST_FORMAT,
        "tool_version": TOOL_VERSION,
        "payload_format": PAYLOAD_FORMAT,
        "files": [
            {
                "name": pin.name,
                "path": f"render-tools/{pin.name}",
                "sha256": pin.sha256,
                "size_bytes": pin.size_bytes,
            }
            for pin in pins
        ],
        "production_eligible": False,
    }
    _validate_manifest(document, pins)
    return document


def _validate_manifest(document: object, pins: Sequence[_ToolPin]) -> None:
    if not isinstance(document, dict) or list(document) != [
        "format",
        "tool_version",
        "payload_format",
        "files",
        "production_eligible",
    ]:
        raise ValueError("render-tool manifest has an invalid top-level shape")
    if (
        document["format"] != _MANIFEST_FORMAT
        or document["tool_version"] != TOOL_VERSION
        or document["payload_format"] != PAYLOAD_FORMAT
        or document["production_eligible"] is not False
    ):
        raise ValueError("render-tool manifest has invalid fixed fields")
    expected = [
        {
            "name": pin.name,
            "path": f"render-tools/{pin.name}",
            "sha256": pin.sha256,
            "size_bytes": pin.size_bytes,
        }
        for pin in pins
    ]
    if document["files"] != expected:
        raise ValueError("render-tool manifest has invalid payload records")
    for record in document["files"]:
        if list(record) != ["name", "path", "sha256", "size_bytes"]:
            raise ValueError("render-tool manifest payload has an open key set")
        if type(record["size_bytes"]) is not int or record["size_bytes"] <= 0:
            raise ValueError("render-tool manifest payload has an invalid size")


def _write_scratch_manifest(scratch_fd: int, pins: Sequence[_ToolPin]) -> tuple[bytes, str]:
    raw = (json.dumps(_manifest_document(pins), indent=2) + "\n").encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    fd = os.open(
        SCRATCH_MANIFEST,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
        0o644,
        dir_fd=scratch_fd,
    )
    try:
        os.fchmod(fd, 0o644)
        _write_all(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)

    verify_fd = os.open(
        SCRATCH_MANIFEST, os.O_RDONLY | os.O_NONBLOCK | _NOFOLLOW, dir_fd=scratch_fd
    )
    try:
        info = os.fstat(verify_fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o644:
            raise ValueError("scratch render-tool manifest has invalid type or mode")
        size, actual_digest = _hash_fd(verify_fd)
        if size != len(raw) or actual_digest != digest:
            raise ValueError("scratch render-tool manifest verification failed")
    finally:
        os.close(verify_fd)
    _validate_manifest(json.loads(raw), pins)
    os.fsync(scratch_fd)
    return raw, digest


def _stage_render_tools(
    build_root: Path, scratch_root: Path, pins: Sequence[_ToolPin]
) -> RenderToolsStageResult:
    """Trusted fixture seam; callers use :func:`stage_render_tools` with fixed pins."""
    pins = _validate_pins(pins)
    build_root = Path(build_root).expanduser().absolute()
    scratch_root = Path(scratch_root).expanduser().absolute()
    build_fd = _open_private_root(build_root)
    scratch_root_fd = None
    stage_fd = contents_fd = resources_fd = tools_fd = scratch_fd = None
    manifest_path = (
        build_root
        / STAGING_LEAF
        / "Contents"
        / "Resources"
        / "render-tools"
        / "MANIFEST.json"
    )
    try:
        scratch_root_fd = _open_private_root(scratch_root)
        if os.fstat(build_fd).st_dev != os.fstat(scratch_root_fd).st_dev:
            raise ValueError("build and scratch roots must be on the same filesystem")
        stage_fd = _mkdir_open(build_fd, STAGING_LEAF)
        contents_fd = _mkdir_open(stage_fd, "Contents")
        resources_fd = _mkdir_open(contents_fd, "Resources")
        tools_fd = _mkdir_open(resources_fd, "render-tools")

        for pin in pins:
            _copy_payload(tools_fd, pin)
            _verify_output(tools_fd, pin)

        scratch_fd = _mkdir_open(scratch_root_fd, SCRATCH_LEAF)
        _, manifest_sha256 = _write_scratch_manifest(scratch_fd, pins)
        if os.fstat(scratch_fd).st_dev != os.fstat(tools_fd).st_dev:
            raise ValueError("manifest scratch and destination must be on the same filesystem")
        os.link(
            SCRATCH_MANIFEST,
            "MANIFEST.json",
            src_dir_fd=scratch_fd,
            dst_dir_fd=tools_fd,
            follow_symlinks=False,
        )
        try:
            os.fsync(tools_fd)
        except OSError as error:
            raise PublishedButDurabilityUnconfirmed(
                manifest_path, manifest_sha256
            ) from error
        return RenderToolsStageResult(
            state="complete",
            staging_root=build_root / STAGING_LEAF,
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256,
        )
    finally:
        for fd in (scratch_fd, tools_fd, resources_fd, contents_fd, stage_fd, scratch_root_fd, build_fd):
            if fd is not None:
                os.close(fd)


def _stage_render_tools_into(
    resources_root: Path, scratch_root: Path, pins: Sequence[_ToolPin],
) -> RenderToolsStageResult:
    """Use the same pinned copier for an already-created app Resources directory."""
    pins = _validate_pins(pins)
    resources_root = Path(resources_root).expanduser().absolute()
    scratch_root = Path(scratch_root).expanduser().absolute()
    resources_fd = os.open(resources_root, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
    scratch_root_fd = tools_fd = scratch_fd = None
    manifest_path = resources_root / "render-tools" / "MANIFEST.json"
    try:
        visible = resources_root.lstat()
        opened = os.fstat(resources_fd)
        if (not stat.S_ISDIR(opened.st_mode) or stat.S_ISLNK(visible.st_mode)
                or _stat_identity(opened) != _stat_identity(visible)):
            raise ValueError("app Resources destination is unsafe")
        scratch_root_fd = _open_private_root(scratch_root)
        if opened.st_dev != os.fstat(scratch_root_fd).st_dev:
            raise ValueError("destination and scratch roots must be on the same filesystem")
        tools_fd = _mkdir_open(resources_fd, "render-tools")
        for pin in pins:
            _copy_payload(tools_fd, pin)
            _verify_output(tools_fd, pin)
        scratch_fd = _mkdir_open(scratch_root_fd, SCRATCH_LEAF)
        _, manifest_sha256 = _write_scratch_manifest(scratch_fd, pins)
        os.link(
            SCRATCH_MANIFEST, "MANIFEST.json", src_dir_fd=scratch_fd,
            dst_dir_fd=tools_fd, follow_symlinks=False,
        )
        try:
            os.fsync(tools_fd)
        except OSError as error:
            raise PublishedButDurabilityUnconfirmed(
                manifest_path, manifest_sha256) from error
        return RenderToolsStageResult(
            state="complete", staging_root=resources_root / "render-tools",
            manifest_path=manifest_path, manifest_sha256=manifest_sha256,
        )
    finally:
        for fd in (scratch_fd, tools_fd, scratch_root_fd, resources_fd):
            if fd is not None:
                os.close(fd)


def _validate_authority_pins(
    mode: RenderAuthorityMode, pins: Sequence[_AuthorityPin]
) -> tuple[_AuthorityPin, ...]:
    if mode not in RENDER_AUTHORITY_MODES:
        raise ValueError(f"unsupported render authority mode: {mode}")
    if mode == "tool-only":
        return ()
    expected = (
        "PROFILE.json",
        "TOOLS.json",
        "COMPILER.json",
    ) + (("qualification-vectors/qualification-vector.json",)
         if mode == "sealed-candidate" else ())
    fixed = tuple(pins)
    if tuple(pin.relative_path for pin in fixed) != expected:
        raise ValueError(f"{mode} requires exactly {', '.join(expected)}")
    for pin in fixed:
        if type(pin.size_bytes) is not int or not 2 <= pin.size_bytes <= 1024 * 1024:
            raise ValueError(f"invalid pinned size for {pin.relative_path}")
        if len(pin.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in pin.sha256
        ):
            raise ValueError(f"invalid pinned sha256 for {pin.relative_path}")
    return fixed


def _copy_authority_payload(directory_fd: int, name: str, pin: _AuthorityPin) -> bytes:
    source_fd = os.open(os.fspath(pin.source), os.O_RDONLY | os.O_NONBLOCK | _NOFOLLOW)
    output_fd = None
    try:
        before = os.fstat(source_fd)
        if (not stat.S_ISREG(before.st_mode)
                or stat.S_IMODE(before.st_mode) != 0o644):
            raise ValueError(f"render authority source is not a 0644 regular file: {pin.source}")
        if before.st_size != pin.size_bytes:
            raise ValueError(f"render authority source size mismatch: {pin.relative_path}")
        output_fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o644, dir_fd=directory_fd,
        )
        os.fchmod(output_fd, 0o644)
        digest = hashlib.sha256()
        raw = bytearray()
        while True:
            chunk = os.read(source_fd, _READ_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            raw.extend(chunk)
            _write_all(output_fd, chunk)
        after = os.fstat(source_fd)
        if _stat_identity(before) != _stat_identity(after):
            raise ValueError(f"render authority source changed: {pin.relative_path}")
        if len(raw) != pin.size_bytes or digest.hexdigest() != pin.sha256:
            raise ValueError(f"render authority source content mismatch: {pin.relative_path}")
        os.fsync(output_fd)
        return bytes(raw)
    finally:
        if output_fd is not None:
            os.close(output_fd)
        os.close(source_fd)


def _validate_authority_documents(raw: dict[str, bytes], *, sealed: bool) -> None:
    from components.hub.backend.render_contract import validate_render_packet_syntax
    from components.hub.backend.render_hash_v1 import decode_render_json
    from components.hub.backend.render_profiles import (
        validate_render_compiler_descriptor,
        validate_render_profile_descriptor,
        validate_render_tool_descriptor,
    )

    tools = validate_render_tool_descriptor(raw["TOOLS.json"])
    compiler = validate_render_compiler_descriptor(raw["COMPILER.json"])
    profile = validate_render_profile_descriptor(raw["PROFILE.json"])
    if (profile.tools_artifact_sha256 != tools.digest
            or profile.compiler_artifact_sha256 != compiler.digest):
        raise ValueError("render authority descriptor digests do not match")
    if not sealed:
        return
    vector = decode_render_json(raw["qualification-vectors/qualification-vector.json"])
    vector_keys = {
        "format", "production_eligible", "profile_digest", "tools_digest",
        "compiler_digest", "qualification_digest", "hardware_class", "selected_tier",
        "exact_encoder", "font_support", "bounds", "files", "vector_id", "packet",
        "packet_digest", "recipe_digest", "manifest_digest", "assets",
    }
    if (type(vector) is not dict or set(vector) != vector_keys
            or vector.get("format") != "studio.render.qualification-vector.v1"
            or vector.get("production_eligible") is not False
            or vector.get("profile_digest") != profile.digest
            or vector.get("tools_digest") != tools.digest
            or vector.get("compiler_digest") != compiler.digest):
        raise ValueError("render qualification vector does not match descriptors")
    digest_keys = (
        "profile_digest", "tools_digest", "compiler_digest", "qualification_digest",
        "packet_digest", "recipe_digest", "manifest_digest",
    )
    if any(type(vector.get(key)) is not str or len(vector[key]) != 64
           or any(character not in "0123456789abcdef" for character in vector[key])
           for key in digest_keys):
        raise ValueError("render qualification vector has an invalid digest")
    if (any(type(vector.get(key)) is not str or not vector[key]
            or vector[key] != vector[key].strip() or len(vector[key]) > 256
            for key in ("vector_id", "hardware_class", "selected_tier", "exact_encoder"))
            or type(vector.get("font_support")) is not bool):
        raise ValueError("render qualification vector has invalid fixed fields")
    bounds = vector.get("bounds")
    bound_keys = {
        "max_rss_bytes", "max_wall_seconds", "min_available_memory_bytes",
        "output_limit_bytes",
    }
    if (type(bounds) is not dict or set(bounds) != bound_keys
            or any(type(bounds[key]) is not int or bounds[key] <= 0 for key in bound_keys)):
        raise ValueError("render qualification vector has invalid bounds")
    tool_value = decode_render_json(raw["TOOLS.json"])
    if vector.get("files") != {
        "ffmpeg": {
            "size_bytes": tool_value["ffmpeg_size_bytes"],
            "sha256": tool_value["ffmpeg_sha256"],
        },
        "ffprobe": {
            "size_bytes": tool_value["ffprobe_size_bytes"],
            "sha256": tool_value["ffprobe_sha256"],
        },
    }:
        raise ValueError("render qualification vector tool identities do not match")
    qualification_digest = vector.get("qualification_digest")
    unsigned = {key: value for key, value in vector.items() if key != "qualification_digest"}
    try:
        expected_qualification_digest = hashlib.sha256(json.dumps(
            unsigned, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        ).encode("ascii")).hexdigest()
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError("invalid render qualification vector value") from error
    packet = validate_render_packet_syntax(json.dumps(
        vector["packet"], sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    ).encode("ascii"))
    expected_assets = [
        {
            "logical_id": item["logical_id"],
            "sha256": item["sha256"],
            "size_bytes": item["size_bytes"],
        }
        for item in packet["manifest"]
    ]
    if (qualification_digest != expected_qualification_digest
            or vector.get("packet_digest") != packet.get("packet_digest")
            or vector.get("recipe_digest") != packet.get("recipe_digest")
            or vector.get("manifest_digest") != packet.get("manifest_digest")
            or packet["recipe"].get("profile_digest") != profile.digest
            or vector.get("assets") != expected_assets):
        raise ValueError("render qualification vector packet identity does not match")


def _stage_render_authority_into(
    resources_root: Path,
    scratch_root: Path,
    mode: RenderAuthorityMode,
    pins: Sequence[_AuthorityPin],
    *, late_labels: bool = False,
) -> RenderAuthorityStageResult:
    """Stage one closed authority state beside the unchanged two-tool manifest."""
    if late_labels and mode != "descriptor-only":
        raise ValueError("late-label authority stages descriptors only")
    fixed = _validate_authority_pins(mode, pins)
    if mode == "tool-only":
        return RenderAuthorityStageResult(staged_paths=())

    resources_root = Path(resources_root).expanduser().absolute()
    scratch_root = Path(scratch_root).expanduser().absolute()
    resources_fd = os.open(resources_root, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
    tools_fd = parent_tools_fd = scratch_root_fd = scratch_fd = scratch_vector_fd = vector_fd = None
    try:
        visible = resources_root.lstat()
        opened = os.fstat(resources_fd)
        if (not stat.S_ISDIR(opened.st_mode) or stat.S_ISLNK(visible.st_mode)
                or _stat_identity(opened) != _stat_identity(visible)):
            raise ValueError("app Resources destination is unsafe")
        tools_fd = os.open(
            "render-tools", os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=resources_fd
        )
        if not stat.S_ISDIR(os.fstat(tools_fd).st_mode):
            raise ValueError("render-tools destination is not a directory")
        scratch_root_fd = _open_private_root(scratch_root)
        if os.fstat(tools_fd).st_dev != os.fstat(scratch_root_fd).st_dev:
            raise ValueError("authority scratch and destination must be on the same filesystem")
        scratch_leaf = AUTHORITY_SCRATCH_LEAF + ("-late-label" if late_labels else "")
        scratch_fd = _mkdir_open(scratch_root_fd, scratch_leaf)
        if mode == "sealed-candidate":
            scratch_vector_fd = _mkdir_open(scratch_fd, "qualification-vectors")

        raw: dict[str, bytes] = {}
        for pin in fixed:
            if "/" in pin.relative_path:
                raw[pin.relative_path] = _copy_authority_payload(
                    scratch_vector_fd, "qualification-vector.json", pin
                )
            else:
                raw[pin.relative_path] = _copy_authority_payload(
                    scratch_fd, pin.relative_path, pin
                )
        _validate_authority_documents(raw, sealed=mode == "sealed-candidate")
        os.fsync(scratch_fd)

        staged: list[Path] = []
        tools_root = resources_root / "render-tools"
        if late_labels:
            profile_document = json.loads(raw["PROFILE.json"])
            compiler_document = json.loads(raw["COMPILER.json"])
            if (profile_document["format"] != "studio.render.profile.v3"
                    or profile_document["implementation"] != "story-classic-late-label-v3"
                    or compiler_document["implementation"] != "hub-final-render-late-label-v3"):
                raise ValueError("late-label staging requires the exact v3 descriptor pair")
            parent_tools_fd = tools_fd
            tools_fd = _mkdir_open(parent_tools_fd, "late-label")
            tools_root /= "late-label"
        for pin in fixed[:3]:
            os.link(
                pin.relative_path, pin.relative_path,
                src_dir_fd=scratch_fd, dst_dir_fd=tools_fd, follow_symlinks=False,
            )
            staged.append(tools_root / pin.relative_path)
        if mode == "sealed-candidate":
            vector_fd = _mkdir_open(tools_fd, "qualification-vectors")
            os.link(
                "qualification-vector.json", "qualification-vector.json",
                src_dir_fd=scratch_vector_fd, dst_dir_fd=vector_fd, follow_symlinks=False,
            )
            os.fsync(vector_fd)
            staged.append(tools_root / "qualification-vectors/qualification-vector.json")
        os.fsync(tools_fd)
        return RenderAuthorityStageResult(staged_paths=tuple(staged))
    finally:
        for fd in (
            vector_fd, scratch_vector_fd, scratch_fd, scratch_root_fd, tools_fd,
            parent_tools_fd, resources_fd,
        ):
            if fd is not None:
                os.close(fd)


def stage_render_tools(
    build_root: Path, scratch_root: Path, tools_dir: Path
) -> RenderToolsStageResult:
    """Stage the two fixed universal FFmpeg 8.1 payloads, read from ``tools_dir``, into a fresh tree."""
    return _stage_render_tools(build_root, scratch_root, production_pins(tools_dir))
