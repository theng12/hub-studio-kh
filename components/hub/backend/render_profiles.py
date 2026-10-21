"""Strict render dependency descriptors; never compatibility or execution authority."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Optional

from .render_contract import validate_render_packet_syntax
from .render_hash_v1 import decode_render_json

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_SAFE = 9007199254740991

_TOOL_KEYS = (
    "format",
    "platform",
    "ffmpeg_sha256",
    "ffmpeg_size_bytes",
    "ffprobe_sha256",
    "ffprobe_size_bytes",
    "architecture_evidence_sha256",
    "minimum_os_evidence_sha256",
    "linked_libraries_evidence_sha256",
    "build_configuration_evidence_sha256",
    "capability_inventory_evidence_sha256",
)
_COMPILER_KEYS = (
    "format",
    "implementation",
    "python_runtime_sha256",
    "render_hash_sha256",
    "render_contract_sha256",
    "final_render_compiler_sha256",
)
_PROFILE_KEYS = (
    "format",
    "implementation",
    "recipe_namespace",
    "compiler_artifact_sha256",
    "tools_artifact_sha256",
    "combination_set",
    "tier_policy",
    "output_policy",
    "execution_bounds",
    "hardware_qualification_record_sha256",
    "fidelity_record_sha256",
    "production_eligible",
)
_BOUND_KEYS = (
    "canvas_pairs",
    "working_fps",
    "max_duration_sec",
    "max_operations",
    "max_assets",
    "max_asset_bytes",
    "max_total_input_bytes",
    "max_pixel_frames",
    "scratch_reserve_bytes",
    "output_limit_bytes",
    "process_tree_rss_limit_bytes",
    "cumulative_wall_seconds",
)
_BOUND_INTEGER_KEYS = _BOUND_KEYS[3:]


@dataclass(frozen=True)
class ToolDescriptor:
    digest: str


@dataclass(frozen=True)
class CompilerDescriptor:
    digest: str


@dataclass(frozen=True)
class ProfileDescriptor:
    digest: str
    compiler_artifact_sha256: Optional[str]
    tools_artifact_sha256: Optional[str]


@dataclass(frozen=True)
class DescriptorMatch:
    descriptor_match: bool
    reason: str
    profile_digest: str


def _invalid(kind, reason):
    raise ValueError("Invalid render " + kind + " descriptor: " + reason)


def _decode(raw, kind):
    try:
        value = decode_render_json(raw)
    except ValueError as exc:
        _invalid(kind, str(exc))
    if type(value) is not dict:
        _invalid(kind, "root must be an object")
    return value


def _exact(value, keys, kind, path):
    if type(value) is not dict or set(value) != set(keys) or len(value) != len(keys):
        _invalid(kind, path + " has missing or unknown fields")
    return value


def _literal(value, expected, kind, path):
    if value != expected or type(value) is not str:
        _invalid(kind, path + " is unsupported")


def _sha256(value, kind, path):
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _invalid(kind, path + " is not lowercase SHA-256")
    return value


def _optional_sha256(value, kind, path):
    if value is None:
        return None
    return _sha256(value, kind, path)


def _positive_int(value, kind, path):
    if type(value) is not int or value <= 0 or value > _MAX_SAFE:
        _invalid(kind, path + " must be a positive safe integer")
    return value


def _positive_number(value, kind, path):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        _invalid(kind, path + " must be a positive finite number")
    if type(value) is int and value > _MAX_SAFE:
        _invalid(kind, path + " must have safe integral magnitude")
    if type(value) is float and value.is_integer() and value > _MAX_SAFE:
        _invalid(kind, path + " must have safe integral magnitude")
    return value


def _bounds(value):
    if value is None:
        return
    bounds = _exact(value, _BOUND_KEYS, "profile", "execution_bounds")
    pairs = bounds["canvas_pairs"]
    if type(pairs) is not list:
        _invalid("profile", "execution_bounds.canvas_pairs must be an array")
    for index, value in enumerate(pairs):
        pair = _exact(value, ("width", "height"), "profile",
                      "execution_bounds.canvas_pairs[" + str(index) + "]")
        _positive_int(pair["width"], "profile",
                      "execution_bounds.canvas_pairs[" + str(index) + "].width")
        _positive_int(pair["height"], "profile",
                      "execution_bounds.canvas_pairs[" + str(index) + "].height")
    rates = bounds["working_fps"]
    if type(rates) is not list:
        _invalid("profile", "execution_bounds.working_fps must be an array")
    for index, value in enumerate(rates):
        _positive_int(value, "profile", "execution_bounds.working_fps[" + str(index) + "]")
    _positive_number(bounds["max_duration_sec"], "profile",
                     "execution_bounds.max_duration_sec")
    for key in _BOUND_INTEGER_KEYS:
        _positive_int(bounds[key], "profile", "execution_bounds." + key)


def validate_render_tool_descriptor(raw):
    """Validate exact receiver-owned tool descriptor bytes and return their identity."""
    value = _exact(_decode(raw, "tool"), _TOOL_KEYS, "tool", "tool")
    _literal(value["format"], "studio.render.tools.v1", "tool", "format")
    _literal(value["platform"], "macos-arm64", "tool", "platform")
    for key in (
        "ffmpeg_sha256",
        "ffprobe_sha256",
        "architecture_evidence_sha256",
        "minimum_os_evidence_sha256",
        "linked_libraries_evidence_sha256",
        "build_configuration_evidence_sha256",
        "capability_inventory_evidence_sha256",
    ):
        _sha256(value[key], "tool", key)
    _positive_int(value["ffmpeg_size_bytes"], "tool", "ffmpeg_size_bytes")
    _positive_int(value["ffprobe_size_bytes"], "tool", "ffprobe_size_bytes")
    return ToolDescriptor(hashlib.sha256(raw).hexdigest())


def validate_render_compiler_descriptor(raw):
    """Validate exact receiver-owned compiler descriptor bytes and return their identity."""
    value = _exact(_decode(raw, "compiler"), _COMPILER_KEYS, "compiler", "compiler")
    _literal(value["format"], "studio.render.compiler.v1", "compiler", "format")
    _literal(value["implementation"], "hub-final-render-v1", "compiler", "implementation")
    for key in (
        "python_runtime_sha256",
        "render_hash_sha256",
        "render_contract_sha256",
        "final_render_compiler_sha256",
    ):
        _sha256(value[key], "compiler", key)
    return CompilerDescriptor(hashlib.sha256(raw).hexdigest())


def validate_render_profile_descriptor(raw):
    """Validate the frozen non-production candidate profile shape and byte identity."""
    value = _exact(_decode(raw, "profile"), _PROFILE_KEYS, "profile", "profile")
    for key, expected in (
        ("format", "studio.render.profile.v1"),
        ("implementation", "story-classic-no-font-v1"),
        ("recipe_namespace", "studio.render.final.v1"),
        ("combination_set", "retained-classic-no-font-candidate-v1"),
        ("tier_policy", "story-final-chain-v1"),
        ("output_policy", "story-final-output-v1"),
    ):
        _literal(value[key], expected, "profile", key)
    compiler_digest = _optional_sha256(
        value["compiler_artifact_sha256"], "profile", "compiler_artifact_sha256")
    tools_digest = _optional_sha256(
        value["tools_artifact_sha256"], "profile", "tools_artifact_sha256")
    _bounds(value["execution_bounds"])
    _optional_sha256(value["hardware_qualification_record_sha256"], "profile",
                     "hardware_qualification_record_sha256")
    _optional_sha256(value["fidelity_record_sha256"], "profile",
                     "fidelity_record_sha256")
    if value["production_eligible"] is not False:
        _invalid("profile", "production_eligible must be false")
    return ProfileDescriptor(
        hashlib.sha256(raw).hexdigest(),
        compiler_digest,
        tools_digest,
    )


def match_render_profile_dependencies(raw_packet, profile, tools, compiler):
    """Compare only claimed profile and dependency byte identities."""
    packet = validate_render_packet_syntax(raw_packet)
    if packet["recipe"]["profile_digest"] != profile.digest:
        return DescriptorMatch(False, "profile_digest", profile.digest)
    if profile.tools_artifact_sha256 != tools.digest:
        return DescriptorMatch(False, "tool_artifact", profile.digest)
    if profile.compiler_artifact_sha256 != compiler.digest:
        return DescriptorMatch(False, "compiler_artifact", profile.digest)
    return DescriptorMatch(True, "matched", profile.digest)
