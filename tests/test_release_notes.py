"""There is one version, and every surface agrees with it.

The app is copied onto machines as a folder, so nothing forces a version to be consistent: the
bundle name, `VERSION`, the packaged `Info.plist` and the changelog must agree. The owner-facing
notes are generated from the changelog. These tests catch a version bump without its release entry.

They also pin the two decisions behind the What's New screen, because both are easy to reverse by
accident: the release notes are the *bundled* file (not a second copy), and a brand-new install
does not greet its operator with release notes.
"""
import importlib.util
import json
import os
import plistlib
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = ROOT / "VERSION"
CHANGELOG = ROOT / "CHANGELOG.md"
STAGE = ROOT / "packaging" / "stage_voice.py"
NATIVE = ROOT / "native"
SPEC = importlib.util.spec_from_file_location("release_notes_stage", STAGE)
STAGER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STAGER)


def generated_notes() -> str:
    return STAGER.release_notes_text(CHANGELOG.read_text(), current_version())


def current_version() -> str:
    return VERSION.read_text().strip()


class VersionAgreementTests(unittest.TestCase):
    def test_the_version_looks_like_a_version(self):
        self.assertRegex(current_version(), r"^\d+\.\d+\.\d+$")

    def test_the_changelog_has_an_entry_for_this_version(self):
        headings = re.findall(r"^## (\d+\.\d+\.\d+)", CHANGELOG.read_text(), re.M)
        self.assertIn(current_version(), headings,
                      f"CHANGELOG.md has no entry for {current_version()}")
        self.assertEqual(headings[0], current_version(),
                         "the newest changelog entry must be the version being shipped")

    def test_the_owner_facing_notes_have_an_entry_for_this_version(self):
        headings = re.findall(r"^## (\d+\.\d+\.\d+)", generated_notes(), re.M)
        self.assertIn(current_version(), headings)
        self.assertEqual(headings[0], current_version())

    def test_the_notes_and_the_changelog_are_different_audiences(self):
        """Keep engineering details out of the generated in-app text."""
        notes = generated_notes()
        self.assertNotIn("REFERENCE_AUDIO_CONTRACT_UNAVAILABLE", notes)
        self.assertNotIn("WORKER_FOR_MODALITY", notes)
        self.assertLess(len(notes), len(CHANGELOG.read_text()),
                        "the owner-facing notes should be the shorter document")

    def test_the_app_does_not_carry_a_version_in_its_name(self):
        """A version in the folder name invalidates every launcher and instruction on a bump.

        Checked against the names the staging tool can actually produce, not against the text of
        the file: the comment explaining this decision has to be allowed to mention the old name.
        """
        text = STAGE.read_text()
        # The bundle's *display* name is the fixed identity; the version rides in the plist and the
        # release manifest, and the folder name is chosen by the caller (the kit stages
        # `Studio Hub.app`).
        self.assertIn("'CFBundleName': 'Studio Hub'", text)
        self.assertIn("'CFBundleShortVersionString': version", text)
        self.assertIn("'version': version", text)

    def test_the_launchers_prefer_the_unversioned_app_name(self):
        for name in ("install_app.command", "copy-models.command"):
            text = (ROOT / name).read_text()
            first = text.index("Studio Hub")
            self.assertTrue(text[first:].startswith("Studio Hub.app") or 'Studio Hub.app"' in text,
                            f"{name} must try the unversioned name")
            # The versioned name may remain only as a fallback for a Mac carrying an older kit.
            self.assertLessEqual(text.count("Studio Hub 0.4.0.app"), 4,
                                 f"{name} still leans on the old versioned name")

    def test_the_notes_are_shipped_where_the_app_reads_them(self):
        """`Bundle.main` looks in Resources, not in the Voice worker's subtree."""
        text = STAGE.read_text()
        self.assertIn("(resources / 'release-notes.md').write_text(", text)
        self.assertIn("release_notes_text((ROOT / 'CHANGELOG.md').read_text(encoding='utf-8'), version)", text)
        self.assertFalse((ROOT / "release-notes.md").exists(),
                         "release text must be authored only in CHANGELOG.md")


class ReleaseNotesProjectionTests(unittest.TestCase):
    def test_plain_history_is_kept_without_technical_details(self):
        changelog = """# Changelog

## 1.2.0 — 2026-11-02

### Easier updates

- Updates explain what changed. Khmer: ថ្មី

### Technical notes

- INTERNAL_NEW

## 1.1.0 — 2026-10-01

Older plain words.

### Technical notes

- INTERNAL_OLD

## 1.0.0 — 2026-09-01

### Technical notes

- Technical-only historical release.
"""
        notes = STAGER.release_notes_text(changelog, "1.2.0")
        self.assertIn("## 1.2.0 — 2026-11-02", notes)
        self.assertIn("### Easier updates", notes)
        self.assertIn("Khmer: ថ្មី", notes)
        self.assertIn("Older plain words.", notes)
        self.assertNotIn("INTERNAL_", notes)
        self.assertNotIn("## 1.0.0", notes)
        self.assertLess(notes.index("1.2.0"), notes.index("1.1.0"))

    def test_missing_or_empty_current_release_is_refused(self):
        for source in ("# Changelog", "## 1.2.0\n\n### Technical notes\n\nInternal only.",
                       "## 1.1.0\n\nOld notes.\n"):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "1.2.0"):
                STAGER.release_notes_text(source, "1.2.0")


class ReleaseNotesScreenTests(unittest.TestCase):
    SOURCE = (NATIVE / "ReleaseNotes.swift").read_text()

    def test_the_screen_reads_the_bundled_file(self):
        self.assertIn('forResource: "release-notes", withExtension: "md"', self.SOURCE)

    def test_the_version_comes_from_the_bundle_not_a_constant(self):
        self.assertIn('object(forInfoDictionaryKey: "CFBundleShortVersionString")', self.SOURCE)

    def test_a_first_run_does_not_announce_itself(self):
        """A new install is not an upgrade."""
        body = self.SOURCE[self.SOURCE.index("static func shouldAnnounce"):]
        self.assertIn("guard let seen, seen != current else { return false }", body,
                      "an unseen version must not be treated as an upgrade")

    def test_the_page_exists_and_is_reachable_without_knowing_a_version(self):
        shell = (NATIVE / "ShellCore.swift").read_text()
        self.assertIn('case whatsNew = "What\'s New"', shell)
        self.assertIn(".whatsNew", shell)
        hub = (NATIVE / "StudioHub.swift").read_text()
        self.assertIn("What's New in Studio Hub", hub)
        self.assertIn("showWhatsNew", hub)


class StagedBundleTests(unittest.TestCase):
    """The real bundle, when one has been staged beside this tree."""

    @staticmethod
    def select_bundle(explicit, default):
        if explicit is not None:
            candidate = Path(explicit).expanduser()
            if not (candidate / "Contents/Info.plist").is_file():
                raise AssertionError(
                    f"STUDIOHUB_TEST_APP is not a staged Studio Hub app: {candidate}")
            return candidate
        return default if (default / "Contents/Info.plist").is_file() else None

    def staged(self):
        return self.select_bundle(os.environ.get("STUDIOHUB_TEST_APP"),
                                  ROOT / "dist" / "Studio Hub.app")

    def test_explicit_bundle_path_is_selected(self):
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "Explicit.app" / "Contents"
            candidate.mkdir(parents=True)
            (candidate / "Info.plist").touch()
            selected = self.select_bundle(str(candidate.parent), ROOT / "missing.app")
        self.assertEqual(selected, candidate.parent)

    def test_explicit_missing_bundle_path_fails(self):
        with self.assertRaises(AssertionError):
            self.select_bundle("/definitely/missing/Studio Hub.app", ROOT / "missing.app")

    def test_default_selection_skips_when_no_repo_artifact_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertIsNone(self.select_bundle(None, Path(temporary) / "Studio Hub.app"))

    def test_the_packaged_plist_carries_the_current_version(self):
        bundle = self.staged()
        if bundle is None:
            self.skipTest("no staged bundle on this machine")
        with (bundle / "Contents/Info.plist").open("rb") as stream:
            plist = plistlib.load(stream)
        self.assertEqual(plist["CFBundleShortVersionString"], current_version())
        self.assertEqual(plist["CFBundleName"], "Studio Hub")

    def test_the_notes_are_readable_from_inside_the_bundle(self):
        bundle = self.staged()
        if bundle is None:
            self.skipTest("no staged bundle on this machine")
        notes = bundle / "Contents/Resources/release-notes.md"
        self.assertTrue(notes.is_file(), "the What's New screen would have nothing to show")
        self.assertIn(current_version(), notes.read_text())

    def test_the_release_manifest_names_this_source_commit(self):
        bundle = self.staged()
        if bundle is None:
            self.skipTest("no staged bundle on this machine")
        manifest_path = bundle / "Contents/Resources/release-manifest.json"
        self.assertTrue(manifest_path.is_file(), "the staged bundle has no release identity")
        manifest = json.loads(manifest_path.read_text())
        source_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        self.assertEqual(manifest["implementation_commit"], source_commit)
        self.assertIsInstance(manifest.get("development_build"), bool)


if __name__ == "__main__":
    unittest.main()
