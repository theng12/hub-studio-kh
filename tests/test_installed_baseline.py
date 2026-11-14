"""The installed baseline: what this Hub may reuse is what is actually on disk.

The app's own embedded manifest is data, not proof - 0.6.224 generated it pre-seal, so its
records can name a digest the Mach-O no longer has and omit files codesign created afterwards.
Every consumer of the baseline (blob reuse, the delta plan, the delta capability) therefore
measures the installed bytes, cached only until the installed identity (release-manifest.json)
changes. The fixture is the producer's sealed-manifest contract: a valid 4-file/399-byte app,
the stale 224 records against it, and the corrupted native.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub.backend import file_manifest as fm
from components.hub.backend import release_delta as rd

FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "sealed_manifest" / "sealed_manifest_fixture.json"
IDENTITY = "Contents/Resources/release-manifest.json"
NATIVE = "Contents/MacOS/StudioHub"
SIGNATURE = "Contents/_CodeSignature/CodeResources"


def load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def make_fixture_app(root: Path, data: dict, *, embedded: dict | None = None) -> Path:
    """The fixture's app bytes on disk; ``embedded`` writes legacy records declaring digests."""
    app = root / "Hub Studio KH.app"
    for rel, spec in data["valid_sealed_release"]["app_files"].items():
        target = app / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(spec["content_b64"]))
    path = app / IDENTITY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"version":"0.6.224"}')
    if embedded is not None:
        fm.write(app, embedded, destination=app / fm.DIR_REL)
    return app


class BaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="studiohub-baseline-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = load_fixture()
        rd._installed_cache.clear()
        self.addCleanup(rd._installed_cache.clear)

    def stale_records(self) -> dict:
        """The embedded records 0.6.224 would have left inside the app: native stale, signature absent."""
        stale = self.data["stale_224_baseline_case"]
        records = {}
        for rel, spec in self.data["valid_sealed_release"]["app_files"].items():
            declared = spec["sha256"]
            record = stale["embedded_224_manifest_records"].get(rel)
            if record is not None:
                declared = record["declared_sha256"]
                if declared is None:
                    continue    # codesign created it after the manifest: not listed at all
            records[rel] = ("f", bool(spec["executable"]), int(spec["size"]), declared)
        return records

    # ── what the baseline is made of ───────────────────────────────────────

    def test_the_baseline_is_the_disk_bytes_not_the_stale_224_records(self) -> None:
        stale = self.data["stale_224_baseline_case"]
        app = make_fixture_app(self.tmp / "stale", self.data, embedded=self.stale_records())
        entries = rd.installed_entries(app)
        self.assertEqual(entries, fm.scan(app))
        # The native is served under its real digest, never the pre-seal one.
        self.assertEqual(entries[NATIVE][3], stale["installed_app_disk_hashes"][NATIVE])
        self.assertNotEqual(entries[NATIVE][3],
                            stale["embedded_224_manifest_records"][NATIVE]["declared_sha256"])
        # The signature codesign created afterwards is part of the baseline, not missing from it.
        self.assertEqual(entries[SIGNATURE][3], stale["installed_app_disk_hashes"][SIGNATURE])

    def test_an_app_with_no_embedded_manifest_is_measured_from_its_bytes(self) -> None:
        # The sealed post-224 shape: the manifest lives beside the app, never inside it.
        app = make_fixture_app(self.tmp / "sidecar", self.data)
        self.assertFalse((app / fm.DIR_REL).exists())
        self.assertEqual(rd.installed_entries(app), fm.scan(app))

    def test_a_corrupted_embedded_manifest_does_not_stop_the_measurement(self) -> None:
        app = make_fixture_app(self.tmp / "corrupt", self.data, embedded=self.stale_records())
        (app / fm.DIR_REL / "index.json").write_bytes(b"{not json")
        self.assertEqual(rd.installed_entries(app), fm.scan(app))

    # ── when the capability may be offered ─────────────────────────────────

    def test_the_capability_is_off_without_an_app_an_identity_or_a_measurable_tree(self) -> None:
        with mock.patch.dict(os.environ):
            os.environ.pop("STUDIOHUB_RELEASE_MANIFEST", None)
            self.assertIsNone(rd.validated_installed_baseline())   # a source checkout
        app = make_fixture_app(self.tmp / "identity", self.data)
        manifest = app / IDENTITY
        with mock.patch.dict(os.environ, {"STUDIOHUB_RELEASE_MANIFEST": str(manifest)}):
            self.assertIsNotNone(rd.validated_installed_baseline())
            manifest.unlink()
            self.assertIsNone(rd.validated_installed_baseline())   # identity gone
        broken = make_fixture_app(self.tmp / "broken", self.data)
        os.symlink("escape\n", broken / "Contents" / "bad-link")
        with mock.patch.dict(os.environ, {"STUDIOHUB_RELEASE_MANIFEST": str(broken / IDENTITY)}):
            self.assertIsNone(rd.validated_installed_baseline())   # tree cannot be measured

    def test_a_held_release_never_proves_a_baseline(self) -> None:
        # The helper reads only the running app's identity and bytes; a held descriptor - even
        # one naming a future version this Mac has never run - is never consulted.
        with mock.patch.dict(os.environ):
            os.environ.pop("STUDIOHUB_RELEASE_MANIFEST", None)
            self.assertIsNone(rd.validated_installed_baseline())

    def test_a_valid_baseline_is_actual_bytes_and_the_cache_lives_until_the_identity_changes(self) -> None:
        app = make_fixture_app(self.tmp / "cache", self.data, embedded=self.stale_records())
        manifest = app / IDENTITY
        with mock.patch.dict(os.environ, {"STUDIOHUB_RELEASE_MANIFEST": str(manifest)}):
            baseline = rd.validated_installed_baseline()
            self.assertEqual(baseline, fm.scan(app))   # capability from actual bytes
            first = rd.installed_entries(app)
            (app / NATIVE).write_bytes(b"edited after the measurement")
            self.assertIs(rd.installed_entries(app), first,
                          "shortcut: the cache lives until the installed identity changes")
            manifest.write_bytes(b'{"version":"0.6.224","installed":true}')
            second = rd.installed_entries(app)
            self.assertIsNot(second, first, "an identity change must re-measure")
            self.assertEqual(second[NATIVE][3], hashlib.sha256(b"edited after the measurement").hexdigest())

    # ── the consumer that must never reuse the cached view ─────────────────

    def test_stage_measures_the_app_it_is_about_to_clone_not_a_view_cached_earlier(self) -> None:
        app = make_fixture_app(self.tmp / "stage", self.data, embedded=self.stale_records())
        original = (app / NATIVE).read_bytes()
        new_entries = fm.scan(app)                     # the release this app is upgraded to
        index, _tsv = fm.build(new_entries)
        self.assertEqual(rd.installed_entries(app)[NATIVE][3], hashlib.sha256(original).hexdigest())
        # A file is edited after the measurement, without the identity changing: a plan built
        # from the cached view would reuse the edited bytes under the release's digest.
        (app / NATIVE).write_bytes(b"different bytes after the measurement")

        def get_blob(sha: str, destination: Path, size: int) -> None:
            self.assertEqual(sha, hashlib.sha256(original).hexdigest())
            destination.write_bytes(original)

        staged, _summary = rd.stage(app, new_entries, index["files_sha256"], get_blob)
        self.assertEqual((staged / NATIVE).read_bytes(), original)
        self.assertEqual(fm.scan(staged), new_entries)


class ProbeTests(unittest.TestCase):
    """The capability probe must never wait for a cold measurement (about 20 s on a real app,
    as long as a gateway's probe timeout), must not start a second one, and must tell the
    truth about a failed measurement."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="studiohub-probe-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = load_fixture()
        rd._installed_cache.clear()
        rd._probe_failed = None
        rd._probe_thread = None
        self.addCleanup(rd._installed_cache.clear)
        self.addCleanup(setattr, rd, "_probe_failed", None)
        self.addCleanup(setattr, rd, "_probe_thread", None)

    def test_probes_answer_at_once_measure_once_and_only_then_report_capability(self) -> None:
        app = make_fixture_app(self.tmp / "probe", self.data)
        real_scan = fm.scan
        calls: list = []
        started = threading.Event()
        release = threading.Event()

        def held_scan(root, **kwargs):
            calls.append(root)
            started.set()
            release.wait(2)     # a blocking implementation would sit here and fail below
            return real_scan(root, **kwargs)

        with mock.patch.dict(os.environ, {"STUDIOHUB_RELEASE_MANIFEST": str(app / IDENTITY)}), \
                mock.patch.object(fm, "scan", side_effect=held_scan):
            self.assertFalse(rd.baseline_ready())       # first probe: null at once
            self.assertTrue(started.wait(5))            # its single measurement is running
            self.assertFalse(rd.baseline_ready())       # concurrent probe: still null
            self.assertEqual(len(calls), 1, "concurrent probes must not start a second scan")
            release.set()
            thread = rd._probe_thread
            self.assertIsNotNone(thread)
            thread.join(timeout=5)
            self.assertTrue(rd.baseline_ready())        # the capability only after success
            self.assertEqual(len(calls), 1)

    def test_a_failed_measurement_stays_null_until_the_identity_changes(self) -> None:
        app = make_fixture_app(self.tmp / "fail", self.data)
        calls: list = []
        failed = threading.Event()

        def broken_scan(root, **kwargs):
            calls.append(root)
            failed.set()
            raise fm.ManifestError("the tree cannot be measured")

        with mock.patch.dict(os.environ, {"STUDIOHUB_RELEASE_MANIFEST": str(app / IDENTITY)}), \
                mock.patch.object(fm, "scan", side_effect=broken_scan):
            self.assertFalse(rd.baseline_ready())
            self.assertTrue(failed.wait(5))
            thread = rd._probe_thread
            self.assertIsNotNone(thread)
            thread.join(timeout=5)
            self.assertFalse(rd.baseline_ready())       # stays null ...
            self.assertFalse(rd.baseline_ready())
            self.assertEqual(len(calls), 1)             # ... without rescanning
        # The installed identity changes: the failure no longer speaks for this app, and the
        # real measurement earns the capability.
        (app / IDENTITY).write_bytes(b'{"version":"0.6.224","installed":true}')
        with mock.patch.dict(os.environ, {"STUDIOHUB_RELEASE_MANIFEST": str(app / IDENTITY)}):
            self.assertFalse(rd.baseline_ready())       # launches the new measurement
            thread = rd._probe_thread
            self.assertIsNotNone(thread)
            thread.join(timeout=5)
            self.assertTrue(rd.baseline_ready())


if __name__ == "__main__":
    unittest.main()
