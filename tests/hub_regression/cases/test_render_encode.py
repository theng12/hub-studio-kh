"""Compiler-derived encode authority for the qualified render lane.

The executor runs one tier, chosen by the installed authority record. These cases pin
that the advertised support is decided by the pinned compiler itself and that the
predicate is the executor's own (label, exact encoder, no dropped features).
"""
from __future__ import annotations

import json

from components.hub.backend.final_render_compiler import compile_final_render_packet
from components.hub.backend import render_encode

BINDINGS = {"audio": "/dev/fd/0", "base": "/dev/fd/1"}


def _packet(encode: dict) -> tuple[dict, bytes]:
    raw = render_encode._synthetic_packet(encode)
    return json.loads(raw), raw


def _tier(encode: dict):
    _, raw = _packet(encode)
    plan = compile_final_render_packet(raw, BINDINGS, "/dev/fd/9")
    return plan.tiers[0]


def _encoder(tier) -> str:
    for candidate in ("libx264", "libx265", "h264_videotoolbox", "libvpx-vp9"):
        if candidate in tier.argv:
            return candidate
    raise AssertionError(f"no known encoder in {tier.argv!r}")


MEDIUM = {
    "container": "mp4", "requested_codec": "h264", "requested_effort": "medium",
    "crf": 23, "quality_cpu_preset": "medium", "bitrate_kbps": None,
}
HARDWARE = {
    "container": "mp4", "requested_codec": "h264", "requested_effort": "hardware",
    "crf": 23, "quality_cpu_preset": "medium", "bitrate_kbps": None,
}
H265 = {
    "container": "mp4", "requested_codec": "h265", "requested_effort": "medium",
    "crf": 23, "quality_cpu_preset": "medium", "bitrate_kbps": None,
}


def test_predicate_is_the_executor_tier_rule():
    medium = _tier(MEDIUM)
    assert render_encode.tier_supports(medium.label, "libx264", [medium])
    assert not render_encode.tier_supports(medium.label, "libx265", [medium])
    assert not render_encode.tier_supports("some other tier", "libx264", [medium])
    recovery = next(tier for tier in compile_final_render_packet(
        render_encode._synthetic_packet(MEDIUM), BINDINGS, "/dev/fd/9").tiers
        if tier.drops_burned_in_features)
    assert not render_encode.tier_supports(
        recovery.label, _encoder(recovery), [recovery])


def test_advertised_encode_is_exactly_what_compiles_to_the_authority_tier():
    medium = _tier(MEDIUM)
    document = render_encode.supported_encode(medium.label, "libx264")
    assert document is not None
    assert document["format"] == render_encode.FORMAT
    assert MEDIUM in document["entries"]
    assert HARDWARE not in document["entries"]
    assert H265 not in document["entries"]


def test_a_different_tier_advertises_a_different_encode():
    hardware = _tier(HARDWARE)
    document = render_encode.supported_encode(hardware.label, _encoder(hardware))
    assert document is not None
    assert HARDWARE in document["entries"]
    assert MEDIUM not in document["entries"]


def test_no_authority_is_unknown_and_a_matching_authority_with_no_grid_entry_is_empty():
    assert render_encode.supported_encode(None, None) is None
    assert render_encode.supported_encode("not a compiled tier", "libx264") == {
        "format": render_encode.FORMAT, "entries": [],
    }


def test_packet_check_compiles_the_real_packet():
    packet, raw = _packet(MEDIUM)
    medium = _tier(MEDIUM)
    assert render_encode.packet_is_supported(packet, raw, medium.label, "libx264")
    assert not render_encode.packet_is_supported(packet, raw, medium.label, "libx265")
    _, h265_raw = _packet(H265)
    h265_packet = json.loads(h265_raw)
    assert not render_encode.packet_is_supported(
        h265_packet, h265_raw, medium.label, "libx264")
