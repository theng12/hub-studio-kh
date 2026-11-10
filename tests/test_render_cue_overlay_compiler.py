#!/usr/bin/env python3
"""Focused offline tests for Hub cue overlay compiler and syntax validation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tests"))

from components.audio_encode import find_tool
from components.hub.backend.render_executor import InstalledRenderAuthorityLoader
from components.hub.backend.render_subtitle_v4.final_render_compiler import (
    compile_final_render_packet as v4_compile,
)
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
        self.assertIn("x='if(lt(t,1.000),-200,if(lt(t,2.000),-200*(1-(t-1.000)/1.000),0))'", graph)

    def test_descriptor_validation_passes(self):
        compiler_raw = (REPO_ROOT / "packaging/render-authority-cue-overlay/COMPILER.json").read_bytes()
        desc = validate_render_compiler_descriptor(compiler_raw)
        self.assertEqual(desc.digest, hashlib.sha256(compiler_raw).hexdigest())

        # The tracked profile pins the tracked compiler bytes; nothing is rewritten to match.
        profile_raw = (REPO_ROOT / "packaging/render-authority-cue-overlay/PROFILE.json").read_bytes()
        pdesc = validate_render_profile_descriptor(profile_raw)
        self.assertEqual(pdesc.format, "studio.render.profile.v5")
        self.assertEqual(pdesc.implementation, "story-classic-cue-overlay-v1")
        self.assertEqual(pdesc.compiler_artifact_sha256, desc.digest)

    def test_frozen_v4_sources_match_subtitle_compiler_pins(self):
        root = Path(__file__).resolve().parents[1]
        descriptor = json.loads(
            (root / "packaging/render-authority-subtitle/COMPILER.json").read_bytes())
        for key, name in (
            ("final_render_compiler_sha256", "final_render_compiler.py"),
            ("render_contract_sha256", "render_contract.py"),
            ("render_hash_sha256", "render_hash_v1.py"),
        ):
            frozen = root / "components/hub/backend/render_subtitle_v4" / name
            self.assertEqual(descriptor[key], hashlib.sha256(frozen.read_bytes()).hexdigest())

    def test_frozen_v4_loader_and_compiler_dispatch(self):
        from tests.test_render_subtitle_scoped import ScopedCase
        from tests.test_render_subtitle_executor import make_png, FONT, ASS
        from tests.test_render_subtitle_v1 import build_packet, raw as sub_raw, LIMITS as SUB_LIMITS
        case = ScopedCase()
        case.setUp()
        try:
            case.write_enabled()
            authority = case.loader.probe_authority(subtitles=True)
            self.assertIsNotNone(authority)

            compiler = case.loader.compiler_for(authority)
            self.assertIs(compiler, v4_compile)

            packet = build_packet()
            packet["recipe"]["profile_digest"] = authority.qualification.profile_digest
            packet["recipe_digest"] = render_hash("recipe", packet["recipe"])
            packet["packet_digest"] = render_hash("packet", {
                "recipe_digest": packet["recipe_digest"],
                "manifest_digest": packet["manifest_digest"],
            })
            self.assertTrue(case.loader.subtitles_allowed(authority, packet))
            assets = {
                row["logical_id"]: str(case.data_root / row["safe_name"])
                for row in packet["manifest"]
            }
            for row in packet["manifest"]:
                Path(assets[row["logical_id"]]).write_bytes(
                    ASS if "ass" in row["safe_name"] else (
                        FONT if "font" in row["safe_name"] else make_png(128, 128)
                    )
                )
            plan = compiler(
                sub_raw(packet), assets, str(case.data_root / "out.mp4"),
                subtitles_allowed=True, subtitle_limits=SUB_LIMITS,
            )
            self.assertIsNotNone(plan)
            graph = plan.tiers[0].argv[plan.tiers[0].argv.index("-filter_complex") + 1]
            self.assertIn("ass=filename=", graph)
        finally:
            case.tearDown()

    def test_ffmpeg_synthetic_slide_execution(self):
        ffmpeg_bin = find_tool("ffmpeg")
        if ffmpeg_bin is None:
            self.skipTest("ffmpeg binary not available")

        # Verify unquoted slide filter fails with exit code 234
        unquoted_slide = (
            "[0:v][1:v]overlay=x=if(lt(t,0.1),-10,if(lt(t,0.5),-10*(1-(t-0.1)/0.4),0)):y=0:eof_action=repeat:eval=frame"
        )
        res_fail = subprocess.run(
            [str(ffmpeg_bin), "-f", "lavfi", "-i", "color=c=black:s=160x120:d=0.5",
             "-f", "lavfi", "-i", "color=c=white:s=40x40:d=0.5",
             "-filter_complex", unquoted_slide, "-f", "null", "-"],
            capture_output=True, text=True,
        )
        self.assertEqual(res_fail.returncode, 234)

        # Verify quoted slide filter succeeds on x-axis
        quoted_x_slide = (
            "[0:v][1:v]overlay=x='if(lt(t,0.1),-10,if(lt(t,0.5),-10*(1-(t-0.1)/0.4),0))':y=0:eof_action=repeat:eval=frame"
        )
        res_pass_x = subprocess.run(
            [str(ffmpeg_bin), "-f", "lavfi", "-i", "color=c=black:s=160x120:d=0.5",
             "-f", "lavfi", "-i", "color=c=white:s=40x40:d=0.5",
             "-filter_complex", quoted_x_slide, "-f", "null", "-"],
            capture_output=True, text=True,
        )
        self.assertEqual(res_pass_x.returncode, 0)

        # Verify quoted slide filter succeeds on y-axis
        quoted_y_slide = (
            "[0:v][1:v]overlay=x=0:y='if(lt(t,0.1),-10,if(lt(t,0.5),-10*(1-(t-0.1)/0.4),0))':eof_action=repeat:eval=frame"
        )
        res_pass_y = subprocess.run(
            [str(ffmpeg_bin), "-f", "lavfi", "-i", "color=c=black:s=160x120:d=0.5",
             "-f", "lavfi", "-i", "color=c=white:s=40x40:d=0.5",
             "-filter_complex", quoted_y_slide, "-f", "null", "-"],
            capture_output=True, text=True,
        )
        self.assertEqual(res_pass_y.returncode, 0)

        # End-to-end synthetic compiler plan execution with slide and fade
        tiny_webm_b64 = (
            b"GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQJChYECGFOAZwEAAAAAAASREU2bdLpNu4tTq4QVSalmU6yBoU27"
            b"i1OrhBZUrmtTrIHWTbuMU6uEElTDZ1OsggE2TbuMU6uEHFO7a1OsggR77AEAAAAAAABZAAAAAAAAAAAAAAAAAAAAAAAA"
            b"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            b"AAAVSalmsCrXsYMPQkBNgIxMYXZmNjMuMS4xMDJXQYxMYXZmNjMuMS4xMDJEiYhAgEAAAAAAABZUrmvbrgEAAAAAAABS"
            b"14EBc8WIt3aLDjNppEqcgQAitZyDdW5kiIEAhoVWX1ZQOYOBASPjg4QCYloA4JSwgSi6gSiagQJTwIEBVbCEVbmBAVXug"
            b"QHsAQAAAAAAAAIAABJUw2f+c3OfY8CAZ8iZRaOHRU5DT0RFUkSHjExhdmY2My4xLjEwMnNz2WPAi2PFiLd2iw4zaaRKZ8"
            b"ikRaOHRU5DT0RFUkSHl0xhdmM2My4xLjEwMiBsaWJ2cHgtdnA5Z8ihRaOIRFVSQVRJT05Eh5MwMDowMDowMC41MjAwMDAw"
            b"MDAAH0O2dUK854EAoNOhr4EAAACCSYNCAAJwAnYAOCQcGEoAADBgAAATv//4cxAACP////cU2/////6sdYAAdaGfpp3ug"
            b"QGlmIJJg0IAAnACdgA4JBwYSgAAMGAAAE4AAKCxoZOBACgAhgBAkpwAUAAAAyAAAFRwdaGWppTugQGlj4YAQJKcAFAAAAM"
            b"gAABUcPuB2KCxoZOBAFAAhgBAkpwATuAAAyAAAFRwdaGWppTugQGlj4YAQJKcAE7gAAMgAABUcPuB2KCxoZOBAHgAhgBA"
            b"kpwAUAAAAyAAAFRwdaGWppTugQGlj4YAQJKcAFAAAAMgAABUcPuB2KCxoZOBAKAAhgBAkpwATUAAAyAAAFRwdaGWppTug"
            b"QGlj4YAQJKcAE1AAAMgAABUcPuB2KCxoZOBAMgAhgBAkpwAUAAAAyAAAFRwdaGWppTugQGlj4YAQJKcAFAAAAMgAABUcPu"
            b"B2KCxoZOBAPAAhgBAkpwATuAAAyAAAFRwdaGWppTugQGlj4YAQJKcAE7gAAMgAABUcPuB2KCxoZOBARgAhgBAkpwAUAAAA"
            b"yAAAFRwdaGWppTugQGlj4YAQJKcAFAAAAMgAABUcPuB2KCxoZOBAUAAhgBAkpwASiAAAyAAAFRwdaGWppTugQGlj4YAQJK"
            b"cAEogAAMgAABUcPuB2KCxoZOBAWgAhgBAkpwAUAAAAyAAAFRwdaGWppTugQGlj4YAQJKcAFAAAAMgAABUcPuB2KCxoZOBA"
            b"ZAAhgDAkpwASiAAAyAAAFRwdaGWppTugQGlj4YAwJKcAEogAAMgAABUcPuB2KCxoZOBAbgAhgBAkpwAUAAAAyAAAFRwdaG"
            b"WppTugQGlj4YAQJKcAFAAAAMgAABUcPuB2KCxoZOBAeAAhgBAkpwATUAAAyAAAFRwdaGWppTugQGlj4YAQJKcAE1AAAMgA"
            b"ABUcPuB2BxTu2uRu4+zgQC3iveBAfGCAbnwgQM="
        )
        import base64
        op = _cue_op(0, "test-cue", 0.1, 0.4, fade={"in_sec": 0.1, "out_sec": 0.1},
                     slide={"axis": "x", "offset_px": -30, "start_sec": 0.1, "duration_sec": 0.2})
        extra = [{"logical_id": "cue_overlay_0000", "safe_name": "cue_overlay_0000.webm",
                  "role": "cue_overlay", "sha256": "3" * 64, "size_bytes": 100}]
        raw = _canonical_packet(_base_recipe([op]), _base_manifest(extra))
        with tempfile.TemporaryDirectory() as td:
            tpath = Path(td)
            base_mp4 = tpath / "base.mp4"
            audio_wav = tpath / "audio.wav"
            cue_webm = tpath / "cue_overlay_0000.webm"
            out_mp4 = tpath / "out.mp4"

            subprocess.run(
                [str(ffmpeg_bin), "-f", "lavfi", "-i", "color=c=black:s=160x120:d=0.5:r=25",
                 "-c:v", "mpeg4", str(base_mp4), "-y"],
                capture_output=True, check=True,
            )
            cue_webm.write_bytes(base64.b64decode(tiny_webm_b64))
            import wave
            with wave.open(str(audio_wav), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(44100)
                w.writeframes(b"\x00" * (44100 * 2))

            assets = {
                "base_video": str(base_mp4),
                "premixed_audio": str(audio_wav),
                "cue_overlay_0000": str(cue_webm),
            }
            plan = compile_final_render_packet(raw, assets, str(out_mp4), cue_overlays_allowed=True)
            filter_complex = plan.tiers[0].argv[plan.tiers[0].argv.index("-filter_complex") + 1]

            res_plan = subprocess.run(
                [str(ffmpeg_bin), "-i", str(base_mp4), "-i", str(audio_wav), "-i", str(cue_webm),
                 "-filter_complex", filter_complex, "-map", "[vout]", "-f", "null", "-"],
                capture_output=True, text=True,
            )
            self.assertEqual(res_plan.returncode, 0)


if __name__ == "__main__":
    unittest.main()
