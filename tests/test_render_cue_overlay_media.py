#!/usr/bin/env python3
"""Focused tests for receiver-side VP9-alpha, CFR, and media integrity validator.

Authority: Story Studio KH studio.render.cue-overlay.v1 qualification contract (R4).
Tests exact contract fixtures, synthetic FFmpeg/ffprobe generation, false alpha,
VFR jitter, stream counts, canvas/fps mismatches, bounds, and security boundaries.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

# Trusted root bootstrap
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from components.audio_encode import find_tool
from components.hub.backend.render_cue_media import (
    CueOverlayMediaValidationError,
    REFUSAL_BOUNDS,
    REFUSAL_MEDIA_INTEGRITY,
    REFUSAL_MEDIA_UNSUPPORTED,
    check_cue_overlay_probe,
    probe_cue_overlay_media,
    validate_cue_overlay_media,
    validate_cue_overlay_probe,
)

FIXTURES_PATH = Path(
    "~/Developer/_orchestra/story-lead/fleet-batch-20261107/cue-overlay-review-r4-a441bfc7/fixtures.json"
).expanduser()

DEFAULT_BOUNDS = {
    "bounds_version": 2,
    "canvas_range": {"max_edge": 3840, "max_pixels": 8294400},
    "working_fps": [24, 25, 30, 60],
    "max_duration_sec": 10800,
    "max_operations": 32,
    "max_assets": 32,
    "max_asset_bytes": 8589934592,
    "max_total_input_bytes": 17179869184,
    "max_pixel_frames": 5374771200000,
    "scratch_reserve_bytes": 17179869184,
    "output_limit_bytes": 8589934592,
    "process_tree_rss_limit_bytes": 10737418240,
    "cumulative_wall_seconds": 14400,
    "max_subtitle_ass_bytes": 4194304,
    "max_subtitle_font_bytes": 33554432,
    "max_subtitle_fonts": 8,
}


class CueOverlayContractFixturesTests(unittest.TestCase):
    """Verify all 16 media probe fixtures from the authority contract pass with 100% parity."""

    def test_all_contract_media_probe_fixtures(self):
        if not FIXTURES_PATH.is_file():
            self.skipTest(f"Fixtures file not found at {FIXTURES_PATH}")

        with FIXTURES_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)

        profiles = data.get("profiles", {})
        fixtures = [fix for fix in data.get("fixtures", []) if fix.get("input", {}).get("kind") == "media_probe"]
        self.assertEqual(len(fixtures), 16, "Expected exactly 16 media_probe fixtures in contract")

        for fix in fixtures:
            fix_id = fix["id"]
            expected_outcome = fix["expect"]
            expected_reason = fix["reason"]
            inp = fix["input"]
            pref = fix.get("profile_ref", "default")
            bounds = profiles.get(pref, {}).get("execution_bounds", DEFAULT_BOUNDS)

            actual_reason = check_cue_overlay_probe(
                inp["probe"],
                recipe_canvas=inp["recipe_canvas"],
                time_range=inp["time_range"],
                manifest_size_bytes=inp["manifest_size_bytes"],
                execution_bounds=bounds,
            )

            if expected_outcome == "accept":
                self.assertIsNone(actual_reason, f"Fixture {fix_id} expected accept but got {actual_reason}")
                # Also verify validate_cue_overlay_probe does not raise
                try:
                    validate_cue_overlay_probe(
                        inp["probe"],
                        recipe_canvas=inp["recipe_canvas"],
                        time_range=inp["time_range"],
                        manifest_size_bytes=inp["manifest_size_bytes"],
                        execution_bounds=bounds,
                    )
                except CueOverlayMediaValidationError as exc:
                    self.fail(f"Fixture {fix_id} raised unexpectedly: {exc}")
            else:
                self.assertEqual(
                    actual_reason,
                    expected_reason,
                    f"Fixture {fix_id} reason mismatch: got {actual_reason}, expected {expected_reason}",
                )
                with self.assertRaises(CueOverlayMediaValidationError) as ctx:
                    validate_cue_overlay_probe(
                        inp["probe"],
                        recipe_canvas=inp["recipe_canvas"],
                        time_range=inp["time_range"],
                        manifest_size_bytes=inp["manifest_size_bytes"],
                        execution_bounds=bounds,
                    )
                self.assertEqual(ctx.exception.reason, expected_reason)


def _find_render_tool(name: str) -> Path | None:
    override = os.environ.get(f"STUDIOHUB_{name.upper()}", "").strip()
    if override and Path(override).is_file():
        return Path(override)
    import shutil
    candidates: list[str | None] = [
        shutil.which(name),
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            if name == "ffmpeg":
                res = subprocess.run([c, "-encoders"], capture_output=True, text=True)
                if "libvpx-vp9" in res.stdout:
                    return Path(c)
            elif name == "ffprobe":
                res = subprocess.run([c, "-decoders"], capture_output=True, text=True)
                if "libvpx-vp9" in res.stdout or "vp9" in res.stdout:
                    return Path(c)
    return find_tool(name)


class CueOverlaySyntheticMediaTests(unittest.TestCase):
    """Offline synthetic execution tests using real staged ffmpeg and ffprobe."""

    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = _find_render_tool("ffmpeg")
        cls.ffprobe = _find_render_tool("ffprobe")

    def setUp(self):
        if self.ffmpeg is None or self.ffprobe is None:
            self.skipTest("ffmpeg or ffprobe binary not available for synthetic tests")

    def _generate_webm(
        self,
        target_path: Path,
        *,
        canvas: tuple[int, int] = (128, 96),
        fps: int = 24,
        frames: int = 6,
        pix_fmt: str = "yuva420p",
        alpha_color: str = "black@0.5",
        extra_args: list[str] | None = None,
    ) -> None:
        """Create a tiny synthetic VP9 WebM fixture."""
        w, h = canvas
        cmd = [
            str(self.ffmpeg),
            "-f", "lavfi",
            "-i", f"color=c={alpha_color}:s={w}x{h}:r={fps}",
            "-frames:v", str(frames),
            "-c:v", "libvpx-vp9",
            "-pix_fmt", pix_fmt,
        ]
        if extra_args:
            cmd.extend(extra_args)
        cmd.extend([str(target_path), "-y"])

        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(res.returncode, 0, f"FFmpeg generation failed: {res.stderr}")

    def test_synthetic_valid_vp9_alpha_cfr_media(self):
        """Verify real synthetic VP9 WebM with alpha decodes and validates all 16 facts."""
        with tempfile.TemporaryDirectory() as td:
            tpath = Path(td)
            media_path = tpath / "valid_cue.webm"
            self._generate_webm(media_path, canvas=(128, 96), fps=24, frames=6)

            recipe_canvas = {"width": 128, "height": 96, "working_fps": 24}
            time_range = {"start_sec": 0.0, "end_sec": 0.25}

            # 1. Validate by Path
            probe = validate_cue_overlay_media(
                self.ffprobe,
                media_path,
                recipe_canvas=recipe_canvas,
                time_range=time_range,
                execution_bounds=DEFAULT_BOUNDS,
            )

            self.assertEqual(probe["container"], "webm")
            self.assertEqual(probe["video_streams"], 1)
            self.assertEqual(probe["codec"], "vp9")
            self.assertEqual(probe["pix_fmt"], "yuva420p")
            self.assertEqual(probe["width"], 128)
            self.assertEqual(probe["height"], 96)
            self.assertEqual(probe["fps"], 24)
            self.assertEqual(probe["frame_count"], 6)
            self.assertEqual(probe["audio_streams"], 0)
            self.assertEqual(probe["subtitle_streams"], 0)
            self.assertEqual(probe["data_streams"], 0)
            self.assertEqual(probe["attachment_streams"], 0)
            self.assertTrue(probe["alpha_mode"])
            self.assertTrue(probe["decoded_alpha"])
            self.assertTrue(probe["cfr"])
            self.assertTrue(probe["pts_monotonic"])

            # 2. Validate by file descriptor (simulating RenderAssetStore reader stream)
            with media_path.open("rb") as f:
                fd_probe = validate_cue_overlay_media(
                    self.ffprobe,
                    f,
                    recipe_canvas=recipe_canvas,
                    time_range=time_range,
                    execution_bounds=DEFAULT_BOUNDS,
                )
                self.assertEqual(fd_probe, probe)

    def test_synthetic_false_alpha_refuses(self):
        """Verify WebM encoded as yuv420p with an alpha_mode=1 metadata tag fails decoded_alpha."""
        with tempfile.TemporaryDirectory() as td:
            tpath = Path(td)
            media_path = tpath / "fake_alpha.webm"
            self._generate_webm(
                media_path,
                canvas=(128, 96),
                fps=24,
                frames=6,
                pix_fmt="yuv420p",
                alpha_color="black",
                extra_args=["-metadata:s:v:0", "alpha_mode=1"],
            )

            recipe_canvas = {"width": 128, "height": 96, "working_fps": 24}
            time_range = {"start_sec": 0.0, "end_sec": 0.25}

            raw_probe = probe_cue_overlay_media(self.ffprobe, media_path)
            self.assertFalse(raw_probe["decoded_alpha"], "False alpha must not report decoded_alpha=True")

            with self.assertRaises(CueOverlayMediaValidationError) as ctx:
                validate_cue_overlay_media(
                    self.ffprobe,
                    media_path,
                    recipe_canvas=recipe_canvas,
                    time_range=time_range,
                    execution_bounds=DEFAULT_BOUNDS,
                )
            self.assertEqual(ctx.exception.reason, REFUSAL_MEDIA_UNSUPPORTED)

    def test_synthetic_extra_stream_refuses(self):
        """Verify a WebM with an audio stream is refused."""
        with tempfile.TemporaryDirectory() as td:
            tpath = Path(td)
            media_path = tpath / "with_audio.webm"
            # Add an audio sine stream
            cmd = [
                str(self.ffmpeg),
                "-f", "lavfi", "-i", "color=c=black@0.5:s=128x96:r=24",
                "-f", "lavfi", "-i", "sine=f=440:r=44100",
                "-frames:v", "6",
                "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
                "-c:a", "libopus",
                "-shortest",
                str(media_path), "-y",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"FFmpeg failed: {res.stderr}")

            recipe_canvas = {"width": 128, "height": 96, "working_fps": 24}
            time_range = {"start_sec": 0.0, "end_sec": 0.25}

            with self.assertRaises(CueOverlayMediaValidationError) as ctx:
                validate_cue_overlay_media(
                    self.ffprobe,
                    media_path,
                    recipe_canvas=recipe_canvas,
                    time_range=time_range,
                    execution_bounds=DEFAULT_BOUNDS,
                )
            self.assertEqual(ctx.exception.reason, REFUSAL_MEDIA_UNSUPPORTED)

    def test_synthetic_wrong_container_refuses(self):
        """Verify non-WebM containers (e.g. mp4) are refused."""
        with tempfile.TemporaryDirectory() as td:
            tpath = Path(td)
            media_path = tpath / "video.mp4"
            cmd = [
                str(self.ffmpeg),
                "-f", "lavfi", "-i", "color=c=black:s=128x96:r=24",
                "-frames:v", "6",
                "-c:v", "libvpx-vp9",
                str(media_path), "-y",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"FFmpeg failed: {res.stderr}")

            recipe_canvas = {"width": 128, "height": 96, "working_fps": 24}
            time_range = {"start_sec": 0.0, "end_sec": 0.25}

            with self.assertRaises(CueOverlayMediaValidationError) as ctx:
                validate_cue_overlay_media(
                    self.ffprobe,
                    media_path,
                    recipe_canvas=recipe_canvas,
                    time_range=time_range,
                    execution_bounds=DEFAULT_BOUNDS,
                )
            self.assertEqual(ctx.exception.reason, REFUSAL_MEDIA_UNSUPPORTED)

    def test_synthetic_canvas_mismatch_refuses(self):
        """Verify dimensions that do not match the recipe canvas are refused."""
        with tempfile.TemporaryDirectory() as td:
            tpath = Path(td)
            media_path = tpath / "wrong_dim.webm"
            self._generate_webm(media_path, canvas=(160, 120), fps=24, frames=6)

            recipe_canvas = {"width": 128, "height": 96, "working_fps": 24}
            time_range = {"start_sec": 0.0, "end_sec": 0.25}

            with self.assertRaises(CueOverlayMediaValidationError) as ctx:
                validate_cue_overlay_media(
                    self.ffprobe,
                    media_path,
                    recipe_canvas=recipe_canvas,
                    time_range=time_range,
                    execution_bounds=DEFAULT_BOUNDS,
                )
            self.assertEqual(ctx.exception.reason, REFUSAL_MEDIA_UNSUPPORTED)

    def test_synthetic_fps_mismatch_refuses(self):
        """Verify video encoded at 30 fps when recipe requires 24 fps is refused."""
        with tempfile.TemporaryDirectory() as td:
            tpath = Path(td)
            media_path = tpath / "wrong_fps.webm"
            self._generate_webm(media_path, canvas=(128, 96), fps=30, frames=10)

            recipe_canvas = {"width": 128, "height": 96, "working_fps": 24}
            time_range = {"start_sec": 0.0, "end_sec": 0.25}

            with self.assertRaises(CueOverlayMediaValidationError) as ctx:
                validate_cue_overlay_media(
                    self.ffprobe,
                    media_path,
                    recipe_canvas=recipe_canvas,
                    time_range=time_range,
                    execution_bounds=DEFAULT_BOUNDS,
                )
            self.assertEqual(ctx.exception.reason, REFUSAL_MEDIA_UNSUPPORTED)

    def test_synthetic_short_duration_refuses(self):
        """Verify asset with fewer frames than required duration window is refused."""
        with tempfile.TemporaryDirectory() as td:
            tpath = Path(td)
            media_path = tpath / "short.webm"
            self._generate_webm(media_path, canvas=(128, 96), fps=24, frames=4)

            # Window of 1.0 second at 24 fps requires 24 frames
            recipe_canvas = {"width": 128, "height": 96, "working_fps": 24}
            time_range = {"start_sec": 0.0, "end_sec": 1.0}

            with self.assertRaises(CueOverlayMediaValidationError) as ctx:
                validate_cue_overlay_media(
                    self.ffprobe,
                    media_path,
                    recipe_canvas=recipe_canvas,
                    time_range=time_range,
                    execution_bounds=DEFAULT_BOUNDS,
                )
            self.assertEqual(ctx.exception.reason, REFUSAL_MEDIA_UNSUPPORTED)

    def test_synthetic_vfr_stream_refuses(self):
        """Verify variable frame rate timing (non-CFR) is refused even with matching nominal fps."""
        with tempfile.TemporaryDirectory() as td:
            tpath = Path(td)
            media_path = tpath / "vfr.webm"
            # Jitter frame 2 PTS by > 2ms using setpts
            extra = ["-vf", r"setpts=if(eq(N\,2)\,0.2\,N*0.0416667)/TB"]
            self._generate_webm(media_path, canvas=(128, 96), fps=24, frames=6, extra_args=extra)

            recipe_canvas = {"width": 128, "height": 96, "working_fps": 24}
            time_range = {"start_sec": 0.0, "end_sec": 0.2}

            raw_probe = probe_cue_overlay_media(self.ffprobe, media_path)
            self.assertFalse(raw_probe["cfr"], "VFR stream must report cfr=False")

            with self.assertRaises(CueOverlayMediaValidationError) as ctx:
                validate_cue_overlay_media(
                    self.ffprobe,
                    media_path,
                    recipe_canvas=recipe_canvas,
                    time_range=time_range,
                    execution_bounds=DEFAULT_BOUNDS,
                )
            self.assertEqual(ctx.exception.reason, REFUSAL_MEDIA_UNSUPPORTED)

    def test_synthetic_max_pixel_frames_bounds_refuses(self):
        """Verify exceeding max_pixel_frames bound refuses with cue_overlay_bounds."""
        with tempfile.TemporaryDirectory() as td:
            tpath = Path(td)
            media_path = tpath / "valid.webm"
            self._generate_webm(media_path, canvas=(128, 96), fps=24, frames=6)

            recipe_canvas = {"width": 128, "height": 96, "working_fps": 24}
            time_range = {"start_sec": 0.0, "end_sec": 0.25}
            # 6 frames * 128 * 96 = 73728 pixels. Set bound lower.
            tight_bounds = dict(DEFAULT_BOUNDS)
            tight_bounds["max_pixel_frames"] = 50000

            with self.assertRaises(CueOverlayMediaValidationError) as ctx:
                validate_cue_overlay_media(
                    self.ffprobe,
                    media_path,
                    recipe_canvas=recipe_canvas,
                    time_range=time_range,
                    execution_bounds=tight_bounds,
                )
            self.assertEqual(ctx.exception.reason, REFUSAL_BOUNDS)

    def test_manifest_media_integrity_validation(self):
        """Verify staged byte count and sha256 are verified against manifest entry."""
        with tempfile.TemporaryDirectory() as td:
            tpath = Path(td)
            media_path = tpath / "valid.webm"
            self._generate_webm(media_path, canvas=(128, 96), fps=24, frames=6)

            bytes_data = media_path.read_bytes()
            import hashlib
            correct_sha = hashlib.sha256(bytes_data).hexdigest()
            correct_size = len(bytes_data)

            recipe_canvas = {"width": 128, "height": 96, "working_fps": 24}
            time_range = {"start_sec": 0.0, "end_sec": 0.25}

            # 1. Matching manifest passes
            manifest_ok = {
                "logical_id": "cue_overlay_0000",
                "safe_name": "cue_overlay_0000.webm",
                "role": "cue_overlay",
                "sha256": correct_sha,
                "size_bytes": correct_size,
            }
            probe = validate_cue_overlay_media(
                self.ffprobe,
                media_path,
                recipe_canvas=recipe_canvas,
                time_range=time_range,
                execution_bounds=DEFAULT_BOUNDS,
                manifest_entry=manifest_ok,
            )
            self.assertIsNotNone(probe)

            # 2. Size mismatch refuses with cue_overlay_media_integrity
            manifest_bad_size = dict(manifest_ok, size_bytes=correct_size + 10)
            with self.assertRaises(CueOverlayMediaValidationError) as ctx:
                validate_cue_overlay_media(
                    self.ffprobe,
                    media_path,
                    recipe_canvas=recipe_canvas,
                    time_range=time_range,
                    execution_bounds=DEFAULT_BOUNDS,
                    manifest_entry=manifest_bad_size,
                )
            self.assertEqual(ctx.exception.reason, REFUSAL_MEDIA_INTEGRITY)

            # 3. Hash mismatch refuses with cue_overlay_media_integrity
            manifest_bad_sha = dict(manifest_ok, sha256="0" * 64)
            with self.assertRaises(CueOverlayMediaValidationError) as ctx:
                validate_cue_overlay_media(
                    self.ffprobe,
                    media_path,
                    recipe_canvas=recipe_canvas,
                    time_range=time_range,
                    execution_bounds=DEFAULT_BOUNDS,
                    manifest_entry=manifest_bad_sha,
                )
            self.assertEqual(ctx.exception.reason, REFUSAL_MEDIA_INTEGRITY)

    def test_security_input_vetting(self):
        """Verify forbidden shapes, URLs, shell characters, and invalid descriptors are rejected."""
        recipe_canvas = {"width": 128, "height": 96, "working_fps": 24}
        time_range = {"start_sec": 0.0, "end_sec": 0.25}

        # URL input
        with self.assertRaises(CueOverlayMediaValidationError) as ctx:
            validate_cue_overlay_media(
                self.ffprobe,
                "http://example.com/asset.webm",
                recipe_canvas=recipe_canvas,
                time_range=time_range,
                execution_bounds=DEFAULT_BOUNDS,
            )
        self.assertEqual(ctx.exception.reason, REFUSAL_MEDIA_UNSUPPORTED)

        # Shell command substitution input
        with self.assertRaises(CueOverlayMediaValidationError) as ctx:
            validate_cue_overlay_media(
                self.ffprobe,
                "/tmp/asset; rm -rf /",
                recipe_canvas=recipe_canvas,
                time_range=time_range,
                execution_bounds=DEFAULT_BOUNDS,
            )
        self.assertEqual(ctx.exception.reason, REFUSAL_MEDIA_UNSUPPORTED)

        # Negative file descriptor
        with self.assertRaises(CueOverlayMediaValidationError) as ctx:
            validate_cue_overlay_media(
                self.ffprobe,
                -1,
                recipe_canvas=recipe_canvas,
                time_range=time_range,
                execution_bounds=DEFAULT_BOUNDS,
            )
        self.assertEqual(ctx.exception.reason, REFUSAL_MEDIA_UNSUPPORTED)


if __name__ == "__main__":
    unittest.main()
