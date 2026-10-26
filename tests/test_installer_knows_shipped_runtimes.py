"""The app's pre-swap check must know every runtime the build stages.

`ReleaseInstaller.verifyDeclaredComponents` (native/UpdateClient.swift) refuses a release whose
manifest names a runtime missing from `runtimePaths`. `video` was missing, so the installer refused
every release that ships Video *after* staging it: no Mac updated itself (0.6.120 rollout,
2026-10-01). The native fixtures never named `video`, so the suite was green by construction.

This pins the invariant rather than a membership: every runtime `packaging/stage_voice.py` writes
into a release manifest - and every one in the real captured manifest - is a runtime the installer
can verify, so the next runtime cannot reintroduce the defect.
"""
import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def installer_runtimes() -> set[str]:
    source = (ROOT / "native/UpdateClient.swift").read_text()
    table = re.search(r"static let runtimePaths: \[String: \[String\]\] = \[(.*?)\n    \]", source, re.S)
    assert table, "runtimePaths table not found in native/UpdateClient.swift"
    return set(re.findall(r'^\s*"([^"]+)":\s*\[', table.group(1), re.M))


class InstallerKnowsShippedRuntimes(unittest.TestCase):
    def test_every_runtime_the_stager_writes_is_verifiable(self):
        staged = set(re.findall(r"'runtime': '([^']+)'", (ROOT / "packaging/stage_voice.py").read_text()))
        self.assertTrue(staged, "no 'runtime' values found in packaging/stage_voice.py")
        self.assertEqual(
            staged - installer_runtimes(), set(),
            "packaging/stage_voice.py stages a runtime that ReleaseInstaller.runtimePaths "
            "(native/UpdateClient.swift) does not know: every update to that release is refused")

    def test_the_real_shipped_manifest_is_verifiable(self):
        manifest = json.loads((ROOT / "tests/native/fixtures/release-manifest-0.6.120-c439bbf.json").read_text())
        needed = {record.get("runtime", name) for name, record in manifest["components"].items()}
        self.assertEqual(needed - installer_runtimes(), set())

    def test_bridge_release_video_is_verifiable_by_the_apps_already_installed(self):
        """0.6.119/0.6.120 apps check a staged update against THEIR table (no `video`). Until the
        fleet is past 0.6.121 (no Mac below it), the video record must name a runtime that old table has.
        Delete this test together with the BRIDGE note in stage_voice.py, and only then."""
        source = (ROOT / "packaging/stage_voice.py").read_text()
        video = source[source.index("def _stage_video"):]
        declared = re.search(r"'runtime': '([^']+)'", video).group(1)
        self.assertIn(declared, {"voice", "image", "voice+music-overlay"})


class FleetInstallerCompatibility(unittest.TestCase):
    """A release must install on the Macs that already run the fleet's installer (0.6.198).

    The installer that validates a release is the one **already installed**, not the one inside
    the release. Its `runtimePaths` (commit 20670dd) has no `khu-agent`; 0.6.199 listed that as a
    top-level component, so every Mac running 0.6.198 refused the update at manifest validation,
    before downloading a byte: *"lists component 'khu-agent' with runtime 'khu-agent', which this
    installer does not know"*. Adding the name to 0.6.200's own Swift cannot fix that - the old
    binary is what validates.
    """

    FIXTURE = ROOT / "tests/native/fixtures/update-client-runtimes-0.6.198.json"
    MANIFEST_199 = ROOT / "tests/native/fixtures/release-manifest-0.6.199-f7f9fab.json"

    def fleet_runtimes(self) -> set[str]:
        return set(json.loads(self.FIXTURE.read_text())["runtimes"])

    def verify(self, manifest: dict, resources: Path | None = None) -> None:
        """`ReleaseInstaller.verifyDeclaredComponents` as the fleet's 0.6.198 installer runs it."""
        components = manifest.get("components") or {}
        self.assertTrue(components, "the manifest lists no components")
        known = self.fleet_runtimes()
        for name, value in sorted(components.items()):
            record = value or {}
            runtime = record.get("runtime", name)
            self.assertIn(runtime, known,
                          f"the 0.6.198 installer refuses component {name!r}: it names runtime "
                          f"{runtime!r}, which its runtimePaths does not know")
            if resources is not None:
                self.assertTrue((resources / f"{name}_worker.py").is_file(),
                                f"{name}_worker.py is missing and the installer requires it")

    def test_the_fixture_is_the_installer_the_fleet_runs(self):
        """The fixture is not a guess: the same table at the 0.6.198 commit is re-read here."""
        try:
            source = subprocess.check_output(
                ["git", "show", "20670dd:native/UpdateClient.swift"], cwd=ROOT, text=True,
                stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            self.skipTest("0.6.198 commit 20670dd is not in this clone")
        table = re.search(r"static let runtimePaths: \[String: \[String\]\] = \[(.*?)\n    \]",
                          source, re.S).group(1)
        self.assertEqual(set(re.findall(r'^\s*"([^"]+)":\s*\[', table, re.M)),
                         self.fleet_runtimes())

    def test_the_installed_installer_refused_the_0_6_199_manifest(self):
        manifest = json.loads(self.MANIFEST_199.read_text())
        with self.assertRaises(AssertionError) as caught:
            self.verify(manifest)
        message = str(caught.exception)
        self.assertIn("khu-agent", message)
        self.assertIn("does not know", message)
        # The evidence recorded by the holding coordinator names the same component and runtime.
        self.assertIn("khu-agent", manifest["components"])

    def test_the_stager_no_longer_writes_a_top_level_khu_agent_component(self):
        """The kit rides inside the hub record, which old installers already accept."""
        source = (ROOT / "packaging/stage_voice.py").read_text()
        self.assertNotIn("components['khu-agent']", source,
                         "a top-level khu-agent component is refused by every 0.6.198 installer")
        self.assertIn("components['hub']['khu_agent']", source)


if __name__ == "__main__":
    unittest.main()
