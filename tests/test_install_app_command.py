"""`install_app.command` builds the new app beside the old one and swaps it in only when complete.

It used to delete the installed app before the new one was in place, so a failed move left the Mac
with no app at all, and it copied all ~5 GB for a release that differs by a handful of files. These
run the real script against a scratch Applications folder (`STUDIOHUB_INSTALL_TARGET`), never the
Mac's own.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = "Contents/Resources/runtimes/voice/bin/python3"


def build_app(path: Path, files: dict[str, bytes], *, readonly: bool = False) -> Path:
    for rel, data in {RUNTIME: b"#!/bin/sh\n", **files}.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o755 if rel == RUNTIME else (0o444 if readonly else 0o644))
    if readonly:
        for directory in sorted((p for p in path.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            directory.chmod(0o555)
        path.chmod(0o555)
    return path


def tree(path: Path) -> dict[str, bytes]:
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}


class InstallAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="studiohub-install-"))
        self.addCleanup(self.cleanup)
        self.kit = self.tmp / "kit"
        self.kit.mkdir()
        shutil.copy(ROOT / "install_app.command", self.kit / "install_app.command")
        self.applications = self.tmp / "Applications"
        self.applications.mkdir()
        self.live = self.applications / "Studio Hub.app"

    def cleanup(self) -> None:
        subprocess.run(["chmod", "-R", "u+w", str(self.tmp)], capture_output=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_installer(self) -> subprocess.CompletedProcess:
        env = {**os.environ, "STUDIOHUB_UNATTENDED": "1", "STUDIOHUB_INSTALL_TARGET": str(self.applications),
               "TERM": "dumb"}
        return subprocess.run(["/bin/sh", str(self.kit / "install_app.command")], env=env,
                              capture_output=True, text=True, timeout=120)

    def leftovers(self) -> list[str]:
        return sorted(p.name for p in self.applications.iterdir() if p.name != "Studio Hub.app")

    def test_a_first_install_copies_the_kit_app(self) -> None:
        build_app(self.kit / "Studio Hub.app", {"Contents/Resources/a.py": b"new"})
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(tree(self.live), tree(self.kit / "Studio Hub.app"))
        self.assertEqual(self.leftovers(), [])

    def test_an_update_over_a_read_only_installed_app_brings_across_exactly_what_differs(self) -> None:
        build_app(self.live, {"Contents/Resources/keep.py": b"same", "Contents/Resources/change.py": b"old",
                              "Contents/Resources/gone.py": b"remove me"}, readonly=True)
        build_app(self.kit / "Studio Hub.app", {"Contents/Resources/keep.py": b"same",
                                                "Contents/Resources/change.py": b"new and longer",
                                                "Contents/Resources/added.py": b"added"})
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(tree(self.live), tree(self.kit / "Studio Hub.app"))
        self.assertFalse((self.live / "Contents/Resources/gone.py").exists())
        self.assertEqual(self.leftovers(), [], "the old app and the staging folder must both be cleaned up")

    def test_a_kit_app_that_is_incomplete_leaves_the_installed_app_exactly_as_it_was(self) -> None:
        build_app(self.live, {"Contents/Resources/keep.py": b"installed"}, readonly=True)
        before = tree(self.live)
        broken = build_app(self.kit / "Studio Hub.app", {"Contents/Resources/keep.py": b"broken kit"})
        (broken / RUNTIME).unlink()                      # a copy that arrived without its runtime
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(tree(self.live), before, "a failed install must not cost the Mac its app")
        self.assertTrue(os.access(self.live / RUNTIME, os.X_OK))
        self.assertEqual(self.leftovers(), [])


if __name__ == "__main__":
    unittest.main()
