"""device_settings.json: serialised read-modify-write, and damage never reads as keep_all_models=false."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components import shelved_models  # noqa: E402
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
            config.update_device_settings(keep_all_models=False, retention_days=3)
            barrier = threading.Barrier(2)

            def keep():
                barrier.wait()
                config.update_device_settings(keep_all_models=True)

            def days():
                barrier.wait()
                config.update_device_settings(retention_days=7)

            threads = [threading.Thread(target=keep), threading.Thread(target=days)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            effective = config.load_device_settings()["effective"]
            self.assertTrue(effective["keep_all_models"], f"round {round_number}: the toggle was lost")
            self.assertEqual(effective["retention_days"], 7, f"round {round_number}")

    def test_a_partial_update_keeps_the_other_fields(self):
        config.update_device_settings(keep_all_models=True)
        config.update_device_settings(max_bytes=7_000_000_000)
        effective = config.load_device_settings()["effective"]
        self.assertTrue(effective["keep_all_models"])
        self.assertEqual(effective["max_bytes"], 7_000_000_000)


class DamageFailsSafe(SettingsFileCase):
    def test_no_file_is_the_default_and_warns_of_nothing(self):
        self.assertFalse(config.load_device_settings()["effective"]["keep_all_models"])
        self.assertEqual(config.settings_warnings(), [])

    def test_every_kind_of_damage_keeps_all_models_and_is_reported(self):
        for text in ("not json", "[]", '{"keep_all_models": "true"}', '{"keep_all_models": 1}', "\x00\x00"):
            with self.subTest(text=text):
                self.file.write_text(text)
                with self.assertLogs("studiohub.settings", "ERROR"):
                    effective = config.load_device_settings()["effective"]
                self.assertTrue(effective["keep_all_models"], "damage must never read as keep_all_models=false")
                self.assertTrue(config.settings_warnings())
                config._logged_problem = None

    def test_an_unreadable_file_keeps_all_models(self):
        self.file.mkdir()
        self.assertTrue(config.load_device_settings()["effective"]["keep_all_models"])
        self.assertIn("could not be read", config.settings_warnings()[0])

    def test_start_up_shelving_moves_nothing_when_the_file_is_damaged(self):
        library = Path(self.tmp.name) / "library"
        (library / "whisper-base").mkdir(parents=True)
        (library / "whisper-base" / "w").write_text("x")
        self.file.write_text("{ truncated")
        manifests = [{"package_id": "whisper-base", "model_id": "mlx-community/whisper-base-mlx"}]
        moved = shelved_models.shelve_installed(
            library, manifests, Path(self.tmp.name) / "trash",
            config.load_device_settings()["effective"]["keep_all_models"])
        self.assertEqual(moved, [])
        self.assertTrue((library / "whisper-base" / "w").exists())

    def test_a_logged_problem_is_logged_once_not_on_every_read(self):
        self.file.write_text("nope")
        with self.assertLogs("studiohub.settings", "ERROR") as captured:
            for _ in range(5):
                config.load_device_settings()
        self.assertEqual(len(captured.records), 1)

    def test_saving_repairs_the_file(self):
        self.file.write_text("nope")
        config.update_device_settings(keep_all_models=False)
        self.assertEqual(config.settings_warnings(), [])
        self.assertFalse(json.loads(self.file.read_text())["keep_all_models"])


if __name__ == "__main__":
    unittest.main()
