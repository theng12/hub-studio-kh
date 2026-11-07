#!/usr/bin/env python3
"""Focused offline tests for Hub cue overlay compiler and syntax validation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from components.hub.backend.render_contract import (
    validate_render_packet_syntax,
)
from components.hub.backend.final_render_compiler import (
    compile_final_render_packet,
    UnsupportedFinalRenderCombination,
)
from components.hub.backend.render_profiles import (
    validate_render_compiler_descriptor,
    validate_render_profile_descriptor,
)
from components.hub.backend.render_hash_v1 import render_hash


def _canonical_packet(recipe: dict, manifest: list) -> bytes:
    recipe_digest = render_hash("recipe", recipe)
    manifest_digest = render_hash("manifest", manifest)
    packet_digest = render_hash("packet", {
        "recipe_digest": recipe_digest,
        "manifest_digest": manifest_digest,
    })
    packet = {
        "recipe": recipe,
        "manifest": manifest,
        "recipe_digest": recipe_digest,
        "manifest_digest": manifest_digest,
        "packet_digest": packet_digest,
    }
    return json.dumps(packet, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _base_recipe(operations: list) -> dict:
    return {
        "namespace": "studio.render.final.v1",
        "profile_digest": "0" * 64,
        "base_video": {"type": "asset_ref", "logical_id": "base_video"},
        "premixed_audio": {"type": "asset_ref", "logical_id": "premixed_audio"},
        "canvas": {"width": 1920, "height": 1080, "working_fps": 30},
        "target_duration_sec": 10.0,
        "effects_gate": {"start_sec": None, "end_sec": None},
        "operations": operations,
        "encode": {
            "container": "mp4",
            "requested_codec": "h264",
            "requested_effort": "medium",
            "crf": 23,
            "quality_cpu_preset": "medium",
            "bitrate_kbps": 8000,
        },
    }


def _base_manifest(extra_assets: list | None = None) -> list:
    items = [
        {"logical_id": "base_video", "safe_name": "base_video.mp4", "role": "base_video", "sha256": "1" * 64, "size_bytes": 1000},
        {"logical_id": "premixed_audio", "safe_name": "premixed_audio.wav", "role": "premixed_audio", "sha256": "2" * 64, "size_bytes": 1000},
    ]
    if extra_assets:
        items.extend(extra_assets)
    return sorted(items, key=lambda x: x["logical_id"])


def _cue_op(index: int = 0, cue_id: str = "cue-1", start: float = 1.0, end: float = 4.0,
            fade: dict | None = None, slide: dict | None = None) -> dict:
    return {
        "kind": "cue_overlay_stage",
        "contract": "studio.render.cue-overlay.v1",
        "cue_id": cue_id,
        "time_range": {"start_sec": start, "end_sec": end},
        "asset": {"type": "asset_ref", "logical_id": f"cue_overlay_{index:04d}"},
        "fade": fade,
        "slide": slide,
    }


class CueOverlayCompilerTests(unittest.TestCase):
    def test_accepts_valid_cue_overlay_packet(self):
        op = _cue_op(0, "intro-cue", 1.0, 5.0, fade={"in_sec": 0.5, "out_sec": 0.5},
                     slide={"axis": "x", "offset_px": -100, "start_sec": 1.0, "duration_sec": 0.5})
        extra = [{"logical_id": "cue_overlay_0000", "safe_name": "cue_overlay_0000.webm",
                  "role": "cue_overlay", "sha256": "3" * 64, "size_bytes": 5000}]
        raw = _canonical_packet(_base_recipe([op]), _base_manifest(extra))
        validated = validate_render_packet_syntax(raw)
        self.assertEqual(validated["recipe"]["operations"][0]["kind"], "cue_overlay_stage")

    def test_refuses_invalid_cue_contract(self):
        op = _cue_op(0)
        op["contract"] = "studio.render.cue-overlay.v999"
        extra = [{"logical_id": "cue_overlay_0000", "safe_name": "cue_overlay_0000.webm",
                  "role": "cue_overlay", "sha256": "3" * 64, "size_bytes": 5000}]
        raw = _canonical_packet(_base_recipe([op]), _base_manifest(extra))
        with self.assertRaises(ValueError) as ctx:
            validate_render_packet_syntax(raw)
        self.assertIn("contract is unsupported", str(ctx.exception))

    def test_refuses_non_ordinal_asset_name(self):
        op = _cue_op(0)
        op["asset"]["logical_id"] = "cue_overlay_0001"
        extra = [{"logical_id": "cue_overlay_0001", "safe_name": "cue_overlay_0001.webm",
                  "role": "cue_overlay", "sha256": "3" * 64, "size_bytes": 5000}]
        raw = _canonical_packet(_base_recipe([op]), _base_manifest(extra))
        with self.assertRaises(ValueError) as ctx:
            validate_render_packet_syntax(raw)
        self.assertIn("does not use its ordinal asset name", str(ctx.exception))

    def test_shared_band_allows_interleaved_cue_and_late_label_in_array_order(self):
        op0 = _cue_op(0, "cue-a", 1.0, 3.0)
        op1 = {
            "kind": "late_label_stage",
            "contract": "studio.render.late-label.v3",
            "cue_id": "label-b",
            "enable": {"start_sec": 2.0, "end_sec": 4.0},
            "placement": {"x_px": 100, "y_px": 200},
            "motion": {"kind": "none", "entrance_sec": 0.3},
            "label": {"asset": {"type": "asset_ref", "logical_id": "cue_0001_label"}},
            "reveal": None,
            "line": None,
            "line_reveal": None,
        }
        op2 = _cue_op(2, "cue-c", 3.0, 5.0)
        extra = [
            {"logical_id": "cue_overlay_0000", "safe_name": "cue_overlay_0000.webm", "role": "cue_overlay", "sha256": "3" * 64, "size_bytes": 100},
            {"logical_id": "cue_0001_label", "safe_name": "cue_0001_label.png", "role": "cue_label", "sha256": "4" * 64, "size_bytes": 100},
            {"logical_id": "cue_overlay_0002", "safe_name": "cue_overlay_0002.webm", "role": "cue_overlay", "sha256": "5" * 64, "size_bytes": 100},
        ]
        raw = _canonical_packet(_base_recipe([op0, op1, op2]), _base_manifest(extra))
        validated = validate_render_packet_syntax(raw)
        self.assertEqual(len(validated["recipe"]["operations"]), 3)

    def test_cross_kind_cue_id_collision_refuses(self):
        op0 = _cue_op(0, "shared-id", 1.0, 3.0)
        op1 = {
            "kind": "late_label_stage",
            "contract": "studio.render.late-label.v3",
            "cue_id": "shared-id",
            "enable": {"start_sec": 2.0, "end_sec": 4.0},
            "placement": {"x_px": 100, "y_px": 200},
            "motion": {"kind": "none", "entrance_sec": 0.3},
            "label": {"asset": {"type": "asset_ref", "logical_id": "cue_0001_label"}},
            "reveal": None,
            "line": None,
            "line_reveal": None,
        }
        extra = [
            {"logical_id": "cue_overlay_0000", "safe_name": "cue_overlay_0000.webm", "role": "cue_overlay", "sha256": "3" * 64, "size_bytes": 100},
            {"logical_id": "cue_0001_label", "safe_name": "cue_0001_label.png", "role": "cue_label", "sha256": "4" * 64, "size_bytes": 100},
        ]
        raw = _canonical_packet(_base_recipe([op0, op1]), _base_manifest(extra))
        with self.assertRaises(ValueError) as ctx:
            validate_render_packet_syntax(raw)
        self.assertIn("repeats cue_id shared-id", str(ctx.exception))

    def test_compiler_requires_cue_overlays_allowed(self):
        op = _cue_op(0, "cue-1", 1.0, 4.0)
        extra = [{"logical_id": "cue_overlay_0000", "safe_name": "cue_overlay_0000.webm",
                  "role": "cue_overlay", "sha256": "3" * 64, "size_bytes": 100}]
        raw = _canonical_packet(_base_recipe([op]), _base_manifest(extra))
        assets = {
            "base_video": "/tmp/base.mp4",
            "premixed_audio": "/tmp/audio.wav",
            "cue_overlay_0000": "/tmp/cue.webm",
        }
        with self.assertRaises(UnsupportedFinalRenderCombination):
            compile_final_render_packet(raw, assets, "/tmp/out.mp4", cue_overlays_allowed=False)

        plan = compile_final_render_packet(raw, assets, "/tmp/out.mp4", cue_overlays_allowed=True)
        self.assertIsNotNone(plan)
        graph = plan.tiers[0].argv[plan.tiers[0].argv.index("-filter_complex") + 1]
        self.assertIn("overlay=x=0:y=0:eof_action=repeat:enable='between(t,1.000,4.000)'", graph)
        self.assertIn("setpts=PTS-STARTPTS+1.000/TB", graph)

    def test_compiler_fade_and_slide_generation(self):
        op = _cue_op(0, "cue-1", 1.0, 5.0, fade={"in_sec": 0.5, "out_sec": 0.5},
                     slide={"axis": "x", "offset_px": -200, "start_sec": 1.0, "duration_sec": 1.0})
        extra = [{"logical_id": "cue_overlay_0000", "safe_name": "cue_overlay_0000.webm",
                  "role": "cue_overlay", "sha256": "3" * 64, "size_bytes": 100}]
        raw = _canonical_packet(_base_recipe([op]), _base_manifest(extra))
        assets = {
            "base_video": "/tmp/base.mp4",
            "premixed_audio": "/tmp/audio.wav",
            "cue_overlay_0000": "/tmp/cue.webm",
        }
        plan = compile_final_render_packet(raw, assets, "/tmp/out.mp4", cue_overlays_allowed=True)
        graph = plan.tiers[0].argv[plan.tiers[0].argv.index("-filter_complex") + 1]
        self.assertIn("fade=t=in:st=1.000:d=0.500:alpha=1", graph)
        self.assertIn("fade=t=out:st=4.500:d=0.500:alpha=1", graph)
        self.assertIn("eval=frame", graph)
        self.assertIn("if(lt(t,1.000),-200,if(lt(t,2.000),-200*(1-(t-1.000)/1.000),0))", graph)

    def test_descriptor_validation_passes(self):
        compiler_path = Path("/build/Developer/_worktrees/studio-hub-kh/fleet-batch-20261107/packaging/render-authority-cue-overlay/COMPILER.json")
        compiler_raw = compiler_path.read_bytes()
        desc = validate_render_compiler_descriptor(compiler_raw)
        self.assertEqual(desc.digest, hashlib.sha256(compiler_raw).hexdigest())

        candidate_path = Path("/build/Developer/_orchestra/gateway-lead/fleet-batch-20261107/cue-overlay-v5-profile/PROFILE.candidate.json")
        profile_doc = json.loads(candidate_path.read_text())
        profile_doc["compiler_artifact_sha256"] = desc.digest
        profile_raw = json.dumps(profile_doc, sort_keys=True, separators=(",", ":")).encode("utf-8")
        pdesc = validate_render_profile_descriptor(profile_raw)
        self.assertEqual(pdesc.format, "studio.render.profile.v5")
        self.assertEqual(pdesc.implementation, "story-classic-cue-overlay-v1")


if __name__ == "__main__":
    unittest.main()
