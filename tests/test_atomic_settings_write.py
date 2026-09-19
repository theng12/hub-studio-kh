"""This Mac's identity file must never be left half-written.

`control_plane` wrote `controller_settings.json` with `write_text`, which truncates the target before
writing. A crash, a kill, or a full disk between those two moments leaves a partial file; the next
start reads it as unreadable and falls back to defaults, which is how a Mac silently forgot its
identity and the settings that keep it contained. The write is now a temporary file plus
`os.replace`: either the new content is there, or the previous content still is.
"""
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub.backend import control_plane  # noqa: E402


class AtomicSettingsWriteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.path = Path(self.temporary.name) / "controller_settings.json"

    def tearDown(self):
        self.temporary.cleanup()

    def test_a_failed_write_leaves_the_previous_content_intact(self):
        control_plane._write_private_file(self.path, '{"machine_name": "Studio One"}\n')
        before = self.path.read_text()
        with mock.patch.object(control_plane.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                control_plane._write_private_file(self.path, '{"machine_name": "Studio Two"}\n')
        self.assertEqual(self.path.read_text(), before,
                         "a failed write must not damage what was already stored")
        self.assertEqual(json.loads(self.path.read_text())["machine_name"], "Studio One")

    def test_a_successful_write_replaces_it_whole_and_privately(self):
        control_plane._write_private_file(self.path, '{"machine_name": "Studio Two"}\n')
        self.assertEqual(json.loads(self.path.read_text())["machine_name"], "Studio Two")
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600,
                         "the identity file is not world-readable")

    def test_no_temporary_file_is_left_behind(self):
        control_plane._write_private_file(self.path, "{}\n")
        leftovers = [name for name in os.listdir(self.path.parent) if name.startswith(".")]
        self.assertEqual(leftovers, [], f"temporary files left behind: {leftovers}")

    def test_the_identity_file_is_not_written_directly_any_more(self):
        source = (Path(__file__).resolve().parents[1]
                  / "components/hub/backend/control_plane.py").read_text()
        self.assertNotIn("SETTINGS_FILE.write_text", source,
                         "a direct write can be interrupted mid-file")
        self.assertIn("_write_private_file(SETTINGS_FILE", source)
        self.assertNotIn("DATABASE_URL_FILE.write_text", source,
                         "the database credential is written the same way")


if __name__ == "__main__":
    unittest.main()
