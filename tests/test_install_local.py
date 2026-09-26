"""The two install guards, each proved to fire on the defect it exists for.

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
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

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


def _run(stage: Path, target: Path, free_kb: int | None = None) -> subprocess.CompletedProcess:
    environment = dict(os.environ)
    environment["STUDIOHUB_INSTALL_TARGET"] = str(target)
    # Keep the real readiness markers out of it: the script clears them on a successful install.
    environment["STUDIOHUB_INSTALL_DATA"] = str(target.parent / "data")
    if free_kb is not None:
        environment["STUDIOHUB_INSTALL_FREE_KB"] = str(free_kb)
    return subprocess.run(
        ["bash", str(SCRIPT), str(stage)],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def test_it_refuses_to_install_when_the_copy_would_fill_the_disk(tmp_path):
    """A full disk is what produced the un-installable bundle; refuse before touching anything."""
    stage = _stage(tmp_path)
    target = _previous_app(tmp_path)
    before = sorted(p.relative_to(target).as_posix() for p in target.rglob("*"))

    completed = _run(stage, target, free_kb=1)  # 1 KB free against a stage needing far more

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0, output
    assert "refusing to install" in output, output
    after = sorted(p.relative_to(target).as_posix() for p in target.rglob("*"))
    assert after == before, "the previous app was touched even though the install was refused"


def test_a_failed_copy_restores_the_previous_app_instead_of_nesting_it(tmp_path):
    """The defect: `rm -rf` half-fails on a read-only tree, then `mv` nests the old app inside it."""
    stage = _stage(tmp_path, poison=True, read_only_dir=True)
    target = _previous_app(tmp_path)

    completed = _run(stage, target)

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0, output
    # The previous app is back in place, whole...
    assert (target / "Contents" / "Info.plist").read_text(encoding="utf-8") == "<plist>previous</plist>"
    assert (target / "Contents" / "MacOS" / "StudioHub").read_text(encoding="utf-8") == "previous build\n"
    # ...and nothing was nested inside it, which is the shape that made the bundle unusable.
    assert not list(target.glob(".studiohub-old-*")), "the previous app was moved inside the target"
    assert not list(target.glob("*/Studio Hub.app")), "a whole app was nested inside another"


@pytest.fixture(autouse=True)
def _unlock_before_cleanup(tmp_path):
    """`chmod 0` on the poisoned file would otherwise stop pytest removing its own temp dir."""
    yield
    for path in sorted(tmp_path.rglob("*"), reverse=True):
        try:
            path.chmod(path.stat().st_mode | stat.S_IRWXU)
        except OSError:
            pass
