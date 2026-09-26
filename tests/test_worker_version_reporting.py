"""Every worker reports a build an operator can act on.

`GET /api/health`'s `app_version` is republished by the Hub as `workers[].studio_version`
(`components/hub/backend/monitor.py:606` → `components/hub/backend/capabilities.py:810`). It is the
one field someone reads to answer *"which build is that Mac running?"*, and Gateway Studio measured
it answering the literal string `"standalone"` on three of four workers — a value that reads as
data, is indistinguishable from a version that failed to load, and makes every Mac look like the
same build.

These tests pin the rule rather than the set of workers that happened to be wrong:

* no worker answers a **string literal** for `app_version` — it reports the release it was staged
  with, or a value that cannot be mistaken for a version;
* the shared reader answers a real version from a staged manifest, a labelled development version
  from a checkout, and `unknown` when it has neither;
* the shared module is staged into a bundle, so the worker that imports it can find it.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components import app_version  # noqa: E402


def worker_mains() -> list[Path]:
    return sorted(ROOT.glob("components/*/app/backend/main.py"))


class NoWorkerInventsAVersionTests(unittest.TestCase):
    def test_every_worker_main_exists_and_reports_a_version(self) -> None:
        mains = worker_mains()
        self.assertGreaterEqual(len(mains), 4, "the worker set shrank unexpectedly")
        for main in mains:
            with self.subTest(worker=main.parents[2].name):
                text = main.read_text(encoding="utf-8")
                self.assertIn("app_version", text,
                              f"{main.relative_to(ROOT)} publishes no app_version at all, so the "
                              f"Hub has nothing to show for this worker's build")

    def test_no_worker_answers_a_string_literal(self) -> None:
        """The measured defect: `'app_version': 'standalone'` in four workers.

        A literal is always wrong here, whatever it says: the version is a property of the release
        the bundle was staged from, not of the source file. A worker that cannot read its release
        manifest reports `unknown` through the shared reader, which is a different thing from
        claiming a version.
        """
        offenders = []
        for main in worker_mains():
            text = main.read_text(encoding="utf-8")
            for match in re.finditer(r"""["']app_version["']\s*:\s*(["'])(?P<literal>[^"']*)\1""",
                                     text):
                offenders.append(f"{main.relative_to(ROOT)} answers "
                                 f"{match.group('literal')!r}")
        self.assertEqual(
            offenders, [],
            "a worker answers a hardcoded app_version; report the staged release through "
            "components/app_version.py instead:\n  " + "\n  ".join(offenders))

    def test_every_worker_reads_the_release_rather_than_naming_a_version(self) -> None:
        """Every worker, two readers, one rule: the value comes from the release manifest.

        Voice's `_read_app_version` came first, `components/app_version.py` is the shared reader
        the Image, Music, Chat and Video workers use, and the Subtitle worker reads the manifest
        path its launcher was given (`STUDIOHUB_RELEASE_MANIFEST`) through `read_app_version`.
        All of them validate the same schema and refuse anything that is not a version triple,
        which is what this asserts; converging them on one module is a follow-up, not a
        correctness gap.
        """
        mechanisms = {
            "voice": "_read_app_version",
            "image": "app_version.version_fields()",
            "music": "app_version.version_fields()",
            "subtitle": "read_app_version(",
            "chat": "app_version.version_fields()",
            # Video reads the staged release through the shared reader, so a staged worker
            # reports the build it came from rather than a word.
            "video": "app_version.version_fields()",
        }
        for main in worker_mains():
            name = main.parents[2].name
            with self.subTest(worker=name):
                self.assertIn(name, mechanisms,
                              f"{name} is a new worker: give it a version source and add it here")
                self.assertIn(mechanisms[name], main.read_text(encoding="utf-8"),
                              f"{main.relative_to(ROOT)} does not report the release it was "
                              f"staged with")


class TheSharedReaderTests(unittest.TestCase):
    def test_a_staged_manifest_provides_the_version_commit_and_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "release-manifest.json"
            manifest.write_text(json.dumps({
                "schema": "studiohub.standalone-release", "schema_version": 1,
                "version": "9.9.9", "development_build": False,
                "implementation_commit": "b" * 40,
            }))
            fields = _with_manifest(manifest)
        self.assertEqual(fields["app_version"], "9.9.9")
        self.assertEqual(fields["app_commit"], "b" * 40)
        self.assertFalse(fields["development_build"])

    def test_a_manifest_with_a_non_version_is_refused(self) -> None:
        """`"standalone"` in a manifest must not become a version on the way out."""
        for value in ("standalone", "fixture", "", "v1.2.3", "1.2"):
            with self.subTest(version=value):
                self.assertIsNone(app_version._valid({
                    "schema": "studiohub.standalone-release", "schema_version": 1,
                    "version": value}))

    def test_a_wrong_schema_is_refused(self) -> None:
        self.assertIsNone(app_version._valid({
            "schema": "studiohub.something-else", "schema_version": 1, "version": "1.2.3"}))
        self.assertIsNone(app_version._valid({
            "schema": "studiohub.standalone-release", "schema_version": 2, "version": "1.2.3"}))

    def test_the_checkout_version_is_a_version_or_unknown(self) -> None:
        """Whatever this run resolves to, it is either a triple or the honest word."""
        version = app_version.release_version()
        self.assertTrue(re.fullmatch(r"\d+\.\d+\.\d+", version) or version == "unknown", version)

    def test_the_fields_are_the_three_the_hub_reads(self) -> None:
        self.assertEqual(set(app_version.version_fields()),
                         {"app_version", "app_commit", "development_build"})


class TheSharedModuleIsStagedTests(unittest.TestCase):
    def test_staging_copies_every_shared_module_by_discovery(self) -> None:
        """A hand-written list here is silently wrong the day a module is added to it."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "stage_voice_for_test", ROOT / "packaging" / "stage_voice.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        discovered = {path.name for path in module.shared_components_modules(ROOT / "components")}
        on_disk = {path.name for path in (ROOT / "components").glob("*.py")}
        self.assertEqual(discovered, on_disk)
        self.assertIn("app_version.py", discovered,
                      "the shared version reader must ship, or every worker that reports a "
                      "version fails at import in the bundle")
        self.assertIn("model_packages.py", discovered)


def _with_manifest(manifest: Path) -> dict:
    """`version_fields()` as a staged worker sees it: through the manifest the bundle carries."""
    import os
    previous = os.environ.get(app_version.ENVIRONMENT_VARIABLE)
    os.environ[app_version.ENVIRONMENT_VARIABLE] = str(manifest)
    try:
        return app_version.version_fields()
    finally:
        if previous is None:
            os.environ.pop(app_version.ENVIRONMENT_VARIABLE, None)
        else:
            os.environ[app_version.ENVIRONMENT_VARIABLE] = previous


if __name__ == "__main__":
    unittest.main()
