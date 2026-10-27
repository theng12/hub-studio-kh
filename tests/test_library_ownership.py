"""Only the Hub that owns a model library may retire or move packages out of it.

The incident (2026-10-01/02): a Hub started with a throwaway --data-dir over the REAL library
(~/StudioHubModels) found "keep all models" false in the empty data directory, moved Whisper base/small
out (shelving, since retired, 2026-10-28) and LTX/Gemma out (retirement) into that directory's Trash
Folder, and deleting the directory destroyed the weights. `components/library_ownership.py` is the
fix; the replay below fails on the old code. Shelving is gone, so the guard now stands on retirement -
the one remaining path that moves real weights - and the tests below assert it there.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from components import library_ownership, retired_models  # noqa: E402

SHELVED, RETIRED, KEPT = "whisper-base", "ltx-2-3-22b-ram-12gb-mlx", "whisper-turbo"


def make_library(base: Path) -> Path:
    library = base / "StudioHubModels"
    for name in (SHELVED, RETIRED, KEPT):
        (library / name / "rev").mkdir(parents=True)
        (library / name / "rev" / "weights.bin").write_text(name)
    state = library / ".studiohub"
    state.mkdir(mode=0o700)
    os.chmod(state, 0o700)
    (state / "installed.json").write_text("{}")
    return library


def weights_intact(library: Path) -> bool:
    return all((library / name / "rev" / "weights.bin").read_text() == name for name in (SHELVED, RETIRED, KEPT))


class OwnershipRules(unittest.TestCase):
    """`resolve` with a pretend temp folder, since the test directories themselves are under a real one."""

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name).resolve()
        self.scratch = self.base / "scratch"
        self.scratch.mkdir()
        self.library = make_library(self.base)
        patch = mock.patch.object(library_ownership, "temp_roots", lambda: [self.scratch])
        patch.start()
        self.addCleanup(patch.stop)
        self.owner = self.base / "app-data" / "hub"
        self.owner.mkdir(parents=True)

    def test_a_temp_data_dir_never_owns_and_never_claims(self):
        throwaway = self.scratch / "hub"
        throwaway.mkdir()
        state = library_ownership.resolve(self.library, throwaway)
        self.assertFalse(state.owned)
        self.assertIn("temp folder", state.reason)
        self.assertFalse((self.library / library_ownership.OWNER_FILE).exists(), "a throwaway Hub must not claim")

    def test_the_first_real_hub_claims_an_unclaimed_library_and_keeps_it(self):
        first = library_ownership.resolve(self.library, self.owner)
        self.assertTrue(first.owned and first.claimed_now)
        record = json.loads((self.library / library_ownership.OWNER_FILE).read_text())
        self.assertEqual(record["data_dir"], str(self.owner))
        self.assertEqual(os.stat(self.library / library_ownership.OWNER_FILE).st_mode & 0o777, 0o600)
        again = library_ownership.resolve(self.library, self.owner)
        self.assertTrue(again.owned and not again.claimed_now)

    def test_a_different_data_dir_is_read_only_while_the_owner_still_exists(self):
        library_ownership.resolve(self.library, self.owner)
        other = self.base / "scratch-but-not-temp" / "hub"
        other.mkdir(parents=True)
        state = library_ownership.resolve(self.library, other)
        self.assertFalse(state.owned)
        self.assertIn(str(self.owner), state.reason)

    def test_a_library_from_another_mac_is_never_owned(self):
        library_ownership.resolve(self.library, self.owner)
        path = self.library / library_ownership.OWNER_FILE
        record = json.loads(path.read_text())
        record["machine"] = "SOME-OTHER-MAC"
        record["data_dir"] = str(self.base / "gone")          # even though that path does not exist here
        path.write_text(json.dumps(record))
        state = library_ownership.resolve(self.library, self.owner)
        self.assertFalse(state.owned)
        self.assertIn("another Mac", state.reason)

    def test_a_reinstall_whose_old_data_dir_is_gone_takes_over(self):
        library_ownership.resolve(self.library, self.owner)
        moved = self.base / "new-app-data" / "hub"
        moved.mkdir(parents=True)
        os.rename(self.owner, self.base / "old")               # the recorded directory no longer exists
        state = library_ownership.resolve(self.library, moved)
        self.assertTrue(state.owned and state.claimed_now, state.reason)

    def test_a_damaged_record_is_read_only_not_a_free_for_all(self):
        (self.library / library_ownership.OWNER_FILE).write_text("{ nope")
        state = library_ownership.resolve(self.library, self.owner)
        self.assertFalse(state.owned)
        self.assertIn("JSON", state.reason)

    def test_an_unwritable_library_cannot_be_claimed(self):
        with mock.patch.object(library_ownership, "_write", return_value=False):
            self.assertFalse(library_ownership.resolve(self.library, self.owner).owned)

    def test_weights_never_move_from_a_real_library_into_a_temp_trash(self):
        trash = self.scratch / "trash"
        self.assertEqual(retired_models.retire_installed(self.library, trash), [])
        self.assertTrue(weights_intact(self.library))
        self.assertFalse(trash.exists())

    def test_a_library_in_temp_may_still_use_a_trash_in_temp(self):
        """The unit tests (and a kit scratch copy) retire inside one temp tree; that is not "across"."""
        with mock.patch.object(library_ownership, "temp_roots", lambda: [self.base]):
            moved = retired_models.retire_installed(self.library, self.base / "trash")
        self.assertEqual(moved, [RETIRED])


# One Hub start-up pass in a clean process (the data directory is read at import).
STARTUP = r'''
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ["PROJECT_ROOT"])
from components import library_ownership
library_ownership.temp_roots = lambda: [Path(p) for p in json.loads(os.environ["PRETEND_TEMP"])]
from components.hub import standalone
print(json.dumps({"retired": standalone.retire_installed_models()}))
'''


class IncidentReplay(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name).resolve()
        self.library = make_library(self.base)
        self.scratch = self.base / "scratch"
        self.scratch.mkdir()

    def start(self, data_dir: Path) -> dict:
        data_dir.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "PROJECT_ROOT": str(ROOT), "STUDIOHUB_DATA_DIR": str(data_dir),
               "STUDIOHUB_MODELS_DIR": str(self.library), "PRETEND_TEMP": json.dumps([str(self.scratch)]),
               "STUDIOHUB_MODEL_MANIFESTS": str(ROOT / "model-manifests")}
        result = subprocess.run([sys.executable, "-B", "-c", STARTUP], env=env, text=True,
                                capture_output=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1]), result.stdout

    def test_a_throwaway_data_dir_over_the_real_library_moves_nothing(self):
        (moved, out) = self.start(self.scratch / "hub")
        self.assertEqual(moved, {"retired": []}, out)
        self.assertTrue(weights_intact(self.library), "the real weights were moved into the throwaway Trash Folder")
        self.assertFalse((self.scratch / "hub" / "trash").exists())
        self.assertIn("read-only", out)

    def test_only_the_owning_hub_moves_packages(self):
        owner = self.base / "app-data" / "hub"
        stranger = self.base / "other-data" / "hub"
        (moved, _) = self.start(owner)                         # first real Hub claims, then sheds
        self.assertEqual(moved["retired"], [RETIRED])
        self.assertTrue((self.library / KEPT / "rev" / "weights.bin").is_file())
        # The one remaining mover is retirement; shelving is gone, so a package nothing retires and
        # nothing shelves (the old `whisper-base`) stays exactly where it is on every start.
        self.assertTrue((self.library / SHELVED / "rev" / "weights.bin").is_file())
        self.assertEqual(len(list((owner / "trash").glob("*/retired-models/*"))), 1)
        # restore the retired package by hand, then start a different (non-temp) data dir
        os.rename(next((owner / "trash").glob(f"*/retired-models/{RETIRED}-*")), self.library / RETIRED)
        (moved, out) = self.start(stranger)
        self.assertEqual(moved, {"retired": []}, out)
        self.assertTrue(weights_intact(self.library))
        (moved, _) = self.start(owner)
        self.assertEqual(moved["retired"], [RETIRED])

    def test_an_existing_install_migrates_once_and_keeps_cleaning_up(self):
        """A fleet Mac updated to this build has no ownership record: its own app data dir claims the
        library on first start, and the retirement clean-up then behaves exactly as before."""
        app_data = self.base / "Library" / "Application Support" / "StudioHub Standalone" / "hub"
        self.assertFalse((self.library / library_ownership.OWNER_FILE).exists())
        (moved, out) = self.start(app_data)
        self.assertEqual(moved["retired"], [RETIRED], out)
        record = json.loads((self.library / library_ownership.OWNER_FILE).read_text())
        self.assertEqual(record["data_dir"], str(app_data))
        self.assertEqual(len(list((app_data / "trash").glob("*/retired-models/*"))), 1)   # purged by the 15-day Trash Folder rule
        os.rename(next((app_data / "trash").glob("*/retired-models/*")), self.library / RETIRED)
        (moved, _) = self.start(app_data)                      # the next start: already owned, still cleans up
        self.assertEqual(moved["retired"], [RETIRED])

    def test_the_old_keep_all_setting_no_longer_changes_anything(self):
        # A Mac that was marked keep-all keeps every model, and so does every other Mac now: the
        # setting is gone and the only mover left is retirement.
        owner = self.base / "app-data" / "hub"
        owner.mkdir(parents=True)
        (owner / "device_settings.json").write_text(json.dumps({"keep_all_models": True}))
        (moved, _) = self.start(owner)
        self.assertTrue((self.library / SHELVED / "rev" / "weights.bin").is_file())
        self.assertEqual(moved["retired"], [RETIRED])


if __name__ == "__main__":
    unittest.main()
