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


if __name__ == "__main__":
    unittest.main()
