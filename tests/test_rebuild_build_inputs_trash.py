"""`packaging/rebuild_build_inputs.sh` must never move a build input through a checkout `_trash`.

The 2026-11-25 gate (issue row `20261125-32`) lost its first run here: the canonical checkout carried
an `_trash` symlink into `~/Desktop/_trash/hub-studio-kh`, the YuE2 builder seals its output
`chmod -R a-w`, and the replacement step's `mv` through that link answered `Permission denied` after
the tree had already been rebuilt and normalised.

Three invariants, each a measured failure:
1. the trash is a real Desktop folder this script names itself — an override is honoured, and a
   **symlink** at that path is refused with a sentence instead of being traversed;
2. a read-only tree is opened for the move first (`chmod -R u+w`), so a sealed build input can be
   replaced at all;
3. the in-place rebuild is not moved aside and "restored" — in this checkout the builder's own
   `$SOURCE/.build` *is* `$ROOT/.build`, and the old code filed the fresh tree in the trash.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[1]
SCRIPT = WORKTREE / "packaging" / "rebuild_build_inputs.sh"


def _run(root: Path, trash: Path, *, only: str = "prepare-kit") -> subprocess.CompletedProcess:
    env = {**os.environ, "KH_BUILD_INPUTS_TRASH": str(trash)}
    return subprocess.run(
        ["bash", str(SCRIPT), "--root", str(root), "--only", only],
        cwd=str(root), env=env, capture_output=True, text=True, timeout=300, check=False,
    )


@unittest.skipUnless(SCRIPT.is_file(), "rebuild_build_inputs.sh is not in this tree")
@unittest.skipUnless(shutil.which("uv") and shutil.which("git"), "the recipe needs uv and git")
class TrashAndReadOnlyTests(unittest.TestCase):
    def test_a_read_only_input_is_opened_and_retired_to_the_override_trash(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hub-rebuild-") as tmp:
            root, trash = Path(tmp), Path(tmp) / "trash"
            build = root / ".build"
            build.mkdir()
            stale = build / "prepare-kit.sh"
            # Deliberately without the marker the recipe looks for, so this copy counts as stale.
            stale.write_text("#!/bin/sh\n# an older copy of the kit script\n")
            stale.chmod(0o555)                     # sealed the way the YuE2 builder seals its output

            done = _run(root, trash)

            self.assertEqual(done.returncode, 0, f"stdout:\n{done.stdout}\nstderr:\n{done.stderr}")
            self.assertIn("PRUNEINDEX", (build / "prepare-kit.sh").read_text(),
                          "the stale copy was not replaced by the tracked one")
            retired = trash / "prepare-kit.sh"
            self.assertTrue(retired.is_file(), f"the stale copy is not in the override trash: {trash}")
            self.assertIn("an older copy of the kit script", retired.read_text(),
                          "the trash must hold the copy that was replaced, not the new one")

    def test_a_symlinked_trash_is_refused_rather_than_traversed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hub-rebuild-") as tmp:
            root = Path(tmp)
            (root / ".build").mkdir()
            elsewhere = root / "elsewhere"
            elsewhere.mkdir()
            link = root / "trash-link"
            link.symlink_to(elsewhere, target_is_directory=True)

            done = _run(root, link)

            self.assertEqual(done.returncode, 1, f"stdout:\n{done.stdout}\nstderr:\n{done.stderr}")
            self.assertIn("is a symlink", done.stderr)
            self.assertEqual(list(elsewhere.iterdir()), [],
                             "something was written through the symlinked trash")


if __name__ == "__main__":
    unittest.main()
