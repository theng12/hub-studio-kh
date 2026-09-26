"""The Video lane's own rules, without running the 22B model.

What is asserted here is what the worker *decides*: the memory tier that keeps a model off a
machine that cannot load it, the bounds its validator enforces (and the ones its contract
therefore publishes), the artifact checks that stand between a runner and a "done" receipt, and
the two engine rows — one runnable, one published as not runnable with its adoption step named.

Nothing here imports `ltx_pipelines_mlx` or loads a weight: the pipeline is imported lazily by the
runner, so these run in the Hub's base runtime in milliseconds. The one run that proves the lane
end to end is `qualification/ltx-video-worker-run.py`, and its receipt is what the manifest and the
changelog cite.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.video.app.backend import generation  # noqa: E402


class MemoryTierTests(unittest.TestCase):
    """The tier is the routing decision, so it is asserted per model and per host size."""

    def test_the_22b_model_is_not_eligible_below_24_gb(self) -> None:
        model = generation.LTX_23
        self.assertEqual(model.qualified_minimum_memory_gb, 24.0)
        self.assertFalse(generation.memory_eligible(model, 8 * 1024**3))
        self.assertFalse(generation.memory_eligible(model, 16 * 1024**3))
        self.assertTrue(generation.memory_eligible(model, 24 * 1024**3))
        self.assertTrue(generation.memory_eligible(model, 32 * 1024**3))

    def test_lance_is_the_16_gb_tier_and_not_an_8_gb_promise(self) -> None:
        model = generation.LANCE_3B
        self.assertEqual(model.qualified_minimum_memory_gb, 16.0)
        # The card groups 8-16 GB together while its own worked example is ~8.7 GB by ri_phys,
        # which is more than an 8 GB machine's whole RAM. The published tier is the credible side.
        self.assertFalse(generation.memory_eligible(model, 8 * 1024**3))
        self.assertTrue(generation.memory_eligible(model, 16 * 1024**3))

    def test_the_free_memory_floor_is_a_start_guard_not_the_footprint(self) -> None:
        """The measured footprint is not a free-memory requirement.

        The Hub answers an unmet free floor with "wait", not "skip" (`broker._memory_gate`), so
        a floor equal to the footprint waits for memory that a healthy Mac keeps in reclaimable
        inactive pages. Measured 2026-09-26: a chain job sat queued at `needs ~16.5GB, ~14.2GB
        free` and every idle sibling model had already been released. The Image lane lowered
        Z-Image's floor from its 7.1 GB footprint to 3.0 GB for the same reason.
        """
        model = generation.LTX_23
        self.assertIsNotNone(model.min_free_memory_gb)
        self.assertLess(model.min_free_memory_gb, model.qualified_minimum_memory_gb,
                        "the live floor must be below the tier it guards")
        footprint = model.qualification["measured_runs"][0]["peak_process_footprint_gb"]
        self.assertLess(model.min_free_memory_gb, footprint,
                        "a floor at or above the measured footprint waits for memory that never "
                        "becomes free on a healthy Mac")
        self.assertGreaterEqual(model.min_free_memory_gb, 4.0,
                                "a floor near zero would start into a full machine")

    def test_an_unmeasurable_host_is_never_eligible(self) -> None:
        for model in generation.MODELS.values():
            with self.subTest(model=model.package_id):
                self.assertFalse(generation.memory_eligible(model, 0))


class RequestValidationTests(unittest.TestCase):
    """Every bound the contract publishes is one `validate_request` enforces."""

    def _request(self, **overrides):
        body = {"repo": generation.MODEL_ID, "prompt": "a quiet harbour at dawn"}
        body.update(overrides)
        return generation.validate_request(body)

    def test_defaults_resolve_to_a_published_canvas_and_duration(self) -> None:
        params = self._request(mode="text-to-video")
        self.assertEqual((params["width"], params["height"]), (512, 288))
        self.assertEqual(params["aspect_ratio"], "16:9")
        self.assertEqual(params["resolution"], "288p")
        self.assertEqual(params["duration_seconds"], 4)
        self.assertEqual(params["frames"], 97)
        self.assertEqual(params["frame_rate"], 24)

    def test_every_published_canvas_pair_is_accepted(self) -> None:
        for resolution, canvases in generation.CANVAS_CLASSES.items():
            for ratio, canvas in canvases.items():
                with self.subTest(ratio=ratio, resolution=resolution):
                    params = self._request(mode="text-to-video", aspect_ratio=ratio,
                                           resolution=resolution)
                    self.assertEqual((params["width"], params["height"]), canvas)

    def test_a_canvas_the_lane_does_not_publish_is_refused_by_name(self) -> None:
        with self.assertRaises(ValueError) as raised:
            self._request(mode="text-to-video", aspect_ratio="16:9", resolution="448p")
        message = str(raised.exception)
        self.assertIn("448p", message, "the refused class must be named")
        self.assertIn("16:9", message, "the ratio it is not published for must be named")
        self.assertIn("288p", message, "and the classes that ratio does offer must be listed")

    def test_every_published_configuration_stays_under_the_pack_s_quality_cliff(self) -> None:
        """The cliff is a published ceiling, and the vocabulary is chosen to stay under it.

        The pack's card states the VAE produces mesh artifacts at n_lat >= ~30k (768x768x49f).
        Nothing this lane publishes may cross that — 704x448x121f would (37,268), which is why
        121 frames is not a published duration — so the check is arithmetic on the whole table
        rather than one refused request.
        """
        for resolution, canvases in generation.CANVAS_CLASSES.items():
            for ratio, (width, height) in canvases.items():
                for seconds in generation.DURATIONS_SECONDS:
                    with self.subTest(ratio=ratio, resolution=resolution, seconds=seconds):
                        frames = generation.frames_for(seconds, generation.DEFAULT_FRAME_RATE)
                        self.assertLess(generation.latent_tokens(width, height, frames),
                                        generation.MAX_LATENT_TOKENS)
        # The guard itself is live, not decorative: the same canvas one duration longer crosses it.
        self.assertGreaterEqual(generation.latent_tokens(704, 448, 121),
                                generation.MAX_LATENT_TOKENS)
        with self.assertRaises(ValueError) as raised:
            self._request(mode="text-to-video", aspect_ratio="11:7", resolution="448p",
                          duration_seconds=5)
        self.assertIn("5", str(raised.exception))

    def test_image_to_video_needs_an_image_and_text_to_video_refuses_one(self) -> None:
        with self.assertRaises(ValueError) as raised:
            self._request(mode="image-to-video")
        self.assertIn("source image", str(raised.exception))
        with self.assertRaises(ValueError) as raised:
            self._request(mode="text-to-video", image_path="/tmp/x.png")
        self.assertIn("text-to-video", str(raised.exception))

    def test_the_hub_s_own_frame_and_duration_vocabulary_resolves(self) -> None:
        # The broker's img2video door flattens form fields: `frames`, `fps`, `duration`, `width`,
        # `height`. They resolve onto the same canonical parameters as the lane's own names.
        params = self._request(mode="image-to-video", image_path="/tmp/x.png",
                               width=704, height=448, frames=97, fps=24)
        self.assertEqual((params["width"], params["height"], params["frames"]), (704, 448, 97))
        self.assertEqual(params["duration_seconds"], 4)
        with self.assertRaises(ValueError) as raised:
            self._request(mode="text-to-video", frames=100)
        self.assertIn("not a published duration", str(raised.exception))

    def test_a_parameter_the_runner_discards_is_refused_not_dropped(self) -> None:
        for refused in ("negative_prompt", "guidance", "steps"):
            with self.subTest(parameter=refused):
                with self.assertRaises(ValueError) as raised:
                    self._request(mode="text-to-video", **{refused: 3})
                self.assertIn(refused, str(raised.exception))


class LaneContractTests(unittest.TestCase):
    def test_the_contract_publishes_the_lane_and_not_the_hub_s_deadline(self) -> None:
        contract = generation.lane_contract(generation.LTX_23)
        self.assertEqual(contract["schema"], "studiohub.lane-contract.v1")
        self.assertEqual(contract["modality"], "video")
        self.assertEqual(contract["operation"], "video.generation")
        self.assertEqual(contract["modes"], ["image-to-video", "text-to-video"])
        self.assertEqual(contract["artifact"]["media_type"], "video/mp4")
        self.assertNotIn("deadline_seconds", contract,
                         "the Hub owns the proxy deadline and adds it on echo")
        self.assertTrue(contract["states"]["discard_supported"])
        self.assertFalse(contract["states"]["uncertain_is_terminal"])
        # The measured runtime travels with the row that has one, and only that row.
        self.assertIn("runtime_seconds", contract)
        self.assertNotIn("runtime_seconds", generation.lane_contract(generation.LANCE_3B))

    def test_the_parameters_are_the_validator_s_bounds(self) -> None:
        names = [p["name"] for p in generation.lane_contract(generation.LTX_23)["parameters"]]
        self.assertEqual(names, ["prompt", "mode", "aspect_ratio", "resolution",
                                 "duration_seconds", "frame_rate", "seed"])
        # A parameter the distilled pipeline cannot read is not offered, and vice versa.
        for absent in ("negative_prompt", "guidance", "steps", "width", "height"):
            self.assertNotIn(absent, names)

    def test_lance_declares_only_the_task_it_has(self) -> None:
        contract = generation.lane_contract(generation.LANCE_3B)
        self.assertEqual(contract["modes"], ["text-to-video"])


class EngineRowTests(unittest.TestCase):
    def test_the_unadopted_runner_refuses_by_naming_its_adoption_step(self) -> None:
        with self.assertRaises(RuntimeError) as raised:
            generation.run_lance(generation.LANCE_3B, None, Path("/nonexistent"), {}, Path("/tmp/x"),
                                 lambda *_: None)
        message = str(raised.exception)
        self.assertIn("MODEL_RUNTIME_UNAVAILABLE", message)
        self.assertIn("lance-mlx", message)

    def test_every_registered_engine_has_a_runner(self) -> None:
        for model in generation.MODELS.values():
            with self.subTest(engine=model.engine):
                self.assertIn(model.engine, generation.VIDEO_ENGINES)
                self.assertIn(model.engine, generation.RUNNERS)

    def test_the_pack_loader_is_applied_and_named(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "generate.py").write_text("def _patch_pipeline_quantization():\n    pass\n")
            (folder / "split_model.json").write_text(json.dumps({"quantized": True}))
            self.assertEqual(generation.apply_pack_quantization(folder),
                             "pack_generate.py:_patch_pipeline_quantization")
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "generate.py").write_text("def _patch_pipeline_quantization():\n    pass\n")
            (folder / "split_model.json").write_text(json.dumps({"quantized": False}))
            self.assertIsNone(generation.apply_pack_quantization(folder),
                              "a pack that is not mixed-precision must be left alone")


class MuxerTests(unittest.TestCase):
    """The pack muxes with ffmpeg, so a worker that cannot see one is not ready."""

    def test_a_missing_muxer_is_reported_rather_than_discovered_at_the_last_step(self) -> None:
        from unittest import mock

        with mock.patch("shutil.which", return_value=None):
            problem = generation.muxer_problem()
        self.assertIsNotNone(problem)
        self.assertIn("ffmpeg", problem)
        with mock.patch("shutil.which", return_value="/usr/local/bin/ffmpeg"):
            self.assertIsNone(generation.muxer_problem())

    def test_the_packaged_worker_puts_the_bundled_ffmpeg_on_path(self) -> None:
        """The app ships ffmpeg in the Voice runtime; the Video worker must be able to see it.

        Measured 2026-09-26: a worker whose PATH was only its own runtime and `/usr/bin`
        generated the whole clip and then failed with the pack's own *"ffmpeg not found on PATH"*.
        """
        source = (ROOT / "packaging" / "video_worker.py").read_text()
        self.assertIn("'PATH': ':'.join([", source,
                      "the worker builds its own PATH; the bundled muxer can no longer be added")
        self.assertIn("str(RESOURCES / 'runtimes' / 'voice' / 'bin')", source,
                      "the bundled ffmpeg lives in the Voice runtime's bin and must be on PATH")
        self.assertIn("str(runtime / 'bin')", source,
                      "the worker's own runtime stays first on PATH")
        self.assertIn("ffmpeg", source.lower(),
                      "the reason must be recorded beside the line, or the next reader removes it")


class ArtifactCheckTests(unittest.TestCase):
    """A file existing is not a clip: the boxes are read before anything is called done."""

    def _mp4(self, *, audio: bool = True, duration: bool = True) -> bytes:
        import struct

        def box(kind: bytes, payload: bytes) -> bytes:
            return struct.pack(">I", len(payload) + 8) + kind + payload

        # A version-0 mvhd is version/flags, ctime, mtime, timescale, duration, then the rest.
        mvhd = (b"\x00" * 4 + b"\x00" * 4 + b"\x00" * 4
                + struct.pack(">II", 1000, 4000) + b"\x00" * 80)
        tracks = b""
        for handler in ([b"vide"] if not audio else [b"vide", b"soun"]):
            hdlr = b"\x00\x00\x00\x00\x00\x00\x00\x00" + handler + b"\x00" * 12
            # A version-0 tkhd is 84 bytes: version/flags, ctime, mtime, track id, reserved,
            # duration, then 8 reserved + layer + alternate group + volume + 2 reserved + a
            # 36-byte matrix, and the 16.16 fixed-point width and height last. The reader is
            # offset-based, so a short payload silently reads nothing.
            tkhd = (b"\x00\x00\x00\x00" + b"\x00" * 8 + struct.pack(">II", 1, 0)
                    + struct.pack(">I", 4000) + b"\x00" * 8
                    + b"\x00" * 2 + b"\x00" * 2 + b"\x00" * 2 + b"\x00" * 2
                    + b"\x00" * 36 + struct.pack(">II", 704 << 16, 448 << 16))
            mdia = box(b"mdia", box(b"hdlr", hdlr))
            tracks += box(b"trak", box(b"tkhd", tkhd) + mdia)
        with_duration = mvhd if duration else b"\x00\x00\x00\x00" + struct.pack(">II", 1000, 0) + b"\x00" * 80
        moov = box(b"moov", box(b"mvhd", with_duration) + tracks)
        return box(b"ftyp", b"isom" + b"\x00" * 8) + moov

    def test_a_real_mp4_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.mp4"
            path.write_bytes(self._mp4())
            facts = generation.mp4_facts(path)
        self.assertEqual(facts["ftyp"], "isom")
        self.assertEqual((facts["width"], facts["height"]), (704, 448))
        self.assertEqual(facts["duration_seconds"], 4.0)
        self.assertEqual((facts["video_tracks"], facts["audio_tracks"]), (1, 1))

    def test_bytes_that_are_not_a_clip_are_reported_as_such(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.mp4"
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"not a video" * 4)
            facts = generation.mp4_facts(path)
        self.assertFalse(facts["ftyp"])
        self.assertEqual(facts["video_tracks"], 0)

    def test_a_silent_clip_is_visible_as_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.mp4"
            path.write_bytes(self._mp4(audio=False))
            facts = generation.mp4_facts(path)
        self.assertEqual(facts["audio_tracks"], 0)
        self.assertEqual(facts["video_tracks"], 1)


class CompanionRowTests(unittest.TestCase):
    """The text encoder is a dependency, not a video model the operator can pick."""

    def test_a_companion_manifest_gets_no_model_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "library"
            library.mkdir()
            model_manifest = {"package_id": "ltx-test", "engine": "video-ltx",
                              "model_id": "baa-ai/LTX", "revision": "a" * 40,
                              "requires_packages": ["encoder-test"]}
            companion = {"package_id": "encoder-test", "engine": "video-ltx",
                         "model_id": "mlx-community/encoder", "revision": "b" * 40}
            manager = generation.GenerationManager(
                Path(tmp) / "state", library, model_manifest, "c" * 64,
                manifests={"ltx-test": model_manifest, "encoder-test": companion})
            rows = {row["package_id"] for row in manager.packages_without_a_runtime_model()}
        self.assertNotIn("encoder-test", rows,
                         "a package another manifest requires must not be published as a model")

    def test_an_unrelated_manifest_is_still_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "library"
            library.mkdir()
            model_manifest = {"package_id": "ltx-test", "engine": "video-ltx",
                              "model_id": "baa-ai/LTX", "revision": "a" * 40}
            other = {"package_id": "video-lance-thing", "engine": "video-lance",
                     "model_id": "org/other", "revision": "b" * 40}
            manager = generation.GenerationManager(
                Path(tmp) / "state", library, model_manifest, "c" * 64,
                manifests={"ltx-test": model_manifest, "other": other})
            rows = {row["package_id"] for row in manager.packages_without_a_runtime_model()}
        self.assertIn("video-lance-thing", rows,
                      "a manifest nothing requires is still reported when no model serves it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
