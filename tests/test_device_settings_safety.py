"""device_settings.json: serialised read-modify-write, and damage is reported rather than guessed at."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub import standalone_config as config  # noqa: E402


class SettingsFileCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "device_settings.json"
        patcher = mock.patch.object(config, "_settings_path", lambda: self.file)
        patcher.start()
        self.addCleanup(patcher.stop)
        config._logged_problem = None


class ConcurrentWrites(SettingsFileCase):
    def test_concurrent_changes_to_different_fields_do_not_undo_each_other(self):
        for round_number in range(40):
            self.file.unlink(missing_ok=True)
            config.update_device_settings(max_bytes=3_000_000_000, retention_days=3)
            barrier = threading.Barrier(2)

            def budget():
                barrier.wait()
                config.update_device_settings(max_bytes=9_000_000_000)

            def days():
                barrier.wait()
                config.update_device_settings(retention_days=7)

            threads = [threading.Thread(target=budget), threading.Thread(target=days)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            effective = config.load_device_settings()["effective"]
            self.assertEqual(effective["max_bytes"], 9_000_000_000, f"round {round_number}: the budget was lost")
            self.assertEqual(effective["retention_days"], 7, f"round {round_number}")

    def test_a_partial_update_keeps_the_other_fields(self):
        config.update_device_settings(retention_days=14)
        config.update_device_settings(max_bytes=7_000_000_000)
        effective = config.load_device_settings()["effective"]
        self.assertEqual(effective["retention_days"], 14)
        self.assertEqual(effective["max_bytes"], 7_000_000_000)


class DamageIsReported(SettingsFileCase):
    """Damage is reported and defaulted, never silently read as a choice somebody made.

    This class used to pin "damage fails safe to keep-all", which existed because a damaged file
    reading as keep-all=false would have moved a keep-all Mac's shelved models into its Trash
    Folder. Shelving is gone (owner decision 2026-10-28) so nothing moves on a settings read, and
    what is left to pin is that a damaged file still logs once and still says so on the settings
    route rather than presenting defaults as the operator's numbers.
    """

    def test_no_file_is_the_default_and_warns_of_nothing(self):
        self.assertEqual(config.load_device_settings()["effective"], config.load_device_settings()["defaults"])
        self.assertEqual(config.settings_warnings(), [])

    def test_every_kind_of_damage_uses_defaults_and_is_reported(self):
        # Damage means the FILE cannot be read as settings. A readable file with one bad value is
        # a different case (that value takes its default, the rest is honoured), and it is not an
        # error to log.
        for text in ("not json", "[]", "\x00\x00"):
            with self.subTest(text=text):
                self.file.write_text(text)
                with self.assertLogs("studiohub.settings", "ERROR"):
                    effective = config.load_device_settings()["effective"]
                self.assertEqual(effective, config.load_device_settings()["defaults"])
                self.assertTrue(config.settings_warnings())
                config._logged_problem = None

    def test_an_unreadable_file_uses_defaults_and_says_why(self):
        self.file.mkdir()
        self.assertEqual(config.load_device_settings()["effective"],
                         config.load_device_settings()["defaults"])
        self.assertIn("could not be read", config.settings_warnings()[0])

    def test_a_non_boolean_keep_all_key_left_in_an_old_file_is_ignored(self):
        # Files written by a build that had the setting still parse; the key is simply no longer
        # read, and it is not a "setting was damaged" problem any more.
        self.file.write_text('{"max_bytes": 7000000000, "keep_all_models": "true"}')
        effective = config.load_device_settings()["effective"]
        self.assertEqual(effective["max_bytes"], 7_000_000_000)
        self.assertNotIn("keep_all_models", effective)
        self.assertEqual(config.settings_warnings(), [])

    def test_a_logged_problem_is_logged_once_not_on_every_read(self):
        self.file.write_text("nope")
        with self.assertLogs("studiohub.settings", "ERROR") as captured:
            for _ in range(5):
                config.load_device_settings()
        self.assertEqual(len(captured.records), 1)

    def test_saving_repairs_the_file(self):
        self.file.write_text("nope")
        config.update_device_settings(retention_days=5)
        self.assertEqual(config.settings_warnings(), [])
        self.assertEqual(json.loads(self.file.read_text())["retention_days"], 5)


if __name__ == "__main__":
    unittest.main()
