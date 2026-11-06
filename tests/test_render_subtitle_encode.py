#!/usr/bin/env python3
"""Subtitle preflight forwarding in render_encode.packet_is_supported.

The v4 executor passes subtitles_allowed and the profile's execution bounds; the
predicate must compile the real packet with them, refuse missing/wider bounds, and
still behave exactly as before for legacy callers.
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from components.hub.backend import render_encode  # noqa: E402
from components.hub.backend.final_render_compiler import (  # noqa: E402
    compile_final_render_packet,
)
from test_render_subtitle_v1 import LIMITS, build_packet, raw, seal  # noqa: E402

BOUNDS = copy.deepcopy(LIMITS)


def authority(packet):
    """The (tier label, output encoder) the real compiler builds for this packet."""
    plan = compile_final_render_packet(
        raw(packet), render_encode._placeholder_assets(packet), "/dev/fd/9",
        subtitles_allowed=True, subtitle_limits=BOUNDS)
    tier = plan.tiers[0]
    index = len(tier.argv) - 1 - tier.argv[::-1].index("-c:v")
    return tier.label, tier.argv[index + 1]


def with_declared_size(packet, logical_id, size_bytes):
    packet = copy.deepcopy(packet)
    entry = next(item for item in packet["manifest"] if item["logical_id"] == logical_id)
    entry["size_bytes"] = size_bytes
    return seal(packet)


class SubtitleEncodePreflightTests(unittest.TestCase):
    def setUp(self):
        self.packet = build_packet()
        self.tier, self.encoder = authority(self.packet)

    def supported(self, packet=None, **options):
        packet = self.packet if packet is None else packet
        return render_encode.packet_is_supported(
            packet, raw(packet), self.tier, self.encoder, **options)

    def test_v4_caller_preflight_accepts_the_real_packet_without_opening_assets(self):
        self.assertTrue(self.supported(
            subtitles_allowed=True, subtitle_limits=BOUNDS))
        # Legacy callers keep the old behaviour: a subtitle packet is refused.
        self.assertFalse(self.supported())

    def test_missing_or_malformed_bounds_fail_closed(self):
        self.assertFalse(self.supported(subtitles_allowed=True))
        self.assertFalse(self.supported(subtitles_allowed=True, subtitle_limits=None))
        for key in ("max_subtitle_fonts", "max_subtitle_font_bytes",
                    "max_subtitle_ass_bytes", "max_asset_bytes",
                    "max_total_input_bytes"):
            for value in (None, 0, -1, True, "8"):
                limits = dict(BOUNDS, **{key: value})
                with self.subTest(key=key, value=value):
                    self.assertFalse(self.supported(
                        subtitles_allowed=True, subtitle_limits=limits))

    def test_a_wider_profile_cannot_widen_the_frozen_subtitle_caps(self):
        packet = with_declared_size(self.packet, "subtitle_font_0000", 40_000_000)
        self.assertFalse(self.supported(
            packet, subtitles_allowed=True,
            subtitle_limits=dict(BOUNDS, max_subtitle_font_bytes=64 * 1024 * 1024)))
        packet = with_declared_size(self.packet, "subtitle_ass", 5_000_000)
        self.assertFalse(self.supported(
            packet, subtitles_allowed=True,
            subtitle_limits=dict(BOUNDS, max_subtitle_ass_bytes=8 * 1024 * 1024)))

    def test_overall_asset_bound_is_a_min_cap(self):
        packet = with_declared_size(self.packet, "subtitle_font_0000", 2_000_000)
        self.assertFalse(self.supported(
            packet, subtitles_allowed=True,
            subtitle_limits=dict(BOUNDS, max_asset_bytes=1_000_000)))
        self.assertTrue(self.supported(
            packet, subtitles_allowed=True,
            subtitle_limits=dict(BOUNDS, max_asset_bytes=2_000_000)))

    def test_bounds_at_the_frozen_caps_are_accepted(self):
        packet = with_declared_size(self.packet, "subtitle_font_0000",
                                    BOUNDS["max_subtitle_font_bytes"])
        packet = with_declared_size(packet, "subtitle_ass",
                                    BOUNDS["max_subtitle_ass_bytes"])
        self.assertTrue(self.supported(
            packet, subtitles_allowed=True, subtitle_limits=BOUNDS))

    def test_an_unsupported_tier_still_refuses(self):
        self.assertFalse(render_encode.packet_is_supported(
            self.packet, raw(self.packet), self.tier, "libx265",
            subtitles_allowed=True, subtitle_limits=BOUNDS))


if __name__ == "__main__":
    unittest.main()
