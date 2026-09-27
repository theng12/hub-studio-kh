"""The YuE2 music lane: its tier, its refusals, and the worker's side of the bridge.

Three things are pinned here, and they are the three that decide whether this engine can be
trusted on a fleet node:

1. **The tier comes from a measurement.** YuE2's model card publishes no memory table at all, so
   the only number that exists for it is the one taken on this Mac — three runs, 10.79-11.93 GB
   peak by sampled `footprint phys_footprint_peak`. The catalogue must publish that tier and the
   memory evidence beside it, or an operator is being offered a model with no stated cost.
2. **The validator refuses what the engine cannot do**, by name: a song with no lyrics (this
   engine has no instrumental switch), a mode the port does not offer, a step count outside the
   two configurations the port documents, and a seed the port would reject.
3. **The worker verifies the artifact rather than the report.** The bridge is a subprocess in
   another interpreter, so the interesting failures are "it said ok and wrote nothing", "it wrote
   half a file", and "it died" — each has to fail the job.

The bridge's own run needs the 11 GB engine and several minutes, so it is exercised through a stub
interpreter that speaks the same JSON contract. The real run is the receipt in
`qualification/yue2-music-results-20260927/`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.music.app.backend import catalog, generation, yue2  # noqa: E402


def yue2_model():
    return catalog.require_model(catalog.YUE2_REPO)


class TierAndCatalogTests(unittest.TestCase):
    def test_the_tier_is_measured_and_says_so(self):
        model = yue2_model()
        self.assertEqual(model.runtime, catalog.RUNTIME_YUE2)
        self.assertEqual(model.min_unified_memory_gb, 16)
        evidence = model.qualification["memory_evidence"]
        self.assertIn("MEASURED", evidence)
        # Every peak the receipt holds, and the method that took it — not MLX's own allocator.
        for peak in ("10.79 GB", "11.81 GB", "11.93 GB"):
            self.assertIn(peak, evidence)
        self.assertIn("footprint", evidence)
        # And the 8 GB verdict is stated rather than left for an operator to discover by trying.
        self.assertIn("8 GB", evidence)

    def test_the_engine_is_served_and_its_companion_is_declared(self):
        from components.hub import standalone
        self.assertEqual(standalone._worker_for_engine(catalog.RUNTIME_YUE2), "music")
        self.assertEqual(standalone._modality_for_engine(catalog.RUNTIME_YUE2), "music")
        manifest = json.loads(
            (Path(__file__).resolve().parents[1] / "model-manifests"
             / "yue2-3b-mlx.json").read_text())
        self.assertEqual(manifest["engine"], catalog.RUNTIME_YUE2)
        self.assertEqual(manifest["requires_packages"], ["yue2-vae"])
        self.assertEqual(manifest["qualification"]["minimum_unified_memory_gb"], 16)

    def test_the_runtime_is_the_port_at_an_exact_commit(self):
        # A version string does not name a runner. The port is a source checkout, so the commit is
        # part of this model's identity and is published beside the tier.
        self.assertEqual(catalog.YUE2_PORT_COMMIT, "9253ed133343406947bde7b67d43c7a63fb39d99")
        self.assertIn(catalog.YUE2_PORT_COMMIT, yue2_model().qualification["measured_on"])


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.model = yue2_model()
        self.base = {"prompt": "Warm acoustic ballad", "lyrics": "[Verse]\nA line of song"}

    def validate(self, **overrides):
        return generation._validate_yue2({**self.base, **overrides}, self.model)

    def test_a_song_is_converted_from_seconds_to_the_ports_token_budget(self):
        params = self.validate(duration_seconds=36, seed=7)
        self.assertEqual(params["mode"], "off")
        self.assertEqual(params["precision"], "8bit")
        # 36 s at the measured ~25 tokens per second.
        self.assertEqual(params["max_tokens"], 900)
        self.assertEqual(params["min_tokens"], 450)
        self.assertFalse(params["instrumental"],
                         "this engine sings; claiming instrumental output would be a lie")

    def test_an_empty_lyric_sheet_is_refused_by_name(self):
        with self.assertRaises(ValueError) as caught:
            self.validate(lyrics="   \n  ")
        self.assertIn("sings from lyrics", str(caught.exception))
        self.assertIn("instrumental", str(caught.exception),
                      "the refusal must point at the engines that can do it")

    def test_a_mode_the_port_does_not_offer_is_refused(self):
        with self.assertRaises(ValueError):
            self.validate(mode="sing")

    def test_the_step_bounds_are_the_ports_two_documented_configurations(self):
        self.assertEqual(self.validate(steps=8)["steps"], 8)
        self.assertEqual(self.validate(steps=32)["steps"], 32)
        with self.assertRaises(ValueError):
            self.validate(steps=4)
        with self.assertRaises(ValueError):
            self.validate(steps=64)

    def test_guidance_is_bounded_by_the_ports_own_protocol(self):
        self.assertEqual(self.validate(guidance_scale=20.0)["guidance_scale"], 20.0)
        with self.assertRaises(ValueError):
            self.validate(guidance_scale=21.0)

    def test_the_published_bounds_are_the_enforced_ones(self):
        params = {item["name"]: item for item in generation.lane_parameters(self.model)}
        self.assertEqual(params["steps"]["minimum"], self.model.limits["min_steps"])
        self.assertEqual(params["steps"]["maximum"], self.model.limits["max_steps"])
        self.assertEqual(params["guidance_scale"]["maximum"], self.model.limits["max_guidance"])
        self.assertEqual(params["mode"]["enum"], ["off", "melody", "full"])
        # A required parameter is per-request content and is offered with no default.
        self.assertTrue(params["lyrics"]["required"])
        self.assertNotIn("default", params["lyrics"])


class BridgeContractTests(unittest.TestCase):
    """The worker's side of the subprocess contract, through a stub interpreter."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.model_dir = self.root / "model"
        self.vae_dir = self.root / "vae"
        self.model_dir.mkdir()
        self.vae_dir.mkdir()
        self.output = self.root / "out" / "song.flac"

    def tearDown(self):
        self.temp.cleanup()

    def write_stub(self, body: str) -> Path:
        script = self.root / "stub-python"
        script.write_text("#!/bin/sh\n" + body)
        script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return script

    def test_a_missing_runtime_is_refused_before_anything_is_spawned(self):
        with self.assertRaises(yue2.Yue2Error) as caught:
            yue2.generate(
                model_dir=self.model_dir, vae_dir=self.vae_dir, output=self.output,
                prompt="p", lyrics="l", mode="off", precision="8bit", duration_seconds=36,
                max_tokens=900, min_tokens=450, steps=32, guidance_scale=1.0, seed=1,
                interpreter=self.root / "nope" / "bin" / "python3")
        self.assertIn("runtime is not staged", str(caught.exception))

    def test_a_missing_decoder_is_refused_by_name(self):
        interpreter = self.write_stub("exit 0\n")
        with self.assertRaises(yue2.Yue2Error) as caught:
            yue2.generate(
                model_dir=self.model_dir, vae_dir=None, output=self.output,
                prompt="p", lyrics="l", mode="off", precision="8bit", duration_seconds=36,
                max_tokens=900, min_tokens=450, steps=32, guidance_scale=1.0, seed=1,
                interpreter=interpreter)
        self.assertIn("yue2-vae", str(caught.exception))

    def test_a_successful_bridge_run_returns_what_the_file_says(self):
        payload = self.root / "payload.bin"
        payload.write_bytes(b"FLAC" + b"\0" * 2048)
        interpreter = self.write_stub(
            'printf \'{"stage": "generating", "detail": "song"}\\n\'\n'
            f'cp "{payload}" "$(dirname "$0")/out/song.flac" 2>/dev/null || '
            f'cp "{payload}" "{self.output}"\n'
            'printf \'{"status": "ok", "seconds": 36.0, "sample_rate": 48000, '
            '"channels": 2, "load_seconds": 6.8, "generate_seconds": 96.3}\\n\'\n'
            'printf \'{"peak_footprint_bytes": 11811160064, "peak_rss_bytes": 6227804160, '
            '"footprint_method": "sampled /usr/bin/footprint phys_footprint_peak at 2 Hz in the '
            'runner process"}\\n\'\n')
        self.output.parent.mkdir(parents=True, exist_ok=True)
        stages: list[tuple[float, str]] = []
        facts = yue2.generate(
            model_dir=self.model_dir, vae_dir=self.vae_dir, output=self.output,
            prompt="p", lyrics="l", mode="off", precision="8bit", duration_seconds=36,
            max_tokens=900, min_tokens=450, steps=32, guidance_scale=1.0, seed=1,
            interpreter=interpreter, on_progress=lambda ratio, label: stages.append((ratio, label)))
        self.assertEqual(facts["sample_rate_hz"], 48000)
        self.assertEqual(facts["channels"], 2)
        self.assertEqual(facts["audio_duration_s"], 36.0)
        self.assertEqual(facts["frames"], 36 * 48000)
        self.assertEqual(facts["bytes"], 2052)
        # The bridge's own measurement travels, because the worker's sampler watches the worker.
        self.assertEqual(facts["peak_footprint_bytes"], 11811160064)
        self.assertIn("footprint", facts["footprint_method"])
        self.assertTrue(stages, "the bridge's stage lines must reach the job's progress")

    def test_a_bridge_that_says_ok_and_writes_nothing_fails_the_job(self):
        interpreter = self.write_stub(
            'printf \'{"status": "ok", "seconds": 36.0, "sample_rate": 48000, "channels": 2}\\n\'\n')
        self.output.parent.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(yue2.Yue2Error) as caught:
            yue2.generate(
                model_dir=self.model_dir, vae_dir=self.vae_dir, output=self.output,
                prompt="p", lyrics="l", mode="off", precision="8bit", duration_seconds=36,
                max_tokens=900, min_tokens=450, steps=32, guidance_scale=1.0, seed=1,
                interpreter=interpreter)
        self.assertIn("missing or empty", str(caught.exception))

    def test_a_bridge_that_reports_failure_carries_its_own_sentence(self):
        interpreter = self.write_stub(
            'printf \'{"status": "failed", "error": "the YuE2 decoder is not installed"}\\n\'\n')
        self.output.parent.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(yue2.Yue2Error) as caught:
            yue2.generate(
                model_dir=self.model_dir, vae_dir=self.vae_dir, output=self.output,
                prompt="p", lyrics="l", mode="off", precision="8bit", duration_seconds=36,
                max_tokens=900, min_tokens=450, steps=32, guidance_scale=1.0, seed=1,
                interpreter=interpreter)
        self.assertIn("decoder is not installed", str(caught.exception))

    def test_an_unreadable_header_fails_rather_than_publishing_a_zero(self):
        interpreter = self.write_stub(
            f'printf x > "{self.output}"\n'
            'printf \'{"status": "ok", "seconds": 0, "sample_rate": 0, "channels": 0}\\n\'\n')
        self.output.parent.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(yue2.Yue2Error) as caught:
            yue2.generate(
                model_dir=self.model_dir, vae_dir=self.vae_dir, output=self.output,
                prompt="p", lyrics="l", mode="off", precision="8bit", duration_seconds=36,
                max_tokens=900, min_tokens=450, steps=32, guidance_scale=1.0, seed=1,
                interpreter=interpreter)
        self.assertIn("header", str(caught.exception))

    def test_a_cancelled_run_is_a_cancellation_not_a_failure(self):
        interpreter = self.write_stub(
            'sleep 30\n'
            'printf \'{"status": "ok", "seconds": 36.0, "sample_rate": 48000, "channels": 2}\\n\'\n')
        cancel = threading.Event()

        def cancel_soon():
            cancel.set()

        threading.Timer(0.4, cancel_soon).start()
        self.output.parent.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(yue2.Yue2Cancelled):
            yue2.generate(
                model_dir=self.model_dir, vae_dir=self.vae_dir, output=self.output,
                prompt="p", lyrics="l", mode="off", precision="8bit", duration_seconds=36,
                max_tokens=900, min_tokens=450, steps=32, guidance_scale=1.0, seed=1,
                interpreter=interpreter, cancel_event=cancel)
        self.assertFalse(self.output.exists(), "a cancelled run must not leave a partial song")


if __name__ == "__main__":
    unittest.main(verbosity=2)
