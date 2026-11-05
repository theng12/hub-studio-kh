"""Compiler-derived encode authority for the qualified render lane.

The executor runs exactly one tier: the one named by the machine's installed authority
record. A packet's encode settings (container, codec, effort, quality pair, bitrate cap)
decide which tier the compiler builds, so a packet can be syntactically valid and still
name an encode this Mac will never execute. The answers below come from the pinned
compiler itself - never from parsing tier labels or copying the encoder table - so the
advertisement cannot drift from the code that runs the job.

This is read-only truth. Admission and the executor remain the authority.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from .final_render_compiler import (
    EncodeTier,
    UnsupportedFinalRenderCombination,
    compile_final_render_packet,
)
from .render_hash_v1 import render_hash

FORMAT = "studio.render.encode-authority.v1"

# The closed encode space the packet contract accepts. `bitrate_kbps` is always null:
# a capped label is operator-specific, so a capped authority advertises nothing and the
# Hub admission check stays the only authority for it.
_CONTAINERS = ("mp4", "mov", "webm")
_CODECS = ("h264", "h265")
_EFFORTS = ("ultrafast", "medium", "slow", "hardware")
_QUALITY_PAIRS = ((23, "medium"), (18, "slow"), (23, "ultrafast"),
                  (28, "ultrafast"), (32, "ultrafast"))
_ENTRY_KEYS = ("container", "requested_codec", "requested_effort", "crf",
               "quality_cpu_preset", "bitrate_kbps")

# selected_tier/exact_encoder change only when the operator record changes, so one entry
# per pair is enough; a process restart is what picks up a new compiler build.
_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


_PLACEHOLDER_ASSETS = {"audio": "/dev/fd/0", "base": "/dev/fd/1"}


def _synthetic_packet(encode: Mapping[str, Any]) -> bytes:
    """One minimal, contract-valid packet whose only variable is `encode`.

    The compiler's tier labels and argv depend on the recipe's encode block only, so a
    minimal recipe answers the question for every real recipe with the same values.
    """
    recipe = {
        "namespace": "studio.render.final.v1",
        "profile_digest": "0" * 64,
        "base_video": {"type": "asset_ref", "logical_id": "base"},
        "premixed_audio": {"type": "asset_ref", "logical_id": "audio"},
        "canvas": {"width": 1920, "height": 1080, "working_fps": 24},
        "target_duration_sec": 1,
        "effects_gate": {"start_sec": None, "end_sec": None},
        "operations": [],
        "encode": dict(encode),
    }
    manifest = [
        {"logical_id": "audio", "safe_name": "audio.wav", "role": "premixed_audio",
         "sha256": "0" * 64, "size_bytes": 1},
        {"logical_id": "base", "safe_name": "base.mp4", "role": "base_video",
         "sha256": "0" * 64, "size_bytes": 1},
    ]
    recipe_digest = render_hash("recipe", recipe)
    manifest_digest = render_hash("manifest", manifest)
    packet = {
        "recipe": recipe,
        "manifest": manifest,
        "recipe_digest": recipe_digest,
        "manifest_digest": manifest_digest,
        "packet_digest": render_hash("packet", {
            "recipe_digest": recipe_digest,
            "manifest_digest": manifest_digest,
        }),
    }
    return json.dumps(packet, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _placeholder_assets(packet: Mapping[str, Any]) -> dict[str, str]:
    """Compiler input bindings only; compilation never opens the paths."""
    return {
        str(entry["logical_id"]): f"/dev/fd/{index}"
        for index, entry in enumerate(packet["manifest"])
    }


def plan_for(encode: Mapping[str, Any]):
    """Compile one encode combination with the pinned compiler."""
    return compile_final_render_packet(
        _synthetic_packet(encode), _PLACEHOLDER_ASSETS, "/dev/fd/9")


def tier_supports(selected_tier: str | None, exact_encoder: str | None,
                  tiers: Iterable[EncodeTier]) -> bool:
    """The executor's exact tier rule: same label, exact encoder, no dropped features."""
    if not selected_tier or not exact_encoder:
        return False
    tier = next((item for item in tiers if item.label == selected_tier), None)
    return (tier is not None
            and exact_encoder in tier.argv
            and not tier.drops_burned_in_features)


def supported_encode(selected_tier: str | None,
                     exact_encoder: str | None) -> dict[str, Any] | None:
    """What this authority can execute, or None when there is no authority to describe.

    A present authority that matches no uncapped combination returns an empty entry list,
    which callers must read as "nothing advertised" (local fallback), not as "unknown".
    """
    if not selected_tier or not exact_encoder:
        return None
    key = (selected_tier, exact_encoder)
    cached = _CACHE.get(key)
    if cached is not None:
        return {"format": cached["format"], "entries": [dict(item) for item in cached["entries"]]}

    entries: list[dict[str, Any]] = []
    for container in _CONTAINERS:
        for codec in _CODECS:
            for effort in _EFFORTS:
                for crf, preset in _QUALITY_PAIRS:
                    encode = {
                        "container": container,
                        "requested_codec": codec,
                        "requested_effort": effort,
                        "crf": crf,
                        "quality_cpu_preset": preset,
                        "bitrate_kbps": None,
                    }
                    try:
                        plan = plan_for(encode)
                    except (UnsupportedFinalRenderCombination, TypeError, ValueError):
                        continue
                    if tier_supports(selected_tier, exact_encoder, plan.tiers):
                        entries.append(encode)
    # One entry per distinct combination; the grid can repeat a tier through webm.
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for entry in entries:
        identity = tuple(entry[name] for name in _ENTRY_KEYS)
        if identity not in seen:
            seen.add(identity)
            deduped.append(entry)
    document = {"format": FORMAT, "entries": deduped}
    _CACHE[key] = document
    return {"format": FORMAT, "entries": [dict(item) for item in deduped]}


def packet_is_supported(packet: Mapping[str, Any], raw_packet: bytes,
                        selected_tier: str | None,
                        exact_encoder: str | None, *,
                        late_labels_allowed: bool = False) -> bool:
    """Compile this exact packet and apply the executor's tier rule to it."""
    if not selected_tier or not exact_encoder:
        return False
    try:
        plan = compile_final_render_packet(
            raw_packet, _placeholder_assets(packet), "/dev/fd/9",
            late_labels_allowed=late_labels_allowed)
    except (UnsupportedFinalRenderCombination, TypeError, ValueError):
        return False
    return tier_supports(selected_tier, exact_encoder, plan.tiers)


# The encode an owner-opt-in leaf is pinned to when the setting names none. An authority is one
# (tier, encoder) pair, exactly as an `enabled/` record is, so the opt-in names the encode and the
# pinned compiler says which pair it builds; the label is never typed or stored by anyone.
DEFAULT_OPT_IN_ENCODE: dict[str, Any] = {
    "container": "mp4", "requested_codec": "h264", "requested_effort": "medium",
    "crf": 23, "quality_cpu_preset": "medium", "bitrate_kbps": None,
}


def normalize_encode(value: object) -> dict[str, Any] | None:
    """The closed encode this module advertises, or None for anything else."""
    if type(value) is not dict or set(value) != set(_ENTRY_KEYS):
        return None
    if (value["container"] not in _CONTAINERS or value["requested_codec"] not in _CODECS
            or value["requested_effort"] not in _EFFORTS
            or (value["bitrate_kbps"] is not None and (
                type(value["bitrate_kbps"]) is not int
                or not 500 <= value["bitrate_kbps"] <= 100000))
            or (value["crf"], value["quality_cpu_preset"]) not in _QUALITY_PAIRS
            or type(value["crf"]) is not int):
        return None
    return {name: value[name] for name in _ENTRY_KEYS}


def primary_pair(encode: Mapping[str, Any]) -> tuple[str, str] | None:
    """The (tier label, exact encoder) the pinned compiler runs first for this encode.

    None when the compiler refuses the combination, the first tier would drop requested
    features, or its argv names no output encoder.
    """
    try:
        plan = plan_for(encode)
    except (UnsupportedFinalRenderCombination, TypeError, ValueError, KeyError):
        return None
    tier = plan.tiers[0]
    if tier.condition != "always" or tier.drops_burned_in_features or "-c:v" not in tier.argv:
        return None
    # An input decoder option can also be spelled -c:v; the output encoder is the last one.
    index = len(tier.argv) - 1 - tier.argv[::-1].index("-c:v")
    return (tier.label, tier.argv[index + 1]) if index + 1 < len(tier.argv) else None
