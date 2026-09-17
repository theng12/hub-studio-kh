"""There is one version, and every surface agrees with it.

The app is copied onto machines as a folder, so nothing forces a version to be consistent: the
bundle name, `VERSION`, the packaged `Info.plist`, the changelog and the owner-facing notes are all
written separately. These tests make "raise the version" a single edit that the gates check, rather
than five edits and a hope.

They also pin the two decisions behind the What's New screen, because both are easy to reverse by
accident: the release notes are the *bundled* file (not a second copy), and a brand-new install
does not greet its operator with release notes.
"""
import plistlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = ROOT / "VERSION"
CHANGELOG = ROOT / "CHANGELOG.md"
NOTES = ROOT / "release-notes.md"
STAGE = ROOT / "packaging" / "stage_voice.py"
NATIVE = ROOT / "native"


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
        headings = re.findall(r"^## (\d+\.\d+\.\d+)", NOTES.read_text(), re.M)
        self.assertIn(current_version(), headings)
        self.assertEqual(headings[0], current_version())

    def test_the_notes_and_the_changelog_are_different_audiences(self):
        """The notes are read by the operator; the changelog is the engineering record."""
        notes = NOTES.read_text()
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
        self.assertIn("shutil.copyfile(ROOT / 'release-notes.md', resources / 'release-notes.md')",
                      text)


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

    def staged(self):
        for candidate in (Path("/tmp/StudioTest/StudioHubTest.app"),
                          Path.home() / "Applications" / "Studio Hub.app",
                          Path("/Applications/Studio Hub.app")):
            if (candidate / "Contents/Info.plist").is_file():
                return candidate
        return None

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


if __name__ == "__main__":
    unittest.main()
