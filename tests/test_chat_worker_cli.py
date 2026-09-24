"""The packaged Chat worker entry point must validate its bundle and answer `--check`.

`packaging/chat_worker.py` is the only thing that runs inside the bundle, and a bundle it
accepts is a bundle the Hub will try to start. These tests run the real script from a
throwaway bundle.

The packaged Chat worker is deliberately single-model - one engine, one manifest, one
`/v1/models` entry - and that is a contract, not an accident. What the tests pin is that the
contract is *enforced from the bundle's own catalog*: the worker serves whichever single
`chat-mlx` manifest the release carries, whatever it is called, and refuses a bundle that
carries none or more than one, naming the manifests.
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


def chat_manifest(package_id: str, model_id: str) -> dict:
    return {"engine": "chat-mlx", "package_id": package_id, "model_id": model_id,
            "revision": "a" * 40, "files": []}


class ChatWorkerCliTests(unittest.TestCase):
    def _bundle(self, root: Path, *, manifests: tuple[str, ...] = ("qwen3-5-9b-4bit.json",)):
        resources = root / "bundle" / "Contents" / "Resources"
        (resources / "components" / "chat" / "app" / "backend").mkdir(parents=True)
        (resources / "model-manifests").mkdir()
        (resources / "runtimes" / "voice" / "bin").mkdir(parents=True)
        shutil.copyfile(ROOT / "packaging" / "chat_worker.py", resources / "chat_worker.py")
        shutil.copyfile(ROOT / "packaging" / "voice_worker.py", resources / "voice_worker.py")
        (resources / "components" / "chat" / "app" / "backend" / "main.py").write_text("# app\n")
        (resources / "runtimes" / "voice" / "bin" / "python3").write_text("")
        for name in manifests:
            (resources / "model-manifests" / name).write_text(json.dumps(
                chat_manifest(Path(name).stem, f"mlx-community/{Path(name).stem}")))
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
        self.assertIn('"model_manifest": "qwen3-5-9b-4bit.json"', result.stdout)
        self.assertIn('"model_id": "mlx-community/qwen3-5-9b-4bit"', result.stdout)

    def test_a_single_chat_model_under_any_name_is_served(self) -> None:
        """No file name is pinned: the catalog decides, so re-pointing the release works."""
        with tempfile.TemporaryDirectory() as tmp:
            resources, data, models, ready = self._bundle(
                Path(tmp), manifests=("qwen3-6-9b-4bit.json",))
            result = self._run(resources, data, models, ready)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"model_manifest": "qwen3-6-9b-4bit.json"', result.stdout)

    def test_a_second_chat_model_is_refused_naming_both_manifests(self) -> None:
        """Silently serving one of two published models is the defect this forbids."""
        with tempfile.TemporaryDirectory() as tmp:
            resources, data, models, ready = self._bundle(
                Path(tmp), manifests=("qwen3-5-9b-4bit.json", "qwen3-6-9b-4bit.json"))
            result = self._run(resources, data, models, ready)
        self.assertEqual(result.returncode, 2)
        self.assertIn("exactly one model", result.stderr)
        self.assertIn("qwen3-5-9b-4bit.json", result.stderr)
        self.assertIn("qwen3-6-9b-4bit.json", result.stderr)

    def test_a_bundle_without_the_manifest_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resources, data, models, ready = self._bundle(Path(tmp), manifests=())
            result = self._run(resources, data, models, ready)
        self.assertEqual(result.returncode, 2)
        self.assertIn("no chat-mlx manifest", result.stderr)


if __name__ == "__main__":
    unittest.main()
