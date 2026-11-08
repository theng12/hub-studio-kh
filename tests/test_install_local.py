"""The install guards, each proved to fire on the defect it exists for.

`packaging/install_local.sh` failed in the worst possible way on 2026-09-26: the disk filled during
the copy, `cp -Rp` stopped after ~1.4 GB of a 2.9 GB bundle, `rm -rf` could not clear the wreckage
(the image runtime's `tcl9` directories are `dr-xr-xr-x`, and unlinking an entry needs write
permission on its *parent*), and the rollback's `mv` then put the previous app **inside** the
wreckage. `/Applications/Studio Hub.app` was left with no `Info.plist` and no `MacOS` — a state no
later step could detect, because the script had already reported its last line.

Both tests drive the real script with `STUDIOHUB_INSTALL_TARGET` pointed at a temp directory, so
neither touches `/Applications` or the running app. The rest of the script is skipped by the guard
under test: the free-space check runs before anything is moved, and the poisoned stage makes the
copy fail before the app is ever quit.

**`unittest`, not `pytest`, and that is a fix rather than a preference.** This file was written with
pytest-style functions, and it is the **only** such file in `tests/` — while the release gate is
`python3 -m unittest discover -s tests -p 'test_*.py'`. `unittest` imports the module and collects
**nothing** from it, silently: measured 2026-09-28, the two tests below reported `Ran 0 tests` under
the gate and passed only when pytest was run by hand. So for two days these guards — written because
an install once bricked the installed app — were not in the gate at all, which is the same
"a guard the runner does not collect is not a guard" that `docs/tasks/HANDOFF.md` records for
`LaneDecidesBoundaryTests`. Ported so the gate collects them.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "packaging" / "install_local.sh"


def _stage(root: Path, *, poison: bool = False, read_only_dir: bool = False) -> Path:
    """A miniature staged app with the shape the script checks for."""
    app = root / "StudioHub-test" / "Studio Hub.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text(
        '<?xml version="1.0"?><plist><dict>'
        "<key>StudioHubBuild</key><string>tEsTbUiLd</string>"
        "<key>CFBundleShortVersionString</key><string>0.0.0</string>"
        "</dict></plist>",
        encoding="utf-8",
    )
    (app / "Contents" / "MacOS" / "StudioHub").write_text("#!/bin/sh\n", encoding="utf-8")
    runtime = app / "Contents" / "Resources" / "runtimes" / "voice" / "bin"
    runtime.mkdir(parents=True)
    interpreter = runtime / "python3"
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    interpreter.chmod(0o755)
    if read_only_dir:
        # The shape that defeats a bare `rm -rf`: a directory with no write bit, so its entries
        # cannot be unlinked until the mode is changed.
        locked = app / "Contents" / "Resources" / "runtimes" / "image" / "lib" / "tcl9"
        locked.mkdir(parents=True)
        (locked / "init.tcl").write_text("# read only\n", encoding="utf-8")
        locked.chmod(0o555)
    if poison:
        # Unreadable to its owner, so `cp -Rp` fails part-way the way a full disk does.
        poison = app / "Contents" / "Resources" / "poison.bin"
        poison.write_text("x", encoding="utf-8")
        poison.chmod(0)
    return app


def _previous_app(root: Path) -> Path:
    """An installed app that must survive a failed install intact."""
    target = root / "Applications" / "Studio Hub.app"
    (target / "Contents").mkdir(parents=True)
    (target / "Contents" / "Info.plist").write_text("<plist>previous</plist>", encoding="utf-8")
    (target / "Contents" / "MacOS").mkdir()
    (target / "Contents" / "MacOS" / "StudioHub").write_text("previous build\n", encoding="utf-8")
    return target


def _run(stage: Path, target: Path, free_kb: int | None = None,
         **overrides: str) -> subprocess.CompletedProcess:
    environment = dict(os.environ)
    environment["STUDIOHUB_INSTALL_TARGET"] = str(target)
    # Keep the real readiness markers out of it: the script clears them on a successful install.
    environment["STUDIOHUB_INSTALL_DATA"] = str(target.parent / "data")
    if free_kb is not None:
        environment["STUDIOHUB_INSTALL_FREE_KB"] = str(free_kb)
    environment.update(overrides)
    return subprocess.run(
        ["bash", str(SCRIPT), str(stage)],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def _fake_commands(root: Path, *, pgrep_pid: int | None) -> Path:
    """A PATH entry whose `pgrep` answers for the test and whose `osascript` leaves a marker."""
    bin_dir = root / "fake-bin"
    bin_dir.mkdir()
    pgrep = bin_dir / "pgrep"
    pgrep.write_text("#!/bin/sh\nexit 1\n" if pgrep_pid is None else f"#!/bin/sh\necho {pgrep_pid}\n",
                     encoding="utf-8")
    pgrep.chmod(0o755)
    osascript = bin_dir / "osascript"
    osascript.write_text(f"#!/bin/sh\ntouch '{root / 'osascript-called'}'\n", encoding="utf-8")
    osascript.chmod(0o755)
    return bin_dir


def _unlock(root: Path) -> None:
    """`chmod 0` on the poisoned file would otherwise stop the temp dir being removed."""
    for path in sorted(root.rglob("*"), reverse=True):
        try:
            path.chmod(path.stat().st_mode | stat.S_IRWXU)
        except OSError:
            pass


class TheInstallGuardsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # The unreadable file has to be unlocked *before* the directory is removed, so this cleanup
        # is registered after `addCleanup(self.tmp.cleanup)` and therefore runs first.
        self.addCleanup(_unlock, Path(self.tmp.name))

    def test_it_refuses_to_install_when_the_copy_would_fill_the_disk(self) -> None:
        """A full disk is what produced the un-installable bundle; refuse before touching anything."""
        root = Path(self.tmp.name)
        stage = _stage(root)
        target = _previous_app(root)
        before = sorted(p.relative_to(target).as_posix() for p in target.rglob("*"))

        completed = _run(stage, target, free_kb=1)  # 1 KB free against a stage needing far more

        output = completed.stdout + completed.stderr
        self.assertNotEqual(completed.returncode, 0, output)
        self.assertIn("refusing to install", output, output)
        after = sorted(p.relative_to(target).as_posix() for p in target.rglob("*"))
        self.assertEqual(after, before,
                         "the previous app was touched even though the install was refused")

    def test_a_failed_copy_restores_the_previous_app_instead_of_nesting_it(self) -> None:
        """The defect: `rm -rf` half-fails on a read-only tree, then `mv` nests the old app in it."""
        root = Path(self.tmp.name)
        stage = _stage(root, poison=True, read_only_dir=True)
        target = _previous_app(root)
        trash = target.parent / "_trash"
        trash.mkdir()
        sentinel = trash / "owner-keeps-this"
        sentinel.write_text("untouched", encoding="utf-8")

        completed = _run(stage, target)

        output = completed.stdout + completed.stderr
        self.assertNotEqual(completed.returncode, 0, output)
        # The previous app is back in place, whole...
        self.assertEqual((target / "Contents" / "Info.plist").read_text(encoding="utf-8"),
                         "<plist>previous</plist>")
        self.assertEqual((target / "Contents" / "MacOS" / "StudioHub").read_text(encoding="utf-8"),
                         "previous build\n")
        # ...and nothing was nested inside it, which is the shape that made the bundle unusable.
        self.assertEqual(list(target.glob(".studiohub-old-*")), [],
                         "the previous app was moved inside the target")
        self.assertEqual(list(target.glob("*/Studio Hub.app")), [],
                         "a whole app was nested inside another")
        failed = list(trash.glob("failed-install-*"))
        self.assertEqual(len(failed), 1, "the partial copy must be archived before rollback")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "untouched")

    def test_a_successful_install_archives_the_previous_app_and_readiness_markers(self) -> None:
        root = Path(self.tmp.name)
        stage = _stage(root)
        target = _previous_app(root)
        data = target.parent / "data"
        (data / "hub").mkdir(parents=True)
        (data / "hub-ready.json").write_text("old hub", encoding="utf-8")
        (data / "hub" / "music-ready.json").write_text("old music", encoding="utf-8")
        trash = target.parent / "_trash"
        trash.mkdir()
        sentinel = trash / "owner-keeps-this"
        sentinel.write_text("untouched", encoding="utf-8")

        completed = _run(stage, target)

        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, output)
        self.assertEqual((target / "Contents" / "Info.plist").read_text(encoding="utf-8"),
                         (stage / "Contents" / "Info.plist").read_text(encoding="utf-8"))
        previous = list(trash.glob("previous-app-*"))
        self.assertEqual(len(previous), 1, "the replaced app must remain recoverable")
        self.assertEqual((previous[0] / "Contents" / "Info.plist").read_text(encoding="utf-8"),
                         "<plist>previous</plist>")
        data_trash = data / "_trash"
        self.assertEqual([p.read_text(encoding="utf-8") for p in data_trash.glob("hub-ready-*")],
                         ["old hub"])
        self.assertEqual([p.read_text(encoding="utf-8") for p in data_trash.glob("music-ready-*")],
                         ["old music"])
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "untouched")

    def test_install_can_archive_the_previous_app_in_project_trash(self) -> None:
        root = Path(self.tmp.name)
        stage = _stage(root)
        target = _previous_app(root)
        project_trash = root / "project" / "_trash"
        with mock.patch.dict(os.environ, {"STUDIOHUB_INSTALL_TRASH": str(project_trash)}):
            completed = _run(stage, target)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        previous = list(project_trash.glob("previous-app-*"))
        self.assertEqual(len(previous), 1)
        self.assertEqual((previous[0] / "Contents/Info.plist").read_text(), "<plist>previous</plist>")
        self.assertFalse((target.parent / "_trash").exists())

    def test_the_local_installer_has_no_destructive_cleanup_command(self) -> None:
        commands = [line.strip() for line in SCRIPT.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
        self.assertFalse(any("rm -rf" in line or "rm -f" in line for line in commands), commands)

    def test_a_temp_path_install_never_starts_an_app(self) -> None:
        """The relaunch added for the "installed but not running" defect must not fire for a temp path.

        The stop half of the script is already guarded on the real install target for exactly this
        reason; the start half has to match, or every guard test above launches Studio Hub.
        """
        source = SCRIPT.read_text(encoding="utf-8")
        start = source.index("== starting Studio Hub")
        guard = source.rindex('if [ "$TARGET" = "/Applications/Studio Hub.app" ] && [ "$START" = 1 ]; then',
                              0, start)
        self.assertGreater(guard, 0)
        # The guard must open before the launch and be the nearest one above it.
        between = source[guard:start]
        self.assertNotIn("\nfi\n", between,
                         "the relaunch sits outside the real-target guard, so a temp install starts an app")

    def test_start_can_be_turned_off_and_a_running_app_is_refused_not_killed(self) -> None:
        """Off mode must leave a running Hub alone: killing it would leave it down with no relaunch."""
        root = Path(self.tmp.name)
        stage = _stage(root)
        target = _previous_app(root)
        before = sorted(p.relative_to(target).as_posix() for p in target.rglob("*"))
        fake_bin = _fake_commands(root, pgrep_pid=4242)

        completed = _run(stage, target, STUDIOHUB_INSTALL_START="0",
                         PATH=f"{fake_bin}:{os.environ['PATH']}")

        output = completed.stdout + completed.stderr
        self.assertNotEqual(completed.returncode, 0, output)
        self.assertIn("refusing to install", output, output)
        self.assertIn("4242", output, output)
        self.assertEqual(sorted(p.relative_to(target).as_posix() for p in target.rglob("*")), before,
                         "the running app's bundle was touched in off mode")
        # The off branch must not reach osascript (`quit` can launch a stopped app) or kill.
        off_block = SCRIPT.read_text(encoding="utf-8")
        off_block = off_block[off_block.index('if [ "$START" = 0 ]; then'):]
        off_block = off_block[:off_block.index("\nelse\n")]
        commands = [line for line in off_block.splitlines() if not line.lstrip().startswith("#")]
        self.assertFalse(any("osascript" in line or "kill" in line for line in commands), commands)

    def test_start_off_installs_without_launching(self) -> None:
        root = Path(self.tmp.name)
        stage = _stage(root)
        target = _previous_app(root)
        fake_bin = _fake_commands(root, pgrep_pid=None)

        completed = _run(stage, target, STUDIOHUB_INSTALL_START="0",
                         PATH=f"{fake_bin}:{os.environ['PATH']}")

        output = completed.stdout + completed.stderr
        self.assertEqual(completed.returncode, 0, output)
        self.assertNotIn("== starting Studio Hub", output, output)
        self.assertFalse((root / "osascript-called").exists(),
                         "off mode used osascript, whose quit can launch a stopped app")

    def test_an_invalid_start_control_is_refused_before_anything_is_touched(self) -> None:
        root = Path(self.tmp.name)
        stage = _stage(root)
        target = _previous_app(root)
        before = sorted(p.relative_to(target).as_posix() for p in target.rglob("*"))
        for value in ("yes", "2", "off"):
            with self.subTest(value=value):
                completed = _run(stage, target, STUDIOHUB_INSTALL_START=value)
                output = completed.stdout + completed.stderr
                self.assertNotEqual(completed.returncode, 0, output)
                self.assertIn("STUDIOHUB_INSTALL_START", output, output)
                self.assertIn("1 or 0", output, output)
                self.assertEqual(sorted(p.relative_to(target).as_posix() for p in target.rglob("*")),
                                 before, "the previous app was touched despite an invalid control")


if __name__ == "__main__":
    unittest.main()
