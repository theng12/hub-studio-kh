"""Packaging contracts for the Hub's private workers.

`packaging/hub_worker.py` decides which private worker processes a Hub spawns, and it decides
it from `release['components']` - the same map `packaging/stage_voice.py` writes and the kit
reads. These tests pin the invariants that catch the next component and the next chat model,
not today's membership:

* every component the release manifest lists produces exactly one worker spec, or is a
  component with no private child of its own (the Hub is the Hub process);
* a component this Hub does not know is refused by name, not silently unspawned;
* the Hub's component table and the staging table are the same set, so a studio cannot be
  staged without a worker or spawned without being staged;
* the packaged Chat worker is deliberately single-model, so a bundle carrying a second
  `chat-mlx` manifest is refused rather than silently serving the wrong model.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
import hub_worker  # noqa: E402
import stage_voice  # noqa: E402


# Components with no private child process: the Hub itself runs as this process.
NO_PRIVATE_WORKER = frozenset(
    name for name, factory in hub_worker.COMPONENT_WORKERS.items() if factory is None)
# A release record for a component that carries its own runtime identity.
IDENTITY = {"runtime_sha256": "a" * 64}


def chat_manifest(package_id: str, model_id: str) -> dict:
    return {"engine": "chat-mlx", "package_id": package_id, "model_id": model_id,
            "revision": "a" * 40, "files": []}


class WorkerSpecTests(unittest.TestCase):
    def _resources(self, root: Path, *, chat_manifests=("qwen3-5-9b-4bit.json",),
                   chat_source: bool = True, image_runtime: bool = True,
                   raw_manifests: dict[str, str] | None = None) -> Path:
        resources = root / "Resources"
        (resources / "model-manifests").mkdir(parents=True)
        for component in ("image", "subtitle", "music", "chat"):
            backend = resources / "components" / component / "app" / "backend"
            backend.mkdir(parents=True)
            (backend / "main.py").write_text(f"# {component} app\n")
        if not chat_source:
            (resources / "components" / "chat" / "app" / "backend" / "main.py").unlink()
        for worker in ("voice_worker.py", "hub_worker.py", "image_worker.py",
                       "subtitle_worker.py", "music_worker.py", "chat_worker.py"):
            (resources / worker).write_text(f"# {worker}\n")
        for runtime, binary in (("runtimes/voice/bin/python3", True),
                                ("runtimes/image/bin/python3", image_runtime)):
            path = resources / runtime
            path.parent.mkdir(parents=True, exist_ok=True)
            if binary:
                path.write_text("")
        for relative in ("engines/ace-step-1-5/studiohub-engine.json",
                         "runtimes/music/manifest/music-runtime.json"):
            path = resources / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n")
        for name in chat_manifests:
            (resources / "model-manifests" / name).write_text(json.dumps(
                chat_manifest(Path(name).stem, f"mlx-community/{Path(name).stem}")))
        for name, text in (raw_manifests or {}).items():
            (resources / "model-manifests" / name).write_text(text)
        return resources

    def _components(self, **overrides) -> dict:
        """A release component map built from the Hub's own table, not a copied list."""
        components = {name: {"runtime_sha256": "a" * 64}
                      for name in hub_worker.COMPONENT_WORKERS}
        components.update(overrides)
        return components

    def _specs(self, release: dict, resources: Path):
        release = {"runtime_sha256": "b" * 64, **release}
        runtime = resources / "runtimes" / "voice"
        data = resources.parent / "data"
        return hub_worker.worker_specs(release, data, runtime, resources)

    def test_every_component_in_the_release_manifest_produces_a_worker_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resources = self._resources(Path(tmp))
            components = self._components()
            specs = self._specs({"components": components}, resources)
        self.assertEqual({spec["id"] for spec in specs},
                         set(components) - NO_PRIVATE_WORKER,
                         "a component the release manifest lists must spawn its private worker")
        self.assertLessEqual(set(specs[0]), {"id", "modality", "python", "runtime_id",
                                             "outputs", "extra_args"})

    def test_an_unregistered_component_is_refused_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resources = self._resources(Path(tmp))
            components = self._components(video={"runtime_sha256": "a" * 64})
            with self.assertRaises(ValueError) as caught:
                self._specs({"components": components}, resources)
        message = str(caught.exception)
        self.assertIn("'video'", message,
                      "the refusal must name the component it cannot spawn")
        self.assertIn("hub_worker.py", message,
                      "the refusal must name the file to update")
        self.assertIn("stage_voice.py", message,
                      "a new component also has to be staged somewhere")

    def test_the_hub_and_staging_component_tables_are_the_same_set(self) -> None:
        """A studio added to one side only cannot ship, and cannot be spawned, by halves."""
        self.assertEqual(set(hub_worker.COMPONENT_WORKERS),
                         set(stage_voice.COMPONENT_STAGERS),
                         "packaging/hub_worker.py and packaging/stage_voice.py disagree about "
                         "which components exist")

    def test_chat_gets_a_spec_only_when_the_release_carries_the_component(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resources = self._resources(Path(tmp))
            without = self._specs({"components": {"voice": {}}}, resources)
            with_chat = self._specs({"components": {"voice": {}, "chat": dict(IDENTITY)}}, resources)
        self.assertNotIn("chat", {spec["id"] for spec in without})
        chat = next(spec for spec in with_chat if spec["id"] == "chat")
        self.assertEqual(chat["modality"], "chat")
        self.assertEqual(chat["runtime_id"], "a" * 64)
        self.assertEqual(chat["python"], resources / "runtimes" / "voice" / "bin" / "python3")
        self.assertEqual(chat["extra_args"], [])

    def test_a_single_chat_model_is_accepted_whatever_its_manifest_is_named(self) -> None:
        """The Chat worker is not pinned to one file name; a renamed model still serves."""
        with tempfile.TemporaryDirectory() as tmp:
            resources = self._resources(Path(tmp),
                                        chat_manifests=("qwen3-6-9b-4bit.json",))
            specs = self._specs(
                {"components": {"voice": {}, "chat": dict(IDENTITY)}}, resources)
        self.assertIn("chat", {spec["id"] for spec in specs})

    def test_a_second_chat_model_is_refused_by_name(self) -> None:
        """The single-model contract must be enforced, not merely documented (audit C9)."""
        with tempfile.TemporaryDirectory() as tmp:
            resources = self._resources(
                Path(tmp),
                chat_manifests=("qwen3-5-9b-4bit.json", "qwen3-6-9b-4bit.json"))
            with self.assertRaises(ValueError) as caught:
                self._specs({"components": {"voice": {}, "chat": dict(IDENTITY)}}, resources)
        message = str(caught.exception)
        self.assertIn("exactly one model", message)
        self.assertIn("qwen3-5-9b-4bit.json", message)
        self.assertIn("qwen3-6-9b-4bit.json", message)

    def test_an_unreadable_manifest_is_refused_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resources = self._resources(Path(tmp),
                                        raw_manifests={"broken.json": "{not json"})
            with self.assertRaises(ValueError) as caught:
                self._specs({"components": {"voice": {}, "chat": dict(IDENTITY)}}, resources)
        self.assertIn("broken.json", str(caught.exception))

    def test_an_incomplete_chat_bundle_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resources = self._resources(Path(tmp), chat_source=False)
            with self.assertRaisesRegex(ValueError, "Incomplete Chat worker"):
                self._specs({"components": {"chat": dict(IDENTITY)}}, resources)
        with tempfile.TemporaryDirectory() as tmp:
            resources = self._resources(Path(tmp), chat_manifests=())
            with self.assertRaisesRegex(ValueError, "no chat-mlx manifest"):
                self._specs({"components": {"chat": dict(IDENTITY)}}, resources)

    def test_a_bad_runtime_identity_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resources = self._resources(Path(tmp))
            with self.assertRaisesRegex(ValueError, "Incomplete Chat worker"):
                self._specs({"components": {"chat": {"runtime_sha256": "short"}}}, resources)

    def test_an_incomplete_image_runtime_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resources = self._resources(Path(tmp), image_runtime=False)
            with self.assertRaisesRegex(ValueError, "Incomplete Image"):
                self._specs({"components": {"image": {"runtime_sha256": "a" * 64}}}, resources)


if __name__ == "__main__":
    unittest.main()
