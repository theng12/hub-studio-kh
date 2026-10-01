"""A retired model must leave a kit drive's model index, not only its folder.

`.build/prepare-kit.sh` moves a retired package's folder to the drive's `_trash`, but the library's
`.studiohub/installed.json` kept the entry, and `qualification/verify-kit.sh` checks every file that
index records - so it failed on every drive the day LTX-2.3 was retired (2026-10-02). The script now
prunes the index too. The prune is a heredoc inside that script (it has to work against the staged
app of the release being prepared), so this test runs the very block the script runs.

`.build/` is git-ignored and local to the release Mac, like the two other tests that read
`prepare-kit.sh`, so without the script the cases below are skipped.
"""
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".build" / "prepare-kit.sh"
RETIRED = "ltx-2-3-22b-ram-12gb-mlx"      # retired 0.6.127; Gemma (gemma-3-12b-it-4bit) is NOT retired
KEPT = "whisper-base"


def _record(package_id, files=("weights.bin",)):
    return {"revision": "r1", "folder": f"{package_id}/r1",
            "files": {name: {"st_size": 4} for name in files}}


@unittest.skipUnless(SCRIPT.is_file(), "prepare-kit.sh is local to the release Mac")
class RetiredModelsLeaveTheKitIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.library = base / "model-library"
        self.trash = base / "_trash"
        (self.library / ".studiohub").mkdir(parents=True)
        self.index_path = self.library / ".studiohub" / "installed.json"
        for package_id in (RETIRED, KEPT):
            folder = self.library / package_id / "r1"
            folder.mkdir(parents=True)
            (folder / "weights.bin").write_bytes(b"data")

    def write_index(self, index):
        self.index_path.write_text(json.dumps(index))

    def prune(self):
        match = re.search(r"<<'PRUNEINDEX'\n(.*?)\nPRUNEINDEX\n", SCRIPT.read_text(), re.S)
        self.assertIsNotNone(match, "prepare-kit.sh has no index prune block")
        return subprocess.run(
            [sys.executable, "-", str(self.library), str(self.trash), str(ROOT / "components")],
            input=match.group(1), text=True, capture_output=True, check=True, cwd=self.tmp.name,
        ).stdout

    def moved_to_trash(self, package_id):
        """What the script's folder step does: the package folder goes to the drive's _trash."""
        (self.trash / "model-library-retired").mkdir(parents=True, exist_ok=True)
        (self.library / package_id).rename(self.trash / "model-library-retired" / package_id)

    def index(self):
        return json.loads(self.index_path.read_text())

    def verifier_would_miss(self):
        """The check `qualification/verify-kit.sh` makes: every recorded file is on disk at its size."""
        missing = []
        for package_id, record in self.index().items():
            for relative, entry in record["files"].items():
                found = [path for path in (self.library / package_id / record["revision"] / relative,
                                           self.library / package_id / relative) if path.is_file()]
                if not found or found[0].stat().st_size != entry["st_size"]:
                    missing.append(f"{package_id}/{relative}")
        return missing

    def test_the_failure_this_fixes_is_real(self):
        self.write_index({RETIRED: _record(RETIRED), KEPT: _record(KEPT)})
        self.moved_to_trash(RETIRED)
        self.assertEqual(self.verifier_would_miss(), [f"{RETIRED}/weights.bin"],
                         "a moved folder with its entry left behind is what failed verify-kit.sh")

    def test_a_retired_entry_whose_folder_is_gone_is_dropped_and_the_old_index_kept(self):
        self.write_index({RETIRED: _record(RETIRED), KEPT: _record(KEPT)})
        original = self.index_path.read_bytes()
        self.moved_to_trash(RETIRED)
        self.assertIn("pruned " + RETIRED, self.prune())
        self.assertEqual(list(self.index()), [KEPT])
        self.assertEqual(self.verifier_would_miss(), [], "verify-kit.sh has nothing left to fail on")
        backups = list(self.trash.glob("installed.json.before-prune-*"))
        self.assertEqual(len(backups), 1, "nothing is deleted: the old index goes to _trash")
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(stat.S_IMODE(self.index_path.stat().st_mode), 0o600)
        self.assertFalse(list((self.library / ".studiohub").glob("*.tmp")), "no temp file left behind")

    def test_a_retired_entry_whose_folder_is_still_there_stays_so_the_verifier_keeps_saying_so(self):
        self.write_index({RETIRED: _record(RETIRED), KEPT: _record(KEPT)})
        self.assertIn("nothing to prune", self.prune())
        self.assertEqual(set(self.index()), {RETIRED, KEPT})
        self.assertFalse(self.trash.exists(), "no backup when nothing changed")

    def test_a_missing_entry_that_is_not_retired_is_a_damaged_copy_and_is_left_alone(self):
        self.write_index({RETIRED: _record(RETIRED), KEPT: _record(KEPT)})
        self.moved_to_trash(RETIRED)
        (self.library / KEPT / "r1" / "weights.bin").unlink()
        self.prune()
        self.assertEqual(list(self.index()), [KEPT])
        self.assertEqual(self.verifier_would_miss(), [f"{KEPT}/weights.bin"])

    def test_a_second_run_changes_nothing(self):
        self.write_index({RETIRED: _record(RETIRED), KEPT: _record(KEPT)})
        self.moved_to_trash(RETIRED)
        self.prune()
        after_first = self.index_path.read_bytes()
        self.assertIn("nothing to prune", self.prune())
        self.assertEqual(self.index_path.read_bytes(), after_first)
        self.assertEqual(len(list(self.trash.glob("installed.json.before-prune-*"))), 1)

    def test_a_drive_with_no_index_or_an_unreadable_one_is_not_touched(self):
        self.index_path.unlink() if self.index_path.exists() else None
        self.assertIn("none to prune", self.prune())
        self.index_path.write_text("{not json")
        self.assertIn("NOT pruned", self.prune())
        self.assertEqual(self.index_path.read_text(), "{not json")

    def test_the_script_runs_it_after_the_folder_move_and_before_the_manifest(self):
        text = SCRIPT.read_text()
        move = text.index("retired models leave the model library (moved to _trash")
        prune = text.index("PRUNEINDEX\n", text.index("retired models leave the model library index too"))
        manifest = text.index('echo "### $VOLUME: kit manifest"')
        self.assertLess(move, prune)
        self.assertLess(prune, manifest, "the manifest is built from the index, so it must be pruned first")


if __name__ == "__main__":
    unittest.main()
