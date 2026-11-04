from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from components.hub.backend.final_render_compiler import _encoder_args
from components.hub.backend.render_encode import primary_pair
from components.hub.backend.render_encode_probe import (
    EncodeProbeError,
    ProbeRun,
    UnsupportedEncodeProbe,
    plan_encode_probe,
    probe_encode_support,
)

ENCODE = {
    "container": "mp4",
    "requested_codec": "h264",
    "requested_effort": "medium",
    "crf": 23,
    "quality_cpu_preset": "medium",
    "bitrate_kbps": None,
}

TOOL_IDENTITY = {"ffmpeg_sha256": "a" * 64, "ffprobe_sha256": "b" * 64}
HARDWARE_IDENTITY = {"profile": "studio-hub-m4"}


def report(*, width=1920, height=1080, fps="24/1", duration="2.000000",
           pix_fmt="yuv420p", video_codec="h264", audio_codec="aac",
           format_name="mov,mp4,m4a,3gp,3g2,mj2"):
    return {
        "streams": [
            {"codec_type": "video", "codec_name": video_codec, "pix_fmt": pix_fmt,
             "width": width, "height": height, "avg_frame_rate": fps,
             "duration": duration},
            {"codec_type": "audio", "codec_name": audio_codec, "duration": duration},
        ],
        "format": {"format_name": format_name, "duration": duration, "bit_rate": "1500000"},
    }


class FakeRunner:
    """Stands in for the process owner: writes the encoded file, answers the probe."""

    def __init__(self, *, probe_report=None, encoder_code=0, probe_code=0,
                 encoder_stderr=b"", encoder_wall=0.5, output=b"probe-bytes"):
        self.probe_report = probe_report if probe_report is not None else report()
        self.encoder_code = encoder_code
        self.probe_code = probe_code
        self.encoder_stderr = encoder_stderr
        self.encoder_wall = encoder_wall
        self.output = output
        self.calls: list[tuple[list[str], float]] = []

    def __call__(self, argv, timeout_seconds):
        argv = list(argv)
        self.calls.append((argv, timeout_seconds))
        if len(self.calls) == 1:
            Path(argv[-1]).write_bytes(self.output)
            return ProbeRun(self.encoder_code, b"", self.encoder_stderr,
                            self.encoder_wall)
        payload = self.probe_report
        if not isinstance(payload, (bytes, bytearray)):
            payload = json.dumps(payload).encode("utf-8")
        return ProbeRun(self.probe_code, payload, b"", 0.2)


class ProbePlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.scratch = Path(temporary.name)

    def plan(self, encode=None, **kwargs):
        return plan_encode_probe(encode or ENCODE, ffmpeg=Path("/tools/ffmpeg"),
                                 ffprobe=Path("/tools/ffprobe"),
                                 scratch_dir=self.scratch, **kwargs)

    def test_plan_uses_the_pinned_compiler_encoder_arguments(self):
        plan = self.plan()
        self.assertEqual(plan.encoder_args,
                         _encoder_args("h264", "mp4", 23, "medium", None))
        for token in plan.encoder_args:
            self.assertIn(token, plan.ffmpeg_argv)

    def test_plan_names_the_compiler_primary_pair(self):
        plan = self.plan()
        self.assertEqual((plan.tier, plan.exact_encoder), primary_pair(ENCODE))

    def test_plan_refuses_containers_outside_the_ceiling(self):
        for encode in (
            dict(ENCODE, container="webm"),
            dict(ENCODE, container="mov"),
        ):
            with self.assertRaises(UnsupportedEncodeProbe):
                self.plan(encode)

    def test_plan_refuses_a_tuple_the_compiler_refuses(self):
        with self.assertRaises(UnsupportedEncodeProbe):
            self.plan(dict(ENCODE, crf=99, quality_cpu_preset="medium"))

    def test_plan_refuses_a_tuple_that_is_not_the_closed_six_field_form(self):
        with self.assertRaises(UnsupportedEncodeProbe):
            self.plan(dict(ENCODE, extra=1))
        with self.assertRaises(UnsupportedEncodeProbe):
            self.plan(dict(ENCODE, crf="23"))

    def test_plan_tests_the_requested_cap_value_itself(self):
        plan = self.plan(dict(ENCODE, bitrate_kbps=4000))
        self.assertEqual(plan.encoder_args,
                         _encoder_args("h264", "mp4", 23, "medium", 4000))
        self.assertIn("-maxrate", plan.ffmpeg_argv)
        self.assertIn("4000k", plan.ffmpeg_argv)
        self.assertIn("8000k", plan.ffmpeg_argv)

    def test_hardware_and_exact_cap_use_the_compiler_primary_tier(self):
        for cap in (None, 8000):
            with self.subTest(cap=cap):
                encode = dict(ENCODE, requested_effort="hardware", bitrate_kbps=cap)
                plan = self.plan(encode)
                self.assertEqual(plan.exact_encoder, "h264_videotoolbox")
                self.assertEqual((plan.tier, plan.exact_encoder), primary_pair(encode))
                self.assertEqual(plan.encode, encode)
                self.assertEqual(plan.encoder_args,
                                 _encoder_args("h264", "mp4", 23, "hardware", cap))
                self.assertNotIn("libx264", plan.ffmpeg_argv)
                if cap is not None:
                    self.assertIn("8000k", plan.ffmpeg_argv)
                else:
                    self.assertNotIn("-maxrate", plan.ffmpeg_argv)

    def test_plan_argv_is_plain_strings_and_stays_in_scratch(self):
        plan = self.plan()
        self.assertTrue(all(type(item) is str for item in plan.ffmpeg_argv))
        self.assertEqual(plan.ffmpeg_argv[0], "/tools/ffmpeg")
        self.assertEqual(plan.ffprobe_argv[0], "/tools/ffprobe")
        self.assertTrue(str(plan.output_path).startswith(
            str(Path(self.scratch).resolve())))
        self.assertTrue(str(plan.output_path).endswith(".mp4"))
        self.assertNotIn("&&", plan.ffmpeg_argv)


class ProbeEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.scratch = Path(temporary.name)

    def probe(self, runner, encode=None, **kwargs):
        options = {
            "ffmpeg": Path("/tools/ffmpeg"),
            "ffprobe": Path("/tools/ffprobe"),
            "scratch_dir": self.scratch,
            "runner": runner,
            "tool_identity": TOOL_IDENTITY,
            "hardware_identity": HARDWARE_IDENTITY,
        }
        options.update(kwargs)
        return probe_encode_support(encode or ENCODE, **options)

    def test_probe_returns_evidence_after_a_real_successful_encode(self):
        runner = FakeRunner()
        evidence = self.probe(runner)
        self.assertEqual(evidence["format"], "studio.render.encode-probe.v1")
        self.assertEqual(evidence["kind"], "encoder_probe")
        self.assertEqual(evidence["encode"], ENCODE)
        self.assertEqual(evidence["tool_identity"], TOOL_IDENTITY)
        self.assertEqual(evidence["hardware_identity"], HARDWARE_IDENTITY)
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(runner.calls[0][0][0], "/tools/ffmpeg")
        self.assertEqual(runner.calls[1][0][0], "/tools/ffprobe")
        measured = evidence["measured"]
        self.assertEqual(measured["video_codec"], "h264")
        self.assertEqual(measured["audio_codec"], "aac")
        self.assertEqual(measured["pix_fmt"], "yuv420p")
        self.assertEqual((measured["width"], measured["height"]), (1920, 1080))
        self.assertEqual(measured["avg_frame_rate"], "24/1")
        self.assertEqual(measured["output_size_bytes"], len(b"probe-bytes"))
        self.assertEqual(len(measured["output_sha256"]), 64)
        self.assertEqual(len(measured["ffprobe_sha256"]), 64)

    def test_probe_honours_the_requested_canvas_and_frame_rate(self):
        runner = FakeRunner(probe_report=report(width=1280, height=720, fps="30/1"))
        with self.assertRaises(EncodeProbeError):
            self.probe(runner)

    def test_probe_refuses_a_non_zero_encoder_exit(self):
        with self.assertRaises(EncodeProbeError) as caught:
            self.probe(FakeRunner(encoder_code=1, encoder_stderr=b"Unknown encoder"))
        self.assertIn("non-zero", caught.exception.reason)

    def test_probe_refuses_an_empty_output(self):
        with self.assertRaises(EncodeProbeError):
            self.probe(FakeRunner(output=b""))

    def test_probe_refuses_a_wrong_pixel_format(self):
        with self.assertRaises(EncodeProbeError) as caught:
            self.probe(FakeRunner(probe_report=report(pix_fmt="yuv444p")))
        self.assertIn("pixel format", caught.exception.reason)

    def test_probe_refuses_a_wrong_audio_codec(self):
        with self.assertRaises(EncodeProbeError) as caught:
            self.probe(FakeRunner(probe_report=report(audio_codec="mp3")))
        self.assertIn("audio", caught.exception.reason)

    def test_probe_refuses_a_wrong_container(self):
        with self.assertRaises(EncodeProbeError) as caught:
            self.probe(FakeRunner(probe_report=report(format_name="matroska,webm")))
        self.assertIn("container", caught.exception.reason)

    def test_probe_refuses_a_wrong_frame_rate(self):
        with self.assertRaises(EncodeProbeError) as caught:
            self.probe(FakeRunner(probe_report=report(fps="30/1")))
        self.assertIn("frame rate", caught.exception.reason)

    def test_probe_refuses_a_wrong_duration(self):
        with self.assertRaises(EncodeProbeError) as caught:
            self.probe(FakeRunner(probe_report=report(duration="9.000000")))
        self.assertIn("duration", caught.exception.reason)

    def test_probe_refuses_a_failed_or_unreadable_probe(self):
        with self.assertRaises(EncodeProbeError):
            self.probe(FakeRunner(probe_code=1))
        with self.assertRaises(EncodeProbeError):
            self.probe(FakeRunner(probe_report=b"not json"),
                       scratch_dir=self.scratch / "unreadable")

    def test_probe_refuses_an_encoder_that_ran_past_its_wall_bound(self):
        with self.assertRaises(EncodeProbeError) as caught:
            self.probe(FakeRunner(encoder_wall=999.0), timeout_seconds=10.0)
        self.assertIn("wall bound", caught.exception.reason)

    def test_probe_bounds_the_diagnostics_it_keeps(self):
        with self.assertRaises(EncodeProbeError) as caught:
            self.probe(FakeRunner(encoder_code=1, encoder_stderr=b"x" * 100000))
        self.assertLessEqual(len(caught.exception.detail), 4096)

    def test_probe_refuses_a_scratch_inside_a_model_library(self):
        library = self.scratch / "library"
        (library / ".studiohub").mkdir(parents=True)
        (library / ".studiohub" / "owner.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(EncodeProbeError) as caught:
            self.probe(FakeRunner(), scratch_dir=library / "probe")
        self.assertIn("library", caught.exception.reason)

    def test_probe_preserves_previous_output_and_does_not_launch(self):
        plan = plan_encode_probe(ENCODE, ffmpeg=Path("/tools/ffmpeg"),
                                 ffprobe=Path("/tools/ffprobe"),
                                 scratch_dir=self.scratch)
        plan.output_path.write_bytes(b"previous-evidence")
        runner = FakeRunner()
        with self.assertRaises(EncodeProbeError):
            self.probe(runner)
        self.assertEqual(plan.output_path.read_bytes(), b"previous-evidence")
        self.assertEqual(runner.calls, [])
        self.assertIn("-n", plan.ffmpeg_argv)
        self.assertNotIn("-y", plan.ffmpeg_argv)

    def test_probe_refuses_nonfinite_durations_and_wall_bounds(self):
        for value in (float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(UnsupportedEncodeProbe):
                    self.probe(FakeRunner(), duration_sec=value)
                with self.assertRaises(EncodeProbeError):
                    self.probe(FakeRunner(), timeout_seconds=value)
                with self.assertRaises(EncodeProbeError):
                    self.probe(FakeRunner(probe_report=report(duration=str(value))),
                               scratch_dir=self.scratch / str(value) / "duration")
                with self.assertRaises(EncodeProbeError):
                    self.probe(FakeRunner(encoder_wall=value),
                               scratch_dir=self.scratch / str(value) / "wall")

    def test_probe_refuses_extra_streams_and_truncated_audio(self):
        extra = report()
        extra["streams"].append(dict(extra["streams"][0]))
        with self.assertRaises(EncodeProbeError):
            self.probe(FakeRunner(probe_report=extra))
        audio = report()
        audio["streams"][1]["duration"] = "0.1"
        with self.assertRaises(EncodeProbeError):
            self.probe(FakeRunner(probe_report=audio), scratch_dir=self.scratch / "audio")

    def test_probe_refuses_ffprobe_that_exceeds_its_wall_bound(self):
        runner = FakeRunner()

        def slow_probe(argv, timeout):
            result = runner(argv, timeout)
            return ProbeRun(result.returncode, result.stdout, result.stderr,
                            999.0 if len(runner.calls) == 2 else result.wall_seconds)

        with self.assertRaises(EncodeProbeError) as caught:
            self.probe(slow_probe)
        self.assertIn("ffprobe exceeded", caught.exception.reason)


class HevcCapabilityProbeTests(unittest.TestCase):
    """HEVC in MP4 is probed as a capability only, never as a render claim."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.scratch = Path(temporary.name)

    def plan(self, encode, **kwargs):
        return plan_encode_probe(encode, ffmpeg=Path("/tools/ffmpeg"),
                                 ffprobe=Path("/tools/ffprobe"),
                                 scratch_dir=self.scratch / self._testMethodName,
                                 **kwargs)

    def probe(self, runner, encode, **kwargs):
        options = {
            "ffmpeg": Path("/tools/ffmpeg"),
            "ffprobe": Path("/tools/ffprobe"),
            "scratch_dir": self.scratch / self._testMethodName,
            "runner": runner,
            "tool_identity": TOOL_IDENTITY,
            "hardware_identity": HARDWARE_IDENTITY,
        }
        options.update(kwargs)
        return probe_encode_support(encode, **options)

    def test_hardware_hevc_is_a_probe_only_vector_not_a_compiler_tier(self):
        encode = dict(ENCODE, requested_codec="h265", requested_effort="hardware")
        plan = self.plan(encode)
        self.assertEqual(plan.scope, "capability_only")
        self.assertFalse(plan.compiler_args)
        self.assertEqual(plan.exact_encoder, "hevc_videotoolbox")
        self.assertIn("hevc_videotoolbox", plan.encoder_args)
        self.assertNotIn("h264_videotoolbox", plan.encoder_args)
        self.assertNotIn("libx264", plan.ffmpeg_argv)
        self.assertIn("probe only", plan.tier)

    def test_hardware_hevc_never_claims_the_compilers_false_h264_encoder(self):
        encode = dict(ENCODE, requested_codec="h265", requested_effort="hardware")
        # The compiler's own hardware tier for this tuple names h264_videotoolbox.
        self.assertEqual(primary_pair(encode)[1], "h264_videotoolbox")
        plan = self.plan(encode)
        self.assertNotEqual(plan.exact_encoder, primary_pair(encode)[1])
        self.assertNotIn("h264_videotoolbox", plan.ffmpeg_argv)

    def test_hardware_hevc_uses_the_compilers_own_hardware_options(self):
        capped = self.plan(dict(ENCODE, requested_codec="h265",
                                 requested_effort="hardware", bitrate_kbps=8000))
        self.assertIn("8000k", capped.ffmpeg_argv)
        self.assertIn("12000k", capped.ffmpeg_argv)
        self.assertIn("16000k", capped.ffmpeg_argv)
        self.assertIn("hvc1", capped.ffmpeg_argv)
        self.assertIn("yuv420p", capped.ffmpeg_argv)
        uncapped = self.plan(dict(ENCODE, requested_codec="h265",
                                   requested_effort="hardware"))
        self.assertNotIn("-maxrate", uncapped.ffmpeg_argv)
        self.assertIn("-q:v", uncapped.ffmpeg_argv)

    def test_software_hevc_uses_the_compilers_own_libx265_tier(self):
        encode = dict(ENCODE, requested_codec="h265")
        plan = self.plan(encode)
        self.assertEqual(plan.scope, "capability_only")
        self.assertTrue(plan.compiler_args)
        self.assertEqual(plan.exact_encoder, "libx265")
        self.assertEqual(plan.encoder_args,
                         _encoder_args("h265", "mp4", 23, "medium", None))
        self.assertIn("libx265", plan.ffmpeg_argv)
        self.assertIn("hvc1", plan.ffmpeg_argv)

    def test_software_hevc_tests_the_requested_cap_value_itself(self):
        encode = dict(ENCODE, requested_codec="h265", bitrate_kbps=8000)
        plan = self.plan(encode)
        self.assertEqual(plan.encoder_args,
                         _encoder_args("h265", "mp4", 23, "medium", 8000))
        self.assertIn("8000k", plan.ffmpeg_argv)
        self.assertIn("16000k", plan.ffmpeg_argv)

    def test_hevc_probe_requires_a_real_hevc_file_not_an_h264_one(self):
        encode = dict(ENCODE, requested_codec="h265", requested_effort="hardware")
        runner = FakeRunner(probe_report=report(video_codec="h264"))
        with self.assertRaises(EncodeProbeError) as caught:
            self.probe(runner, encode)
        self.assertIn("codec", caught.exception.reason)

    def test_hevc_probe_returns_capability_scoped_evidence(self):
        encode = dict(ENCODE, requested_codec="h265", requested_effort="hardware")
        evidence = self.probe(FakeRunner(probe_report=report(video_codec="hevc")),
                               encode)
        self.assertEqual(evidence["scope"], "capability_only")
        self.assertFalse(evidence["compiler_args"])
        self.assertEqual(evidence["exact_encoder"], "hevc_videotoolbox")
        self.assertEqual(evidence["encode"], encode)
        self.assertEqual(evidence["measured"]["video_codec"], "hevc")
        self.assertEqual(evidence["expected"]["requested_codec"], "h265")

    def test_probe_only_vector_publishes_no_compiler_tier(self):
        encode = dict(ENCODE, requested_codec="h265", requested_effort="hardware")
        evidence = self.probe(FakeRunner(probe_report=report(video_codec="hevc")),
                               encode)
        # No compiler tier exists for this vector, so it must not borrow one.
        self.assertIsNone(evidence["selected_tier"])

    def test_compiler_tier_is_published_under_the_authority_field_name(self):
        evidence = self.probe(FakeRunner(), ENCODE)
        self.assertEqual(evidence["selected_tier"], evidence["tier"])
        self.assertTrue(evidence["compiler_args"])

    def test_software_hevc_probe_returns_capability_scoped_evidence(self):
        encode = dict(ENCODE, requested_codec="h265")
        evidence = self.probe(FakeRunner(probe_report=report(video_codec="hevc")),
                               encode)
        self.assertEqual(evidence["scope"], "capability_only")
        self.assertTrue(evidence["compiler_args"])
        self.assertEqual(evidence["exact_encoder"], "libx265")

    def test_hevc_hardware_tuple_the_compiler_refuses_is_refused(self):
        with self.assertRaises(UnsupportedEncodeProbe):
            self.plan(dict(ENCODE, requested_codec="h265",
                           requested_effort="hardware", crf=99,
                           quality_cpu_preset="medium"))

    def test_h264_stays_scoped_to_the_render_lane(self):
        plan = self.plan(ENCODE)
        self.assertEqual(plan.scope, "render_lane")
        self.assertTrue(plan.compiler_args)
        evidence = self.probe(FakeRunner(), ENCODE)
        self.assertEqual(evidence["scope"], "render_lane")
        self.assertTrue(evidence["compiler_args"])


if __name__ == "__main__":
    unittest.main()
