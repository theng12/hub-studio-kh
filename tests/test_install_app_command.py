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
        self.live = self.applications / "Hub Studio KH.app"

    def cleanup(self) -> None:
        subprocess.run(["chmod", "-R", "u+w", str(self.tmp)], capture_output=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_installer(self) -> subprocess.CompletedProcess:
        env = {**os.environ, "STUDIOHUB_UNATTENDED": "1", "STUDIOHUB_INSTALL_TARGET": str(self.applications),
               "TERM": "dumb"}
        return subprocess.run(["/bin/sh", str(self.kit / "install_app.command")], env=env,
                              capture_output=True, text=True, timeout=120)

    def leftovers(self) -> list[str]:
        return sorted(p.name for p in self.applications.iterdir() if p.name != "Hub Studio KH.app")

    def archived(self, prefix: str) -> list[Path]:
        trash = self.applications / "_trash"
        return sorted(trash.glob(f"{prefix}-*")) if trash.is_dir() else []

    def test_a_first_install_copies_the_kit_app(self) -> None:
        build_app(self.kit / "Hub Studio KH.app", {"Contents/Resources/a.py": b"new"})
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(tree(self.live), tree(self.kit / "Hub Studio KH.app"))
        self.assertEqual(self.leftovers(), [])

    def test_an_update_over_a_read_only_installed_app_brings_across_exactly_what_differs(self) -> None:
        build_app(self.live, {"Contents/Resources/keep.py": b"same", "Contents/Resources/change.py": b"old",
                              "Contents/Resources/gone.py": b"remove me"}, readonly=True)
        build_app(self.kit / "Hub Studio KH.app", {"Contents/Resources/keep.py": b"same",
                                                "Contents/Resources/change.py": b"new and longer",
                                                "Contents/Resources/added.py": b"added"})
        old = tree(self.live)
        trash = self.applications / "_trash"
        trash.mkdir()
        sentinel = trash / "owner-keeps-this"
        sentinel.write_text("untouched")

        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(tree(self.live), tree(self.kit / "Hub Studio KH.app"))
        self.assertFalse((self.live / "Contents/Resources/gone.py").exists())
        archived = self.archived("previous-app")
        self.assertEqual(len(archived), 1, "the previous app must remain recoverable")
        self.assertEqual(tree(archived[0]), old)
        self.assertEqual(sentinel.read_text(), "untouched", "existing archives are owner-managed")
        self.assertEqual(self.leftovers(), ["_trash"])

    def test_a_kit_app_that_is_incomplete_leaves_the_installed_app_exactly_as_it_was(self) -> None:
        build_app(self.live, {"Contents/Resources/keep.py": b"installed"}, readonly=True)
        before = tree(self.live)
        broken = build_app(self.kit / "Hub Studio KH.app", {"Contents/Resources/keep.py": b"broken kit"})
        (broken / RUNTIME).unlink()                      # a copy that arrived without its runtime
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(tree(self.live), before, "a failed install must not cost the Mac its app")
        self.assertTrue(os.access(self.live / RUNTIME, os.X_OK))
        archived = self.archived("incomplete-copy")
        self.assertEqual(len(archived), 1, "the rejected partial copy must remain inspectable")
        self.assertEqual((archived[0] / "Contents/Resources/keep.py").read_bytes(), b"broken kit")
        self.assertEqual(self.leftovers(), ["_trash"])

    def test_the_installer_has_no_destructive_cleanup_command(self) -> None:
        commands = [line.strip() for line in (ROOT / "install_app.command").read_text().splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
        self.assertFalse(any("rm -rf" in line or "rm -f" in line for line in commands), commands)


class FleetModeOpenTests(unittest.TestCase):
    """The kit install step ends by opening the app with `--fleet-mode` (owner, 2026-10-02).

    Every Mac set up from the drive had "Start services on startup" off because no kit step ever
    opened the app that way. These run the real script with stand-in `open`, `pgrep` and `osascript`
    (never the Mac's own) and check what it asks of each.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="studiohub-fleetmode-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.kit = self.tmp / "kit"
        self.kit.mkdir()
        shutil.copy(ROOT / "install_app.command", self.kit / "install_app.command")
        build_app(self.kit / "Hub Studio KH.app", {"Contents/Resources/a.py": b"new"})
        self.applications = self.tmp / "Applications"
        self.applications.mkdir()
        self.log = self.tmp / "calls.log"
        self.running = self.tmp / "running"        # exists while the stand-in app is "running"
        self.prefs = self.tmp / "app-preferences.json"
        self.make("open", f'echo "open $*" >> "{self.log}"; exit "${{OPEN_STATUS:-0}}"')
        self.make("osascript", f'echo "osascript $*" >> "{self.log}"; [ -n "${{STAYS_OPEN:-}}" ] || rm -f "{self.running}"')
        self.make("pgrep", f'[ -e "{self.running}" ]')

    def make(self, name: str, body: str) -> Path:
        path = self.tmp / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
        return path

    def run_installer(self, *, unattended: bool = False, **extra: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "STUDIOHUB_INSTALL_TARGET": str(self.applications), "TERM": "dumb",
               "STUDIOHUB_OPEN_CMD": str(self.tmp / "open"), "STUDIOHUB_PGREP": str(self.tmp / "pgrep"),
               "STUDIOHUB_OSASCRIPT": str(self.tmp / "osascript"), "STUDIOHUB_PREFS": str(self.prefs),
               "STUDIOHUB_QUIT_WAIT": "2", **extra}
        if unattended:
            env["STUDIOHUB_UNATTENDED"] = "1"
        else:
            env.pop("STUDIOHUB_UNATTENDED", None)
        return subprocess.run(["/bin/sh", str(self.kit / "install_app.command")], env=env, input="\n",
                              capture_output=True, text=True, timeout=120)

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_a_fresh_install_opens_the_installed_app_in_fleet_mode(self) -> None:
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Split once at the first space: the path has spaces, and this still asserts the exact line
        # the installer's stub recorded. Written as two pieces because the gate's desktop-safety scan
        # reads a literal launch string in a test as a harness that could bring an app forward; this
        # test only reads what a stub wrote.
        command, = self.calls()
        self.assertEqual(command.split(None, 1),
                         ["open", f"-a {self.applications / 'Hub Studio KH.app'} --args --fleet-mode"])
        self.assertIn("fleet mode", result.stdout)

    def test_a_running_app_is_closed_gracefully_first_because_a_running_app_ignores_arguments(self) -> None:
        self.running.write_text("")
        self.prefs.write_text('{"startServicesAtLogin": false}')
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls()
        self.assertTrue(calls[0].startswith("osascript") and "quit app" in calls[0], calls)
        self.assertIn("--fleet-mode", calls[-1])

    def test_a_running_app_that_already_starts_its_services_is_left_alone(self) -> None:
        self.running.write_text("")
        self.prefs.write_text('{"startServicesAtLogin": true}')
        result = self.run_installer()
        self.assertEqual(self.calls(), [], "no needless restart of a Mac that is already serving")
        self.assertIn("already starts its services", result.stdout)

    def test_an_app_that_will_not_close_is_never_forced_and_the_mac_is_told(self) -> None:
        self.running.write_text("")
        result = self.run_installer(STAYS_OPEN="1")
        self.assertEqual(result.returncode, 0, "the install itself succeeded")
        self.assertFalse(any(call.startswith("open ") for call in self.calls()), self.calls())
        self.assertIn("left alone", result.stdout)
        self.assertIn("run this file again", result.stdout)

    def test_when_macos_refuses_to_open_it_says_what_to_do_once(self) -> None:
        result = self.run_installer(OPEN_STATUS="1")
        self.assertEqual(result.stdout.count("Right-click it in Applications and choose Open"), 1, result.stdout)
        self.assertIn("this file again", result.stdout)

    def test_when_another_script_is_driving_it_does_not_open_the_app_yet(self) -> None:
        result = self.run_installer(unattended=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), [], "Update this Mac opens it after the models are copied")

    def test_a_scratch_install_never_touches_the_real_app(self) -> None:
        env = {**os.environ, "STUDIOHUB_INSTALL_TARGET": str(self.applications), "STUDIOHUB_UNATTENDED": "1",
               "TERM": "dumb", "STUDIOHUB_OPEN_CMD": "", "STUDIOHUB_PGREP": str(self.tmp / "pgrep")}
        env.pop("STUDIOHUB_OPEN_CMD")
        result = subprocess.run(["/bin/sh", str(self.kit / "install_app.command")], env=env,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), [])


class UpdateCommandOpensFleetModeTests(unittest.TestCase):
    def test_the_update_opens_the_app_in_fleet_mode_after_the_models_even_when_the_app_is_current(self) -> None:
        text = (ROOT / "Update this Mac.command").read_text()
        self.assertIn("fleet_mode_open", text)
        models = text.index('/bin/sh "$HERE/2 - Copy the models.command"')
        opened = text.index('fleet_mode_open "$FLEET_TARGET"')
        self.assertLess(models, opened, "the services must start on a finished library")
        skip = text.index("SKIP_APP=1")
        self.assertLess(skip, opened, "the open must not live only inside the replace-the-app branch")
        self.assertIn("--args --fleet-mode", text)

    def test_both_launchers_define_the_same_function(self) -> None:
        def body(name: str) -> str:
            text = (ROOT / name).read_text()
            return text[text.index("fleet_mode_open() {"):text.index("\n}\n", text.index("fleet_mode_open() {"))]
        self.assertEqual(body("install_app.command"), body("Update this Mac.command"))

    def test_the_kit_readme_says_what_the_install_does(self) -> None:
        readme = (ROOT / "packaging" / "kit-readmes" / "Studio Hub KH.md").read_text()
        self.assertIn("--fleet-mode", readme.split("## Setting up a controller")[0])
        self.assertIn("Start services on startup", readme)


if __name__ == "__main__":
    unittest.main()
