"""The Video lane's own rules, without running the 22B model.

What is asserted here is what the worker *decides*: the memory tier that keeps a model off a
machine that cannot load it, the bounds its validator enforces (and the ones its contract
therefore publishes), the artifact checks that stand between a runner and a "done" receipt, and
the engine row: LTX-2.3 is the only video model (Lance was retired in 0.6.120).

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
import unittest.mock
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

    def test_defaults_resolve_to_the_measured_canvas(self) -> None:
        params = self._request(mode="text-to-video")
        self.assertEqual((params["width"], params["height"]), (704, 448))
        self.assertEqual(params["aspect_ratio"], "11:7")
        self.assertEqual(params["resolution"], "448p")
        self.assertEqual(params["duration_seconds"], 4)
        self.assertEqual(params["frames"], 97)
        self.assertEqual(params["frame_rate"], 24)

    def test_every_published_canvas_pair_is_accepted(self) -> None:
        for canvas in generation.published_canvases(generation.LTX_23):
            for seconds in canvas["durations"]:
                with self.subTest(canvas=canvas, seconds=seconds):
                    params = self._request(mode="text-to-video",
                                           aspect_ratio=canvas["aspect_ratio"],
                                           resolution=canvas["resolution"],
                                           duration_seconds=seconds)
                    self.assertEqual((params["width"], params["height"]),
                                     (canvas["width"], canvas["height"]))

    def test_every_published_canvas_is_one_the_pack_will_not_resize(self) -> None:
        """The two-stage pipeline floors each side to 64 px, so a canvas off that grid is a
        different shape than the one advertised.

        Measured 2026-09-27: the published 288p 16:9 canvas (512x288) came back as 512x256 — 2:1 —
        while the job's acceptance still said 512x288, so the lane's DEFAULT request was a canvas
        the lane does not produce. The guard is an import-time check, and this pins that it exists
        and that it fires.
        """
        for resolution, canvases in generation.LTX_CANVASES.items():
            for ratio, (width, height) in canvases.items():
                with self.subTest(ratio=ratio, resolution=resolution):
                    self.assertEqual(width % generation.TWO_STAGE_GRID, 0)
                    self.assertEqual(height % generation.TWO_STAGE_GRID, 0)
        with self.assertRaises(ValueError) as raised:
            with unittest.mock.patch.dict(generation.LTX_CANVASES,
                                          {"288p": {"16:9": (512, 288)}}):
                generation._assert_canvases_are_snap_stable(
                    generation.LTX_CANVASES, grid=generation.TWO_STAGE_GRID, name='LTX',
                    why='the two-stage pipeline')
        message = str(raised.exception)
        self.assertIn("512x288", message, "the off-grid canvas must be named")
        self.assertIn("512x256", message, "and the size the pack would substitute for it")

    def test_a_ratio_the_lane_does_not_publish_is_refused_by_name(self) -> None:
        # Two refusals, and the second is the one this test is named for.
        #
        # `16:9` used to be the example of an unpublished ratio. It now exists — at 576p, measured
        # 2026-09-27 — so the pair `16:9 @ 448p` is the interesting refusal: the ratio is real and
        # the class is real, and the pair is not. The sentence must name the class that ratio does
        # offer, or a caller has no way to fix the request.
        with self.assertRaises(ValueError) as raised:
            self._request(mode="text-to-video", aspect_ratio="16:9", resolution="448p")
        message = str(raised.exception)
        self.assertIn("16:9", message, "the refused ratio must be named")
        self.assertIn("576p", message, "and the class that ratio does offer must be listed")

        # And a ratio that is not published at all names the ratios that are.
        with self.assertRaises(ValueError) as raised:
            self._request(mode="text-to-video", aspect_ratio="9:16")
        message = str(raised.exception)
        self.assertIn("9:16", message)
        self.assertIn("16:9", message, "the published ratios must be listed")

    def test_a_pair_the_lane_does_not_publish_is_refused_by_name(self) -> None:
        # 7:4 exists, and 448p exists, and their PAIR does not: 448x256 is the 256p canvas.
        with self.assertRaises(ValueError) as raised:
            self._request(mode="text-to-video", aspect_ratio="7:4", resolution="448p")
        message = str(raised.exception)
        self.assertIn("7:4", message)
        self.assertIn("256p", message, "the class that ratio does offer must be listed")

    def test_every_published_configuration_stays_under_the_pack_s_quality_cliff(self) -> None:
        """The cliff is a published ceiling, and the vocabulary is chosen to stay under it.

        The pack's card states the VAE produces mesh artifacts at n_lat >= ~30k (768x768x49f).
        Nothing this lane publishes may cross that — 704x448x121f would (37,268), which is why
        121 frames is not a published duration — so the check is arithmetic on the whole table
        rather than one refused request.
        """
        for resolution, canvases in generation.LTX_CANVASES.items():
            for ratio, (width, height) in canvases.items():
                # The canvas's own published durations, not the lane's whole list: a canvas may
                # take fewer (576p takes 2 s only, because 4 s crosses this very cliff).
                for seconds in generation.canvas_durations(generation.LTX_23, width, height):
                    with self.subTest(ratio=ratio, resolution=resolution, seconds=seconds):
                        frames = generation.frames_for(seconds, generation.DEFAULT_FRAME_RATE,
                                                   generation.LTX_23.frame_grid)
                        self.assertLess(generation.latent_load(generation.LTX_23, width, height, frames),
                                        generation.MAX_LATENT_TOKENS)
        # The guard itself is live, not decorative: the same canvas one duration longer crosses it.
        self.assertGreaterEqual(generation.latent_load(generation.LTX_23, 704, 448, 121),
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

    def test_sound_is_offered_only_by_a_model_that_has_any(self) -> None:
        """The audio switch, and the rule that keeps it honest.

        LTX denoises picture and sound together and its pack has a real switch for the decode
        (`generate_audio`, the CLI's `--no-audio`), so the lane publishes `audio`. A silent model
        would not publish the control: a knob the lane ignores is "a lie with a schema".
        """
        ltx = {p["name"]: p for p in generation.lane_parameters(generation.LTX_23)}
        self.assertIn("audio", ltx)
        self.assertIs(ltx["audio"]["default"], True)
        self.assertIs(ltx["audio"]["type"], "boolean")

    def test_the_audio_switch_defaults_on_and_resolves_both_ways(self) -> None:
        """Absent is not "off": a field nobody sent must read as the model's default."""
        default = self._request(mode="text-to-video")
        self.assertIs(default["audio"], True)
        for asked, expected in ((False, False), (True, True), ("false", False), ("true", True),
                                (0, False), (1, True)):
            with self.subTest(asked=asked):
                params = self._request(mode="text-to-video", audio=asked)
                self.assertIs(params["audio"], expected)

    def test_the_contract_publishes_the_switch_and_its_honest_note(self) -> None:
        contract = generation.lane_contract(generation.LTX_23)
        entry = next(p for p in contract["parameters"] if p["name"] == "audio")
        self.assertIs(entry["default"], True)
        note = entry["note"].lower()
        # The note has to say what it does NOT do, because a caller would otherwise assume a
        # shorter generation: the joint denoise is untouched.
        self.assertIn("does not change", note)
        self.assertIn("silent", note)

    def test_a_silent_request_is_not_refused_for_having_no_audio(self) -> None:
        """The artifact gate follows the request rather than the pack.

        The LTX gate requires an audio track because the measured configuration has one; that
        check must not fire for a caller who asked for silence, and the mirror check must fire
        when a silent request comes back with a track.
        """
        source = Path(generation.__file__).read_text()
        self.assertIn("params.get('audio', True)\n                        and not "
                      "facts.get('audio_tracks')", source)
        self.assertIn("not params.get('audio', True)\n                        and "
                      "facts.get('audio_tracks')", source)

    def test_a_parameter_the_runner_discards_is_refused_not_dropped(self) -> None:
        for refused in ("negative_prompt", "guidance", "steps"):
            with self.subTest(parameter=refused):
                with self.assertRaises(ValueError) as raised:
                    self._request(mode="text-to-video", **{refused: 3})
                self.assertIn(refused, str(raised.exception))


class LaneContractTests(unittest.TestCase):
    def test_the_published_canvases_are_exactly_the_accepted_ones(self) -> None:
        """The invariant the first consumer broke: published pairs == accepted pairs.

        A contract that lists `aspect_ratio` and `resolution` as two enums says every pair of them
        exists, and this lane's do not: `7:4` is the 256p canvas and `448p` carries `11:7` and
        `1:1`. A scheduler reading only the enums offered `448p` with a ratio that class does not
        have, and the lane refused work its own contract implied. So the contract now publishes
        `canvases`, and this test walks the whole cross product: every published combination is
        accepted, and every unpublished one is refused by name.
        """
        contract = generation.lane_contract(generation.LTX_23)
        published = {(canvas["resolution"], canvas["aspect_ratio"], seconds)
                     for canvas in contract["canvases"]
                     for seconds in canvas["durations"]}
        self.assertTrue(published, "the lane must publish at least one canvas")

        for resolution in generation.resolutions(generation.LTX_23):
            for ratio in generation.aspect_ratios(generation.LTX_23):
                for seconds in generation.DURATIONS_SECONDS:
                    request = {"repo": generation.MODEL_ID, "mode": "text-to-video",
                               "prompt": "a quiet harbour at dawn",
                               "aspect_ratio": ratio, "resolution": resolution,
                               "duration_seconds": seconds}
                    if (resolution, ratio, seconds) in published:
                        with self.subTest(published=(resolution, ratio, seconds)):
                            params = generation.validate_request(dict(request))
                            self.assertEqual(params["resolution"], resolution)
                            self.assertEqual(params["aspect_ratio"], ratio)
                            self.assertEqual(params["duration_seconds"], seconds)
                    else:
                        with self.subTest(refused=(resolution, ratio, seconds)):
                            with self.assertRaises(ValueError):
                                generation.validate_request(dict(request))

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
        # The measured runtime travels with the row that has one.
        self.assertIn("runtime_seconds", contract)

    def test_the_parameters_are_the_validator_s_bounds(self) -> None:
        names = [p["name"] for p in generation.lane_contract(generation.LTX_23)["parameters"]]
        # `audio` sits before `seed` for a model that has sound, and is absent for one that does
        # not — the list is the validator's vocabulary, so it is asserted as published.
        self.assertEqual(names, ["prompt", "mode", "aspect_ratio", "resolution",
                                 "duration_seconds", "frame_rate", "audio", "seed"])
        # A parameter the distilled pipeline cannot read is not offered, and vice versa.
        for absent in ("negative_prompt", "guidance", "steps", "width", "height"):
            self.assertNotIn(absent, names)

class EngineRowTests(unittest.TestCase):
    """Video is LTX-2.3 and nothing else: Lance was retired in 0.6.120 and must not come back quietly."""

    def test_ltx_is_the_only_video_model_and_the_default(self) -> None:
        self.assertEqual(list(generation.MODELS), [generation.LTX_23.repo])
        self.assertEqual(generation.VIDEO_ENGINES, ("video-ltx",))
        self.assertEqual(generation.MODEL_ID, generation.LTX_23.repo)
        self.assertEqual(generation.PACKAGE_ID, "ltx-2-3-22b-ram-12gb-mlx")
        self.assertEqual(generation.LTX_23.requires, ("gemma-3-12b-it-4bit",))
        self.assertEqual({m.package_id for m in generation.MODELS.values()},
                         {"ltx-2-3-22b-ram-12gb-mlx"})

    def test_nothing_of_lance_is_left_to_run(self) -> None:
        for name in ("LANCE_3B", "LANCE_ENGINE", "run_lance", "lance_runtime_python"):
            self.assertFalse(hasattr(generation, name), name)
        self.assertFalse((ROOT / "components/video/app/backend/lance_runner.py").exists())
        self.assertFalse((ROOT / "packaging/lance-runtime").exists())
        self.assertEqual(list((ROOT / "model-manifests").glob("*lance*")), [])

    def test_a_lance_manifest_left_on_a_drive_is_not_a_video_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            ltx = json.loads((ROOT / "components/video/retired/ltx-2-3-22b-ram-12gb-mlx.json").read_text())
            (folder / "ltx.json").write_text(json.dumps(ltx))
            (folder / "lance.json").write_text(json.dumps(
                {"package_id": "lance-3b-video-bf16", "engine": "video-lance",
                 "model_id": "mlx-community/Lance-3B-Video-bf16"}))
            manifests, _errors = generation.load_video_manifests(folder, folder / "ltx.json")
        self.assertEqual(set(manifests), {"ltx-2-3-22b-ram-12gb-mlx"})

    def test_ltx_keeps_its_own_frame_grid(self) -> None:
        self.assertEqual(generation.frames_for(2, 24, generation.LTX_23.frame_grid), 49)
        self.assertEqual(generation.frames_for(4, 24, generation.LTX_23.frame_grid), 97)

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

    def _probe(self, *, which, output):
        from unittest import mock

        generation._MUXER_STATE.clear()
        try:
            with mock.patch("shutil.which", return_value=which), \
                 mock.patch("subprocess.run") as run:
                run.return_value = mock.Mock(stdout=output, stderr="")
                return generation.muxer_problem()
        finally:
            generation._MUXER_STATE.clear()

    def test_a_missing_muxer_is_reported_rather_than_discovered_at_the_last_step(self) -> None:
        problem = self._probe(which=None, output="")
        self.assertIsNotNone(problem)
        self.assertIn("ffmpeg", problem)

    def test_an_ffmpeg_without_libx264_is_not_good_enough(self) -> None:
        """The exact defect the first through-the-chain run hit: ffmpeg present, no H.264.

        The Voice runtime's build answers `-h encoder=libx264` with *"Codec 'libx264' is not
        recognized by FFmpeg"* and exits 0, so the presence of a binary is not the question.
        """
        problem = self._probe(which="/usr/bin/ffmpeg",
                              output="Codec 'libx264' is not recognized by FFmpeg.\n")
        self.assertIsNotNone(problem)
        self.assertIn("libx264", problem)

    def test_a_video_capable_ffmpeg_passes(self) -> None:
        self.assertIsNone(self._probe(
            which="/Applications/Studio Hub.app/Contents/Resources/runtimes/video/bin/ffmpeg",
            output="Encoder libx264 [libx264 H.264 / AVC / MPEG-4 AVC]:"))

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
            other = {"package_id": "video-other-thing", "engine": "video-ltx",
                     "model_id": "org/other", "revision": "b" * 40}
            manager = generation.GenerationManager(
                Path(tmp) / "state", library, model_manifest, "c" * 64,
                manifests={"ltx-test": model_manifest, "other": other})
            rows = {row["package_id"] for row in manager.packages_without_a_runtime_model()}
        self.assertIn("video-other-thing", rows,
                      "a manifest nothing requires is still reported when no model serves it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
