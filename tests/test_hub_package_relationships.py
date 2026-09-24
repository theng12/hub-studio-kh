"""One companion relationship, and the one spelling it is declared in.

A manifest declares the packages it cannot run without: ``requires_packages``. ``dependencies``
was the first spelling, and both shipped — ``chatterbox-{4,8}bit`` used it while
``qwen3-tts-*.json`` used the other. Reading only one of them had two measured consequences:

* ``whisper-turbo`` — a Subtitle ASR package that two Qwen voice packages require — was
  published by the Hub as a pickable **model** in the Voice list, because the Hub's own typing
  reader looked at ``dependencies`` and the Qwen manifests use ``requires_packages``;
* ``chatterbox``'s declared ``s3tokenizerv2`` companion was never verified or downloaded,
  because `preflight`/`download_package` look at ``requires_packages`` — so the engine fetched
  its tokenizer at generation time, which is exactly the offline failure the relationship
  exists to prevent.

The catalog is migrated and `validate_manifest` now refuses a manifest that still declares
companions in ``dependencies``, so there is no second place for a relationship to hide. These
tests assert the rule rather than a list: every declared companion resolves, any package some
manifest needs is typed as a dependency rather than offered to a person as a model, and the
legacy spelling is an error that names the field to move rather than a silently honoured alias.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import contextmanager
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[1]
CATALOG = WORKTREE / "model-manifests"

if str(WORKTREE) not in sys.path:
    sys.path.insert(0, str(WORKTREE))

from components import model_packages  # noqa: E402


def _runtime() -> str:
    candidates = [
        os.environ.get("STUDIOHUB_TEST_RUNTIME"),
        str(WORKTREE / ".build/voice-runtime/bin/python3"),
        str(WORKTREE.parents[1] / ".build/voice-runtime/bin/python3"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return sys.executable


class TheReaderHonoursEitherSpellingTests(unittest.TestCase):
    """`required_packages` stays tolerant; `validate_manifest` is what refuses the old spelling.

    The two are deliberately different: a manifest that arrived from an older kit still gets its
    companion gate, while nothing this project ships can declare one in the wrong field.
    """

    def test_a_companion_is_read_under_either_spelling(self) -> None:
        self.assertEqual(
            model_packages.required_packages({"requires_packages": ["whisper-turbo"]}),
            ["whisper-turbo"])
        self.assertEqual(
            model_packages.required_packages({"dependencies": ["s3tokenizerv2"]}),
            ["s3tokenizerv2"])

    def test_a_manifest_mid_migration_does_not_declare_one_companion_twice(self) -> None:
        self.assertEqual(
            model_packages.required_packages({
                "requires_packages": ["whisper-turbo"],
                "dependencies": ["whisper-turbo", "s3tokenizerv2"],
            }),
            ["whisper-turbo", "s3tokenizerv2"])

    def test_every_shipped_manifest_resolves_every_companion_it_declares(self) -> None:
        """Derived from the manifests themselves, so a new one is covered by existing."""
        checked = 0
        for path in sorted(CATALOG.glob("*.json")):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            declared = [*(manifest.get("requires_packages") or ()),
                        *(manifest.get("dependencies") or ())]
            with self.subTest(manifest=path.name):
                resolved = model_packages.required_packages(manifest)
                for dependency in declared:
                    self.assertIn(
                        dependency, resolved,
                        f"{path.name} declares {dependency!r} but the Hub no longer resolves "
                        f"it, so that package would ship without its companion")
                    checked += 1
        self.assertGreater(checked, 0, "no shipped manifest declares a companion at all")

    def test_no_shipped_manifest_carries_the_legacy_spelling(self) -> None:
        """The key is present exactly when there is a relationship, and only in one shape."""
        declaring = 0
        for path in sorted(CATALOG.glob("*.json")):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            with self.subTest(manifest=path.name):
                self.assertNotIn(
                    "dependencies", manifest,
                    f"{path.name} still declares companions in the legacy field")
                # And the canonical key is not left behind empty: its presence is the statement.
                self.assertEqual(
                    "requires_packages" in manifest,
                    bool(manifest.get("requires_packages")),
                    f"{path.name} carries an empty 'requires_packages' list, so the key no "
                    f"longer means 'this package has a companion'")
                declaring += bool(manifest.get("requires_packages"))
        self.assertGreater(declaring, 0, "no shipped manifest declares a companion at all")

    def test_the_legacy_spelling_is_refused_by_name(self) -> None:
        with self.assertRaises(model_packages.PackageError) as caught:
            model_packages.validate_manifest({
                "schema_version": 1, "package_id": "a-legacy-package",
                "model_id": "org/a-legacy-package", "engine": "voice-mlx",
                "revision": "a" * 40, "minimum_macos": "14.0",
                "dependencies": ["s3tokenizerv2"],
                "files": [{"path": "model/x.bin", "size": 1, "sha256": "b" * 64}],
            })
        message = str(caught.exception)
        self.assertIn("a-legacy-package", message,
                      "the refusal must name the manifest it cannot accept")
        self.assertIn("requires_packages", message,
                      "the refusal must name the field to move the list to")


class AMissingCompanionIsRefusedTests(unittest.TestCase):
    """The controlled experiment: same model, same installed bytes, companion absent."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-companions-")
        self.root = Path(self.temporary.name)
        (self.root / ".studiohub").mkdir(mode=0o700)
        self.work = self.root / "one-file.bin"
        self.work.write_bytes(b"x" * 32)
        self.base = json.loads(
            (CATALOG / "qwen3-tts-12hz-0-6b-base-8bit.json").read_text(encoding="utf-8"))
        with open(self.work, "rb") as handle:
            self.stamp = model_packages._stamp(os.fstat(handle.fileno()))
        self.original = (model_packages._index, model_packages._repair_if_stale,
                         model_packages._open_regular)
        model_packages._repair_if_stale = lambda manifest, record, root: None

        @contextmanager
        def _fake_open(_root, _relative):
            with open(self.work, "rb") as handle:
                yield handle

        model_packages._open_regular = _fake_open
        self.addCleanup(self.restore)

    def restore(self) -> None:
        (model_packages._index, model_packages._repair_if_stale,
         model_packages._open_regular) = self.original
        self.temporary.cleanup()

    def _preflight_with_companion_absent(self, spelling: str) -> dict:
        manifest = {key: value for key, value in self.base.items()
                    if key not in ("requires_packages", "dependencies")}
        # A synthetic id, so this fixture cannot be mistaken for the shipped manifest it is
        # shaped after in a log line or a warning set.
        manifest["package_id"] = "a-package-with-a-companion"
        manifest[spelling] = ["whisper-turbo"]
        record = {
            "manifest_sha256": model_packages.manifest_digest(manifest),
            "folder": "installed",
            "files": {entry["path"]: {**self.stamp, "relative": entry["path"]}
                      for entry in manifest["files"]},
        }
        model_packages._index = lambda _root: {manifest["package_id"]: record}
        catalog = {manifest["package_id"]: manifest}
        return model_packages.preflight(manifest, self.root, catalog=catalog)

    def test_a_missing_companion_is_refused(self) -> None:
        result = self._preflight_with_companion_absent("requires_packages")
        self.assertEqual(
            result["state"], "dependency_missing",
            "a companion declared as 'requires_packages' was ignored, so this package would be "
            "dispatched without it")
        self.assertEqual(result["dependency"], "whisper-turbo")

    def test_a_companion_cannot_hide_in_the_legacy_spelling(self) -> None:
        """The field nothing used to read is now a hard error, not a quiet alias.

        This is the exact shape the shipped chatterbox manifests had: bytes present, a companion
        named only in `dependencies`. A receipt written for it would be a receipt for a package
        whose companion was never checked, so the manifest is refused before one exists.
        """
        with self.assertRaises(model_packages.PackageError) as caught:
            self._preflight_with_companion_absent("dependencies")
        message = str(caught.exception)
        self.assertIn("a-package-with-a-companion", message)
        self.assertIn("requires_packages", message)


CHILD_PACKAGE_ROWS = r'''
"""What the Hub publishes about the packages whose companions it now resolves."""
import json

from components.hub import standalone
from components.hub.backend.monitor import StudioMonitor

catalog = standalone._model_catalog()
monitor = StudioMonitor()
rows = {manifest["package_id"]: standalone._model_row(manifest, catalog, monitor)
        for manifest in catalog.values()}
print("RESULT:" + json.dumps({
    "types": {package_id: row["type"] for package_id, row in rows.items()},
    "dependencies": {package_id: row.get("dependencies") for package_id, row in rows.items()},
}))
'''


class PackageTypingTests(unittest.TestCase):
    """A package another manifest needs must not be offered to a person as a model."""

    def test_every_declared_companion_is_typed_a_dependency(self) -> None:
        with tempfile.TemporaryDirectory(prefix="studiohub-typing-") as root:
            root_path = Path(root)
            library = root_path / "library"
            library.mkdir()
            workers = root_path / "private-workers.json"
            workers.write_text(json.dumps([{
                "id": "voice", "title": "Voice Studio", "modality": "voice",
                "host": "127.0.0.1", "port": 51102, "machine": "local", "local": True,
                "hub_token": "fixture-token",
                "runtime_identity": {"id": "r", "compatible": True},
                "managed_output_roots": [],
            }]))
            os.chmod(workers, 0o600)
            release = root_path / "release.json"
            release.write_text(json.dumps({"version": "0.0.0", "components": {}}))
            env = os.environ.copy()
            env.update({
                "STUDIOHUB_DATA_DIR": str(root_path / "state"),
                "STUDIOHUB_WORKERS_FILE": str(workers),
                "STUDIOHUB_MODELS_DIR": str(library),
                "STUDIOHUB_PACKAGED": "1",
                "STUDIOHUB_RELEASE_MANIFEST": str(release),
                "PYTHONPATH": os.pathsep.join(
                    [str(WORKTREE), os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep),
            })
            completed = subprocess.run(
                [_runtime(), "-c", textwrap.dedent(CHILD_PACKAGE_ROWS)],
                cwd=WORKTREE, env=env, text=True, capture_output=True, check=False,
            )
        self.assertEqual(
            completed.returncode, 0,
            "isolated package-typing fixture failed:\nstdout:\n%s\nstderr:\n%s"
            % (completed.stdout, completed.stderr),
        )
        line = next((row for row in reversed(completed.stdout.splitlines())
                     if row.startswith("RESULT:")), None)
        self.assertIsNotNone(line, completed.stdout)
        published = json.loads(line[len("RESULT:"):])
        types = published["types"]
        for path in sorted(CATALOG.glob("*.json")):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            for dependency in model_packages.required_packages(manifest):
                self.assertIn(
                    dependency, types,
                    f"{path.name} requires {dependency!r}, which the Hub does not publish")
                self.assertEqual(
                    types[dependency], "dependency",
                    f"{dependency!r} is required by {path.name} but is published as a "
                    f"pickable model, so a person can choose a tokenizer or an ASR package "
                    f"as if it were a voice")
        # The measured incident: a Subtitle ASR package two Voice packages require.
        self.assertEqual(types["whisper-turbo"], "dependency")
        self.assertEqual(published["dependencies"]["qwen3-tts-12hz-0-6b-base-8bit"],
                         ["whisper-turbo"])
        self.assertEqual(published["dependencies"]["chatterbox-4bit"], ["s3tokenizerv2"])


if __name__ == "__main__":
    unittest.main()
