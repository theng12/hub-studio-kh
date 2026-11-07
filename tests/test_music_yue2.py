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
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.music.app.backend import catalog, generation, yue2  # noqa: E402


def yue2_model():
    return catalog.require_model(catalog.YUE2_REPO)


class SecondEngineIsReadableTests(unittest.TestCase):
    """The worker must be able to *read* its second engine's package, not only run it.

    The bug this pins: `MusicCache` verified every manifest against one global engine constant
    (`catalog.ENGINE`, "music-mlx"). YuE2's manifest declares "music-yue2" — correctly, because it
    runs in its own pinned interpreter — so constructing the cache raised, `preflight` reported
    `manifest_unavailable`, and the catalogue route flattened that to `cache.state: "missing"`.
    The app then told the owner to download a package that was installed, verified and eligible.
    """

    def test_every_model_declares_the_engine_its_manifest_carries(self):
        root = Path(__file__).resolve().parents[1]
        for model in catalog.CATALOG:
            with self.subTest(repo=model.repo):
                manifest = json.loads(
                    (root / "model-manifests" / model.manifest_name).read_text())
                self.assertEqual(manifest["engine"], model.manifest_engine,
                                 f"{model.package_id}'s manifest engine is not the one its "
                                 f"catalogue entry declares")
                self.assertEqual(catalog.manifest_engine_for(model.repo), model.manifest_engine)

    def test_the_yue2_package_is_readable_from_the_workers_own_cache(self):
        model = yue2_model()
        with tempfile.TemporaryDirectory() as library:
            cache = generation.cache_module.MusicCache(library=library, repo=model.repo)
            status = cache.status()
        # Nothing is installed in an empty library, and the worker's own word for that is
        # "missing" (it maps `not_installed`). What matters is that the manifest was *read*: an
        # engine mismatch raised before any file was looked for, which is what this pins — the
        # revision comes back, and the reason is about files rather than about the engine.
        self.assertEqual(status["state"], "missing")
        self.assertEqual(status["manifest_revision"], model.revision)
        self.assertEqual(status["package_id"], "yue2-3b-mlx")
        self.assertIn("no files in this library", status["reason"])
        self.assertNotIn("engine", status["reason"].lower())

    def test_preflight_answers_for_the_second_engine_rather_than_refusing_it(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            library = root / "library"
            library.mkdir()
            manager = generation.GenerationManager(root / "data", library_dir=str(library))
            report = manager.preflight(catalog.YUE2_REPO)
        self.assertNotIn(report["state"], ("manifest_unavailable", "runtime_unsupported"),
                         f"the worker refused its own second engine: {report}")
        # The worker's own word for an empty library, and the reason is about the files rather
        # than about the engine — which is the whole difference this test exists to pin.
        self.assertEqual(report["state"], "missing")
        self.assertIn("no files in this library", report["reason"])

    def test_the_companion_check_needs_the_catalog_that_names_the_companion(self):
        """A companion is named by *another* manifest; a check without the catalog cannot see it.

        The second half of the same defect: `yue2-3b-mlx` requires `yue2-vae`, and the worker
        asked `model_packages.preflight` without the catalog map — so a package that was installed,
        hashed and verified answered `dependency_missing` for ever.

        Both packages are installed here for real, from the shipped manifests with one-byte stand-in
        files, because the rule being pinned is the *preflight* one and a stubbed receipt would skip
        the code under test.
        """
        import hashlib
        from components import model_packages

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as scratch:
            work = Path(scratch)
            library, manifests = work / "library", work / "model-manifests"
            library.mkdir()
            manifests.mkdir()
            stand_in = b"x"
            digest = hashlib.sha256(stand_in).hexdigest()
            for name in ("yue2-3b-mlx", "yue2-vae"):
                manifest = json.loads((root / "model-manifests" / f"{name}.json").read_text())
                for entry in manifest["files"]:
                    entry["size"] = len(stand_in)
                    entry["sha256"] = digest
                    entry.pop("urls", None)
                (manifests / f"{name}.json").write_text(json.dumps(manifest))
                installed = library / manifest["package_id"] / manifest["revision"]
                for entry in manifest["files"]:
                    target = installed / entry["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(stand_in)
            catalog_map = model_packages.load_catalog(manifests)
            model_manifest = json.loads((manifests / "yue2-3b-mlx.json").read_text())
            # The companion first: a package may only be adopted once what it requires is.
            for name in ("yue2-vae", "yue2-3b-mlx"):
                verified = model_packages.verify_installed(
                    json.loads((manifests / f"{name}.json").read_text()), library,
                    catalog=catalog_map)
                self.assertEqual(verified["state"], "files_verified", verified)

            without = model_packages.preflight(model_manifest, library)
            with_catalog = model_packages.preflight(model_manifest, library, catalog=catalog_map)

        self.assertEqual(without["state"], "dependency_missing")
        self.assertEqual(without.get("dependency"), "yue2-vae")
        self.assertEqual(with_catalog["state"], "files_verified")

    def test_the_worker_hands_its_package_check_the_companion_catalog(self):
        from components import model_packages

        seen = {}
        real = model_packages.preflight

        def spy(manifest, library, **kwargs):
            seen["catalog"] = kwargs.get("catalog")
            return real(manifest, library, **kwargs)

        with tempfile.TemporaryDirectory() as library, \
                mock.patch.object(generation.cache_module.model_packages, "preflight", spy):
            generation.cache_module.MusicCache(library=library, repo=catalog.YUE2_REPO).status()
        self.assertTrue(seen.get("catalog"),
                        "the worker checked a package without the catalog that names its companion")

    def test_the_route_publishes_the_package_state_it_measured(self):
        """`cache.state` is the package's state, so a manifest problem cannot read as "missing"."""
        source = (Path(__file__).resolve().parents[1]
                  / "components" / "music" / "app" / "backend" / "main.py").read_text()
        self.assertIn('"cache": {"state": files["state"]', source)
        self.assertNotIn('"state": "cached" if cached else "missing"', source)


class DecoderResolvesFromARealLayoutTests(unittest.TestCase):
    """The decoder is found where a staged Mac keeps it, not where a fixture said it would be.

    The defect this pins: every YuE2 job failed with "the YuE2 decoder (yue2-vae) is not
    installed" although the decoder was installed and verified. The worker read a `requires` key
    off the generator's preflight report; that report has never carried one, so the lookup was
    `None` on every Mac and no test noticed, because the only fixture that filled `requires` was
    hand-written. Here the library is laid out the way the installer lays it out - both shipped
    manifests, one-byte stand-in files, receipts written by `verify_installed` - and nothing in
    it names the decoder's location for the worker.
    """

    def setUp(self):
        import hashlib
        from components import model_packages

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        work = Path(self.temp.name)
        root = Path(__file__).resolve().parents[1]
        self.library, self.manifests = work / "library", work / "model-manifests"
        self.library.mkdir()
        self.manifests.mkdir()
        # Every shipped manifest, so a worker built against this folder can construct its default
        # engine too; only the two YuE2 packages are installed.
        for path in (root / "model-manifests").glob("*.json"):
            (self.manifests / path.name).write_text(path.read_text())
        stand_in = b"x"
        digest = hashlib.sha256(stand_in).hexdigest()
        for name in ("yue2-3b-mlx", "yue2-vae"):
            manifest = json.loads((root / "model-manifests" / f"{name}.json").read_text())
            for entry in manifest["files"]:
                entry["size"] = len(stand_in)
                entry["sha256"] = digest
                entry.pop("urls", None)
            (self.manifests / f"{name}.json").write_text(json.dumps(manifest))
            installed = self.library / manifest["package_id"] / manifest["revision"]
            for entry in manifest["files"]:
                target = installed / entry["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(stand_in)
            if name == "yue2-vae":
                self.vae_folder = installed
        catalog_map = model_packages.load_catalog(self.manifests)
        for name in ("yue2-vae", "yue2-3b-mlx"):
            verified = model_packages.verify_installed(
                json.loads((self.manifests / f"{name}.json").read_text()), self.library,
                catalog=catalog_map)
            self.assertEqual(verified["state"], "files_verified", verified)

    def cache(self):
        return generation.cache_module.MusicCache(
            library=self.library, manifest_path=self.manifests / "yue2-3b-mlx.json",
            repo=catalog.YUE2_REPO)

    def test_the_decoder_directory_is_the_one_holding_the_weights_and_the_module(self):
        vae_dir = self.cache().companion_root(catalog.YUE2_VAE_PACKAGE_ID)
        self.assertIsNotNone(vae_dir, "an installed, verified decoder was not found")
        self.assertEqual(vae_dir.resolve(), (self.vae_folder / "model").resolve())
        self.assertTrue((vae_dir / "modeling_vae.py").is_file())
        self.assertTrue((vae_dir / "model.safetensors").is_file())

    def test_a_decoder_that_is_not_installed_is_not_found(self):
        import shutil

        shutil.rmtree(self.library / "yue2-vae")
        self.assertIsNone(self.cache().companion_root(catalog.YUE2_VAE_PACKAGE_ID))

    def test_a_song_job_hands_the_port_that_directory(self):
        """Through the real bridge call, so a missing decoder fails with the owner's own words."""
        import time

        stub = Path(self.temp.name) / "stub-python"
        stub.write_text(
            "#!/bin/sh\n"
            'spec="$4"\n'
            'out=$(sed -n \'s/.*"output": "\\([^"]*\\)".*/\\1/p\' "$spec")\n'
            'vae=$(sed -n \'s/.*"vae_dir": "\\([^"]*\\)".*/\\1/p\' "$spec")\n'
            # The port loads the decoder by path, so the stub demands what the port would.
            'if [ ! -f "$vae/modeling_vae.py" ] || [ ! -f "$vae/model.safetensors" ]; then\n'
            '  printf \'{"status": "failed", "error": "no decoder at %s"}\\n\' "$vae"; exit 1\n'
            "fi\n"
            'printf "fLaC0000" > "$out"\n'
            'printf \'{"status": "ok", "seconds": 36.0, "sample_rate": 48000, "channels": 2}\\n\'\n')
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

        def candidates(name=generation.cache_module.MANIFEST_NAME):
            return [self.manifests / name]

        with tempfile.TemporaryDirectory() as data, \
                mock.patch.object(generation.cache_module, "_candidate_manifests", candidates), \
                mock.patch.object(generation, "yue2_runtime_python", lambda: stub):
            manager = generation.GenerationManager(
                Path(data), library_dir=str(self.library))
            job = manager.submit({"repo": catalog.YUE2_REPO, "prompt": "warm ballad",
                                  "lyrics": "[Verse]\nmorning light\n", "duration_seconds": 36})
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                state = manager.get(job["id"])
                if state["state"] in generation.TERMINAL:
                    break
                time.sleep(0.05)
        self.assertEqual(state["state"], "done", state.get("error"))


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

    def test_language_reaches_style_without_changing_lyrics(self):
        for code, word in (("en", "English"), ("zh", "Mandarin"), ("yue", "Cantonese"),
                           ("ja", "Japanese"), ("ko", "Korean"), ("es", "Spanish"),
                           ("ru", "Russian"), ("French", "French"),
                           ("English, French", "English, French")):
            with self.subTest(language=code):
                params = self.validate(vocal_language=code)
                self.assertEqual(params["vocal_language"], code)
                self.assertEqual(params["prompt"], word + ", " + self.base["prompt"])
                self.assertEqual(params["lyrics"], self.base["lyrics"])
                self.assertEqual(self.validate(**params)["prompt"], params["prompt"])
        self.assertEqual(self.validate()["prompt"], self.base["prompt"])
        self.assertEqual(self.validate(vocal_language=" ")["vocal_language"], "unknown")

    def test_language_is_bounded_before_becoming_style(self):
        for value in (None, 123, "a" * 33, "en\nignore"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.validate(vocal_language=value)
        with self.assertRaises(ValueError):
            self.validate(prompt="a" * generation.PROMPT_MAX_CHARACTERS, vocal_language="en")
        language = {p["name"]: p for p in generation.lane_parameters(self.model)}["vocal_language"]
        self.assertTrue(language["request_only"])
        self.assertEqual(language["enum"], ["en", "zh", "yue", "ja", "ko", "es", "ru"])
        self.assertEqual(language["default"], "en")

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
