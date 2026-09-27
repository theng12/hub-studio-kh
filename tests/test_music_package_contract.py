"""The first Music package has one installer-owned checkpoint layout."""
from __future__ import annotations

import dataclasses
import json
import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
import tempfile
import unittest
from unittest import mock
import wave

from components import model_packages


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "model-manifests" / "ace-step-1-5-turbo.json"

# A third music model, used only by these tests.  It is deliberately unlike both
# shipped models: its own package, revision, runtime contract and caps.
THIRD_REPO = "fixture/third-music-model"
THIRD_PACKAGE_ID = "fixture-third-medium"
THIRD_REVISION = "c" * 40
THIRD_DEFAULTS = {
    "duration_seconds": 12, "steps": 4, "guidance_scale": 2.0, "shift": 1.0,
    "seed": -1, "instrumental": True, "lyrics": "[Instrumental]",
    "vocal_language": "unknown", "dit_model": "dit_third", "lm_model": "third-lm",
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
        runtime=catalog.RUNTIME_ACE_STEP,
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
        default_lm_model="third-lm",
        inference_steps=4,
        guidance_scale=2.0,
    )


class MusicPackageContractTests(unittest.TestCase):
    def test_manifest_is_valid_and_matches_its_filename(self):
        manifest = json.loads(MANIFEST_PATH.read_text())
        self.assertEqual(MANIFEST_PATH.stem, manifest["package_id"])
        self.assertEqual(manifest["engine"], "music-mlx")
        self.assertIs(model_packages.validate_manifest(manifest), manifest)

    def test_first_package_contains_only_the_proven_turbo_and_06b_set(self):
        manifest = json.loads(MANIFEST_PATH.read_text())
        paths = {entry["path"] for entry in manifest["files"]}
        self.assertTrue(paths)
        self.assertTrue(all(path.startswith("model/") for path in paths))
        self.assertTrue(any(path.startswith("model/acestep-v15-turbo/") for path in paths))
        self.assertTrue(any(path.startswith("model/acestep-5Hz-lm-0.6B/") for path in paths))
        self.assertFalse(any("xl" in path.casefold() or "1.7b" in path.casefold()
                             or "4b" in path.casefold() for path in paths))
        for entry in manifest["files"]:
            if entry.get("provider", "huggingface") == "huggingface":
                self.assertFalse(entry["source_path"].startswith("model/"))

    def test_package_pins_the_engine_model_code_that_upstream_would_sync(self):
        manifest = json.loads(MANIFEST_PATH.read_text())
        files = {entry["path"]: entry for entry in manifest["files"]}
        expected = {
            "model/acestep-v15-turbo/configuration_acestep_v15.py":
                (212, "5f15aa79fe793bba3c00b6992dd193a269954950603d5031ecd7703b0fa69da1"),
            "model/acestep-v15-turbo/modeling_acestep_v15_turbo.py":
                (108712, "71b22e01b490bd9e149e4c0c5da2e1f512126f3ab3316e0ae97779af84d19512"),
        }
        self.assertEqual({path for path in files if path.endswith(".py")}, set(expected))
        for path, (size, digest) in expected.items():
            self.assertEqual((files[path]["size"], files[path]["sha256"]), (size, digest))
            self.assertEqual(files[path]["revision"], "ca1e85fe9430179831e6bc6be790c332190a3866")

    def test_cache_resolves_the_installer_receipt_instead_of_an_external_root(self):
        from components.music.app.backend import cache

        manifest = json.loads(MANIFEST_PATH.read_text())
        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory)
            installed = library / "adopted-location" / manifest["revision"]
            (installed / "model").mkdir(parents=True)
            receipt = {"state": "files_verified", "folder": str(installed),
                       "package_id": manifest["package_id"]}
            with mock.patch.object(cache.model_packages, "preflight", return_value=receipt):
                result = cache.MusicCache(library=library, manifest_path=MANIFEST_PATH).status()
        self.assertEqual(result["state"], "cached")
        self.assertEqual(result["checkpoints_root"], str(installed / "model"))

    def test_missing_installer_receipt_never_falls_back_to_a_checkpoint_directory(self):
        from components.music.app.backend import cache

        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory)
            receipt = {"state": "not_installed", "package_id": "ace-step-1-5-turbo"}
            with mock.patch.object(cache.model_packages, "preflight", return_value=receipt):
                result = cache.MusicCache(library=library, manifest_path=MANIFEST_PATH).status()
        self.assertEqual(result["state"], "missing")
        self.assertNotIn("checkpoints_root", result)

    def test_worker_check_reports_the_package_receipt_without_loading_a_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            models = root / "models"
            engine = root / "engine"
            data.mkdir()
            models.mkdir()
            engine.mkdir()
            token = data / "hub.token"
            token.write_text("a" * 32)
            token.chmod(0o600)
            result = subprocess.run([
                sys.executable, str(ROOT / "packaging" / "music_worker.py"),
                "--data-dir", str(data), "--models-dir", str(models),
                "--engine-root", str(engine), "--ready-file", str(data / "ready.json"),
                "--hub-token-file", str(token), "--check", "--offline",
            ], text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["manifest_package_id"], "ace-step-1-5-turbo")
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

        Before this, a third entry was published with ACE-Step's manifest name,
        package id, package root, revision, duration caps, defaults and
        qualification text while its own label and capabilities were correct —
        partly-right data nobody would spot.
        """
        from components.music.app.backend import catalog

        third = third_model()
        with mock.patch.object(catalog, "CATALOG", catalog.CATALOG + (third,)):
            rows = published_rows(catalog.CATALOG)
            row = rows[third.repo]
            first = rows[catalog.MAIN_REPO]

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
        """No getter may fall back to ACE-Step for a repo the catalog lacks."""
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
                ace = manager.cache_for(catalog.MAIN_REPO)
                stable = manager.cache_for(catalog.STABLE_AUDIO_REPO)
                self.assertEqual(ace.manifest_path.name, "ace-step-1-5-turbo.json")
                self.assertEqual(ace.package_id, "ace-step-1-5-turbo")
                self.assertEqual(stable.manifest_path.name, "stable-audio-3-medium.json")
                self.assertEqual(stable.package_id, "stable-audio-3-medium")
                # The third model has no manifest on disk: the worker must say so
                # about *its* package instead of verifying ACE-Step's.
                with self.assertRaises(ValueError) as caught:
                    manager.cache_for(third.repo)
                message = str(caught.exception)
                self.assertIn(f"{third.package_id}.json", message)
                self.assertNotIn("ace-step-1-5-turbo", message)

    def test_a_catalog_that_confuses_two_models_is_refused_at_import(self):
        """The catalog's own guard, so two entries cannot share an identity."""
        from components.music.app.backend import catalog

        shared_package = dataclasses.replace(catalog.CATALOG[0], repo="fixture/shared-package")
        with mock.patch.object(catalog, "CATALOG", catalog.CATALOG + (shared_package,)):
            with self.assertRaises(ValueError) as caught:
                catalog._validate_catalog()
            self.assertIn("fixture/shared-package", str(caught.exception))
            self.assertIn("ace-step-1-5-turbo", str(caught.exception))
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
        """A model without ACE-Step's tiers gets its own cap, not ACE-Step's 480."""
        from components.music.app.backend import catalog, generation

        third = third_model()
        self.assertEqual(third.tiers, ())
        self.assertEqual(generation._tier_cap_for_this_host(third), 90)
        ace = catalog.get_model(catalog.MAIN_REPO)
        self.assertIn(generation._tier_cap_for_this_host(ace), {240, 480, 600})

    def test_shipped_models_keep_their_published_package_and_limits(self):
        from components.music.app.backend import catalog, generation

        ace = catalog.get_model(catalog.MAIN_REPO)
        stable = catalog.get_model(catalog.STABLE_AUDIO_REPO)
        self.assertEqual(catalog.CATALOG[0].dit_tiers, (catalog.TURBO_DIT,))
        self.assertEqual(catalog.CATALOG[0].lm_tiers, (catalog.LM_06B,))
        self.assertEqual(ace.package_id, "ace-step-1-5-turbo")
        self.assertEqual(ace.manifest_name, "ace-step-1-5-turbo.json")
        self.assertEqual(ace.revision, catalog.MAIN_REVISION)
        self.assertEqual(ace.limits["max_duration_seconds"], 600.0)
        self.assertEqual(ace.limits["single_pass_max_seconds"], 480)
        self.assertEqual(stable.capabilities, ("music", "instrumental", "text-to-music"))
        self.assertEqual(stable.sample_rate_hz, 44100)
        self.assertEqual(stable.package_id, "stable-audio-3-medium")
        self.assertEqual(stable.revision, catalog.STABLE_AUDIO_REVISION)
        self.assertEqual(stable.limits["max_duration_seconds"], 380)
        with self.assertRaisesRegex(ValueError, "Only the acestep-5Hz-lm-0.6B"):
            generation.validate_request({"prompt": "piano", "lm_model": "acestep-5Hz-lm-1.7B"})


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
    stand-in that reports every model's package cached, and the ACE-Step engine
    source check is stubbed so no checkout is needed.
    """
    if importlib.util.find_spec("fastapi") is None:
        raise unittest.SkipTest("FastAPI is not installed in the system test interpreter")
    from fastapi.testclient import TestClient
    from components.music.app.backend import generation
    from components.music.app.backend.main import create_app

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "models").mkdir()
        (root / "engine").mkdir()
        with mock.patch.object(generation.cache_module, "MusicCache", _VerifiedCache):
            manager = generation.GenerationManager(
                root / "data", engine_root=str(root / "engine"),
                library_dir=str(root / "models"), runtime_sha256="f" * 64)
            manager._engine_contract_reason = lambda: None
            try:
                with mock.patch.object(generation.catalog, "CATALOG", tuple(models)):
                    with TestClient(create_app(manager, "secret")) as client:
                        response = client.get(
                            "/api/catalog", headers={"X-Studio-Token": "secret"})
                    assert response.status_code == 200, response.text
                    return {row["repo"]: row for row in response.json()["models"]}
            finally:
                manager.close()


class _FakeEngine:
    child_pid = None

    def __init__(self):
        self.engine = {}

    def run(self, job_id, payload, cancel_event, on_progress=None):
        if on_progress:
            on_progress(0.5, "synthesis")
        output = Path(payload["staging_dir"]) / "result.wav"
        with wave.open(str(output), "wb") as stream:
            stream.setnchannels(2)
            stream.setsampwidth(2)
            stream.setframerate(48000)
            stream.writeframes(b"\0\0\0\0" * 480)
        return {"state": "done", "path": str(output), "sample_rate": 48000,
                "engine_seconds": 0.01, "peak_rss_bytes": 1}

    def close(self):
        return None


class MusicWorkerContractTests(unittest.TestCase):
    def setUp(self):
        from components.music.app.backend import generation

        self.generation = generation
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.models = self.root / "models"
        self.engine = self.root / "engine"
        self.models.mkdir()
        self.engine.mkdir()

    def manager(self):
        with mock.patch.object(self.generation.cache_module, "MusicCache", _VerifiedCache):
            manager = self.generation.GenerationManager(
                self.data, engine_root=str(self.engine), library_dir=str(self.models),
                runtime_sha256="f" * 64)
        manager._engine_contract_reason = lambda: None
        # The default model's resident child is the fake; the map is how _run finds
        # it, and close() still closes everything the manager owns.
        manager._executors[self.generation.catalog.MAIN_REPO] = _FakeEngine()
        self.addCleanup(manager.close)
        return manager

    def test_readiness_refuses_an_arbitrary_engine_directory(self):
        with mock.patch.object(self.generation.cache_module, "MusicCache", _VerifiedCache):
            manager = self.generation.GenerationManager(
                self.data, engine_root=str(self.engine), library_dir=str(self.models),
                runtime_sha256="f" * 64)
        self.addCleanup(manager.close)
        result = manager.preflight()
        self.assertEqual(result["state"], "engine_source_invalid")
        self.assertIn("pinned ACE-Step", result["reason"])

    def test_readiness_rejects_an_acestep_import_outside_the_engine_root(self):
        for relative in ("acestep/handler.py", "acestep/inference.py",
                         "acestep/llm_inference.py",
                         "acestep/models/turbo/configuration_acestep_v15.py",
                         "acestep/models/turbo/modeling_acestep_v15_turbo.py"):
            path = self.engine / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("placeholder")
        manager = self.manager()
        manager._engine_contract_reason = self.generation.GenerationManager._engine_contract_reason.__get__(manager)
        spec = mock.Mock(origin=str(self.root / "other" / "acestep" / "__init__.py"),
                         submodule_search_locations=[str(self.root / "other" / "acestep")])
        with mock.patch.object(self.generation.importlib.util, "find_spec", return_value=spec), \
                mock.patch.object(self.generation, "_file_sha256",
                                  side_effect=self.generation.catalog.ENGINE_MODEL_CODE_SHA256.values()), \
                mock.patch.object(self.generation.subprocess, "run", side_effect=[
                    mock.Mock(stdout=self.generation.catalog.ENGINE_REVISION + "\n"),
                    mock.Mock(stdout=""),
                ]):
            self.assertIn("outside", manager._engine_contract_reason())

    def test_bundled_engine_source_is_content_verified_without_git_metadata(self):
        for relative in ("acestep/__init__.py", "acestep/handler.py",
                         "acestep/inference.py", "acestep/llm_inference.py",
                         "acestep/models/turbo/configuration_acestep_v15.py",
                         "acestep/models/turbo/modeling_acestep_v15_turbo.py",
                         "LICENSE", "pyproject.toml"):
            path = self.engine / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(relative)
        digest = self.generation.source_tree_digest(self.engine)
        (self.engine / "studiohub-engine.json").write_text(json.dumps({
            "schema": "studiohub.music-engine-source", "schema_version": 1,
            "repo": self.generation.catalog.ENGINE_REPO,
            "revision": self.generation.catalog.ENGINE_REVISION,
            "source_sha256": digest,
        }))
        spec = mock.Mock(origin=str(self.engine / "acestep/__init__.py"),
                         submodule_search_locations=[str(self.engine / "acestep")])
        with mock.patch.object(self.generation.catalog, "ENGINE_SOURCE_SHA256", digest), \
                mock.patch.object(self.generation.importlib.util, "find_spec", return_value=spec), \
                mock.patch.object(self.generation, "_file_sha256",
                                  side_effect=self.generation.catalog.ENGINE_MODEL_CODE_SHA256.values()):
            self.assertIsNone(self.generation.engine_contract_reason(str(self.engine)))
            (self.engine / "acestep/handler.py").write_text("changed")
            self.assertIn("digest", self.generation.engine_contract_reason(str(self.engine)))

    def test_bounded_engine_precheck_never_requires_the_excluded_17b_planner(self):
        from components.music.app.backend import engine_child

        checkpoints = self.root / "checkpoints"
        for component in ("acestep-v15-turbo", "vae", "Qwen3-Embedding-0.6B",
                          "acestep-5Hz-lm-0.6B"):
            path = checkpoints / component
            path.mkdir(parents=True)
            (path / "model.safetensors").write_bytes(b"weight")
        self.assertIsNone(engine_child._bounded_model_precheck(
            checkpoint_path=checkpoints, config_path="acestep-v15-turbo"))
        self.assertFalse((checkpoints / "acestep-5Hz-lm-1.7B").exists())

    def test_engine_environment_separates_mutable_state_from_verified_inputs(self):
        from components.music.app.backend import engine_child

        with mock.patch.dict("os.environ", {
            "ACESTEP_PROJECT_ROOT": "/foreign/source",
            "ACESTEP_CHECKPOINTS_DIR": "/foreign/checkpoints",
        }, clear=False):
            engine_child._configure_environment({
                "engine_root": "/pinned/source",
                "checkpoints_dir": "/verified/package/model",
                "state_root": "/private/music/engine-state",
            })
            self.assertEqual(os.environ["ACESTEP_PROJECT_ROOT"],
                             "/private/music/engine-state")
            self.assertEqual(os.environ["ACESTEP_CHECKPOINTS_DIR"],
                             "/verified/package/model")

    def test_engine_parent_monitor_kills_its_isolated_group_on_owner_loss(self):
        from components.music.app.backend import engine_child

        with mock.patch.object(engine_child.os, "getppid", return_value=1), \
                mock.patch.object(engine_child.os, "getpgrp", return_value=4321), \
                mock.patch.object(engine_child.os, "getpid", return_value=4321), \
                mock.patch.object(engine_child.os, "killpg") as kill_group:
            engine_child._monitor_parent(1234, poll_seconds=0)
        kill_group.assert_called_once_with(4321, engine_child.signal.SIGKILL)

    def test_engine_executor_records_the_worker_owner(self):
        with mock.patch.object(self.generation.cache_module, "MusicCache", _VerifiedCache):
            manager = self.generation.GenerationManager(
                self.data, engine_root=str(self.engine), library_dir=str(self.models),
                runtime_sha256="f" * 64)
        self.addCleanup(manager.close)
        self.assertEqual(manager.executor.engine["parent_pid"], os.getpid())
        self.assertEqual(Path(manager.executor.engine["state_root"]),
                         self.data / "engine-state")

    def test_zero_guidance_and_shift_are_forwarded_without_defaulting(self):
        from components.music.app.backend import engine_child

        class Params:
            def __init__(self, **values):
                captured.update(values)

        class Config:
            def __init__(self, **unused):
                pass

        class Result:
            success = True
            audios = [{"path": "/tmp/result.wav", "sample_rate": 48000}]

        captured = {}
        fake = type(sys)("acestep.inference")
        fake.GenerationConfig = Config
        fake.GenerationParams = Params
        fake.generate_music = lambda *args, **kwargs: Result()
        payload = {"job_id": "j", "prompt": "p", "duration": 1, "seed": 1,
                   "staging_dir": "/tmp", "guidance_scale": 0.0, "shift": 0.0}
        with mock.patch.dict(sys.modules, {"acestep.inference": fake}):
            engine_child._generate(object(), object(), payload,
                                   mock.Mock(is_set=lambda: False), mock.Mock())
        self.assertEqual(captured["guidance_scale"], 0.0)
        self.assertEqual(captured["shift"], 0.0)

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
        self.assertEqual(done["sample_rate_hz"], 48000)
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
        self.assertEqual(failed["error_code"], "JOB_PERSISTENCE_FAILED")
        self.assertFalse((manager.outputs / f"{job['id']}.wav").exists())
        self.assertFalse((manager.outputs / f"{job['id']}.json").exists())

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
        self.assertEqual(failed["error_code"], "JOB_PERSISTENCE_FAILED")
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
                    self.data, engine_root=str(self.engine), library_dir=str(self.models),
                    runtime_sha256="f" * 64)
        self.assertEqual(ledger.read_text(), "{broken")

    def test_truncated_wav_payload_is_rejected(self):
        path = self.root / "truncated.wav"
        fmt = struct.pack("<HHIIHH", 1, 2, 48000, 192000, 4, 16)
        path.write_bytes(b"RIFF" + struct.pack("<I", 36 + 1920) + b"WAVE" +
                         b"fmt " + struct.pack("<I", len(fmt)) + fmt +
                         b"data" + struct.pack("<I", 1920))
        with self.assertRaisesRegex(RuntimeError, "truncated"):
            self.generation.inspect_wav(path)

    def test_cleanup_failure_remains_explicit_after_restart(self):
        manager = self.manager()
        persist = manager._persist

        def fail_done():
            if any(job.get("state") == "done" for job in manager.jobs.values()):
                raise OSError("receipt disk unavailable")
            persist()

        manager._persist = fail_done
        manager._discard_published = lambda paths: (False, [str(path) for path in paths])
        job = manager.submit({"prompt": "warm Khmer instrumental", "duration": 1})
        failed = self.wait_for(manager, job["id"], state="error")
        self.assertEqual(failed["error_code"], "JOB_PERSISTENCE_UNCERTAIN")
        self.assertEqual(len(failed["unverified_artifact_paths"]), 2)
        self.assertIsNone(failed["output_url"])

        restarted = self.manager()
        restored = restarted.get(job["id"])
        self.assertEqual(restored["error_code"], "JOB_PERSISTENCE_UNCERTAIN")
        self.assertEqual(restored["unverified_artifact_paths"],
                         failed["unverified_artifact_paths"])

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
