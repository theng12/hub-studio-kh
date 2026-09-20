"""The first Music package has one installer-owned checkpoint layout."""
from __future__ import annotations

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

    def test_catalog_and_requests_offer_only_the_packaged_model_set(self):
        from components.music.app.backend import catalog, generation

        self.assertEqual([model.repo for model in catalog.CATALOG], [catalog.MAIN_REPO])
        self.assertEqual(catalog.CATALOG[0].dit_tiers, (catalog.TURBO_DIT,))
        self.assertEqual(catalog.CATALOG[0].lm_tiers, (catalog.LM_06B,))
        with self.assertRaisesRegex(ValueError, "Only the acestep-5Hz-lm-0.6B"):
            generation.validate_request({"prompt": "piano", "lm_model": "acestep-5Hz-lm-1.7B"})


class _VerifiedCache:
    def __init__(self, *unused, library=None, **also_unused):
        self.library = Path(library)
        self.root = self.library / "installed" / "model"
        self.root.mkdir(parents=True, exist_ok=True)

    def status(self, **unused):
        return {"state": "cached", "checkpoints_root": str(self.root)}


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
        manager.executor = _FakeEngine()
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

    def test_engine_environment_cannot_override_the_verified_checkpoint_root(self):
        from components.music.app.backend import engine_child

        with mock.patch.dict("os.environ", {
            "ACESTEP_PROJECT_ROOT": "/foreign/source",
            "ACESTEP_CHECKPOINTS_DIR": "/foreign/checkpoints",
        }, clear=False):
            engine_child._configure_environment({
                "engine_root": "/pinned/source",
                "checkpoints_dir": "/verified/package/model",
            })
            self.assertEqual(os.environ["ACESTEP_PROJECT_ROOT"], "/pinned/source")
            self.assertEqual(os.environ["ACESTEP_CHECKPOINTS_DIR"],
                             "/verified/package/model")

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
