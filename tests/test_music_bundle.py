"""Music staging and private supervision stay pinned and bundle-relative."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import Request, urlopen
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_packaging(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "packaging" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MusicBundleTests(unittest.TestCase):
    def test_overlay_builder_is_hash_locked_and_excludes_heavy_base_packages(self):
        lock = (ROOT / "packaging/music-runtime/requirements.lock.txt").read_text()
        for package in ("diskcache", "einops", "einx", "frozendict", "loguru", "toml",
                        "vector-quantize-pytorch"):
            self.assertIn(package + "==", lock)
        locked_names = {
            line.split("==", 1)[0]
            for line in lock.splitlines()
            if line and not line.startswith(("#", " ")) and "==" in line
        }
        self.assertNotIn("torch", locked_names)
        self.assertNotIn("mlx", locked_names)
        build = (ROOT / "packaging/music-runtime/build.sh").read_text()
        self.assertIn("--require-hashes", build)
        self.assertIn("--no-deps", build)

    def test_stage_copies_only_verified_engine_source_and_music_overlay(self):
        stage = load_packaging("stage_voice")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            engine = root / "engine"
            engine.mkdir()
            for relative in ("acestep/__init__.py", "acestep/handler.py", "LICENSE",
                             "pyproject.toml"):
                path = engine / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative)
            subprocess.run(["git", "init", "-q", str(engine)], check=True)
            subprocess.run(["git", "-C", str(engine), "add", "."], check=True)
            subprocess.run(["git", "-C", str(engine), "-c", "user.name=Fixture",
                            "-c", "user.email=fixture@example.invalid", "commit", "-qm",
                            "fixture"], check=True)
            revision = subprocess.check_output(
                ["git", "-C", str(engine), "rev-parse", "HEAD"], text=True).strip()
            digest = stage.music_source_digest(engine)
            overlay = root / "overlay"
            (overlay / "manifest").mkdir(parents=True)
            (overlay / "manifest/music-runtime.json").write_text(json.dumps({
                "schema": "studiohub.music-runtime-overlay", "schema_version": 1,
                "packages": stage.MUSIC_OVERLAY_PACKAGES,
            }))
            (overlay / "loguru").mkdir()
            (overlay / "loguru/__init__.py").write_text("# fixture")
            overlay_digest = stage.tree_digest(overlay)
            resources = root / "Resources"
            resources.mkdir()
            with mock.patch.object(stage, "MUSIC_ENGINE_REVISION", revision), \
                    mock.patch.object(stage, "MUSIC_ENGINE_SOURCE_SHA256", digest), \
                    mock.patch.object(stage, "MUSIC_OVERLAY_SHA256", overlay_digest, create=True):
                identity = stage.stage_music_assets(engine, overlay, resources, "a" * 64)
            self.assertEqual(identity["engine_revision"], revision)
            self.assertEqual(identity["engine_source_sha256"], digest)
            self.assertTrue((resources / "engines/ace-step-1-5/acestep/handler.py").is_file())
            self.assertFalse((resources / "engines/ace-step-1-5/.git").exists())
            self.assertTrue((resources / "runtimes/music/loguru/__init__.py").is_file())
            provenance = json.loads((resources / "engines/ace-step-1-5/studiohub-engine.json").read_text())
            self.assertEqual(provenance["source_sha256"], digest)
            (overlay / "injected.py").write_text("raise RuntimeError('unexpected code')")
            with mock.patch.object(stage, "MUSIC_OVERLAY_SHA256", overlay_digest, create=True):
                with self.assertRaisesRegex(ValueError, "digest"):
                    stage.validate_music_overlay(overlay)

    def test_hub_music_spec_uses_voice_python_and_explicit_bundled_engine(self):
        hub = load_packaging("hub_worker")
        with tempfile.TemporaryDirectory() as temporary:
            resources = Path(temporary)
            runtime = resources / "runtimes/voice"
            (runtime / "bin").mkdir(parents=True)
            (runtime / "bin/python3").write_text("python")
            for relative in ("music_worker.py", "components/music/app/backend/main.py",
                             "engines/ace-step-1-5/studiohub-engine.json",
                             "runtimes/music/manifest/music-runtime.json"):
                path = resources / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}")
            release = {"runtime_sha256": "a" * 64, "components": {
                "music": {"runtime_sha256": "b" * 64}}}
            specs = hub.worker_specs(release, Path(temporary) / "data", runtime, resources)
            music = next(spec for spec in specs if spec["id"] == "music")
            self.assertEqual(music["python"], runtime / "bin/python3")
            self.assertEqual(music["modality"], "music")
            command = hub.worker_command(
                music, resources, Path(temporary) / "data", Path(temporary) / "models",
                Path(temporary) / "token", Path(temporary) / "ready", 123, offline=True)
            self.assertEqual(command[command.index("--engine-root") + 1],
                             str(resources / "engines/ace-step-1-5"))
            self.assertIn("--offline", command)

    def test_hub_peer_router_maps_music_to_the_owned_music_worker(self):
        source = (ROOT / "components/hub/standalone.py").read_text()
        self.assertIn('"music": "music"', source)

    def test_real_bundle_music_worker_starts_privately_without_models(self):
        overlay = Path(os.environ.get("STUDIOHUB_TEST_MUSIC_OVERLAY", ""))
        engine = Path(os.environ.get("STUDIOHUB_TEST_MUSIC_ENGINE", ""))
        python = Path(os.environ.get("STUDIOHUB_TEST_VOICE_PYTHON", ""))
        if not (overlay.is_dir() and engine.is_dir() and python.is_file()):
            self.skipTest("built Music overlay, engine source, and Voice Python are unavailable")
        stage = load_packaging("stage_voice")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resources = root / "Studio Hub.app/Contents/Resources"
            resources.mkdir(parents=True)
            for worker in ("music_worker.py", "voice_worker.py"):
                shutil.copyfile(ROOT / "packaging" / worker, resources / worker)
            shutil.copytree(ROOT / "components/music", resources / "components/music")
            shutil.copyfile(
                ROOT / "components/model_packages.py",
                resources / "components/model_packages.py")
            (resources / "model-manifests").mkdir()
            shutil.copyfile(
                ROOT / "model-manifests/ace-step-1-5-turbo.json",
                resources / "model-manifests/ace-step-1-5-turbo.json")
            identity = stage.stage_music_assets(engine, overlay, resources, "a" * 64)
            runtime_sha256 = identity["runtime_sha256"]
            (resources / "release-manifest.json").write_text(json.dumps({
                "schema": "studiohub.standalone-release", "schema_version": 1,
                "version": "test", "components": {"music": identity},
            }))
            data = root / "data"
            models = root / "models"
            data.mkdir(mode=0o700)
            models.mkdir(mode=0o700)
            token_file = data / "hub.token"
            token_file.write_text("fixture_music_owner_token_1234\n")
            token_file.chmod(0o600)
            ready = data / "music-ready.json"
            command = [
                str(python), "-I", "-B", str(resources / "music_worker.py"),
                "--data-dir", str(data), "--models-dir", str(models),
                "--ready-file", str(ready), "--hub-token-file", str(token_file),
                "--engine-root", str(resources / "engines/ace-step-1-5"),
                "--parent-pid", str(os.getpid()), "--offline",
            ]
            process = subprocess.Popen(
                command, cwd=data, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8"})
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline and not ready.is_file():
                    if process.poll() is not None:
                        stdout, stderr = process.communicate()
                        self.fail(f"Music worker exited early ({process.returncode}):\n{stdout}\n{stderr}")
                    time.sleep(0.1)
                self.assertTrue(ready.is_file(), "Music worker did not publish readiness")
                descriptor = json.loads(ready.read_text())
                request = Request(
                    descriptor["url"] + "/api/health",
                    headers={"Authorization": "Bearer fixture_music_owner_token_1234"})
                with urlopen(request, timeout=10) as response:
                    health = json.load(response)
                self.assertTrue(health["ok"])
                self.assertEqual(health["generation"]["runtime_revision"], runtime_sha256)
                self.assertEqual(health["generation"]["checkpoints"], "missing")
                self.assertFalse(health["generation"]["engine_loaded"])
            finally:
                if process.poll() is None:
                    process.terminate()
                process.communicate(timeout=15)
            self.assertFalse(ready.exists())
            staged_overlay = resources / "runtimes/music"
            staged_overlay.chmod(0o700)
            (staged_overlay / "injected.py").write_text("raise RuntimeError('unexpected code')")
            tampered = subprocess.run(
                [*command, "--check"], cwd=data, text=True, capture_output=True,
                env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8"})
            self.assertNotEqual(tampered.returncode, 0)
            self.assertIn("overlay digest", tampered.stderr.lower())


if __name__ == "__main__":
    unittest.main()
