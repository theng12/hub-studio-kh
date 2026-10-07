"""The packaged Chat worker entry point must validate its bundle and answer `--check`.

`packaging/chat_worker.py` is the only thing that runs inside the bundle, and a bundle it
accepts is a bundle the Hub will try to start. These tests run the real script from a
throwaway bundle.

The packaged Chat worker validates the whole manifest-driven catalog without importing MLX or
loading weights. Metadata for pending models may coexist with one eligible default; ambiguous,
malformed and empty catalogs are refused before the server starts.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def chat_manifest(package_id: str, model_id: str, *, default: bool | None = None,
                  allowed: bool = True) -> dict:
    manifest = {"schema_version": 1, "engine": "chat-mlx", "package_id": package_id,
                "model_id": model_id, "revision": "a" * 40, "minimum_macos": "14.0",
                "production_allowed": allowed,
                "files": [{"path": "model/config.json", "size": 2,
                           "sha256": "f" * 64, "urls": []}]}
    if default is not None:
        manifest["default"] = default
    return manifest


class ChatWorkerCliTests(unittest.TestCase):
    def _bundle(self, root: Path, *, manifests: tuple[str, ...] = ("qwen3-5-9b-4bit.json",),
                raw_manifests: dict[str, dict | str] | None = None):
        resources = root / "bundle" / "Contents" / "Resources"
        (resources / "components" / "chat" / "app" / "backend").mkdir(parents=True)
        (resources / "model-manifests").mkdir()
        (resources / "runtimes" / "voice" / "bin").mkdir(parents=True)
        shutil.copyfile(ROOT / "packaging" / "chat_worker.py", resources / "chat_worker.py")
        shutil.copyfile(ROOT / "packaging" / "voice_worker.py", resources / "voice_worker.py")
        (resources / "components" / "chat" / "app" / "backend" / "main.py").write_text("# app\n")
        for relative in (
                "components/model_packages.py",
                "components/chat/__init__.py",
                "components/chat/app/__init__.py",
                "components/chat/app/backend/__init__.py",
                "components/chat/app/backend/model_manager.py",
                "components/chat/app/backend/llm_engine.py"):
            target = resources / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        (resources / "runtimes" / "voice" / "bin" / "python3").write_text("")
        for index, name in enumerate(manifests):
            (resources / "model-manifests" / name).write_text(json.dumps(
                chat_manifest(Path(name).stem, f"mlx-community/{Path(name).stem}",
                              default=index == 0, allowed=index == 0)))
        for name, value in (raw_manifests or {}).items():
            (resources / "model-manifests" / name).write_text(
                value if isinstance(value, str) else json.dumps(value))
        (resources / "release-manifest.json").write_text(json.dumps({
            "schema": "studiohub.standalone-release", "schema_version": 1, "version": "0.0.0",
            "components": {"chat": {"runtime_sha256": "a" * 64}},
        }))
        data = root / "data"
        data.mkdir()
        token = data / ".hub_token"
        token.write_text("t" * 40 + "\n")
        os.chmod(token, 0o600)
        models = root / "models"
        models.mkdir()
        return resources, data, models, data / "ready.json"

    def _run(self, resources: Path, data: Path, models: Path, ready: Path):
        return subprocess.run(
            [sys.executable, str(resources / "chat_worker.py"),
             "--data-dir", str(data), "--models-dir", str(models),
             "--ready-file", str(ready), "--hub-token-file", str(data / ".hub_token"),
             "--check"],
            capture_output=True, text=True, timeout=60)

    def test_check_reports_the_chat_component_and_the_model_it_serves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resources, data, models, ready = self._bundle(Path(tmp))
            result = self._run(resources, data, models, ready)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"component": "chat"', result.stdout)
        self.assertIn("a" * 64, result.stdout)
        self.assertIn('"offline": false', result.stdout)
        self.assertIn('"model_manifests": ["qwen3-5-9b-4bit.json"]', result.stdout)
        self.assertIn('"model_ids": ["mlx-community/qwen3-5-9b-4bit"]', result.stdout)
        self.assertIn('"default_model_id": "mlx-community/qwen3-5-9b-4bit"', result.stdout)

    def test_a_single_chat_model_under_any_name_is_served(self) -> None:
        """No file name is pinned: the catalog decides, so re-pointing the release works."""
        with tempfile.TemporaryDirectory() as tmp:
            resources, data, models, ready = self._bundle(
                Path(tmp), manifests=("qwen3-6-9b-4bit.json",))
            result = self._run(resources, data, models, ready)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"model_manifests": ["qwen3-6-9b-4bit.json"]', result.stdout)

    def test_pending_models_coexist_with_one_eligible_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resources, data, models, ready = self._bundle(
                Path(tmp), manifests=("qwen3-5-9b-4bit.json", "qwen3-6-9b-4bit.json"))
            result = self._run(resources, data, models, ready)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("qwen3-5-9b-4bit.json", result.stdout)
        self.assertIn("qwen3-6-9b-4bit.json", result.stdout)
        self.assertIn('"default_model_id": "mlx-community/qwen3-5-9b-4bit"', result.stdout)

    def test_ambiguous_and_duplicate_defaults_are_refused(self) -> None:
        first = chat_manifest("one", "org/one")
        second = chat_manifest("two", "org/two")
        with tempfile.TemporaryDirectory() as tmp:
            resources, data, models, ready = self._bundle(
                Path(tmp), manifests=(), raw_manifests={"one.json": first, "two.json": second})
            result = self._run(resources, data, models, ready)
        self.assertEqual(result.returncode, 2)
        self.assertIn("no explicit default", result.stderr)

        first["default"] = True
        second["default"] = True
        with tempfile.TemporaryDirectory() as tmp:
            resources, data, models, ready = self._bundle(
                Path(tmp), manifests=(), raw_manifests={"one.json": first, "two.json": second})
            result = self._run(resources, data, models, ready)
        self.assertEqual(result.returncode, 2)
        self.assertIn("explicit defaults", result.stderr)

    def test_a_bundle_without_the_manifest_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resources, data, models, ready = self._bundle(Path(tmp), manifests=())
            result = self._run(resources, data, models, ready)
        self.assertEqual(result.returncode, 2)
        self.assertIn("no chat-mlx manifest", result.stderr)

    def test_a_malformed_manifest_is_refused_by_name(self) -> None:
        malformed = chat_manifest("broken", "org/broken", default=True)
        malformed["files"] = []
        with tempfile.TemporaryDirectory() as tmp:
            resources, data, models, ready = self._bundle(
                Path(tmp), manifests=(), raw_manifests={"broken.json": malformed})
            result = self._run(resources, data, models, ready)
        self.assertEqual(result.returncode, 2)
        self.assertIn("broken.json", result.stderr)
        self.assertIn("complete file manifest", result.stderr)


if __name__ == "__main__":
    unittest.main()
