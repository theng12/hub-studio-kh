"""Packaged final-render authority and the broker-owned execution lane."""
from __future__ import annotations

import asyncio
import asyncio.subprocess
from contextlib import ExitStack
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import sys
import time
from typing import Any, Callable
import uuid

import psutil

from components import host_memory
from . import render_authority_files, render_encode
from .final_render_compiler import (
    compile_final_render_packet,
    late_label_input_requirements,
    late_label_sequence_path,
    validate_late_label_dimensions,
    validate_png_bytes,
)
from .render_hash_v1 import decode_render_json
from .render_jobs import (
    decode_render_request,
    ProcessIdentity,
    QualificationPermit,
    RenderExecutionContext,
    RenderJobUnavailable,
    RuntimeQualification,
    StoppedProcessEvidence,
    ValidatedOutputReceipt,
)
from .render_profiles import (
    match_render_profile_dependencies,
    validate_render_compiler_descriptor,
    validate_render_profile_descriptor,
    validate_render_tool_descriptor,
)


SUBTITLE_CONTRACT = "studio.render.subtitle.v1"
SUBTITLE_CAPABILITIES_FORMAT = "studio.render.hub-subtitle-capabilities.v1"
_SUBTITLE_PROFILE_FORMAT = "studio.render.profile.v4"
_SUBTITLE_PROFILE_IMPLEMENTATION = "story-classic-subtitle-v1"
_SUBTITLE_COMPILER_IMPLEMENTATION = "hub-final-render-subtitle-v1"
_CUE_OVERLAY_CONTRACT = "studio.render.cue-overlay.v1"
_CUE_OVERLAY_PROFILE_FORMAT = "studio.render.profile.v5"
_CUE_OVERLAY_PROFILE_IMPLEMENTATION = "story-classic-cue-overlay-v1"
_CUE_OVERLAY_COMPILER_IMPLEMENTATION = "hub-final-render-cue-overlay-v1"
_CUE_OVERLAY_CAPABILITIES_FORMAT = "studio.render.hub-cue-overlay-capabilities.v1"
_LATE_LABEL_COMPILER_IMPLEMENTATION = "hub-final-render-late-label-v3"
# The frozen wire order Gateway validates the optional joint marker against.
LATE_LABEL_MOTIONS = ("none", "pop", "slide", "float", "pulse", "wipe", "line-draw")


@dataclass(frozen=True)
class ResourceBounds:
    max_rss_bytes: int
    max_wall_seconds: int
    min_available_memory_bytes: int
    output_limit_bytes: int


@dataclass(frozen=True)
class InstalledRenderAuthority:
    qualification: RuntimeQualification
    ffmpeg: Path
    ffprobe: Path
    selected_tier: str
    exact_encoder: str
    bounds: ResourceBounds
    font_support: bool
    profile_execution_bounds: dict[str, Any]
    # Present only for an owner-opted-in, unmeasured authority: who accepted it and when. A
    # measured or enabled authority never carries it.
    opt_in: dict[str, Any] | None = None


class _UncertainProcessOutcome(RuntimeError):
    """An invoked owned tool cannot yet be proved stopped."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _asset_stream(reader: Any) -> Any:
    stream = reader.get("stream") if type(reader) is dict else reader
    if stream is None or not callable(getattr(stream, "fileno", None)):
        raise RuntimeError("render asset reader did not provide a fixed descriptor")
    return stream


def _input_options(entry: dict[str, Any], stream: Any) -> tuple[str, ...]:
    try:
        header = os.pread(stream.fileno(), 32, 0)
    except OSError as exc:
        raise RuntimeError("render asset header could not be verified") from exc
    role = entry["role"]
    suffix = Path(entry["safe_name"]).suffix.lower()
    if role == "base_video" and suffix in {".mp4", ".mov"} and header[4:8] == b"ftyp":
        return ("-f", "mov", "-enable_drefs", "0", "-use_absolute_path", "0",
                "-protocol_whitelist", "file,pipe")
    if role == "premixed_audio" and suffix == ".wav" and (
            header[:4] == b"RIFF" and header[8:12] == b"WAVE"):
        return ("-f", "wav", "-protocol_whitelist", "file,pipe")
    if role in {"logo", "frame_background", "overlay", "cue_label"} and suffix == ".png" and (
            header.startswith(b"\x89PNG\r\n\x1a\n")):
        return ("-f", "png_pipe", "-protocol_whitelist", "file,pipe")
    if role in {"overlay", "cue_overlay"} and suffix == ".webm" and header.startswith(b"\x1aE\xdf\xa3"):
        return ("-f", "matroska,webm", "-protocol_whitelist", "file,pipe")
    raise RuntimeError(f"render asset {entry.get('logical_id', '')} has an unsupported format")


def _confined_argv(
    argv: tuple[str, ...], packet: dict[str, Any],
    local_assets: dict[str, str], readers: dict[str, Any], *,
    sequence_inputs: dict[str, tuple[str, ...]] | None = None,
) -> tuple[str, ...]:
    sequence_inputs = sequence_inputs or {}
    if (set(sequence_inputs) & set(local_assets.values())
            or any(not ids or not set(ids) <= set(local_assets)
                   for ids in sequence_inputs.values())):
        raise RuntimeError("invalid receiver-owned render sequence")
    manifest = {entry["logical_id"]: entry for entry in packet["manifest"]}
    by_path = {path: logical_id for logical_id, path in local_assets.items()}
    options = {
        logical_id: _input_options(manifest[logical_id], readers[logical_id])
        for logical_id in local_assets
    }
    result: list[str] = []
    seen: set[str] = set()
    seen_sequences: set[str] = set()
    index = 0
    while index < len(argv):
        value = argv[index]
        if value == "-i":
            path = argv[index + 1] if index + 1 < len(argv) else None
            if path in sequence_inputs:
                result.extend(("-f", "image2", "-pattern_type", "sequence",
                               "-c:v", "png", "-protocol_whitelist", "file"))
                seen.update(sequence_inputs[path])
                seen_sequences.add(path)
            elif path in by_path:
                logical_id = by_path[path]
                result.extend(options[logical_id])
                seen.add(logical_id)
            else:
                raise RuntimeError("compiled render input escaped the accepted asset set")
        result.append(value)
        index += 1
    if seen != set(local_assets) or seen_sequences != set(sequence_inputs):
        raise RuntimeError("compiled render inputs do not match the accepted asset set")
    return tuple(result)


def _stage_late_label_inputs(stack, context, requirements, readers, *, scratch_limit):
    manifest = {entry["logical_id"]: entry for entry in context.packet["manifest"]}
    dimensions = {}
    for logical_id in requirements.png_logical_ids:
        entry = manifest[logical_id]
        reader = readers[logical_id]
        if os.fstat(reader.fileno()).st_size != entry["size_bytes"]:
            raise RuntimeError("late-label PNG size differs from its manifest")
        data = os.pread(reader.fileno(), entry["size_bytes"] + 1, 0)
        dimensions[logical_id] = validate_png_bytes(
            data, expected_sha256=entry["sha256"],
            expected_size_bytes=entry["size_bytes"])
        del data
    validate_late_label_dimensions(_canonical(context.packet), dimensions)
    if not requirements.sequences:
        return {}
    staged_size = sum(manifest[logical_id]["size_bytes"]
                      for sequence in requirements.sequences
                      for logical_id in sequence.logical_ids)
    if staged_size > scratch_limit:
        raise RuntimeError("late-label sequences exceed the published scratch limit")
    root = Path(context.output_path + ".late-label")
    root.mkdir(mode=0o700, exist_ok=False)
    stack.callback(shutil.rmtree, root)
    sequence_inputs = {}
    for sequence in requirements.sequences:
        pattern = late_label_sequence_path(context.output_path, sequence.key)
        directory = Path(pattern).parent
        directory.mkdir(mode=0o700, exist_ok=False)
        for index, logical_id in enumerate(sequence.logical_ids):
            entry = manifest[logical_id]
            digest = hashlib.sha256()
            offset = 0
            descriptor = os.open(directory / f"{index:04d}.png",
                                 os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600)
            with os.fdopen(descriptor, "wb") as output:
                while offset < entry["size_bytes"]:
                    chunk = os.pread(readers[logical_id].fileno(),
                                     min(1024 * 1024, entry["size_bytes"] - offset), offset)
                    if not chunk:
                        raise RuntimeError("late-label PNG changed during staging")
                    output.write(chunk)
                    digest.update(chunk)
                    offset += len(chunk)
            if digest.hexdigest() != entry["sha256"]:
                raise RuntimeError("late-label PNG changed during staging")
        sequence_inputs[pattern] = sequence.logical_ids
    return sequence_inputs


def _subtitle_input_ids(packet: dict[str, Any]) -> tuple[str, tuple[str, ...]] | None:
    """The exact ASS and ascending font logical ids a subtitle stage references."""
    stages = [op for op in packet["recipe"]["operations"]
              if op.get("kind") == "subtitle_stage"]
    if not stages:
        return None
    if len(stages) != 1:
        raise RuntimeError("exactly one subtitle stage is required")
    stage = stages[0]
    if set(stage) != {"kind", "contract", "ass", "fonts"}:
        raise RuntimeError("subtitle stage is not closed")
    if stage["contract"] != SUBTITLE_CONTRACT:
        raise RuntimeError("subtitle stage names an unknown contract")
    ass = stage["ass"]
    fonts = stage["fonts"]
    if (type(ass) is not dict or set(ass) != {"type", "logical_id"}
            or ass["type"] != "asset_ref" or type(ass["logical_id"]) is not str
            or not ass["logical_id"]):
        raise RuntimeError("subtitle stage ASS reference is invalid")
    if type(fonts) is not list or not fonts:
        raise RuntimeError("subtitle stage requires at least one font")
    ids = []
    for record in fonts:
        if (type(record) is not dict or set(record) != {"asset", "family", "sha256"}
                or type(record["family"]) is not str or not record["family"]
                or re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is None):
            raise RuntimeError("subtitle font record is invalid")
        asset = record["asset"]
        if (type(asset) is not dict or set(asset) != {"type", "logical_id"}
                or asset["type"] != "asset_ref"
                or type(asset["logical_id"]) is not str):
            raise RuntimeError("subtitle font reference is invalid")
        ids.append(asset["logical_id"])
    expected = [f"subtitle_font_{index:04d}" for index in range(len(ids))]
    if ids != expected:
        raise RuntimeError("subtitle font ids are not contiguous and ascending")
    return ass["logical_id"], tuple(ids)


def _copy_verified(reader, entry: dict[str, Any], path: Path) -> None:
    """Copy exactly the manifest bytes into a private file, proving the digest."""
    if os.fstat(reader.fileno()).st_size != entry["size_bytes"]:
        raise RuntimeError("subtitle asset size differs from its manifest")
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        offset = 0
        while offset < entry["size_bytes"]:
            chunk = os.pread(reader.fileno(),
                             min(1024 * 1024, entry["size_bytes"] - offset), offset)
            if not chunk:
                raise RuntimeError("subtitle asset changed during staging")
            output.write(chunk)
            digest.update(chunk)
            offset += len(chunk)
    if digest.hexdigest() != entry["sha256"]:
        raise RuntimeError("subtitle asset changed during staging")


def _stage_subtitle_inputs(stack, context, ids, readers, *, limits):
    """Stage the ASS document and every font as private real files.

    FFmpeg's subtitles filter needs a font directory, so unlike late-label PNGs these
    cannot stay on `/dev/fd`: the sender's names never become paths, the bytes are read
    from the accepted asset handles, and everything lands inside the job's attempt folder.
    """
    try:
        bounds_version = limits["bounds_version"]
        fonts_limit = limits["max_subtitle_fonts"]
        font_bytes_limit = limits["max_subtitle_font_bytes"]
        ass_bytes_limit = limits["max_subtitle_ass_bytes"]
        asset_bytes_limit = limits["max_asset_bytes"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("subtitle profile bounds are missing") from exc
    if (bounds_version != 2
            or any(type(value) is not int or value < 1
                   for value in (fonts_limit, font_bytes_limit,
                                 ass_bytes_limit, asset_bytes_limit))):
        raise RuntimeError("subtitle profile bounds are not the published v2 shape")
    # MIN(role ceiling, max_asset_bytes): the shared asset cap still applies.
    font_bytes_limit = min(font_bytes_limit, asset_bytes_limit)
    ass_bytes_limit = min(ass_bytes_limit, asset_bytes_limit)
    ass_id, font_ids = ids
    manifest = {entry["logical_id"]: entry for entry in context.packet["manifest"]}
    if len(font_ids) > fonts_limit:
        raise RuntimeError("subtitle font count exceeds the published limit")
    try:
        ass_entry = manifest[ass_id]
        font_entries = [manifest[logical_id] for logical_id in font_ids]
    except KeyError as exc:
        raise RuntimeError("subtitle assets are missing from the manifest") from exc
    if (ass_entry["role"] != "subtitle_ass"
            or ass_entry["size_bytes"] > ass_bytes_limit):
        raise RuntimeError("subtitle ASS asset is invalid or over the published limit")
    if (any(entry["size_bytes"] > font_bytes_limit for entry in font_entries)
            or any(entry["role"] != "subtitle_font" for entry in font_entries)):
        raise RuntimeError("subtitle fonts are invalid or over the published limit")

    root = Path(context.output_path + ".subtitle")
    root.mkdir(mode=0o700, exist_ok=False)
    stack.callback(shutil.rmtree, root)
    fonts_dir = root / "fonts"
    fonts_dir.mkdir(mode=0o700, exist_ok=False)
    ass_path = root / "subtitle.ass"
    _copy_verified(readers[ass_id], ass_entry, ass_path)
    paths = {ass_id: str(ass_path)}
    for index, (logical_id, entry) in enumerate(zip(font_ids, font_entries)):
        suffix = Path(entry["safe_name"]).suffix.lower()
        if suffix not in {".ttf", ".otf"}:
            raise RuntimeError("subtitle font has an unsupported extension")
        path = fonts_dir / f"{index:04d}{suffix}"
        _copy_verified(readers[logical_id], entry, path)
        paths[logical_id] = str(path)
    return paths, str(fonts_dir)


def _closed(document: Any, keys: set[str], name: str) -> dict[str, Any]:
    if type(document) is not dict or set(document) != keys:
        raise ValueError(f"{name} is not closed")
    return document


def _group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _hardware_class(identity: dict[str, Any]) -> str:
    explicit = identity.get("hardware_class")
    if isinstance(explicit, str) and explicit:
        return explicit
    chip = re.sub(r"[^a-z0-9]+", "-", str(identity.get("chip") or "").lower()).strip("-")
    chip = chip.removeprefix("apple-")
    memory = identity.get("memory_gb")
    if not chip or type(memory) is not int or memory < 1:
        return ""
    return f"{chip}-{memory}gb"


OPT_IN_PROVENANCE = "owner-opt-in-unqualified"


class InstalledRenderAuthorityLoader:
    """Installed tool evidence plus the operator's runtime authority records.

    ``<data root>/render-authority/enabled/<hardware_class>.json`` present for this Mac's
    own hardware class is what grants production. ``qualify.json`` is a time-windowed,
    one-use intent to run one qualification job. Absence or mismatch means no authority.

    A third, weaker grant exists only when the owner turned it on through the controller's
    settings relay: ``opt-in/unqualified.json``. It makes the installed, identity-checked tools
    offer capacity for one named encode as ``accepted_unqualified`` - never as a measured
    qualification. The memory floor, tool identity and tier checks are the same ones; what is
    missing is the measurement, and it stays missing: the first real render writes the first
    real receipt (see ``receipt_document_for``).
    """

    _UNQUALIFIED_FORMAT = "studio.render.accepted-unqualified.v1"
    _OPT_IN_FORMAT = "studio.render.accept-unqualified-setting.v1"
    _OPT_IN_KEYS = {"format", "enabled", "encode", "set_by", "set_at"}
    _PROBED_FORMAT = "studio.render.accepted-unqualified.v2"
    _QUALIFIED_BINDING_FORMAT = "studio.render.qualified-encode-binding.v1"

    _RECEIPT_KEYS = {
        "format", "production_eligible", "profile_digest", "tools_digest",
        "compiler_digest", "qualification_digest", "hardware_class", "selected_tier",
        "exact_encoder", "font_support", "bounds", "files",
    }
    _ENABLED_KEYS = _RECEIPT_KEYS | {"intent_id", "measured"}
    _UNQUALIFIED_KEYS = _RECEIPT_KEYS | {"opt_in"}
    _MEASURED_KEYS = {
        "peak_rss_bytes", "wall_seconds", "min_free_bytes", "output_bytes",
        "hub_job_id", "receipt_digest",
    }
    _INTENT_KEYS = {
        "format", "intent_id", "accept_before", "selected_tier", "exact_encoder",
        "font_support", "min_available_memory_bytes",
    }
    _MANIFEST_KEYS = {
        "format", "tool_version", "payload_format", "files", "production_eligible",
    }
    _DESCRIPTOR_NAMES = {
        "profile": "PROFILE.json", "tools": "TOOLS.json", "compiler": "COMPILER.json",
    }

    def __init__(self, resources_root: str | Path,
                 *, hardware_identity: Callable[[], dict[str, Any]],
                 data_root: str | Path | None = None,
                 clock: Callable[[], float] = time.time,
                 expected_uid: int | None = None) -> None:
        self.root = Path(resources_root).absolute() / "render-tools"
        self._hardware_identity = hardware_identity
        # Absolute but never resolved: the strict reader must walk and validate every
        # configured component, so a symlinked ancestor is refused rather than followed.
        self.data_root = (
            Path(os.path.abspath(data_root)) if data_root is not None else None)
        self._clock = clock
        self._uid = os.getuid() if expected_uid is None else expected_uid
        self.encode_probe_control = None
        self.physical_node_id = lambda: ""

    def _read(self, path: Path, keys: set[str], name: str) -> dict[str, Any] | None:
        try:
            raw = path.read_bytes()
            return _closed(decode_render_json(raw), keys, name)
        except (OSError, ValueError):
            return None

    def _record(self, keys: set[str], *names: str) -> dict[str, Any] | None:
        if self.data_root is None:
            return None
        try:
            raw = render_authority_files.read_record(
                self.data_root, *names, expected_uid=self._uid)
            return _closed(decode_render_json(raw), keys, "render authority record")
        except (OSError, ValueError):
            return None

    def _own_class(self) -> str | None:
        try:
            hardware_class = _hardware_class(self._hardware_identity())
        except (OSError, TypeError, ValueError, KeyError):
            return None
        return (hardware_class
                if re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,63}", hardware_class) else None)

    def _descriptor_root(self, profile_digest: str) -> Path | None:
        """The installed triple whose PROFILE bytes are exactly the row's pin.

        The roots are the existing legacy, late-label, subtitle and cue-overlay install slots; a
        missing or unreadable slot is simply not this pin. The digest never chooses a
        scope, so a copied descriptor cannot make one scope answer for another.
        """
        for name in ("", "late-label", "subtitle", "cue-overlay"):
            root = self.root / name if name else self.root
            try:
                if hashlib.sha256((root / "PROFILE.json").read_bytes()).hexdigest() == profile_digest:
                    return root
            except OSError:
                continue
        return None

    def _descriptors(self, document: dict[str, Any], packet: dict[str, Any] | None):
        try:
            descriptor_root = self._descriptor_root(document["profile_digest"])
            if descriptor_root is None:
                return None
            raw = {
                name: (descriptor_root / filename).read_bytes()
                for name, filename in self._DESCRIPTOR_NAMES.items()
            }
            profile = validate_render_profile_descriptor(raw["profile"])
            tools = validate_render_tool_descriptor(raw["tools"])
            compiler = validate_render_compiler_descriptor(raw["compiler"])
            profile_document = decode_render_json(raw["profile"])
            compiler_document = decode_render_json(raw["compiler"])
            compiler_implementation = compiler_document["implementation"]
            cue_overlays = (
                profile.format == _CUE_OVERLAY_PROFILE_FORMAT
                and profile.implementation == _CUE_OVERLAY_PROFILE_IMPLEMENTATION)
            subtitles = (
                profile.format == _SUBTITLE_PROFILE_FORMAT
                and profile.implementation == _SUBTITLE_PROFILE_IMPLEMENTATION)
            late_labels = (
                profile.format == "studio.render.profile.v3"
                and profile.implementation == "story-classic-late-label-v3")
            if cue_overlays:
                if compiler_implementation != _CUE_OVERLAY_COMPILER_IMPLEMENTATION:
                    return None
            elif subtitles:
                # The v4 triple is the same worker serving both markers; it is never inferred
                # from a late-label triple whose compiler only knows v3.
                if compiler_implementation != _SUBTITLE_COMPILER_IMPLEMENTATION:
                    return None
            elif late_labels != (
                    compiler_implementation == _LATE_LABEL_COMPILER_IMPLEMENTATION):
                return None
            kinds = set()
            if packet is not None:
                kinds = {op["kind"] for op in packet["recipe"]["operations"]}
            if "late_label_stage" in kinds and not (late_labels or subtitles or cue_overlays):
                return None
            if "subtitle_stage" in kinds and not (subtitles or cue_overlays):
                return None
            if "cue_overlay_stage" in kinds and not cue_overlays:
                return None
            if (
                profile.digest != document["profile_digest"]
                or tools.digest != document["tools_digest"]
                or compiler.digest != document["compiler_digest"]
                or profile.tools_artifact_sha256 != tools.digest
                or profile.compiler_artifact_sha256 != compiler.digest
            ):
                return None
            if packet is not None and not match_render_profile_dependencies(
                    _canonical(packet), profile, tools, compiler).descriptor_match:
                return None
            # The existing descriptor validators above own every descriptor key and value rule.
            # Decode only after they accept so execution never grows a second descriptor schema.
            tool_document = decode_render_json(raw["tools"])
            return tool_document, compiler_document, profile_document
        except (OSError, TypeError, ValueError, KeyError):
            return None

    def late_labels_allowed(self, authority: InstalledRenderAuthority,
                            packet: dict[str, Any]) -> bool:
        qualification = authority.qualification
        descriptors = self._descriptors({
            "profile_digest": qualification.profile_digest,
            "tools_digest": qualification.tools_digest,
            "compiler_digest": qualification.compiler_digest,
        }, packet)
        if descriptors is None:
            return False
        profile = descriptors[2]
        # The v5 cue-overlay or v4 subtitle triple executes the combined packet's late-label stage in the same
        # compiler; a v3 triple keeps its own exact rule.
        if (profile["format"] == _CUE_OVERLAY_PROFILE_FORMAT
                and profile["implementation"] == _CUE_OVERLAY_PROFILE_IMPLEMENTATION):
            return descriptors[1]["implementation"] == _CUE_OVERLAY_COMPILER_IMPLEMENTATION
        if (profile["format"] == _SUBTITLE_PROFILE_FORMAT
                and profile["implementation"] == _SUBTITLE_PROFILE_IMPLEMENTATION):
            return descriptors[1]["implementation"] == _SUBTITLE_COMPILER_IMPLEMENTATION
        return (profile["format"] == "studio.render.profile.v3"
                and profile["implementation"] == "story-classic-late-label-v3"
                and descriptors[1]["implementation"] == _LATE_LABEL_COMPILER_IMPLEMENTATION)

    def subtitles_allowed(self, authority: InstalledRenderAuthority,
                          packet: dict[str, Any]) -> bool:
        qualification = authority.qualification
        descriptors = self._descriptors({
            "profile_digest": qualification.profile_digest,
            "tools_digest": qualification.tools_digest,
            "compiler_digest": qualification.compiler_digest,
        }, packet)
        if descriptors is None:
            return False
        if (descriptors[2]["format"] == _CUE_OVERLAY_PROFILE_FORMAT
                and descriptors[2]["implementation"] == _CUE_OVERLAY_PROFILE_IMPLEMENTATION):
            return descriptors[1]["implementation"] == _CUE_OVERLAY_COMPILER_IMPLEMENTATION
        return bool(
            descriptors[2]["format"] == _SUBTITLE_PROFILE_FORMAT
            and descriptors[2]["implementation"] == _SUBTITLE_PROFILE_IMPLEMENTATION
            and descriptors[1]["implementation"] == _SUBTITLE_COMPILER_IMPLEMENTATION)

    def cue_overlays_allowed(self, authority: InstalledRenderAuthority,
                             packet: dict[str, Any]) -> bool:
        qualification = authority.qualification
        descriptors = self._descriptors({
            "profile_digest": qualification.profile_digest,
            "tools_digest": qualification.tools_digest,
            "compiler_digest": qualification.compiler_digest,
        }, packet)
        return bool(
            descriptors is not None
            and descriptors[2]["format"] == _CUE_OVERLAY_PROFILE_FORMAT
            and descriptors[2]["implementation"] == _CUE_OVERLAY_PROFILE_IMPLEMENTATION
            and descriptors[1]["implementation"] == _CUE_OVERLAY_COMPILER_IMPLEMENTATION)

    def compiler_for(self, authority: InstalledRenderAuthority, packet=None):
        qualification = authority.qualification
        descriptors = self._descriptors({
            "profile_digest": qualification.profile_digest,
            "tools_digest": qualification.tools_digest,
            "compiler_digest": qualification.compiler_digest,
        }, packet)
        if descriptors is None:
            raise RuntimeError("installed render compiler descriptors no longer match")
        if descriptors[1]["implementation"] == "hub-final-render-v1":
            from .render_legacy_v1.final_render_compiler import compile_final_render_packet as legacy_compile
            return legacy_compile
        if descriptors[1]["implementation"] == _LATE_LABEL_COMPILER_IMPLEMENTATION:
            frozen = Path(__file__).parent / "render_late_label_v3"
            if frozen.is_dir():
                from .render_late_label_v3.final_render_compiler import (
                    compile_final_render_packet as late_label_compile,
                )
                return late_label_compile
        if descriptors[1]["implementation"] == _SUBTITLE_COMPILER_IMPLEMENTATION:
            frozen_v4 = Path(__file__).parent / "render_subtitle_v4"
            if frozen_v4.is_dir():
                from .render_subtitle_v4.final_render_compiler import (
                    compile_final_render_packet as subtitle_compile,
                )
                return subtitle_compile
        if descriptors[1]["implementation"] == _CUE_OVERLAY_COMPILER_IMPLEMENTATION:
            return compile_final_render_packet
        return compile_final_render_packet

    def _manifest_matches(self, files: dict[str, Any]) -> bool:
        manifest = self._read(self.root / "MANIFEST.json", self._MANIFEST_KEYS, "tool manifest")
        if manifest is None or (
            manifest["format"] != "studiohub.render-tools.payload.v1"
            or manifest["payload_format"] != "macos-universal"
            or type(manifest["tool_version"]) is not str
            or not manifest["tool_version"]
            or manifest["production_eligible"] is not False
            or type(manifest["files"]) is not list
        ):
            return False
        expected = [
            {"name": name, "path": f"render-tools/{name}",
             "sha256": files[name]["sha256"], "size_bytes": files[name]["size_bytes"]}
            for name in ("ffmpeg", "ffprobe")
        ]
        return manifest["files"] == expected

    @classmethod
    def _record_digest(cls, document: dict[str, Any]) -> str:
        # intent_id and measured are evidence appended after the qualification run; they are
        # not part of the qualified authority, so a receipt copied into enabled/ keeps the
        # qualification job's digest.
        return hashlib.sha256(_canonical({
            key: value for key, value in document.items()
            if key in cls._RECEIPT_KEYS and key != "qualification_digest"
        })).hexdigest()

    @classmethod
    def _measured_evidence(
        cls, document: dict[str, Any], measured: Any,
    ) -> dict[str, Any] | None:
        """Validate evidence appended after the qualification run.

        ``intent_id`` and ``measured`` are deliberately outside
        ``qualification_digest``: the Hub appends them after the run and the
        operator's enabled/ copy keeps the qualification job's digest. Their
        types, declared bounds and job/output/receipt identity are therefore
        checked here so a malformed copy cannot stand in for a completed run.
        """
        try:
            bounds = _closed(
                document["bounds"],
                {"max_rss_bytes", "max_wall_seconds", "min_available_memory_bytes",
                 "output_limit_bytes"},
                "resource bounds",
            )
            if any(type(value) is not int or value < 1 for value in bounds.values()):
                return None
            value = _closed(measured, cls._MEASURED_KEYS, "measured evidence")
            peak_rss_bytes = value["peak_rss_bytes"]
            wall_seconds = value["wall_seconds"]
            min_free_bytes = value["min_free_bytes"]
            output_bytes = value["output_bytes"]
            hub_job_id = value["hub_job_id"]
            receipt_digest = value["receipt_digest"]
            if (
                type(peak_rss_bytes) is not int
                or not 0 <= peak_rss_bytes <= bounds["max_rss_bytes"]
                or type(wall_seconds) is not int
                or not 1 <= wall_seconds <= bounds["max_wall_seconds"]
                or type(min_free_bytes) is not int
                or not 1 <= min_free_bytes <= 2**63 - 1
                or type(output_bytes) is not int
                or not 1 <= output_bytes <= bounds["output_limit_bytes"]
                or type(hub_job_id) is not str
                or not 0 < len(hub_job_id) <= 256
                or not hub_job_id.isascii()
                or not all(" " <= char <= "~" for char in hub_job_id)
                or type(receipt_digest) is not str
                or re.fullmatch(r"[0-9a-f]{64}", receipt_digest) is None
            ):
                return None
            return value
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _profile_allows(packet: dict[str, Any], bounds: dict[str, Any]) -> bool:
        try:
            recipe = packet["recipe"]
            canvas = recipe["canvas"]
            manifest = packet["manifest"]
            frame_count = math.ceil(
                recipe["target_duration_sec"] * canvas["working_fps"])
            width, height = canvas["width"], canvas["height"]
            if "bounds_version" in bounds:
                limits = bounds["canvas_range"]
                canvas_allowed = (
                    type(bounds["bounds_version"]) is int and bounds["bounds_version"] == 2
                    and type(width) is int and type(height) is int
                    and width > 0 and height > 0 and width % 2 == height % 2 == 0
                    and max(width, height) <= limits["max_edge"]
                    and width * height <= limits["max_pixels"]
                )
            else:
                canvas_allowed = {"width": width, "height": height} in bounds["canvas_pairs"]
            return (
                canvas_allowed
                and canvas["working_fps"] in bounds["working_fps"]
                and recipe["target_duration_sec"] <= bounds["max_duration_sec"]
                and len(recipe["operations"]) <= bounds["max_operations"]
                and len(manifest) <= bounds["max_assets"]
                and all(row["size_bytes"] <= bounds["max_asset_bytes"] for row in manifest)
                and sum(row["size_bytes"] for row in manifest)
                    <= bounds["max_total_input_bytes"]
                and canvas["width"] * canvas["height"] * frame_count
                    <= bounds["max_pixel_frames"]
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return False

    def _authority(
        self, document: dict[str, Any], packet: dict[str, Any] | None = None,
        *, production: bool = False,
    ) -> InstalledRenderAuthority | None:
        if document.get("format") == self._QUALIFIED_BINDING_FORMAT:
            try:
                if set(document) != {"format", "qualified_document", "requested_encode", "encode_authority"}:
                    return None
                base = document["qualified_document"]
                if (type(base) is not dict or set(base) != self._ENABLED_KEYS
                        or self._measured_evidence(base, base.get("measured")) is None):
                    return None
                binding = document["encode_authority"]
                if (type(binding) is not dict or set(binding) != {
                        "format", "authority_digest", "selected_tier", "exact_encoder"}
                        or binding["format"] != "studio.render.encode-binding.v1"
                        or binding["selected_tier"] != base["selected_tier"]
                        or binding["exact_encoder"] != base["exact_encoder"]
                        or re.fullmatch(r"[0-9a-f]{64}", binding["authority_digest"]) is None
                        or render_encode.normalize_encode(document["requested_encode"]) is None
                        or (packet is not None and packet["recipe"]["encode"] != document["requested_encode"])):
                    return None
                authority = self._authority(base, packet, production=production)
                if authority is None:
                    return None
                return replace(authority, qualification=replace(
                    authority.qualification, document=json.loads(_canonical(document))))
            except (KeyError, TypeError, ValueError):
                return None
        if document.get("format") == self._PROBED_FORMAT:
            try:
                if set(document) != self._UNQUALIFIED_KEYS | {
                        "requested_encode", "encode_authority", "grant_digest"}:
                    return None
                binding = document["encode_authority"]
                if (type(binding) is not dict or set(binding) != {
                        "format", "authority_digest", "selected_tier", "exact_encoder"}
                        or binding["format"] != "studio.render.encode-binding.v1"
                        or binding["selected_tier"] != document["selected_tier"]
                        or binding["exact_encoder"] != document["exact_encoder"]
                        or re.fullmatch(r"[0-9a-f]{64}", binding["authority_digest"]) is None
                        or re.fullmatch(r"[0-9a-f]{64}", document["grant_digest"]) is None
                        or render_encode.normalize_encode(document["requested_encode"]) is None
                        or (packet is not None
                            and packet["recipe"]["encode"] != document["requested_encode"])):
                    return None
                base = {key: document[key] for key in self._UNQUALIFIED_KEYS}
                base["format"] = self._UNQUALIFIED_FORMAT
                base["qualification_digest"] = self._record_digest(base)
                if document["qualification_digest"] != base["qualification_digest"]:
                    return None
                authority = self._authority(base, packet, production=production)
                if authority is None:
                    return None
                return replace(authority, qualification=replace(
                    authority.qualification, document=json.loads(_canonical(document))))
            except (KeyError, TypeError, ValueError):
                return None
        try:
            unqualified = set(document) == self._UNQUALIFIED_KEYS
            if (
                set(document) not in (
                    self._RECEIPT_KEYS, self._ENABLED_KEYS, self._UNQUALIFIED_KEYS)
                or document["format"] != (
                    self._UNQUALIFIED_FORMAT if unqualified
                    else "studio.render.qualification-receipt.v1")
                or (unqualified and not self._opt_in_evidence(document["opt_in"]))
                # An unqualified document is only ever an opt-in authority: it can never be the
                # contents of an enabled/ record, because that record's key set is closed.
                # Always false: it sits inside qualification_digest. Production comes only
                # from the record's placement in enabled/, never from this field.
                or document["production_eligible"] is not False
                or document["qualification_digest"] != self._record_digest(document)
                or type(document["font_support"]) is not bool
                or type(document["hardware_class"]) is not str
                or type(document["selected_tier"]) is not str
                or not document["selected_tier"]
                or type(document["exact_encoder"]) is not str
                or not document["exact_encoder"]
            ):
                return None
            if document["hardware_class"] != self._own_class():
                return None
            files = _closed(document["files"], {"ffmpeg", "ffprobe"}, "tool files")
            descriptors = self._descriptors(document, packet)
            if descriptors is None or not self._manifest_matches(files):
                return None
            tool_descriptor, compiler_descriptor, profile_descriptor = descriptors
            # V1 carries no hardware qualification or fidelity record. A non-null pin
            # would claim evidence this loader cannot read, so refuse it instead of
            # silently ignoring it; this does not require a fidelity record for V1.
            if (profile_descriptor["hardware_qualification_record_sha256"] is not None
                    or profile_descriptor["fidelity_record_sha256"] is not None):
                return None
            paths = {}
            for name in ("ffmpeg", "ffprobe"):
                expected = _closed(files[name], {"size_bytes", "sha256"}, name)
                if type(expected["size_bytes"]) is not int or expected["size_bytes"] < 1:
                    return None
                path = self.root / name
                info = path.lstat()
                if (not stat.S_ISREG(info.st_mode) or path.is_symlink()
                        or not os.access(path, os.X_OK)):
                    return None
                if _sha(path) != (expected["size_bytes"], expected["sha256"]):
                    return None
                paths[name] = path
            if (
                (tool_descriptor["ffmpeg_size_bytes"], tool_descriptor["ffmpeg_sha256"])
                    != (files["ffmpeg"]["size_bytes"], files["ffmpeg"]["sha256"])
                or (tool_descriptor["ffprobe_size_bytes"], tool_descriptor["ffprobe_sha256"])
                    != (files["ffprobe"]["size_bytes"], files["ffprobe"]["sha256"])
            ):
                return None
            source_root = Path(__file__).parent
            if compiler_descriptor["implementation"] == "hub-final-render-v1":
                source_root = source_root / "render_legacy_v1"
            elif compiler_descriptor["implementation"] == _LATE_LABEL_COMPILER_IMPLEMENTATION:
                # The v3 wire is frozen at its release bytes. The v4 work edits the live
                # compiler/contract, so v3 validates from its own copy once that copy is
                # installed; without it the live root is still read, exactly as before.
                frozen_v3 = source_root / "render_late_label_v3"
                if frozen_v3.is_dir():
                    source_root = frozen_v3
            elif compiler_descriptor["implementation"] == _SUBTITLE_COMPILER_IMPLEMENTATION:
                # The v4 wire is frozen at its release bytes. The v5 work edits the live
                # compiler/contract, so v4 validates from its own copy once that copy is
                # installed; without it the live root is still read, exactly as before.
                frozen_v4 = source_root / "render_subtitle_v4"
                if frozen_v4.is_dir():
                    source_root = frozen_v4
            source_hashes = {
                "python_runtime_sha256": _sha(Path(sys.executable))[1],
                "render_hash_sha256": _sha(source_root / "render_hash_v1.py")[1],
                "render_contract_sha256": _sha(source_root / "render_contract.py")[1],
                "final_render_compiler_sha256": _sha(source_root / "final_render_compiler.py")[1],
            }
            if any(compiler_descriptor[key] != value for key, value in source_hashes.items()):
                return None
            bounds_doc = _closed(
                document["bounds"],
                {"max_rss_bytes", "max_wall_seconds", "min_available_memory_bytes",
                 "output_limit_bytes"},
                "resource bounds",
            )
            if any(type(value) is not int for value in bounds_doc.values()):
                return None
            bounds = ResourceBounds(**bounds_doc)
            if min(asdict(bounds).values()) < 1:
                return None
            profile_bounds = profile_descriptor["execution_bounds"]
            if packet is not None and not self._profile_allows(packet, profile_bounds):
                return None
            if (
                profile_bounds["output_limit_bytes"] != bounds.output_limit_bytes
                or profile_bounds["process_tree_rss_limit_bytes"] != bounds.max_rss_bytes
                or profile_bounds["cumulative_wall_seconds"] != bounds.max_wall_seconds
            ):
                return None
            qualification = RuntimeQualification(
                profile_digest=document["profile_digest"], tools_digest=document["tools_digest"],
                compiler_digest=document["compiler_digest"],
                qualification_digest=document["qualification_digest"],
                hardware_class=document["hardware_class"],
                production_eligible=production,
                min_free_bytes=(profile_bounds["scratch_reserve_bytes"]
                                + profile_bounds["output_limit_bytes"]),
                scratch_reserve_bytes=profile_bounds["scratch_reserve_bytes"],
                output_limit_bytes=bounds.output_limit_bytes,
                document=json.loads(_canonical(document)),
            )
            return InstalledRenderAuthority(
                qualification=qualification, ffmpeg=paths["ffmpeg"], ffprobe=paths["ffprobe"],
                selected_tier=str(document["selected_tier"]),
                exact_encoder=str(document["exact_encoder"]), bounds=bounds,
                font_support=document["font_support"] is True,
                profile_execution_bounds=json.loads(_canonical(profile_bounds)),
                opt_in=dict(document["opt_in"]) if unqualified else None,
            )
        except (OSError, TypeError, ValueError, KeyError):
            return None

    @staticmethod
    def _opt_in_evidence(value: Any) -> bool:
        """Who accepted the unmeasured authority and when; evidence, never part of the digest."""
        try:
            return (
                type(value) is dict
                and set(value) == {"provenance", "accepted_by", "accepted_at"}
                and value["provenance"] == OPT_IN_PROVENANCE
                and type(value["accepted_by"]) is str
                and 0 < len(value["accepted_by"]) <= 120
                and value["accepted_by"].isascii() and value["accepted_by"].isprintable()
                and type(value["accepted_at"]) is int and value["accepted_at"] > 0
            )
        except (KeyError, TypeError):
            return False

    def _enabled(self) -> dict[str, Any] | None:
        hardware_class = self._own_class()
        if hardware_class is None:
            return None
        document = self._record(self._ENABLED_KEYS, "enabled", f"{hardware_class}.json")
        try:
            if (document is None or type(document["intent_id"]) is not str
                    or str(uuid.UUID(document["intent_id"])) != document["intent_id"]
                    or self._measured_evidence(document, document["measured"]) is None):
                return None
        except (ValueError, TypeError, AttributeError):
            return None
        return document

    def snapshot(self) -> InstalledRenderAuthority | None:
        document = self._enabled()
        if document is not None:
            return self._authority(document, production=True)
        # A valid enabled/ record always wins; the weaker, unmeasured grant is only ever what
        # is left when there is no measured one.
        return self._unqualified_authority()

    def probe_authority(self, *, late_labels: bool = False,
                        subtitles: bool = False,
                        cue_overlays: bool = False) -> InstalledRenderAuthority | None:
        """The installed, identity-checked tools an encode probe runs under, or None.

        A probe names the digests of the tools that are actually pinned whether or not this
        Mac has been granted production rendering: probing is how that evidence is produced
        in the first place. This reuses the exact descriptor, manifest, tool-hash and
        source-hash validation a measured authority passes. The tier fields carry the
        pinned compiler's own default and are not part of a probe's identity (the probe
        tuple and the evidence decide those). The result grants nothing: it is never
        production_eligible and is never written anywhere. `late_labels=True` reads the
        bundled late-label v3 triple, `subtitles=True` the bundled v4 subtitle triple, and
        `cue_overlays=True` the bundled v5 cue-overlay triple instead of the v1 root, so a scoped probe's
        identity carries that scope's pins and never the legacy ones.
        """
        if sum((bool(late_labels), bool(subtitles), bool(cue_overlays))) > 1:
            return None
        pair = render_encode.primary_pair(render_encode.DEFAULT_OPT_IN_ENCODE)
        if pair is None:
            return None
        document = self._installed_document(
            pair[0], pair[1], subtitles or cue_overlays,
            late_labels=late_labels, subtitles=subtitles, cue_overlays=cue_overlays)
        if document is None:
            return None
        return self._authority(document, production=False)

    def late_label_profile_digest(self) -> str | None:
        """The installed late-label v3 profile digest, or None while it is unusable."""
        try:
            descriptor = validate_render_profile_descriptor(
                (self.root / "late-label" / "PROFILE.json").read_bytes())
        except (OSError, TypeError, ValueError):
            return None
        return descriptor.digest if descriptor.format == "studio.render.profile.v3" else None

    def subtitle_profile_digest(self) -> str | None:
        """The installed v4 subtitle profile digest, or None while it is unusable."""
        try:
            descriptor = validate_render_profile_descriptor(
                (self.root / "subtitle" / "PROFILE.json").read_bytes())
        except (OSError, TypeError, ValueError):
            return None
        return (descriptor.digest
                if descriptor.format == _SUBTITLE_PROFILE_FORMAT
                and descriptor.implementation == _SUBTITLE_PROFILE_IMPLEMENTATION else None)

    def cue_overlay_profile_digest(self) -> str | None:
        """The installed v5 cue overlay profile digest, or None while it is unusable."""
        try:
            descriptor = validate_render_profile_descriptor(
                (self.root / "cue-overlay" / "PROFILE.json").read_bytes())
        except (OSError, TypeError, ValueError):
            return None
        return (descriptor.digest
                if descriptor.format == _CUE_OVERLAY_PROFILE_FORMAT
                and descriptor.implementation == _CUE_OVERLAY_PROFILE_IMPLEMENTATION else None)

    def subtitle_snapshot(
        self, packet: dict[str, Any] | None = None,
    ) -> InstalledRenderAuthority | None:
        """Production authority for the v4 subtitle scope only.

        Like the late-label scope there is no unmeasured opt-in and no v1 fallback: only
        this Mac's own enabled/ record validated against the installed v4 triple grants
        production, so a v1 or v3 grant can never answer as subtitles.
        """
        installed = self.subtitle_profile_digest()
        if installed is None:
            return None
        document = self._enabled()
        if document is None:
            return None
        authority = self._authority(document, packet, production=True)
        if authority is None or authority.qualification.profile_digest != installed:
            return None
        return authority

    def cue_overlay_snapshot(
        self, packet: dict[str, Any] | None = None,
    ) -> InstalledRenderAuthority | None:
        """Production authority for the v5 cue overlay scope only.

        Like the subtitle scope there is no unmeasured opt-in and no v1 fallback: only
        this Mac's own enabled/ record validated against the installed v5 triple grants
        production, so a v1, v3 or v4 grant can never answer as cue overlay.
        """
        installed = self.cue_overlay_profile_digest()
        if installed is None:
            return None
        document = self._enabled()
        if document is None:
            return None
        authority = self._authority(document, packet, production=True)
        if authority is None or authority.qualification.profile_digest != installed:
            return None
        return authority

    def late_label_snapshot(
        self, packet: dict[str, Any] | None = None,
    ) -> InstalledRenderAuthority | None:
        """Production authority for the late-label v3 scope only.

        The scope has no unmeasured opt-in and never falls back to the v1 root's records:
        only this Mac's own enabled/ record validated against the installed v3 triple
        grants production, so a v1 grant, proof or opt-in can never answer as v3.
        """
        installed = self.late_label_profile_digest()
        if installed is None:
            return None
        document = self._enabled()
        if document is None:
            return None
        authority = self._authority(document, packet, production=True)
        if authority is None or authority.qualification.profile_digest != installed:
            return None
        return authority

    def _intent(self) -> dict[str, Any] | None:
        intent = self._record(self._INTENT_KEYS, "qualify.json")
        try:
            if (
                intent is None
                or intent["format"] != "studio.render.qualification-intent.v1"
                or type(intent["intent_id"]) is not str
                or str(uuid.UUID(intent["intent_id"])) != intent["intent_id"]
                or type(intent["accept_before"]) is not int or intent["accept_before"] < 1
                or any(type(intent[name]) is not str or not 0 < len(intent[name]) <= 256
                       for name in ("selected_tier", "exact_encoder"))
                or type(intent["font_support"]) is not bool
                or type(intent["min_available_memory_bytes"]) is not int
                or intent["min_available_memory_bytes"] < 1
            ):
                return None
        except (TypeError, ValueError, AttributeError):
            return None
        return intent

    def _installed_document(
        self, selected_tier: str, exact_encoder: str, font_support: bool,
        min_available_memory_bytes: int | None = None, *, late_labels: bool = False,
        subtitles: bool = False, cue_overlays: bool = False,
    ) -> dict[str, Any] | None:
        """Hub, not the operator, derives everything but the run settings from installed files.

        The memory floor defaults to the profile's own process-tree limit: the Hub never
        offers capacity to a Mac that could not hold the most the profile allows one render
        to use. `late_labels=True` reads the bundled `render-tools/late-label` triple,
        `subtitles=True` the `render-tools/subtitle` v4 triple, and `cue_overlays=True`
        the `render-tools/cue-overlay` v5 triple; anything unreadable or not
        exactly that scope fails closed, so an absent packaging step is a negative, never
        invented pins.
        """
        if sum((bool(late_labels), bool(subtitles), bool(cue_overlays))) > 1:
            return None
        hardware_class = self._own_class()
        if hardware_class is None:
            return None
        if cue_overlays:
            descriptor_root = self.root / "cue-overlay"
        elif subtitles:
            descriptor_root = self.root / "subtitle"
        elif late_labels:
            descriptor_root = self.root / "late-label"
        else:
            descriptor_root = self.root
        try:
            raw = {
                name: (descriptor_root / filename).read_bytes()
                for name, filename in self._DESCRIPTOR_NAMES.items()
            }
            profile = validate_render_profile_descriptor(raw["profile"])
            profile_document = decode_render_json(raw["profile"])
            if late_labels and (profile.format != "studio.render.profile.v3"
                                or profile_document["implementation"]
                                != "story-classic-late-label-v3"):
                return None
            if subtitles and (profile.format != _SUBTITLE_PROFILE_FORMAT
                              or profile_document["implementation"]
                              != _SUBTITLE_PROFILE_IMPLEMENTATION):
                return None
            if cue_overlays and (profile.format != _CUE_OVERLAY_PROFILE_FORMAT
                                 or profile_document["implementation"]
                                 != _CUE_OVERLAY_PROFILE_IMPLEMENTATION):
                return None
            tools = validate_render_tool_descriptor(raw["tools"])
            compiler = validate_render_compiler_descriptor(raw["compiler"])
            compiler_document = decode_render_json(raw["compiler"])
            if subtitles and compiler_document["implementation"] != _SUBTITLE_COMPILER_IMPLEMENTATION:
                return None
            if cue_overlays and compiler_document["implementation"] != _CUE_OVERLAY_COMPILER_IMPLEMENTATION:
                return None
            tool_document = decode_render_json(raw["tools"])
            profile_bounds = profile_document["execution_bounds"]
            document = {
                "format": "studio.render.qualification-receipt.v1",
                "production_eligible": False,
                "profile_digest": profile.digest, "tools_digest": tools.digest,
                "compiler_digest": compiler.digest, "hardware_class": hardware_class,
                "selected_tier": selected_tier,
                "exact_encoder": exact_encoder,
                "font_support": font_support,
                "bounds": {
                    "max_rss_bytes": profile_bounds["process_tree_rss_limit_bytes"],
                    "max_wall_seconds": profile_bounds["cumulative_wall_seconds"],
                    "min_available_memory_bytes": (
                        profile_bounds["process_tree_rss_limit_bytes"]
                        if min_available_memory_bytes is None else min_available_memory_bytes),
                    "output_limit_bytes": profile_bounds["output_limit_bytes"],
                },
                "files": {
                    name: {"size_bytes": tool_document[f"{name}_size_bytes"],
                           "sha256": tool_document[f"{name}_sha256"]}
                    for name in ("ffmpeg", "ffprobe")
                },
            }
        except (OSError, TypeError, ValueError, KeyError):
            return None
        document["qualification_digest"] = self._record_digest(document)
        return document

    def _intent_document(self, intent: dict[str, Any]) -> dict[str, Any] | None:
        return self._installed_document(
            intent["selected_tier"], intent["exact_encoder"], intent["font_support"],
            intent["min_available_memory_bytes"])

    # --- owner opt-in: accepted_unqualified -------------------------------------------------

    def _opt_in_record(self) -> dict[str, Any] | None:
        """The persisted setting, or None when it is absent, unsafe or malformed (= off)."""
        record = self._record(self._OPT_IN_KEYS, "opt-in", "unqualified.json")
        try:
            if (
                record is None
                or record["format"] != self._OPT_IN_FORMAT
                or type(record["enabled"]) is not bool
                or render_encode.normalize_encode(record["encode"]) is None
                or record["encode"]["bitrate_kbps"] is not None
                or type(record["set_by"]) is not str
                or not 0 < len(record["set_by"]) <= 120
                or not record["set_by"].isascii() or not record["set_by"].isprintable()
                or type(record["set_at"]) is not int or record["set_at"] < 1
            ):
                return None
        except (KeyError, TypeError):
            return None
        return record

    def _unqualified_authority(
        self, packet: dict[str, Any] | None = None,
    ) -> InstalledRenderAuthority | None:
        """Capacity the owner accepted without a measurement, or None while the setting is off."""
        record = self._opt_in_record()
        if record is None or record["enabled"] is not True:
            return None
        pair = render_encode.primary_pair(record["encode"])
        if pair is None:
            return None
        # font_support is False: the shipped profile is a no-font combination set, and a flag
        # nobody measured must not claim more than the profile itself does.
        document = self._installed_document(pair[0], pair[1], False)
        if document is None:
            return None
        document["format"] = self._UNQUALIFIED_FORMAT
        document["opt_in"] = {
            "provenance": OPT_IN_PROVENANCE, "accepted_by": record["set_by"],
            "accepted_at": record["set_at"],
        }
        # The evidence is outside the digest, so re-saving the setting never changes the
        # authority a queued job was accepted under.
        document["qualification_digest"] = self._record_digest(document)
        return self._authority(document, packet, production=True)

    def probed_grant(self) -> dict[str, Any] | None:
        from .render_encode_grants import read_v2_grants
        if self.data_root is None:
            return None
        return read_v2_grants(self.data_root, expected_uid=self._uid,
                              physical_node_id=self.physical_node_id())

    def granted_probe_entries(self) -> list[dict[str, Any]]:
        grant = self.probed_grant()
        control = self.encode_probe_control
        if grant is None or control is None:
            return []
        result = []
        for entry in grant["entries"]:
            encode = entry["requested_encode"]
            # Phase 1 output validation remains MP4/H.264, even when probes prove HEVC.
            if encode["container"] != "mp4" or encode["requested_codec"] != "h264":
                continue
            state = control.inspect(encode)
            if (state["state"] == "supported"
                    and state["authority_digest"] == entry["authority_digest"]):
                result.append(entry)
        return result

    def executable_probe_entries(self) -> list[dict[str, Any]]:
        entries = self.granted_probe_entries()
        control = self.encode_probe_control
        measured = self._enabled()
        if control is None or measured is None:
            return entries
        for state in control.supported_entries():
            encode = state["requested_encode"]
            evidence = state.get("evidence") or {}
            entry = {"requested_encode": encode, "authority_digest": state["authority_digest"]}
            if (state["state"] == "supported" and encode["container"] == "mp4"
                    and encode["requested_codec"] == "h264"
                    and evidence.get("selected_tier") == measured["selected_tier"]
                    and evidence.get("exact_encoder") == measured["exact_encoder"]
                    and entry not in entries):
                entries.append(entry)
        return entries

    def migrate_probed_grant(self) -> None:
        from .render_encode_grants import write_v2_grants
        record = self._opt_in_record()
        control = self.encode_probe_control
        if record is None or not record["enabled"] or control is None:
            return
        entries = []
        for state in control.supported_entries():
            if (state["state"] == "supported"
                    and state["requested_encode"]["container"] == "mp4"
                    and state["requested_encode"]["requested_codec"] == "h264"):
                entries.append({"requested_encode": state["requested_encode"],
                                "authority_digest": state["authority_digest"]})
        if not entries:
            return
        previous = self.probed_grant()
        migration = previous["migration"] if previous else {
            "authorization_id": "owner-direction-via-developer-2026-11-04",
            "authorized_at": int(self._clock()),
        }
        write_v2_grants(self.data_root, expected_uid=self._uid,
                        physical_node_id=self.physical_node_id(), entries=entries,
                        **migration)

    def _probed_authority(self, encode: dict[str, Any],
                          packet: dict[str, Any] | None = None) -> InstalledRenderAuthority | None:
        entry = next((entry for entry in self.executable_probe_entries()
                      if entry["requested_encode"] == encode), None)
        if entry is None:
            return None
        state = self.encode_probe_control.inspect(encode)
        if (state["state"] != "supported"
                or state["authority_digest"] != entry["authority_digest"]):
            return None
        evidence = state["evidence"]
        binding = {"format": "studio.render.encode-binding.v1",
                   "authority_digest": entry["authority_digest"],
                   "selected_tier": evidence["selected_tier"],
                   "exact_encoder": evidence["exact_encoder"]}
        measured = self._enabled()
        if (measured is not None and measured["selected_tier"] == evidence["selected_tier"]
                and measured["exact_encoder"] == evidence["exact_encoder"]):
            return self._authority({
                "format": self._QUALIFIED_BINDING_FORMAT,
                "qualified_document": measured, "requested_encode": dict(encode),
                "encode_authority": binding,
            }, packet, production=True)
        record = self._opt_in_record()
        grant = self.probed_grant()
        if record is None or not record["enabled"] or grant is None:
            return None
        document = self._installed_document(
            evidence["selected_tier"], evidence["exact_encoder"], False)
        if document is None:
            return None
        document["format"] = self._UNQUALIFIED_FORMAT
        document["opt_in"] = {"provenance": OPT_IN_PROVENANCE,
                              "accepted_by": record["set_by"], "accepted_at": record["set_at"]}
        document["qualification_digest"] = self._record_digest(document)
        document.update({
            "format": self._PROBED_FORMAT, "requested_encode": dict(encode),
            "grant_digest": hashlib.sha256(_canonical(grant)).hexdigest(),
            "encode_authority": binding,
        })
        return self._authority(document, packet, production=True)

    def _late_label_packet(self, packet: dict[str, Any]) -> bool:
        """True when this packet claims the installed late-label v3 profile digest."""
        installed = self.late_label_profile_digest()
        if installed is None:
            return False
        try:
            return packet["recipe"]["profile_digest"] == installed
        except (KeyError, TypeError):
            return False

    def _subtitle_packet(self, packet: dict[str, Any]) -> bool:
        """True when this packet claims the installed v4 subtitle profile digest."""
        installed = self.subtitle_profile_digest()
        if installed is None:
            return False
        try:
            return packet["recipe"]["profile_digest"] == installed
        except (KeyError, TypeError):
            return False

    def authority_for_packet(self, packet: dict[str, Any]) -> InstalledRenderAuthority | None:
        # A submitting packet selects its profile's own scope: a v4 subtitle packet reads
        # only the v4 records, and a v3 packet only the v3 records, never the v1
        # probe/opt-in paths and never their snapshot fallback.
        if self._subtitle_packet(packet):
            return self.subtitle_snapshot(packet)
        if self._late_label_packet(packet):
            return self.late_label_snapshot(packet)
        return self._probed_authority((packet.get("recipe") or {}).get("encode"), packet)

    def execution_for_encode(self, encode: dict[str, Any]) -> dict[str, Any] | None:
        """Current exact permission, separate from support and from the legacy worker row."""
        authority = self._probed_authority(encode)
        if authority is None:
            return None
        qualification = authority.qualification
        document = qualification.document
        measured = document["format"] == self._QUALIFIED_BINDING_FORMAT
        grant = None
        if not measured:
            current = self.probed_grant()
            if current is None:
                return None
            digest = hashlib.sha256(_canonical(current)).hexdigest()
            if digest != document["grant_digest"]:
                return None
            grant = {"format": current["format"], "digest": digest,
                     "v1": current["v1"], "migration": current["migration"],
                     "node": current["node"]}
        return {
            "format": "studio.render.encode-execution-authority.v1",
            "requested_encode": dict(encode),
            "encode_authority": document["encode_authority"],
            "authority_kind": "measured" if measured else "accepted_unqualified",
            "qualification_digest": qualification.qualification_digest,
            "profile_digest": qualification.profile_digest,
            "tools_digest": qualification.tools_digest,
            "compiler_digest": qualification.compiler_digest,
            "hardware_class": qualification.hardware_class,
            "selected_tier": authority.selected_tier,
            "exact_encoder": authority.exact_encoder,
            "font_support": authority.font_support,
            "bounds": asdict(authority.bounds),
            "profile_execution_bounds": json.loads(_canonical(authority.profile_execution_bounds)),
            "grant": grant,
        }

    def resolve_probed(self, packet: dict[str, Any]) -> RuntimeQualification | None:
        authority = self.authority_for_packet(packet)
        return authority.qualification if authority is not None else None

    def opt_in_state(self) -> dict[str, Any]:
        """What the setting says now, and whether this Mac can act on it."""
        record = self._opt_in_record()
        enabled = record is not None and record["enabled"] is True
        authority = self._unqualified_authority() if enabled else None
        grant = self.probed_grant() if enabled else None
        entries = self.granted_probe_entries() if grant else []
        return {
            "grant_version": "v2" if grant else "v1",
            "granted_encodes": [entry["requested_encode"] for entry in entries] if grant else (
                [dict(record["encode"])] if enabled and authority is not None else []),
            "migration_provenance": grant["migration"] if grant else None,
            "accept_unqualified": enabled,
            "provenance": OPT_IN_PROVENANCE,
            "set_by": record["set_by"] if record else None,
            "set_at": record["set_at"] if record else None,
            "encode": dict(record["encode"]) if record else None,
            "default_encode": dict(render_encode.DEFAULT_OPT_IN_ENCODE),
            "selected_tier": authority.selected_tier if authority else None,
            "exact_encoder": authority.exact_encoder if authority else None,
            # True only when the installed tools, hardware class and encode all pass the same
            # checks a measured authority passes; a Mac that cannot act on it says so here.
            "effective": bool(entries) if grant else authority is not None,
        }

    def set_opt_in(self, enabled: object, requested_by: object,
                   encode: object = None) -> dict[str, Any]:
        """Persist the owner's choice. Raises ``ValueError`` with a sentence for the caller."""
        if type(enabled) is not bool:
            raise ValueError("accept_unqualified must be true or false")
        if self.data_root is None:
            raise ValueError("this Hub has no data folder to keep the setting in")
        who = requested_by if isinstance(requested_by, str) else ""
        if not (0 < len(who) <= 120 and who.isascii() and who.isprintable()):
            raise ValueError(
                "requested_by must name who changed this (1-120 printable ASCII characters)")
        previous = self._opt_in_record()
        if encode is not None:
            chosen = render_encode.normalize_encode(encode)
            if chosen is None:
                raise ValueError("encode is not one of the combinations the pinned compiler offers")
            if chosen["bitrate_kbps"] is not None:
                raise ValueError("a capped encode needs an exact successful probe and a v2 grant")
        else:
            chosen = (dict(previous["encode"]) if previous
                      else dict(render_encode.DEFAULT_OPT_IN_ENCODE))
        if enabled and render_encode.primary_pair(chosen) is None:
            raise ValueError("the pinned compiler cannot build that encode without dropping features")
        record = {
            "format": self._OPT_IN_FORMAT, "enabled": enabled, "encode": chosen,
            "set_by": who, "set_at": int(self._clock()),
        }
        try:
            render_authority_files.write_record_replace(
                self.data_root, "opt-in", "unqualified.json", _canonical(record),
                expected_uid=self._uid)
        except (OSError, ValueError) as exc:
            raise ValueError("the setting could not be saved on this Mac") from exc
        # The enable boundary: only tuples this node successfully probed, with their exact
        # evidence digests, become the v2 grant here. Nothing unprobed - the default CPU
        # pair included - ever enters a grant on an enable alone.
        self.migrate_probed_grant()
        return self.opt_in_state()

    @classmethod
    def receipt_document_for(cls, document: dict[str, Any]) -> dict[str, Any] | None:
        """The measured-receipt form of an opted-in authority, for the run that just proved it.

        Same tier, encoder, bounds and tool pins; the receipt format and its own digest. The
        opt-in evidence stays on the job row - the receipt's key set is closed so it can later
        be copied into ``enabled/`` exactly as a qualification receipt can.
        """
        if (type(document) is not dict or set(document) not in (
                cls._UNQUALIFIED_KEYS,
                cls._UNQUALIFIED_KEYS | {"requested_encode", "encode_authority", "grant_digest"})
                or document["format"] not in (cls._UNQUALIFIED_FORMAT, cls._PROBED_FORMAT)):
            return None
        receipt = {key: document[key] for key in cls._RECEIPT_KEYS}
        receipt["format"] = "studio.render.qualification-receipt.v1"
        receipt["qualification_digest"] = cls._record_digest(receipt)
        return receipt

    @classmethod
    def read_job_measurement(
        cls, data_root: Path, document: dict[str, Any], job_id: str,
        output_receipt: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Read only this completed job's retained measurement, never a qualification baseline."""
        try:
            expected = (
                cls.receipt_document_for(document)
                if document.get("format") in (cls._UNQUALIFIED_FORMAT, cls._PROBED_FORMAT)
                else {key: document[key] for key in cls._RECEIPT_KEYS})
            if expected is None:
                return None
            name = cls._receipt_name(
                expected["hardware_class"], job_id, expected["qualification_digest"])
            receipt = _closed(json.loads(render_authority_files.read_record(
                data_root, "receipts", name, expected_uid=os.getuid())),
                cls._ENABLED_KEYS, "render measurement receipt")
            if (any(receipt[key] != expected[key] for key in cls._RECEIPT_KEYS)
                    or receipt["qualification_digest"] != cls._record_digest(receipt)
                    or receipt["intent_id"] != job_id):
                return None
            measured = cls._measured_evidence(receipt, receipt["measured"])
            if (measured is None or measured["hub_job_id"] != job_id
                    or measured["receipt_digest"] != output_receipt.get("receipt_digest")
                    or measured["output_bytes"] != output_receipt.get("output_size_bytes")):
                return None
            return measured
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def _candidate(
        self, packet: dict[str, Any] | None = None, *, open_only: bool = True,
    ) -> tuple[dict[str, Any], InstalledRenderAuthority] | None:
        intent = self._intent()
        if intent is None or (open_only and self._clock() >= intent["accept_before"]):
            return None
        document = self._intent_document(intent)
        authority = self._authority(document, packet) if document is not None else None
        return (intent, authority) if authority is not None else None

    def qualification_candidates(self) -> list[tuple[dict[str, Any], InstalledRenderAuthority]]:
        candidate = self._candidate()
        return [candidate] if candidate is not None else []

    def resolve(self, packet: dict[str, Any]) -> RuntimeQualification | None:
        enabled = self._enabled()
        production = (self._authority(enabled, packet, production=True)
                      if enabled is not None else None)
        if (production is not None
                and production.qualification.profile_digest == packet["recipe"]["profile_digest"]):
            return production.qualification
        unqualified = self._unqualified_authority(packet) if enabled is None else None
        if (unqualified is not None
                and unqualified.qualification.profile_digest == packet["recipe"]["profile_digest"]):
            return unqualified.qualification
        # A qualification job may claim after its acceptance window closes; the one-use
        # intent was spent at acceptance under the permit, so only the installed evidence
        # and the intent's own settings are re-checked here.
        candidate = self._candidate(packet, open_only=False)
        return candidate[1].qualification if candidate is not None else None

    def authority_for(self, job: dict[str, Any]) -> InstalledRenderAuthority | None:
        qualification = job.get("qualification")
        if getattr(qualification, "document", None) is not None:
            # A claimed job runs on the exact document stored on its row, so revoking or
            # deleting the operator file never orphans it.
            return self._authority(
                qualification.document, production=qualification.production_eligible)
        # A candidate and the enabled record built from its receipt share one digest, so the
        # source is chosen by purpose: a production row never runs on a qualify.json candidate.
        if job.get("purpose") == "qualification":
            candidate = self._candidate(open_only=False)
            authority = candidate[1] if candidate is not None else None
        else:
            authority = self.snapshot()
        if (authority is not None and job.get("qualification_digest")
                == authority.qualification.qualification_digest):
            return authority
        return None

    def qualification_permit(
        self, vector_id: str, owner_scope: str, raw_request: bytes,
        descriptors: dict[str, dict[str, Any]],
    ) -> QualificationPermit | None:
        # The service already matched every asset descriptor to the packet manifest.
        del descriptors
        try:
            prelim = decode_render_json(raw_request)
            if type(prelim) is not dict or type(prelim.get("dispatch")) is not dict:
                return None
            value, packet, _packet_json = decode_render_request(
                raw_request, prelim["dispatch"].get("dispatch_key"))
            dispatch = value["dispatch"]
            candidate = self._candidate(packet)
            if candidate is None or candidate[0]["intent_id"] != vector_id:
                return None
            authority = candidate[1]
            return QualificationPermit(
                vector_id=vector_id, owner_scope=owner_scope,
                exact_request_sha256=hashlib.sha256(raw_request).hexdigest(),
                qualification_digest=authority.qualification.qualification_digest,
                hardware_class=authority.qualification.hardware_class,
                story_uuid=dispatch["story_uuid"], intent_id=dispatch["intent_id"],
                attempt_no=dispatch["attempt_no"],
                gateway_attempt_key=dispatch["gateway_attempt_key"],
                destination_site=dispatch["destination_site"],
                destination_controller=dispatch["destination_controller"],
                destination_node=dispatch["destination_node"],
                dispatch_key=dispatch["dispatch_key"], dispatch_fence=dispatch["dispatch_fence"],
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _receipt_name(
        hardware_class: str, intent_id: str, qualification_digest: str,
    ) -> str:
        """One retained receipt per intent; never one editable slot per hardware class."""
        name = f"{hardware_class}.{intent_id}.{qualification_digest}.json"
        if len(name) > 255:
            raise ValueError("qualification receipt name is too long")
        return name

    def write_receipt(
        self, document: dict[str, Any], intent_id: str, measured: dict[str, Any],
    ) -> bool:
        """Record one qualification run; never replaces an existing receipt."""
        if self.data_root is None:
            return False
        try:
            # production_eligible stays false (it is inside qualification_digest): the
            # coordinator copying this receipt into enabled/ is what grants production.
            if (set(document) != self._RECEIPT_KEYS
                    or document["qualification_digest"] != self._record_digest(document)
                    or type(intent_id) is not str
                    or str(uuid.UUID(intent_id)) != intent_id
                    or self._authority(document) is None
                    or self._measured_evidence(document, measured) is None):
                return False
            receipt = {key: document[key] for key in self._RECEIPT_KEYS}
            receipt.update(intent_id=intent_id, measured=dict(measured))
            render_authority_files.write_record_once(
                self.data_root, "receipts",
                self._receipt_name(
                    receipt["hardware_class"], intent_id,
                    receipt["qualification_digest"]),
                _canonical(receipt))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return False
        return True


class LateLabelProbeScope:
    """The loader view an encode probe runs under in the late-label v3 scope.

    The scoped control builds its identity from these two lookups only: the bundled v3
    tools and, when this Mac has one, its granted v3 record. Probe keys mix in those
    digests, so the shared record and evidence directories isolate v1 and v3 proofs by
    construction, and the v1 opt-in grant is unreachable from this scope.
    """

    def __init__(self, loader: InstalledRenderAuthorityLoader) -> None:
        self._loader = loader

    def snapshot(self) -> InstalledRenderAuthority | None:
        return self._loader.late_label_snapshot()

    def probe_authority(self) -> InstalledRenderAuthority | None:
        return self._loader.probe_authority(late_labels=True)


class SubtitleProbeScope:
    """The loader view an encode probe runs under in the v4 subtitle scope.

    Same isolation as the late-label scope: the control's identity is built from the
    bundled v4 triple and this Mac's own granted v4 record only, so v1 and v3 proofs and
    grants are unreachable from it.
    """

    def __init__(self, loader: InstalledRenderAuthorityLoader) -> None:
        self._loader = loader

    def snapshot(self) -> InstalledRenderAuthority | None:
        return self._loader.subtitle_snapshot()

    def probe_authority(self) -> InstalledRenderAuthority | None:
        return self._loader.probe_authority(subtitles=True)


class CueOverlayProbeScope:
    """The loader view an encode probe runs under in the v5 cue overlay scope.

    Same isolation as the subtitle scope: the control's identity is built from the
    bundled v5 triple and this Mac's own granted v5 record only, so v1, v3 and v4 proofs and
    grants are unreachable from it.
    """

    def __init__(self, loader: InstalledRenderAuthorityLoader) -> None:
        self._loader = loader

    def snapshot(self) -> InstalledRenderAuthority | None:
        return self._loader.cue_overlay_snapshot()

    def probe_authority(self) -> InstalledRenderAuthority | None:
        return self._loader.probe_authority(cue_overlays=True)


_configured_executor = None


def configure(executor) -> None:
    global _configured_executor
    _configured_executor = executor


def capability() -> dict[str, Any] | None:
    return _configured_executor.capability() if _configured_executor is not None else None


def activity_batches() -> dict[str, dict[str, Any]]:
    if _configured_executor is None:
        return {}
    return dict(_configured_executor.activity_batches())


class RenderExecutor:
    def __init__(
        self, runtime, assets, authority, *, machine: str, broker_api,
        compile_plan=compile_final_render_packet,
        spawn=asyncio.create_subprocess_exec,
        output_validator=None,
        process_identity=None,
        kill_group=os.killpg,
        group_exists=_group_exists,
        available_memory=None,
        monotonic=time.monotonic,
        free_bytes=None,
        cancel_grace_seconds: float = 10.0,
        process_interruptible=None,
        recorded_process_state=None,
        shutdown_timeout: float | None = None,
    ) -> None:
        self.runtime = runtime
        self.assets = assets
        self._authority = authority
        self.machine = machine
        self.broker = broker_api
        self._compile = compile_plan
        self._spawn = spawn
        self._uses_builtin_validator = output_validator is None
        self._validate = output_validator or self._validate_output
        self._process_identity = process_identity or self._identity
        self._kill_group = kill_group
        self._group_exists = group_exists
        self._available_memory = available_memory or (
            lambda: host_memory.available_bytes(psutil.virtual_memory()))
        self._monotonic = monotonic
        self._free_bytes = free_bytes or (lambda path: shutil.disk_usage(path).free)
        self._cancel_grace_seconds = cancel_grace_seconds
        # Only a real child returned by asyncio's subprocess transport may be signalled
        # during shutdown; injected fake processes in tests are never OS-signalled.
        self._process_interruptible = process_interruptible or (
            lambda process: isinstance(process, asyncio.subprocess.Process))
        self._recorded_state = recorded_process_state or self._recorded_process_state
        self._shutdown_timeout = (
            max(5.0, cancel_grace_seconds + 5.0)
            if shutdown_timeout is None else shutdown_timeout)
        self._accepting = True
        self._tasks: set[asyncio.Task] = set()
        self._active: dict[
            str, tuple[Any, ProcessIdentity, RenderExecutionContext, Path]
        ] = {}
        self._settle_locks: dict[str, asyncio.Lock] = {}
        self._release_hook = None

    def attach_release_hook(self, release) -> None:
        """Attach the service-owned job-scoped asset release, if one exists.

        The hook is best-effort and runs only after the durable settle/terminal state is
        visible; an exception is swallowed and cannot change lifecycle decisions.
        """
        self._release_hook = release if callable(release) else None

    def _authority_for(self, job: dict[str, Any]) -> InstalledRenderAuthority | None:
        if hasattr(self._authority, "authority_for"):
            return self._authority.authority_for(job)
        try:
            return self._authority(job)
        except TypeError:
            return self._authority()

    def recover_and_restore_physical_holds(self) -> None:
        recover = getattr(self.runtime, "recover_incomplete_with_proof", None)
        rows = (
            recover(self._recorded_process_cannot_run)
            if callable(recover) else self.runtime.recover_incomplete())
        for row in rows:
            if row.get("leases", {}).get("compute") and row.get("machine"):
                self.broker.restore_external_machine(
                    row["machine"], f"render:{row['id']}")

    @staticmethod
    def _boot_changed_since(created_at: float) -> bool:
        """A row created before the current boot cannot own a live process."""
        if (isinstance(created_at, bool) or not isinstance(created_at, (int, float))
                or not math.isfinite(created_at) or created_at <= 0):
            return False
        try:
            return psutil.boot_time() > created_at + 1.0
        except (OSError, psutil.Error):
            return False

    def _recorded_process_cannot_run(
        self, identity: ProcessIdentity | None, created_at: float,
    ) -> bool:
        if self._boot_changed_since(created_at):
            return True
        return identity is not None and self._recorded_state(identity) == "gone"

    def _recorded_process_state(self, identity: ProcessIdentity) -> str:
        """Classify the exact recorded process as gone, live, or unproved.

        Only a leader whose pid, create time, executable and process group all match the
        durable identity is ``live``. A missing leader is ``gone`` only when its process
        group no longer exists; a surviving group (or a recycled pid) stays ``unknown``.
        """
        try:
            boot_raw, create_raw, executable = identity.start_token.split(":", 2)
            recorded_boot = float(boot_raw)
            recorded_create = float(create_raw)
            if not (math.isfinite(recorded_boot) and math.isfinite(recorded_create)):
                return "unknown"
            current_boot = psutil.boot_time()
            if current_boot > recorded_boot + 1.0:
                return "gone"
            if recorded_boot > current_boot + 1.0:
                return "unknown"
            observed = psutil.Process(identity.pid)
            same = (
                abs(observed.create_time() - recorded_create) <= 1e-3
                and Path(observed.exe()).resolve() == Path(executable).resolve()
            )
            if same:
                try:
                    if os.getpgid(identity.pid) == identity.process_group_id:
                        return "live"
                except OSError:
                    pass
                return "unknown"
        except (ValueError, OSError, psutil.NoSuchProcess):
            pass
        except psutil.Error:
            return "unknown"
        try:
            return "unknown" if self._group_exists(identity.process_group_id) else "gone"
        except (OSError, psutil.Error):
            return "unknown"

    async def _stop_recovered_exact_group(self, identity: ProcessIdentity) -> bool:
        """Signal one exact recorded group and prove it empty; never a pattern kill."""
        if self._recorded_state(identity) != "live":
            return False
        try:
            self._kill_group(identity.process_group_id, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except (OSError, psutil.Error):
            return False
        if await self._await_group_empty(
                identity.process_group_id, self._cancel_grace_seconds):
            return True
        # The leader may have exited while descendants remain. Only kill the group when
        # the exact leader is still proved alive; otherwise keep the honest unknown state.
        if self._recorded_state(identity) != "live":
            return False
        try:
            self._kill_group(identity.process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except (OSError, psutil.Error):
            return False
        return await self._await_group_empty(identity.process_group_id, 2.0)

    async def _reconcile_uncertain(self, record: dict[str, Any]) -> dict[str, Any]:
        """Bounded reconciliation for a row whose process handle did not survive."""
        owner_scope = record["owner_scope"]
        job_id = record["id"]
        owner = f"render:{job_id}"
        pending = self.runtime.request_cancel_unsettled(owner_scope, job_id)
        if record.get("stopped_evidence_digest") is not None:
            settled = self.runtime.settle_recovered(
                owner_scope, job_id,
                proof="recovered: durable stop evidence proves the recorded process stopped")
            self._release_if_durably_settled(owner_scope, job_id, owner)
            return settled
        if self._boot_changed_since(record.get("created_at", 0)):
            settled = self.runtime.settle_recovered(
                owner_scope, job_id,
                proof="recovered: attempt predates the current boot and cannot still run")
            self._release_if_durably_settled(owner_scope, job_id, owner)
            return settled
        identity = record.get("process")
        if identity is None:
            return pending
        state = self._recorded_state(identity)
        if state == "gone":
            settled = self.runtime.settle_recovered(
                owner_scope, job_id,
                proof="recovered: recorded process cannot still run")
            self._release_if_durably_settled(owner_scope, job_id, owner)
            return settled
        if state == "live":
            try:
                stopped = await self._stop_recovered_exact_group(identity)
            except asyncio.CancelledError:
                raise
            except Exception:
                stopped = False
            if stopped:
                settled = self.runtime.settle_recovered(
                    owner_scope, job_id,
                    proof="recovered: exact owned process group proved stopped")
                self._release_if_durably_settled(owner_scope, job_id, owner)
                return settled
        return self.runtime.get(owner_scope, job_id) or pending

    def _admission_allowed(self, owner: str | None = None) -> bool:
        if not self._accepting:
            return False
        gate = getattr(self.broker, "render_dispatch_allowed", None)
        return bool(gate(self.machine, owner)) if callable(gate) else False

    async def dispatchable_candidate(self, machine: str):
        if machine != self.machine or not self._admission_allowed():
            return None
        for row in self.runtime.dispatchable_jobs():
            authority = self._authority_for(row)
            if authority is not None and self._has_memory(authority):
                created_at = row.get("created_at")
                if (type(created_at) not in (int, float)
                        or not math.isfinite(created_at) or created_at <= 0):
                    continue
                return float(created_at), row["id"]
        return None

    def pending_for_machine(self, machine: str) -> bool:
        if machine != self.machine or not self._admission_allowed():
            return False
        return any(
            (authority := self._authority_for(row)) is not None
            and self._has_memory(authority)
            for row in self.runtime.dispatchable_jobs()
        )

    def _has_memory(self, authority: InstalledRenderAuthority) -> bool:
        try:
            available = self._available_memory()
        except (OSError, psutil.Error):
            return False
        return (type(available) is int
                and available >= authority.bounds.min_available_memory_bytes)

    async def dispatch_ready(self) -> bool:
        candidate = await self.dispatchable_candidate(self.machine)
        if candidate is None:
            return False
        created_at, job_id = candidate
        row = next(row for row in self.runtime.dispatchable_jobs() if row["id"] == job_id)
        if await self.broker.higher_priority_work_waiting(
                machine=self.machine, modality="render", created_at=created_at,
                identity=f"render:{job_id}"):
            return False
        owner = f"render:{job_id}"
        if not self.broker.acquire_external_machine(self.machine, owner):
            return False
        claimed = False
        claim = None
        try:
            if not self._admission_allowed(owner):
                self.broker.release_external_machine(self.machine, owner)
                return False
            try:
                claim = self.runtime.claim_launch(
                    row["owner_scope"], job_id, machine=self.machine,
                    dispatch_generation=max(1, time.monotonic_ns()),
                )
            except RenderJobUnavailable:
                # Nothing was claimed: this one row is not ready, and it must never stop
                # the broker loop from dispatching other work.
                self.broker.release_external_machine(self.machine, owner)
                return False
            claimed = True
            context = self.runtime.execution_context(
                row["owner_scope"], job_id, claim["attempt_id"], claim["launch_token"])
            authority = self._authority_for({
                **row, "qualification_digest": context.qualification.qualification_digest,
                "qualification": context.qualification,
            })
            if authority is None:
                raise RuntimeError("installed render authority changed after durable claim")
            if not self._admission_allowed(owner):
                self.runtime.record_execution_failed(
                    row["owner_scope"], job_id, claim["attempt_id"],
                    claim["launch_token"], "prelaunch_failed")
                self._release_if_durably_settled(row["owner_scope"], job_id, owner)
                return False
            task = asyncio.create_task(self._run(context, authority, owner))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return True
        except BaseException:
            if not claimed:
                self.broker.release_external_machine(self.machine, owner)
            else:
                try:
                    self.runtime.record_execution_failed(
                        row["owner_scope"], job_id, claim["attempt_id"],
                        claim["launch_token"], "prelaunch_failed")
                except BaseException:
                    pass
                self._release_if_durably_settled(row["owner_scope"], job_id, owner)
            raise

    @staticmethod
    def _identity(process, executable: Path) -> ProcessIdentity:
        observed = psutil.Process(process.pid)
        if Path(observed.exe()).resolve() != executable.resolve():
            raise RuntimeError("render process executable identity changed")
        process_group_id = os.getpgid(process.pid)
        if process_group_id != process.pid:
            raise RuntimeError("render process does not own its isolated process group")
        return ProcessIdentity(
            pid=process.pid, process_group_id=process_group_id,
            start_token=f"{psutil.boot_time()}:{observed.create_time()}:{executable}",
        )

    def _same_process(
        self, process: Any, identity: ProcessIdentity, executable: Path,
    ) -> bool:
        try:
            return self._process_identity(process, executable) == identity
        except (OSError, RuntimeError, psutil.Error):
            return False

    async def _await_group_empty(self, process_group_id: int, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while self._group_exists(process_group_id) and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        return not self._group_exists(process_group_id)

    async def _stop_exact_group(
        self, process: Any, identity: ProcessIdentity, executable: Path,
    ) -> int | None:
        if getattr(process, "returncode", None) is not None:
            if self._group_exists(identity.process_group_id):
                return None
            return int(await process.wait())
        if not self._same_process(process, identity, executable):
            return None
        try:
            self._kill_group(identity.process_group_id, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            exit_code = await asyncio.wait_for(
                asyncio.shield(process.wait()), timeout=self._cancel_grace_seconds)
        except asyncio.TimeoutError:
            try:
                self._kill_group(identity.process_group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
            exit_code = await process.wait()
        if self._group_exists(identity.process_group_id):
            try:
                self._kill_group(identity.process_group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if not await self._await_group_empty(identity.process_group_id):
            return None
        return int(exit_code)

    def _release_if_durably_settled(
        self, owner_scope: str, job_id: str, physical_owner: str,
    ) -> bool:
        try:
            public = self.runtime.get(owner_scope, job_id)
        except BaseException:
            return False
        releasable = (
            type(public) is dict
            and public.get("state") in {"cancelled", "failed", "output_ready"}
            and type(public.get("leases")) is dict
            and public["leases"].get("compute") is False
        )
        if releasable:
            self.broker.release_external_machine(self.machine, physical_owner)
            hook = self._release_hook
            if hook is not None:
                try:
                    hook(owner_scope, job_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Asset-retirement bookkeeping never changes a durable lifecycle result.
                    pass
        return releasable

    async def _settle_shutdown_process(
        self, process: Any, identity: ProcessIdentity,
        context: RenderExecutionContext, executable: Path,
    ) -> bool:
        """Stop one exact owned group and record its stop before any release."""
        exit_code = await self._stop_exact_group(process, identity, executable)
        if exit_code is None:
            return False
        async with self._settle_locks.setdefault(
                context.hub_job_id, asyncio.Lock()):
            public = self.runtime.get(context.owner_scope, context.hub_job_id)
            if type(public) is not dict:
                return False
            if public.get("state") in {"cancelled", "failed", "output_ready", "stopped"}:
                return True
            if public.get("state") not in {"rendering", "cancel_requested", "uncertain"}:
                return False
            self.runtime.record_process_stopped(
                context.owner_scope, context.hub_job_id, context.attempt_id,
                StoppedProcessEvidence(
                    process=identity, observed_at=time.time(), exit_code=exit_code,
                    reason=(
                        "cancelled" if public.get("cancel_requested_at") is not None
                        else "failed")),
            )
        return True

    async def _run(self, context: RenderExecutionContext,
                   authority: InstalledRenderAuthority, owner: str) -> None:
        process = None
        identity = None
        spawn_invoked = False
        main_stopped = False
        started = self._monotonic()
        attempt_deadline = started + authority.bounds.max_wall_seconds
        measured: dict[str, Any] = {"peak_rss_bytes": 0, "min_free_bytes": None}
        try:
            packet_raw = _canonical(context.packet)
            operations = context.packet["recipe"].get("operations", [])
            requirements = late_label_input_requirements(packet_raw) if any(
                op.get("kind") == "late_label_stage" for op in operations) else None
            subtitle_ids = _subtitle_input_ids(context.packet) if any(
                op.get("kind") == "subtitle_stage" for op in operations) else None
            cue_overlay_ops = [op for op in operations if op.get("kind") == "cue_overlay_stage"]
            compiler = self._compile
            compile_options = {}
            if (compiler is compile_final_render_packet
                    and isinstance(self._authority, InstalledRenderAuthorityLoader)):
                if self._authority.late_labels_allowed(authority, context.packet):
                    compile_options["late_labels_allowed"] = True
                if subtitle_ids is not None:
                    if not self._authority.subtitles_allowed(authority, context.packet):
                        raise RenderJobUnavailable(
                            "subtitle_profile_required: subtitles require matched v4 "
                            "profile/compiler pins")
                    compile_options["subtitles_allowed"] = True
                if cue_overlay_ops:
                    if not self._authority.cue_overlays_allowed(authority, context.packet):
                        raise RenderJobUnavailable(
                            "cue_overlay_profile_required: cue overlays require matched v5 "
                            "profile/compiler pins")
                    compile_options["cue_overlays_allowed"] = True
                if (requirements is None
                        or compile_options.get("late_labels_allowed") is True) and (
                        subtitle_ids is None
                        or compile_options.get("subtitles_allowed") is True) and (
                        not cue_overlay_ops
                        or compile_options.get("cue_overlays_allowed") is True):
                    compiler = self._authority.compiler_for(authority, context.packet)
            if requirements is not None and compile_options.get("late_labels_allowed") is not True:
                raise RenderJobUnavailable(
                    "late_label_profile_required: late labels require matched v3 profile/compiler pins")
            if subtitle_ids is not None and compile_options.get("subtitles_allowed") is not True:
                raise RenderJobUnavailable(
                    "subtitle_profile_required: subtitles require matched v4 profile/compiler pins")
            if cue_overlay_ops and compile_options.get("cue_overlays_allowed") is not True:
                raise RenderJobUnavailable(
                    "cue_overlay_profile_required: cue overlays require matched v5 profile/compiler pins")
            if requirements is not None or subtitle_ids is not None or cue_overlay_ops:
                expected_workspace = (self.runtime.root / "jobs" / context.hub_job_id
                                      / context.attempt_id)
                if (Path(context.workspace) != expected_workspace
                        or Path(context.output_path).parent != expected_workspace):
                    raise RuntimeError("scoped render staging requires a Hub-owned workspace")
                for directory in (expected_workspace.parent.parent,
                                  expected_workspace.parent, expected_workspace):
                    info = directory.lstat()
                    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                            or stat.S_IMODE(info.st_mode) & 0o022):
                        raise RuntimeError("scoped render staging requires a Hub-owned workspace")
            with ExitStack() as stack:
                readers = {
                    logical_id: _asset_stream(stack.enter_context(
                        self.assets.open_reader(context.owner_scope, handle)))
                    for logical_id, handle in context.assets.items()
                }
                if cue_overlay_ops:
                    from .render_cue_media import validate_cue_overlay_media, CueOverlayMediaValidationError
                    manifest_by_id = {
                        entry["logical_id"]: entry
                        for entry in context.packet.get("manifest", [])
                        if isinstance(entry, dict) and "logical_id" in entry
                    }
                    for cue_op in cue_overlay_ops:
                        asset_ref = cue_op.get("asset")
                        if (not isinstance(asset_ref, dict) or asset_ref.get("type") != "asset_ref"
                                or not isinstance(asset_ref.get("logical_id"), str)):
                            raise RuntimeError(f"cue overlay asset_ref is malformed")
                        asset_id = asset_ref["logical_id"]
                        if not asset_id or asset_id not in readers:
                            raise RuntimeError(f"missing asset reader for cue overlay {asset_id}")
                        stream = readers[asset_id]
                        manifest_entry = manifest_by_id.get(asset_id)
                        if manifest_entry is None:
                            raise RuntimeError(f"missing manifest entry for cue overlay {asset_id}")
                        try:
                            validate_cue_overlay_media(
                                authority.ffprobe,
                                stream,
                                recipe_canvas=context.packet["recipe"]["canvas"],
                                time_range=cue_op["time_range"],
                                execution_bounds=authority.profile_execution_bounds,
                                manifest_entry=manifest_entry,
                            )
                        except CueOverlayMediaValidationError as exc:
                            raise RuntimeError(f"cue overlay media rejected: {exc.reason}") from exc
                staged_ids = (
                    {subtitle_ids[0], *subtitle_ids[1]} if subtitle_ids is not None else set())
                local_assets = {
                    logical_id: f"/dev/fd/{reader.fileno()}"
                    for logical_id, reader in readers.items()
                    if logical_id not in staged_ids
                }
                sequence_inputs = (
                    _stage_late_label_inputs(
                        stack, context, requirements, readers,
                        scratch_limit=authority.profile_execution_bounds["scratch_reserve_bytes"])
                    if requirements is not None else {})
                subtitle_paths = {}
                if subtitle_ids is not None:
                    subtitle_paths, fonts_dir = _stage_subtitle_inputs(
                        stack, context, subtitle_ids, readers,
                        limits=authority.profile_execution_bounds)
                    compile_options["subtitle_fonts_dir"] = fonts_dir
                    compile_options["subtitle_limits"] = authority.profile_execution_bounds
                plan = compiler(
                    packet_raw, {**local_assets, **subtitle_paths},
                    context.output_path, **compile_options)
                expected_generated = {
                    sequence.key for sequence in requirements.sequences
                } if requirements is not None else set()
                if {key for key, _index in getattr(plan, "generated_inputs", ())} != expected_generated:
                    raise RuntimeError("compiled render sequence inventory differs from staging")
                tier = next(
                    (tier for tier in plan.tiers if tier.label == authority.selected_tier), None)
                if tier is None or authority.exact_encoder not in tier.argv:
                    raise RuntimeError("qualified render tier is not in the compiled plan")
                if tier.drops_burned_in_features:
                    raise RuntimeError("qualified render tier would drop requested features")
                confined = _confined_argv(
                    tuple(tier.argv), context.packet, local_assets, readers,
                    sequence_inputs=sequence_inputs)
                argv = (
                    str(authority.ffmpeg), "-nostdin", *confined,
                )
                command_digest = hashlib.sha256(_canonical(argv)).hexdigest()
                if not self._admission_allowed(owner):
                    raise RuntimeError("render admission closed before process launch")
                spawn_invoked = True
                process = await self._spawn(
                    *argv, start_new_session=True,
                    pass_fds=tuple(reader.fileno() for reader in readers.values()),
                    cwd=context.workspace,
                    env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"},
                )
                identity = self._process_identity(process, authority.ffmpeg)
                self.runtime.record_process_started(
                    context.owner_scope, context.hub_job_id, context.attempt_id,
                    context.launch_token, identity)
                self._active[context.hub_job_id] = (
                    process, identity, context, authority.ffmpeg)
                if not self._accepting and self._process_interruptible(process):
                    if not await self._settle_shutdown_process(
                            process, identity, context, authority.ffmpeg):
                        raise _UncertainProcessOutcome(
                            "render process could not be proved stopped during shutdown")
                    return
                public = self.runtime.get(context.owner_scope, context.hub_job_id)
                if type(public) is dict and public.get("cancel_requested_at") is not None:
                    await self.request_cancel(context.owner_scope, context.hub_job_id)
                    return
                exit_code, limit_failure = await self._wait_with_bounds(
                    process, identity, context, authority, attempt_deadline,
                    measured=measured)
                if exit_code is None:
                    return
                async with self._settle_locks.setdefault(
                        context.hub_job_id, asyncio.Lock()):
                    public = self.runtime.get(context.owner_scope, context.hub_job_id)
                    if type(public) is not dict:
                        return
                    if public.get("state") in {"cancelled", "failed", "output_ready"}:
                        return
                    cancelled = public.get("cancel_requested_at") is not None
                    reason = (
                        "cancelled" if cancelled else
                        "failed" if exit_code != 0 or limit_failure else "completed"
                    )
                    stopped = self.runtime.record_process_stopped(
                        context.owner_scope, context.hub_job_id, context.attempt_id,
                        StoppedProcessEvidence(
                            process=identity, observed_at=time.time(),
                            exit_code=exit_code, reason=reason),
                        validation_pending=reason == "completed",
                    )
                if stopped.get("state") != "stopped":
                    return
                main_stopped = True
                if not self._accepting and self._process_interruptible(process):
                    self.runtime.record_execution_failed(
                        context.owner_scope, context.hub_job_id, context.attempt_id,
                        context.launch_token, "output_validation_failed")
                    return
                receipt = (
                    self._validate(
                        context, authority, command_digest,
                        attempt_deadline=attempt_deadline)
                    if self._uses_builtin_validator
                    else self._validate(context, authority, command_digest)
                )
                if inspect.isawaitable(receipt):
                    receipt = await receipt
                if type(receipt) is not ValidatedOutputReceipt:
                    raise TypeError("render validator did not return a trusted receipt")
                public = self.runtime.get(context.owner_scope, context.hub_job_id)
                if type(public) is not dict:
                    return
                if public.get("cancel_requested_at") is not None:
                    self.runtime.record_execution_failed(
                        context.owner_scope, context.hub_job_id, context.attempt_id,
                        context.launch_token, "output_validation_failed")
                    return
                if public.get("state") != "stopped":
                    return
                if not self._record_qualification(context, receipt, measured, started):
                    # A consumed one-use intent must never end with a silently missing
                    # receipt: settle the row failed and do not publish output_ready.
                    self.runtime.record_execution_failed(
                        context.owner_scope, context.hub_job_id, context.attempt_id,
                        context.launch_token, "output_validation_failed")
                    return
                self.runtime.acquire_file_lease(
                    context.owner_scope, context.hub_job_id, "ack",
                    f"output:{context.attempt_id}")
                self.runtime.publish_output(context.owner_scope, context.hub_job_id, receipt)
        except BaseException as error:
            phase = (
                None if isinstance(error, _UncertainProcessOutcome) else
                "output_validation_failed" if main_stopped else
                "prelaunch_failed" if not spawn_invoked else None
            )
            if phase is not None:
                try:
                    self.runtime.record_execution_failed(
                        context.owner_scope, context.hub_job_id, context.attempt_id,
                        context.launch_token, phase)
                except BaseException:
                    pass
            raise
        finally:
            self._active.pop(context.hub_job_id, None)
            self._release_if_durably_settled(
                context.owner_scope, context.hub_job_id, owner)

    def _record_qualification(
        self, context: RenderExecutionContext, receipt: ValidatedOutputReceipt,
        measured: dict[str, Any], started: float,
    ) -> bool:
        """Retain this render's measurements, separate from any earlier qualification run.

        A qualification uses its one-use intent; production uses its own job id so a
        previously measured authority never substitutes old numbers for this run.
        """
        permit = context.qualification_permit
        document = context.qualification.document
        writer = getattr(self._authority, "write_receipt", None)
        opted_in = (type(document) is dict
                    and document.get("format")
                    in (InstalledRenderAuthorityLoader._UNQUALIFIED_FORMAT,
                        InstalledRenderAuthorityLoader._PROBED_FORMAT))
        if context.purpose != "qualification" and not opted_in:
            if (type(document) is not dict
                    or document.get("format") != "studio.render.qualification-receipt.v1"):
                return True
            if writer is None:
                return False
            document = {key: document[key]
                        for key in InstalledRenderAuthorityLoader._RECEIPT_KEYS}
            intent_id = context.hub_job_id
        elif opted_in:
            convert = getattr(self._authority, "receipt_document_for", None)
            document = convert(document) if callable(convert) else None
            intent_id = context.hub_job_id
            if document is None or writer is None:
                return False
        else:
            if permit is None or document is None or writer is None:
                return False
            intent_id = permit.vector_id
        wall_seconds = math.ceil(self._monotonic() - started)
        if wall_seconds > document["bounds"]["max_wall_seconds"]:
            # Never clamp measured evidence: a run whose post-validation wall exceeds its
            # declared bound is an honest failed qualification, not a shortened receipt.
            return False
        return writer(document, intent_id, {
            "peak_rss_bytes": measured["peak_rss_bytes"],
            "wall_seconds": wall_seconds,
            "min_free_bytes": measured["min_free_bytes"],
            "output_bytes": receipt.output_size_bytes,
            "hub_job_id": context.hub_job_id,
            "receipt_digest": receipt.receipt_digest,
        }) is True

    @staticmethod
    def _process_tree_rss(process_id: int) -> int | None:
        try:
            root = psutil.Process(process_id)
            members = [root, *root.children(recursive=True)]
            return sum(member.memory_info().rss for member in members)
        except psutil.NoSuchProcess:
            return 0
        except (OSError, psutil.Error):
            return None

    async def _wait_with_bounds(
        self, process: Any, identity: ProcessIdentity,
        context: RenderExecutionContext, authority: InstalledRenderAuthority,
        attempt_deadline: float, *, measured: dict[str, Any] | None = None,
    ) -> tuple[int | None, bool]:
        wait = asyncio.create_task(process.wait())
        violation = False
        while True:
            await asyncio.wait({wait}, timeout=0.25)
            rss = self._process_tree_rss(identity.pid)
            try:
                output_size = Path(context.output_path).stat().st_size
            except FileNotFoundError:
                output_size = 0
            except OSError:
                output_size = authority.bounds.output_limit_bytes + 1
            try:
                free = self._free_bytes(Path(context.workspace))
            except OSError:
                free = -1
            if measured is not None and type(rss) is int and type(free) is int:
                measured["peak_rss_bytes"] = max(measured["peak_rss_bytes"], rss)
                measured["min_free_bytes"] = (
                    free if measured["min_free_bytes"] is None
                    else min(measured["min_free_bytes"], free))
            violation = (
                self._monotonic() >= attempt_deadline
                or rss is None or rss > authority.bounds.max_rss_bytes
                or output_size > authority.bounds.output_limit_bytes
                or type(free) is not int
                or free < authority.qualification.min_free_bytes
            )
            if violation:
                exit_code = await self._stop_exact_group(
                    process, identity, authority.ffmpeg)
                if exit_code is None:
                    return None, True
                return exit_code, True
            if wait.done():
                break
        exit_code = int(await wait)
        if self._group_exists(identity.process_group_id):
            try:
                self._kill_group(identity.process_group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if not await self._await_group_empty(identity.process_group_id):
                return None, True
            violation = True
        return exit_code, violation

    @staticmethod
    async def _read_probe_stream(stream: Any, limit: int, *, tail: bool) -> bytes:
        if stream is None or not callable(getattr(stream, "read", None)):
            raise RuntimeError("pinned ffprobe did not expose bounded output streams")
        collected = bytearray()
        while True:
            chunk = await stream.read(64 * 1024)
            if not chunk:
                return bytes(collected)
            if tail:
                collected.extend(chunk)
                if len(collected) > limit:
                    del collected[:-limit]
            else:
                if len(collected) + len(chunk) > limit:
                    raise RuntimeError("pinned ffprobe metadata exceeded its qualified bound")
                collected.extend(chunk)

    @staticmethod
    def _stream_duration(stream: dict[str, Any]) -> float:
        try:
            if stream.get("duration") is not None:
                duration = float(stream["duration"])
            elif stream.get("duration_ts") is not None and stream.get("time_base") is not None:
                duration = float(Fraction(str(stream["duration_ts"]))
                                 * Fraction(str(stream["time_base"])))
            elif stream.get("codec_type") == "video" and stream.get("nb_frames") is not None:
                duration = float(Fraction(str(stream["nb_frames"]))
                                 / Fraction(str(stream["avg_frame_rate"])))
            elif (stream.get("codec_type") == "audio"
                  and stream.get("nb_samples") is not None
                  and stream.get("sample_rate") is not None):
                duration = float(stream["nb_samples"]) / float(stream["sample_rate"])
            else:
                raise ValueError("missing duration")
        except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
            raise RuntimeError("render output media lacks qualified timing evidence") from exc
        if not math.isfinite(duration) or duration <= 0:
            raise RuntimeError("render output media lacks qualified timing evidence")
        return duration

    async def _validate_output(
        self, context: RenderExecutionContext, authority: InstalledRenderAuthority,
        command_digest: str, *, attempt_deadline: float | None = None,
        metadata_limit_bytes: int = 1024 * 1024,
        diagnostic_tail_bytes: int = 64 * 1024,
    ) -> ValidatedOutputReceipt:
        output = Path(context.output_path)
        size, output_sha = _sha(output)
        if size < 1 or size > authority.bounds.output_limit_bytes:
            raise RuntimeError("render output size is outside qualified bounds")
        deadline = (
            self._monotonic() + authority.bounds.max_wall_seconds
            if attempt_deadline is None else attempt_deadline)
        try:
            probe = await self._spawn(
                str(authority.ffprobe), "-v", "error", "-show_streams", "-show_format",
                "-of", "json", "-protocol_whitelist", "file,pipe", str(output),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True, cwd=context.workspace,
                env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"},
            )
        except BaseException as exc:
            raise _UncertainProcessOutcome(
                "pinned ffprobe spawn was invoked without a returned handle") from exc
        try:
            probe_identity = self._process_identity(probe, authority.ffprobe)
        except BaseException as exc:
            raise _UncertainProcessOutcome(
                "pinned ffprobe process identity is unproved") from exc
        self._active[context.hub_job_id] = (
            probe, probe_identity, context, authority.ffprobe)
        wait = asyncio.create_task(probe.wait())
        stdout_read = asyncio.create_task(self._read_probe_stream(
            getattr(probe, "stdout", None), metadata_limit_bytes, tail=False))
        stderr_read = asyncio.create_task(self._read_probe_stream(
            getattr(probe, "stderr", None), diagnostic_tail_bytes, tail=True))
        tasks = {wait, stdout_read, stderr_read}
        try:
            while not all(task.done() for task in tasks):
                await asyncio.wait(tasks, timeout=0.05)
                for task in tasks:
                    if task.done() and not task.cancelled() and task.exception() is not None:
                        raise task.exception()
                rss = self._process_tree_rss(probe_identity.pid)
                try:
                    free = self._free_bytes(Path(context.workspace))
                except OSError:
                    free = -1
                if self._monotonic() >= deadline:
                    raise RuntimeError("render attempt exceeded its cumulative wall time")
                if rss is None or rss > authority.bounds.max_rss_bytes:
                    raise RuntimeError("pinned ffprobe exceeded qualified memory")
                if type(free) is not int or free < authority.qualification.min_free_bytes:
                    raise RuntimeError("pinned ffprobe violated the qualified scratch reserve")
            stdout = stdout_read.result()
            exit_code = int(wait.result())
        except BaseException as exc:
            if await self._stop_exact_group(
                    probe, probe_identity, authority.ffprobe) is None:
                raise _UncertainProcessOutcome(
                    "pinned ffprobe process outcome is uncertain") from exc
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        if self._group_exists(probe_identity.process_group_id):
            try:
                self._kill_group(probe_identity.process_group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if not await self._await_group_empty(probe_identity.process_group_id):
                raise _UncertainProcessOutcome(
                    "pinned ffprobe descendants did not settle")
        if exit_code != 0:
            raise RuntimeError("pinned ffprobe rejected render output")
        try:
            document = json.loads(stdout)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("pinned ffprobe returned invalid bounded metadata") from exc
        streams = document.get("streams") if isinstance(document, dict) else None
        if (type(streams) is not list or len(streams) != 2
                or any(type(row) is not dict for row in streams)):
            raise RuntimeError("render output media has unsupported stream cardinality")
        video_rows = [row for row in streams if row.get("codec_type") == "video"]
        audio_rows = [row for row in streams if row.get("codec_type") == "audio"]
        if len(video_rows) != 1 or len(audio_rows) != 1:
            raise RuntimeError("render output media has unsupported stream cardinality")
        video, audio = video_rows[0], audio_rows[0]
        recipe = context.packet["recipe"]
        canvas = recipe["canvas"]
        try:
            fps = Fraction(str(video["avg_frame_rate"]))
            duration = float((document.get("format") or {})["duration"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            raise RuntimeError("render output media lacks qualified timing evidence")
        video_duration = self._stream_duration(video)
        audio_duration = self._stream_duration(audio)
        if not math.isfinite(duration) or duration <= 0:
            raise RuntimeError("render output media lacks qualified timing evidence")
        duration_tolerance = max(0.1, 2 / canvas["working_fps"])
        media_matches = (
            video.get("codec_name") == "h264"
            and audio.get("codec_name") == "aac"
            and video.get("width") == canvas["width"]
            and video.get("height") == canvas["height"]
            and fps == canvas["working_fps"]
            and video.get("pix_fmt") == "yuv420p"
            and abs(duration - recipe["target_duration_sec"]) <= duration_tolerance
            and abs(video_duration - recipe["target_duration_sec"]) <= duration_tolerance
            and abs(audio_duration - recipe["target_duration_sec"]) <= duration_tolerance
            and abs(video_duration - audio_duration) <= duration_tolerance
        )
        if not media_matches:
            raise RuntimeError("render output media does not match the qualified profile")
        if _sha(output) != (size, output_sha):
            raise RuntimeError("render output changed during validation")
        fields = {
            "owner_scope": context.owner_scope, "hub_job_id": context.hub_job_id,
            "attempt_id": context.attempt_id,
            "accepted_request_sha256": context.accepted_request_sha256,
            "output_sha256": output_sha, "output_size_bytes": size,
            "profile_digest": authority.qualification.profile_digest,
            "tools_digest": authority.qualification.tools_digest,
            "compiler_digest": authority.qualification.compiler_digest,
            "qualification_digest": authority.qualification.qualification_digest,
            "generated_command_digest": command_digest,
            "selected_tier": authority.selected_tier, "feature_losses": (),
        }
        document = authority.qualification.document or {}
        if document.get("encode_authority") is not None:
            fields["encode_authority"] = dict(document["encode_authority"])
        receipt_digest = hashlib.sha256(_canonical({
            **fields, "feature_losses": [],
        })).hexdigest()
        return ValidatedOutputReceipt(receipt_digest=receipt_digest, **fields)

    async def request_cancel(self, owner_scope: str, job_id: str) -> dict[str, Any]:
        owner = f"render:{job_id}"
        active = self._active.get(job_id)
        if active is None:
            record = getattr(self.runtime, "recovery_record", None)
            recovery = record(owner_scope, job_id) if callable(record) else None
            if (type(recovery) is dict and recovery.get("compute_held")
                    and recovery.get("state") in {
                        "launch_pending", "rendering", "cancel_requested",
                        "uncertain", "stopped"}):
                return await self._reconcile_uncertain(recovery)
            row = self.runtime.request_cancel(owner_scope, job_id)
            self._release_if_durably_settled(owner_scope, job_id, owner)
            return row
        row = self.runtime.request_cancel_pending(owner_scope, job_id)
        process, identity, context, executable = active
        if not self._same_process(process, identity, executable):
            return row
        exit_code = await self._stop_exact_group(process, identity, executable)
        if exit_code is None:
            return row
        async with self._settle_locks.setdefault(job_id, asyncio.Lock()):
            current = self.runtime.get(owner_scope, job_id)
            if (type(current) is dict and current.get("leases", {}).get("compute")
                    and current.get("state") in {"rendering", "cancel_requested", "uncertain"}):
                if current.get("stopped_evidence_digest") is not None:
                    self.runtime.record_execution_failed(
                        owner_scope, job_id, context.attempt_id,
                        context.launch_token, "output_validation_failed")
                else:
                    self.runtime.record_process_stopped(
                        owner_scope, job_id, context.attempt_id,
                        StoppedProcessEvidence(
                            process=identity, observed_at=time.time(), exit_code=exit_code,
                            reason="cancelled"),
                    )
        self._release_if_durably_settled(owner_scope, job_id, owner)
        return self.runtime.get(owner_scope, job_id)

    def begin_shutdown(self) -> None:
        self._accepting = False

    async def stop(self) -> None:
        """Signal every exact owned process group, prove stop, then drain settlement.

        A shutdown never waits for the render wall-time bound. Each live owned group is
        signalled through its exact identity; its task records durable stop evidence and
        releases the physical hold only through the normal settlement path. A group that
        cannot be proved stopped stays uncertain and keeps its compute hold.
        """
        self.begin_shutdown()
        deadline = self._monotonic() + self._shutdown_timeout
        while True:
            for process, identity, _context, executable in tuple(self._active.values()):
                if (not self._process_interruptible(process)
                        or getattr(process, "returncode", None) is not None):
                    continue
                try:
                    await self._stop_exact_group(process, identity, executable)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # The task will observe the same unproved outcome and keep the hold.
                    continue
            if not self._tasks:
                return
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return
            await asyncio.wait(
                tuple(self._tasks), timeout=min(0.1, max(0.0, remaining)))

    def activity_batches(self) -> dict[str, dict[str, Any]]:
        jobs = (
            self.runtime.activity_jobs()
            if hasattr(self.runtime, "activity_jobs") else self.runtime.dispatchable_jobs()
        )
        return {
            f"render:{row['id']}": {
                "id": row["id"], "model": "final-render", "modality": "render",
                "created_at": row.get("created_at", 0), "items": [{
                    "id": row["id"], "state": row["state"], "machine": row.get("machine"),
                    "operation": "video.final_render",
                }],
            }
            for row in jobs
        }

    def capability(self) -> dict[str, Any]:
        authority = (
            self._authority.snapshot()
            if hasattr(self._authority, "snapshot") else self._authority_for({})
        )
        admission_allowed = self._admission_allowed()
        busy = self.machine in self.broker.busy_machines()
        # An enabled authority record for this Mac's hardware class is production.
        ready = bool(
            authority
            and self._has_memory(authority)
            and admission_allowed
            and not busy
        )
        qualification_candidates = []
        consumed = getattr(self.runtime, "qualification_consumed", None)
        if hasattr(self._authority, "qualification_candidates") and consumed is not None:
            for intent, candidate in self._authority.qualification_candidates():
                # The loader only returns an intent still inside its accept_before window;
                # a spent intent is never advertised again.
                if consumed(intent["intent_id"]):
                    continue
                available = self._has_memory(candidate) and admission_allowed and not busy
                qualification_candidates.append({
                    "vector_id": intent["intent_id"],
                    "hardware_class": candidate.qualification.hardware_class,
                    "profile_digest": candidate.qualification.profile_digest,
                    "tools_digest": candidate.qualification.tools_digest,
                    "compiler_digest": candidate.qualification.compiler_digest,
                    "qualification_digest": candidate.qualification.qualification_digest,
                    "selected_tier": candidate.selected_tier,
                    "exact_encoder": candidate.exact_encoder,
                    "font_support": candidate.font_support,
                    "bounds": asdict(candidate.bounds),
                    "profile_execution_bounds": json.loads(
                        _canonical(candidate.profile_execution_bounds)),
                    "production_eligible": False,
                    "qualification_only": True,
                    "available_capacity": {
                        "slots": int(available), "slots_total": 1,
                    },
                })
        row = {
            "operation": "video.final_render",
            "physical_machine_id": self.machine,
            "hardware_class": (
                authority.qualification.hardware_class if authority else None),
            "profile_digest": (
                authority.qualification.profile_digest if authority else None),
            "tools_digest": (
                authority.qualification.tools_digest if authority else None),
            "compiler_digest": (
                authority.qualification.compiler_digest if authority else None),
            "qualification_digest": (
                authority.qualification.qualification_digest if authority else None),
            "selected_tier": authority.selected_tier if authority else None,
            "exact_encoder": authority.exact_encoder if authority else None,
            "font_support": authority.font_support if authority else False,
            "bounds": asdict(authority.bounds) if authority else None,
            "profile_execution_bounds": (
                json.loads(_canonical(authority.profile_execution_bounds))
                if authority else None),
            "production_eligible": bool(
                authority and authority.qualification.production_eligible),
            "production_enabled": bool(authority),
            "available_capacity": {"slots": int(ready), "slots_total": 1},
            "qualification_candidates": qualification_candidates,
        }
        if authority is not None and authority.opt_in is not None:
            # The one extra key, present only while the owner's opt-in is the authority. A Mac
            # with the setting off publishes exactly the row an unmodified Hub does.
            row["accepted_unqualified"] = dict(authority.opt_in)
        # **The one optional key this build adds, and only while the owner switched it on**
        # (owner, 2026-10-28). It follows the `accepted_unqualified` rule exactly: absent is the
        # old row, byte for byte, so a Mac that never set it publishes what it always did.
        #
        # Measured render speed is deliberately **not** here. The row's key set is the mixed-fleet
        # contract (`fleet_nodes._render_capability`), and an older controller drops a row that
        # carries a key it does not know — which for `accepted_unqualified` is the documented
        # trade for a Mac the owner opted in, but for a *measurement* would silently remove every
        # Mac that has ever rendered from an older controller's roster. The speed is published on
        # the read-only Render view instead (`render_history.read` `by_machine`), and the Gateway
        # orders by the timings in its own render journal.
        from . import registry
        if registry.render_while_paused(self.machine):
            row["render_while_paused"] = True
        grant = getattr(self._authority, "probed_grant", None)
        entries = (self._authority.executable_probe_entries()
                   if hasattr(self._authority, "executable_probe_entries") else [])
        if entries or (callable(grant) and grant() is not None):
            row["encode_support"] = {
                "format": render_encode.FORMAT,
                "entries": [entry["requested_encode"] for entry in entries],
            }
        return row

    def late_label_capability(self) -> tuple[dict[str, Any] | None, str | None]:
        """The closed v3 worker row for the late-label capabilities contract.

        Returns ``(row, None)`` or ``(None, reason)``. A scoped nonproduction row with zero
        capacity is publishable, but only from the genuinely installed v3 triple: the row
        carries the v3 pins, never the v1 opt-in evidence or v1 encode-support keys, and
        capacity opens only when this Mac's own granted v3 record exists.
        """
        loader = self._authority
        snapshot = getattr(loader, "late_label_snapshot", None)
        probe = getattr(loader, "probe_authority", None)
        if not (callable(snapshot) and callable(probe)):
            return None, "this Hub cannot read the late-label v3 render scope"
        try:
            installed = probe(late_labels=True)
        except TypeError:
            return None, "this Hub cannot read the late-label v3 render scope"
        if installed is None:
            return None, "the installed late-label v3 descriptor triple is missing or invalid"
        granted = snapshot()
        authority = granted if granted is not None else installed
        admission_allowed = self._admission_allowed()
        busy = self.machine in self.broker.busy_machines()
        ready = bool(
            granted is not None
            and self._has_memory(granted)
            and admission_allowed
            and not busy
        )
        row = {
            "operation": "video.final_render",
            "physical_machine_id": self.machine,
            "hardware_class": authority.qualification.hardware_class,
            "profile_digest": authority.qualification.profile_digest,
            "tools_digest": authority.qualification.tools_digest,
            "compiler_digest": authority.qualification.compiler_digest,
            "qualification_digest": authority.qualification.qualification_digest,
            "selected_tier": authority.selected_tier,
            "exact_encoder": authority.exact_encoder,
            "font_support": authority.font_support,
            "bounds": asdict(authority.bounds),
            "profile_execution_bounds": json.loads(
                _canonical(authority.profile_execution_bounds)),
            "production_eligible": bool(
                granted is not None and granted.qualification.production_eligible),
            "production_enabled": granted is not None,
            "available_capacity": {"slots": int(ready), "slots_total": 1},
            "qualification_candidates": [],
        }
        from . import registry
        if registry.render_while_paused(self.machine):
            row["render_while_paused"] = True
        return row, None

    def subtitle_capability(self) -> tuple[dict[str, Any] | None, str | None]:
        """The closed v4 worker row for the subtitle capabilities contract.

        Returns ``(row, None)`` or ``(None, reason)``. Only the genuinely installed v4
        triple can produce a row, so an absent packaging step is a measured negative: no
        v1/v3 filter, opt-in evidence, encode-support key or fabricated digest is ever
        reused. Capacity opens only from this Mac's own granted v4 record. The optional
        ``late_labels`` marker is published only from that same triple, which is what
        makes it valid for a combined packet rather than a fleet union.
        """
        loader = self._authority
        snapshot = getattr(loader, "subtitle_snapshot", None)
        probe = getattr(loader, "probe_authority", None)
        if not (callable(snapshot) and callable(probe)):
            return None, "this Hub cannot read the v4 subtitle render scope"
        try:
            installed = probe(subtitles=True)
        except TypeError:
            return None, "this Hub cannot read the v4 subtitle render scope"
        if installed is None:
            return None, "the installed v4 subtitle descriptor triple is missing or invalid"
        granted = snapshot()
        authority = granted if granted is not None else installed
        admission_allowed = self._admission_allowed()
        busy = self.machine in self.broker.busy_machines()
        ready = bool(
            granted is not None
            and self._has_memory(granted)
            and admission_allowed
            and not busy
        )
        row = {
            "operation": "video.final_render",
            "physical_machine_id": self.machine,
            "hardware_class": authority.qualification.hardware_class,
            "profile_digest": authority.qualification.profile_digest,
            "tools_digest": authority.qualification.tools_digest,
            "compiler_digest": authority.qualification.compiler_digest,
            "qualification_digest": authority.qualification.qualification_digest,
            "selected_tier": authority.selected_tier,
            "exact_encoder": authority.exact_encoder,
            "font_support": authority.font_support,
            "bounds": asdict(authority.bounds),
            "profile_execution_bounds": json.loads(
                _canonical(authority.profile_execution_bounds)),
            "production_eligible": bool(
                granted is not None and granted.qualification.production_eligible),
            "production_enabled": granted is not None,
            "available_capacity": {"slots": int(ready), "slots_total": 1},
            "qualification_candidates": [],
            "subtitles": {"format": SUBTITLE_CONTRACT},
            "late_labels": {
                "format": "studio.render.late-label.v3",
                "motions": list(LATE_LABEL_MOTIONS),
            },
        }
        from . import registry
        if registry.render_while_paused(self.machine):
            row["render_while_paused"] = True
        return row, None

    def cue_overlay_capability(self) -> tuple[dict[str, Any] | None, str | None]:
        """The closed v5 worker row for the cue overlay capabilities contract.

        Returns ``(row, None)`` or ``(None, reason)``. Only the genuinely installed v5
        triple can produce a row, so an absent packaging step is a measured negative: no
        v1/v3/v4 filter, opt-in evidence, encode-support key or fabricated digest is ever
        reused. Capacity opens only from this Mac's own granted v5 record.
        """
        loader = self._authority
        snapshot = getattr(loader, "cue_overlay_snapshot", None)
        probe = getattr(loader, "probe_authority", None)
        if not (callable(snapshot) and callable(probe)):
            return None, "this Hub cannot read the v5 cue overlay render scope"
        try:
            installed = probe(cue_overlays=True)
        except TypeError:
            return None, "this Hub cannot read the v5 cue overlay render scope"
        if installed is None:
            return None, "the installed v5 cue overlay descriptor triple is missing or invalid"
        granted = snapshot()
        authority = granted if granted is not None else installed
        admission_allowed = self._admission_allowed()
        busy = self.machine in self.broker.busy_machines()
        ready = bool(
            granted is not None
            and self._has_memory(granted)
            and admission_allowed
            and not busy
        )
        row = {
            "operation": "video.final_render",
            "physical_machine_id": self.machine,
            "hardware_class": authority.qualification.hardware_class,
            "profile_digest": authority.qualification.profile_digest,
            "tools_digest": authority.qualification.tools_digest,
            "compiler_digest": authority.qualification.compiler_digest,
            "qualification_digest": authority.qualification.qualification_digest,
            "selected_tier": authority.selected_tier,
            "exact_encoder": authority.exact_encoder,
            "font_support": authority.font_support,
            "bounds": asdict(authority.bounds),
            "profile_execution_bounds": json.loads(
                _canonical(authority.profile_execution_bounds)),
            "production_eligible": bool(
                granted is not None and granted.qualification.production_eligible),
            "production_enabled": granted is not None,
            "available_capacity": {"slots": int(ready), "slots_total": 1},
            "qualification_candidates": [],
            "cue_overlays": {"format": _CUE_OVERLAY_CONTRACT},
            "subtitles": {"format": SUBTITLE_CONTRACT},
            "late_labels": {
                "format": "studio.render.late-label.v3",
                "motions": list(LATE_LABEL_MOTIONS),
            },
        }
        from . import registry
        if registry.render_while_paused(self.machine):
            row["render_while_paused"] = True
        return row, None
