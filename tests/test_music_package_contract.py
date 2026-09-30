"""The Music packages have one installer-owned checkpoint layout, and ACE-Step 1.5 is not among them."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import importlib.util
from pathlib import Path
import subprocess
import sys
import time
import tempfile
import unittest
from unittest import mock
import wave

from components import model_packages


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "model-manifests" / "stable-audio-3-medium.json"

# A third music model, used only by these tests.  It is deliberately unlike both
# shipped models (Stable Audio 3 and YuE2): its own package, revision, runtime contract and caps.
THIRD_REPO = "fixture/third-music-model"
THIRD_PACKAGE_ID = "fixture-third-medium"
THIRD_REVISION = "c" * 40
THIRD_DEFAULTS = {
    "duration_seconds": 12, "steps": 4, "guidance_scale": 2.0,
    "seed": -1, "dit_model": "dit_third",
}
THIRD_LIMITS = {
    "min_duration_seconds": 2, "max_duration_seconds": 90,
    "single_pass_max_seconds": 90, "min_steps": 1, "max_steps": 12,
    "min_guidance": 0.0, "max_guidance": 12.0, "min_seed": -1, "max_seed": 1000,
}
THIRD_QUALIFICATION = {
    "historical_source_workload": "fixture third model",
    "packaged_inference": "fixture only",
}


def third_model():
    """One ModelEntry that states every one of its own per-model facts."""
    from components.music.app.backend import catalog

    return catalog.ModelEntry(
        repo=THIRD_REPO,
        label="Fixture Third Music Model",
        family="fixture-third",
        checkpoint="dit_third",
        size_gb=1.0,
        gated=False,
        package_id=THIRD_PACKAGE_ID,
        package_root="model/third",
        revision=THIRD_REVISION,
        runtime=catalog.RUNTIME_STABLE_AUDIO,
        defaults=dict(THIRD_DEFAULTS),
        limits=dict(THIRD_LIMITS),
        qualification=dict(THIRD_QUALIFICATION),
        min_unified_memory_gb=8,
        sample_rate_hz=44100,
        channels=2,
        license_name="Fixture License",
        license_source="fixture",
        capabilities=("music", "text-to-music"),
        tiers=(),
        long_form=catalog.LongFormStrategy(
            single_pass_max_seconds=90, single_pass_cap_source="fixture",
            beyond_cap="fixture", repaint_task_type="", repaint_mode="",
            repaint_strength=0.0, repaint_latent_crossfade_frames=0,
            repaint_wav_crossfade_sec=0.0, note="fixture"),
        default_lm_model="",
        inference_steps=4,
        guidance_scale=2.0,
    )


class MusicPackageContractTests(unittest.TestCase):
    def test_manifest_is_valid_and_matches_its_filename(self):
        manifest = json.loads(MANIFEST_PATH.read_text())
        self.assertEqual(MANIFEST_PATH.stem, manifest["package_id"])
        self.assertEqual(manifest["engine"], "music-mlx")
        self.assertIs(model_packages.validate_manifest(manifest), manifest)

    def test_stable_audio_package_is_the_pinned_mlx_set(self):
        manifest = json.loads(MANIFEST_PATH.read_text())
        paths = {entry["path"] for entry in manifest["files"]}
        self.assertEqual(paths, {
            "model/mlx/dit_medium_f16.npz", "model/mlx/same_l_decoder_f32.npz",
            "model/mlx/same_l_encoder_f32.npz", "model/mlx/t5gemma_f16.npz"})
        for entry in manifest["files"]:
            self.assertEqual(entry["revision"] if "revision" in entry else manifest["revision"],
                             manifest["revision"])

    def test_cache_resolves_the_installer_receipt_instead_of_an_external_root(self):
        from components.music.app.backend import cache

        manifest = json.loads(MANIFEST_PATH.read_text())
        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory)
            installed = library / "adopted-location" / manifest["revision"]
            (installed / "model" / "mlx").mkdir(parents=True)
            receipt = {"state": "files_verified", "folder": str(installed),
                       "package_id": manifest["package_id"]}
            with mock.patch.object(cache.model_packages, "preflight", return_value=receipt):
                result = cache.MusicCache(library=library, manifest_path=MANIFEST_PATH).status()
        self.assertEqual(result["state"], "cached")
        self.assertEqual(result["checkpoints_root"], str(installed / "model" / "mlx"))

    def test_missing_installer_receipt_never_falls_back_to_a_checkpoint_directory(self):
        from components.music.app.backend import cache

        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory)
            receipt = {"state": "not_installed", "package_id": "stable-audio-3-medium"}
            with mock.patch.object(cache.model_packages, "preflight", return_value=receipt):
                result = cache.MusicCache(library=library, manifest_path=MANIFEST_PATH).status()
        self.assertEqual(result["state"], "missing")
        self.assertNotIn("checkpoints_root", result)

    def test_worker_check_reports_the_package_receipt_without_loading_a_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            models = root / "models"
            data.mkdir()
            models.mkdir()
            token = data / "hub.token"
            token.write_text("a" * 32)
            token.chmod(0o600)
            result = subprocess.run([
                sys.executable, str(ROOT / "packaging" / "music_worker.py"),
                "--data-dir", str(data), "--models-dir", str(models),
                "--ready-file", str(data / "ready.json"),
                "--hub-token-file", str(token), "--check", "--offline",
            ], text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["manifest_package_id"], "stable-audio-3-medium")
        self.assertEqual(report["package"]["state"], "missing")
        self.assertNotIn("checkpoints", report)

    def test_every_catalog_model_publishes_its_own_package_identity_and_revision(self):
        """The invariant, not a membership list.

        The assertion that used to live here pinned ``CATALOG`` to exactly the two
        repos that shipped, so a third model that silently inherited ACE-Step's
        manifest, package id, package root or revision could not fail it.  This
        walks whatever the catalog holds and requires each model's published
        identity to be its own — and unique — so the next model is covered.
        """
        from components.music.app.backend import catalog

        packages: dict[str, str] = {}
        for model in catalog.CATALOG:
            with self.subTest(model=model.repo):
                self.assertTrue(model.package_id, "the entry declares no package id")
                self.assertTrue(model.package_root, "the entry declares no package root")
                self.assertTrue(model.revision, "the entry declares no revision")
                self.assertTrue(model.runtime, "the entry declares no runtime")
                self.assertTrue(model.defaults, "the entry declares no defaults")
                self.assertTrue(model.limits, "the entry declares no limits")
                self.assertTrue(model.qualification, "the entry declares no qualification")
                self.assertEqual(model.manifest_name, f"{model.package_id}.json")
                self.assertEqual(catalog.package_id_for(model.repo), model.package_id)
                self.assertEqual(catalog.manifest_name_for(model.repo), model.manifest_name)
                self.assertEqual(catalog.package_root_for(model.repo), model.package_root)
                self.assertEqual(catalog.revision_for(model.repo), model.revision)
                self.assertEqual(catalog.runtime_for(model.repo), model.runtime)
                self.assertNotIn(
                    model.package_id, packages,
                    f"{model.repo} reuses {packages.get(model.package_id)}'s package")
                packages[model.package_id] = model.repo

    def test_a_model_that_states_no_own_contract_cannot_be_registered(self):
        """A missing per-model fact is a construction error, not a silent fallback."""
        from components.music.app.backend import catalog

        with self.assertRaises(TypeError) as caught:
            catalog.ModelEntry(repo="fixture/no-contract", label="No contract",
                               family="fixture", checkpoint="c", size_gb=1.0,
                               gated=False)
        for field_name in ("package_id", "package_root", "revision", "runtime",
                           "defaults", "limits", "qualification"):
            self.assertIn(field_name, str(caught.exception))

    def test_a_third_music_model_publishes_its_own_contract(self):
        """The regression test for the old per-repo maps and the two-way if/else.

        Before this, a third entry was published with the first model's manifest name,
        package id, package root, revision, duration caps, defaults and
        qualification text while its own label and capabilities were correct —
        partly-right data nobody would spot.
        """
        from components.music.app.backend import catalog

        third = third_model()
        with mock.patch.object(catalog, "CATALOG", catalog.CATALOG + (third,)):
            rows = published_rows(catalog.CATALOG)
            row = rows[third.repo]
            first = rows[catalog.DEFAULT_REPO]

            self.assertEqual(row["name"], third.label)
            self.assertEqual(row["model_revision"], third.revision)
            self.assertEqual(row["qualified_revision"], third.revision)
            self.assertEqual(row["cache"]["package_id"], third.package_id)
            self.assertEqual(row["generation_defaults"], third.defaults)
            self.assertEqual(row["generation_limits"], third.limits)
            self.assertEqual(row["qualification"], third.qualification)
            # Nothing above may be the first model's answer.
            self.assertNotEqual(row["model_revision"], first["model_revision"])
            self.assertNotEqual(row["cache"]["package_id"], first["cache"]["package_id"])
            self.assertNotEqual(row["generation_limits"], first["generation_limits"])
            self.assertNotEqual(row["qualification"], first["qualification"])
            # The identity getters the package cache uses resolve that same entry.
            self.assertEqual(catalog.package_id_for(third.repo), third.package_id)
            self.assertEqual(catalog.manifest_name_for(third.repo), third.manifest_name)
            self.assertEqual(catalog.package_root_for(third.repo), third.package_root)
            self.assertEqual(catalog.revision_for(third.repo), third.revision)

    def test_an_unregistered_music_repo_is_refused_by_name(self):
        """No getter may fall back to the default model for a repo the catalog lacks."""
        from components.music.app.backend import catalog

        for lookup in (catalog.package_id_for, catalog.manifest_name_for,
                       catalog.package_root_for, catalog.revision_for,
                       catalog.runtime_for):
            with self.subTest(lookup=lookup.__name__):
                with self.assertRaises(ValueError) as caught:
                    lookup("fixture/not-registered")
                self.assertIn("fixture/not-registered", str(caught.exception))
                self.assertIn("catalog.py", str(caught.exception))

    def test_a_model_with_an_unknown_runtime_is_refused_by_name(self):
        """The worker refuses a runtime it cannot validate or run, by name."""
        from components.music.app.backend import catalog, generation

        model = dataclasses.replace(
            catalog.CATALOG[0], repo="fixture/other-engine",
            package_id="fixture-other-engine", runtime="a-new-engine")
        with mock.patch.object(catalog, "CATALOG", catalog.CATALOG + (model,)):
            with self.assertRaises(ValueError) as caught:
                generation.validate_request({"repo": model.repo, "prompt": "piano"})
            self.assertIn("a-new-engine", str(caught.exception))
            self.assertIn(model.repo, str(caught.exception))
            row = published_rows(catalog.CATALOG)[model.repo]
        # The published state is the one the worker measured. It used to be flattened to
        # "missing" for every failure, which told an operator to download a package when the real
        # answer was "this worker has no executor for that engine" — the refusal named its own
        # cause and the row threw the name away.
        self.assertEqual(row["cache"]["state"], "runtime_unsupported")
        self.assertIn("a-new-engine", row["cache"]["reason"])
        self.assertIn("generation.py", row["cache"]["reason"])
        self.assertIn("a-new-engine", row["readiness"]["reason"])

    def test_each_model_verifies_its_own_package_manifest(self):
        """A3: the package the worker verifies is the model's own, by manifest name."""
        from components.music.app.backend import catalog, generation

        third = third_model()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "models").mkdir()
            with mock.patch.object(catalog, "CATALOG", catalog.CATALOG + (third,)):
                manager = generation.GenerationManager(
                    root / "data", library_dir=str(root / "models"),
                    runtime_sha256="f" * 64)
                self.addCleanup(manager.close)
                stable = manager.cache_for(catalog.DEFAULT_REPO)
                yue2 = manager.cache_for(catalog.YUE2_REPO)
                self.assertEqual(stable.manifest_path.name, "stable-audio-3-medium.json")
                self.assertEqual(stable.package_id, "stable-audio-3-medium")
                self.assertEqual(yue2.manifest_path.name, "yue2-3b-mlx.json")
                self.assertEqual(yue2.package_id, "yue2-3b-mlx")
                # The third model has no manifest on disk: the worker must say so
                # about *its* package instead of verifying another model's.
                with self.assertRaises(ValueError) as caught:
                    manager.cache_for(third.repo)
                message = str(caught.exception)
                self.assertIn(f"{third.package_id}.json", message)
                self.assertNotIn("stable-audio-3-medium", message)

    def test_a_catalog_that_confuses_two_models_is_refused_at_import(self):
        """The catalog's own guard, so two entries cannot share an identity."""
        from components.music.app.backend import catalog

        shared_package = dataclasses.replace(catalog.CATALOG[0], repo="fixture/shared-package")
        with mock.patch.object(catalog, "CATALOG", catalog.CATALOG + (shared_package,)):
            with self.assertRaises(ValueError) as caught:
                catalog._validate_catalog()
            self.assertIn("fixture/shared-package", str(caught.exception))
            self.assertIn("stable-audio-3-medium", str(caught.exception))
        shared_repo = dataclasses.replace(catalog.CATALOG[0],
                                          package_id="fixture-other-package")
        with mock.patch.object(catalog, "CATALOG", catalog.CATALOG + (shared_repo,)):
            with self.assertRaises(ValueError) as caught:
                catalog._validate_catalog()
            self.assertIn("repeats the repo", str(caught.exception))
        contradictory = dataclasses.replace(
            catalog.CATALOG[0], repo="fixture/contradictory",
            package_id="fixture-contradictory",
            limits={**catalog.CATALOG[0].limits, "single_pass_max_seconds": 999})
        with mock.patch.object(catalog, "CATALOG", catalog.CATALOG + (contradictory,)):
            with self.assertRaises(ValueError) as caught:
                catalog._validate_catalog()
            self.assertIn("single_pass_max_seconds", str(caught.exception))

    def test_the_host_cap_comes_from_the_models_own_tier_table(self):
        """A model without a tier table gets its own cap, not the default model's 380."""
        from components.music.app.backend import catalog, generation

        third = third_model()
        self.assertEqual(third.tiers, ())
        self.assertEqual(generation._tier_cap_for_this_host(third), 90)
        stable = catalog.get_model(catalog.DEFAULT_REPO)
        self.assertEqual(generation._tier_cap_for_this_host(stable), 380)

    def test_shipped_models_keep_their_published_package_and_limits(self):
        from components.music.app.backend import catalog

        stable = catalog.get_model(catalog.STABLE_AUDIO_REPO)
        yue2 = catalog.get_model(catalog.YUE2_REPO)
        self.assertEqual(stable.capabilities, ("music", "instrumental", "text-to-music"))
        self.assertEqual(stable.sample_rate_hz, 44100)
        self.assertEqual(stable.package_id, "stable-audio-3-medium")
        self.assertEqual(stable.manifest_name, "stable-audio-3-medium.json")
        self.assertEqual(stable.revision, catalog.STABLE_AUDIO_REVISION)
        self.assertEqual(stable.limits["max_duration_seconds"], 380)
        self.assertEqual(yue2.package_id, "yue2-3b-mlx")
        self.assertEqual(yue2.revision, catalog.YUE2_REVISION)
        self.assertEqual(yue2.limits["max_duration_seconds"], 360)

    def test_music_is_yue2_and_stable_audio_3_and_nothing_else(self):
        """ACE-Step 1.5 was retired in 0.6.116: not in the catalog, the manifests, or the code.

        Asserted as the two things the owner said the Hub offers, plus the absences that would let
        the retired model back in by a side door: a manifest a library scan would list, a family
        the picker would group under, a repo id a request could name, and the engine child that
        ran it.
        """
        import importlib.util as util
        from components.music.app.backend import catalog, generation

        self.assertEqual({model.repo for model in catalog.CATALOG},
                         {catalog.STABLE_AUDIO_REPO, catalog.YUE2_REPO})
        self.assertNotIn("ace-step-1.5", {family.id for family in catalog.families()})
        self.assertEqual(generation.DEFAULT_MODEL.repo, catalog.STABLE_AUDIO_REPO,
                         "Stable Audio 3 is the default music model")
        self.assertEqual(generation.MODEL_ID, catalog.STABLE_AUDIO_REPO)
        self.assertEqual(generation.validate_request({"prompt": "piano"})["repo"],
                         catalog.STABLE_AUDIO_REPO)
        music_packages = {
            path.stem for path in (ROOT / "model-manifests").glob("*.json")
            if json.loads(path.read_text())["engine"].startswith("music-")}
        self.assertEqual(music_packages, {"stable-audio-3-medium", "yue2-3b-mlx", "yue2-vae"})
        self.assertEqual(list((ROOT / "model-manifests").glob("*ace*")), [])
        for retired in ("ACE-Step/Ace-Step1.5", "ace-step-1-5-turbo"):
            with self.subTest(repo=retired):
                self.assertIsNone(catalog.get_model(retired))
                with self.assertRaisesRegex(ValueError, "is not in catalog.CATALOG"):
                    generation.validate_request({"repo": retired, "prompt": "piano"})
        self.assertFalse(hasattr(generation, "EngineExecutor"))
        self.assertFalse(hasattr(catalog, "RUNTIME_ACE_STEP"))
        self.assertIsNone(util.find_spec("components.music.app.backend.engine_child"))
        rows = published_rows(catalog.CATALOG)
        self.assertEqual(set(rows), {catalog.STABLE_AUDIO_REPO, catalog.YUE2_REPO})
        self.assertNotIn("ace-step", json.dumps(rows).lower(),
                         "a published row still names the retired model")


class _VerifiedCache:
    def __init__(self, *unused, library=None, **also_unused):
        self.library = Path(library)
        self.root = self.library / "installed" / "model"
        self.root.mkdir(parents=True, exist_ok=True)

    def status(self, **unused):
        return {"state": "cached", "checkpoints_root": str(self.root)}


def published_rows(models) -> dict:
    """The real ``/api/catalog`` rows the worker publishes for these entries.

    The worker and the route are the real ones; only the package cache is a
    stand-in that reports every model's package cached.
    """
    if importlib.util.find_spec("fastapi") is None:
        raise unittest.SkipTest("FastAPI is not installed in the system test interpreter")
    from fastapi.testclient import TestClient
    from components.music.app.backend import generation
    from components.music.app.backend.main import create_app

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "models").mkdir()
        with mock.patch.object(generation.cache_module, "MusicCache", _VerifiedCache):
            manager = generation.GenerationManager(
                root / "data", library_dir=str(root / "models"), runtime_sha256="f" * 64)
            try:
                with mock.patch.object(generation.catalog, "CATALOG", tuple(models)):
                    with TestClient(create_app(manager, "secret")) as client:
                        response = client.get(
                            "/api/catalog", headers={"X-Studio-Token": "secret"})
                    assert response.status_code == 200, response.text
                    return {row["repo"]: row for row in response.json()["models"]}
            finally:
                manager.close()


def _fake_generate(*, output, seconds, **unused):
    """Stands in for the MLX subprocess: writes the WAV it was asked for and reports its facts."""
    frames = 480
    with wave.open(str(output), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(44100)
        stream.writeframes(b"\0\0\0\0" * frames)
    data = Path(output).read_bytes()
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "audio_duration_s": frames / 44100, "sample_rate_hz": 44100, "channels": 2,
            "frames": frames, "engine_seconds": 0.01}


class MusicWorkerContractTests(unittest.TestCase):
    def setUp(self):
        from components.music.app.backend import generation

        self.generation = generation
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.models = self.root / "models"
        self.models.mkdir()
        patcher = mock.patch.object(generation.stable_audio, "generate", _fake_generate)
        patcher.start()
        self.addCleanup(patcher.stop)

    def manager(self):
        with mock.patch.object(self.generation.cache_module, "MusicCache", _VerifiedCache):
            manager = self.generation.GenerationManager(
                self.data, library_dir=str(self.models), runtime_sha256="f" * 64)
        self.addCleanup(manager.close)
        return manager

    def test_the_worker_has_no_engine_child_and_holds_no_model(self):
        manager = self.manager()
        self.assertFalse(hasattr(manager, "executor"))
        self.assertEqual(manager.loaded_model_keys(), [])
        self.assertEqual(manager.preflight()["state"], "cached")

    def test_a_request_reaches_stable_audio_with_zero_guidance_undefaulted(self):
        captured = {}

        def capture(**kwargs):
            captured.update(kwargs)
            return _fake_generate(**kwargs)

        manager = self.manager()
        with mock.patch.object(self.generation.stable_audio, "generate", capture):
            job = manager.submit({"prompt": "warm Khmer instrumental", "duration": 1,
                                  "guidance_scale": 0.0})
            self.wait_for(manager, job["id"])
        self.assertEqual(captured["cfg"], 0.0)
        self.assertEqual(captured["seconds"], 1.0)

    @staticmethod
    def wait_for(manager, job_id, state="done"):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = manager.get(job_id)
            if job["state"] == state:
                return job
            time.sleep(0.01)
        raise AssertionError(manager.get(job_id))

    def test_fake_generation_persists_verified_artifact_and_ack_across_restart(self):
        manager = self.manager()
        job = manager.submit({"prompt": "warm Khmer instrumental", "duration": 1})
        done = self.wait_for(manager, job["id"])
        self.assertEqual(done["state"], "done")
        self.assertEqual(done["sample_rate_hz"], 44100)
        self.assertTrue(manager.ack(job["id"])["ok"])

        restarted = self.manager()
        restored = restarted.get(job["id"])
        self.assertEqual(restored["state"], "done")
        self.assertTrue(restored["acknowledged"])

    def test_failed_terminal_persist_discards_published_music_artifacts(self):
        manager = self.manager()
        persist = manager._persist

        def fail_done():
            if any(job.get("state") == "done" for job in manager.jobs.values()):
                raise OSError("receipt disk unavailable")
            persist()

        manager._persist = fail_done
        job = manager.submit({"prompt": "warm Khmer instrumental", "duration": 1})
        failed = self.wait_for(manager, job["id"], state="error")
        self.assertIn("receipt disk unavailable", failed["error"])
        self.assertFalse((manager.outputs / f"{job['id']}.wav").exists())
        self.assertFalse((manager.outputs / f"{job['id']}.json").exists())
        self.assertIsNone(failed["output_url"],
                          "a failed job must not point at an artifact that was discarded")
        self.assertIsNone(failed["sha256"])

    def test_ack_rolls_back_memory_when_its_receipt_cannot_be_persisted(self):
        manager = self.manager()
        job = manager.submit({"prompt": "warm Khmer instrumental", "duration": 1})
        self.wait_for(manager, job["id"])
        persist = manager._persist
        calls = 0

        def fail_once():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("receipt disk unavailable")
            persist()

        manager._persist = fail_once
        with self.assertRaises(OSError):
            manager.ack(job["id"])
        self.assertFalse(manager.get(job["id"])["acknowledged"])
        self.assertTrue(manager.ack(job["id"])["ok"])
        self.assertEqual(calls, 2)

    def test_running_transition_persist_failure_finishes_and_cleans_up(self):
        manager = self.manager()
        persist = manager._persist

        def fail_running():
            if any(job.get("state") == "running" for job in manager.jobs.values()):
                raise OSError("receipt disk unavailable")
            persist()

        manager._persist = fail_running
        job = manager.submit({"prompt": "warm Khmer instrumental", "duration": 1})
        failed = self.wait_for(manager, job["id"], state="error")
        self.assertIn("receipt disk unavailable", failed["error"])
        deadline = time.monotonic() + 1
        while job["id"] in manager.cancel_events and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertNotIn(job["id"], manager.cancel_events)

    def test_unverified_child_exit_is_never_downgraded_to_cancelled(self):
        manager = self.manager()
        job = {"id": "job", "state": "running", "started_at": time.time()}
        manager.jobs["job"] = job
        event = manager.cancel_events["job"] = __import__("threading").Event()
        event.set()
        manager._finish_failure(job, {
            "state": "error", "error": "engine process exit was not verified",
            "error_code": "ENGINE_CHILD_EXIT_UNVERIFIED",
        })
        self.assertEqual(job["state"], "error")
        self.assertEqual(job["error_code"], "ENGINE_CHILD_EXIT_UNVERIFIED")

    def test_corrupt_history_is_preserved_and_startup_fails_closed(self):
        self.data.mkdir()
        ledger = self.data / "jobs.json"
        ledger.write_text("{broken")
        with mock.patch.object(self.generation.cache_module, "MusicCache", _VerifiedCache):
            with self.assertRaisesRegex(RuntimeError, "history"):
                self.generation.GenerationManager(
                    self.data, library_dir=str(self.models), runtime_sha256="f" * 64)
        self.assertEqual(ledger.read_text(), "{broken")

    def test_worker_api_requires_its_owner_token(self):
        if importlib.util.find_spec("fastapi") is None:
            self.skipTest("FastAPI is not installed in the system test interpreter")
        from fastapi.testclient import TestClient
        from components.music.app.backend.main import create_app

        manager = self.manager()
        with TestClient(create_app(manager, "secret")) as client:
            self.assertEqual(client.get("/api/health").status_code, 401)
            response = client.get("/api/health", headers={"X-Studio-Token": "secret"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])


if __name__ == "__main__":
    unittest.main()
