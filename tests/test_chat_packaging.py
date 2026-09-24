"""Packaging contract for the local Chat worker.

The chat spec in `hub_worker.worker_specs` is deliberately **guarded** on the release carrying
a `chat` component, so this can land before ST-13 frees the shared staging/manifest files:
until `stage_voice.py` records the component, the spec is inert and the packaged app is
unchanged. These tests pin both halves of that contract.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
import hub_worker  # noqa: E402


class ChatWorkerSpecTests(unittest.TestCase):
    def _resources(self, root: Path, *, source: bool = True, manifest: bool = True) -> Path:
        resources = root / "Resources"
        (resources / "model-manifests").mkdir(parents=True)
        backend = resources / "components" / "chat" / "app" / "backend"
        backend.mkdir(parents=True)
        (resources / "chat_worker.py").write_text("# chat worker\n")
        if source:
            (backend / "main.py").write_text("# chat app\n")
        if manifest:
            (resources / "model-manifests" / "qwen3-5-9b-4bit.json").write_text("{}\n")
        return resources

    def _specs(self, release: dict, resources: Path):
        release = {"runtime_sha256": "b" * 64, **release}
        runtime = resources / "runtimes" / "voice"
        data = resources.parent / "data"
        return hub_worker.worker_specs(release, data, runtime, resources)

    def test_chat_spec_is_added_only_when_the_release_carries_the_component(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resources = self._resources(Path(tmp))
            without = self._specs({"components": {}}, resources)
            self.assertEqual([spec["id"] for spec in without], ["voice"])
            with_chat = self._specs(
                {"components": {"chat": {"runtime_sha256": "a" * 64}}}, resources)
        ids = [spec["id"] for spec in with_chat]
        self.assertEqual(ids, ["voice", "chat"])
        chat = next(spec for spec in with_chat if spec["id"] == "chat")
        self.assertEqual(chat["modality"], "chat")
        self.assertEqual(chat["runtime_id"], "a" * 64)
        self.assertEqual(chat["python"], resources / "runtimes" / "voice" / "bin" / "python3")
        self.assertEqual(chat["extra_args"], [])

    def test_an_incomplete_chat_bundle_is_refused(self) -> None:
        for source, manifest in ((False, True), (True, False)):
            with self.subTest(source=source, manifest=manifest):
                with tempfile.TemporaryDirectory() as tmp:
                    resources = self._resources(Path(tmp), source=source, manifest=manifest)
                    with self.assertRaisesRegex(ValueError, "Incomplete Chat worker"):
                        self._specs({"components": {"chat": {"runtime_sha256": "a" * 64}}},
                                    resources)

    def test_a_bad_runtime_identity_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resources = self._resources(Path(tmp))
            with self.assertRaisesRegex(ValueError, "Incomplete Chat worker"):
                self._specs({"components": {"chat": {"runtime_sha256": "short"}}}, resources)


if __name__ == "__main__":
    unittest.main()
