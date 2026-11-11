"""Music staging and private supervision stay pinned and bundle-relative."""
from __future__ import annotations

import importlib.util
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def load_packaging(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "packaging" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MusicBundleTests(unittest.TestCase):
    def test_stage_ships_no_ace_step_engine_and_no_music_overlay(self):
        """ACE-Step 1.5 is retired: no engine source, no overlay, and no flag to bring them back."""
        stage = load_packaging("stage_voice")
        self.assertFalse((ROOT / "packaging/music-runtime").exists())
        for retired in ("music_source_digest", "validate_music_overlay", "stage_music_assets",
                        "MUSIC_ENGINE_REVISION", "MUSIC_OVERLAY_SHA256"):
            self.assertFalse(hasattr(stage, retired), retired)
        parameters = inspect.signature(stage.stage).parameters
        self.assertNotIn("music_runtime", parameters)
        self.assertNotIn("music_engine", parameters)
        with tempfile.TemporaryDirectory() as temporary:
            resources = Path(temporary) / "Resources"
            resources.mkdir()
            record = stage._stage_music({"include_hub": True, "resources": resources,
                                         "runtime_hash": "a" * 64})
            # Music has no runtime of its own now: it runs on the Voice runtime, so its identity
            # is that runtime's. The record names no engine source and no overlay.
            self.assertEqual(record["runtime"], "voice")
            self.assertEqual(record["runtime_sha256"], "a" * 64)
            self.assertEqual(set(record), {"source_sha256", "runtime", "runtime_sha256"})
            self.assertTrue((resources / "music_worker.py").is_file())
            self.assertTrue((resources / "components/music/app/backend/main.py").is_file())
            self.assertFalse((resources / "engines").exists())
            self.assertFalse((resources / "runtimes").exists())
            self.assertIsNone(stage._stage_music(
                {"include_hub": False, "resources": resources, "runtime_hash": "a" * 64}))

    def test_hub_music_spec_uses_voice_python_and_needs_no_engine_root(self):
        hub = load_packaging("hub_worker")
        with tempfile.TemporaryDirectory() as temporary:
            resources = Path(temporary)
            runtime = resources / "runtimes/voice"
            (runtime / "bin").mkdir(parents=True)
            (runtime / "bin/python3").write_text("python")
            for relative in ("music_worker.py", "components/music/app/backend/main.py"):
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
            self.assertNotIn("--engine-root", command)
            self.assertIn("--offline", command)

    def test_hub_peer_router_maps_music_to_the_owned_music_worker(self):
        """The Hub's peer routing table must map music to the Music worker.

        The table no longer has to live inline in `standalone.py` - it moved to
        `components/hub/backend/vocabulary.py` so the peer surface, the fleet join and the
        packaged worker table read one vocabulary. Read both, so this asserts the mapping
        rather than the file that happens to hold it today.
        """
        sources = (ROOT / "components/hub/standalone.py",
                   ROOT / "components/hub/backend/vocabulary.py")
        text = "\n".join(path.read_text() for path in sources if path.is_file())
        self.assertIn('"music": "music"', text)

    def test_real_bundle_music_worker_starts_privately_without_models(self):
        python = Path(os.environ.get("STUDIOHUB_TEST_VOICE_PYTHON", ""))
        if not python.is_file():
            self.skipTest("a built Voice Python is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resources = root / "Hub Studio KH.app/Contents/Resources"
            resources.mkdir(parents=True)
            for worker in ("music_worker.py", "voice_worker.py"):
                shutil.copyfile(ROOT / "packaging" / worker, resources / worker)
            shutil.copytree(ROOT / "components/music", resources / "components/music")
            for module in (ROOT / "components").glob("*.py"):
                shutil.copyfile(module, resources / "components" / module.name)
            (resources / "model-manifests").mkdir()
            for manifest in ("stable-audio-3-medium", "yue2-3b-mlx", "yue2-vae"):
                shutil.copyfile(ROOT / f"model-manifests/{manifest}.json",
                                resources / f"model-manifests/{manifest}.json")
            runtime_sha256 = "a" * 64
            (resources / "release-manifest.json").write_text(json.dumps({
                "schema": "studiohub.standalone-release", "schema_version": 1,
                "version": "test", "components": {"music": {
                    "runtime": "voice", "runtime_sha256": runtime_sha256}},
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
            finally:
                if process.poll() is None:
                    process.terminate()
                process.communicate(timeout=15)
            self.assertFalse(ready.exists())


if __name__ == "__main__":
    unittest.main()
