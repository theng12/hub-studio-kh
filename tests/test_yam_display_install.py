"""Yam Display rides in every kit: `Extras/Install Yam Display.command`, run by the install and update launchers.

Everything here runs the real shell scripts against scratch folders. `STUDIOHUB_YAMDISPLAY_TARGET` (or
`STUDIOHUB_INSTALL_TARGET`) stands in for Applications and `STUDIOHUB_HDIUTIL` for `hdiutil`, so no test
mounts anything or touches this Mac's own /Applications. One test uses the real `hdiutil` against a DMG it
builds itself, attached at a scratch mount point.
"""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTRAS_SRC = ROOT / "packaging" / "kit-extras"
COMMAND = "Install Yam Display.command"
RUNTIME = "Contents/Resources/runtimes/voice/bin/python3"

# A stand-in `hdiutil`: `attach ... -mountpoint DIR image` fills DIR from $FAKE_APP_SRC; every call is logged.
FAKE_HDIUTIL = """#!/bin/sh
echo "$*" >> "$FAKE_HDIUTIL_LOG"
case "$1" in
  attach)
    [ -n "${FAKE_ATTACH_FAIL:-}" ] && exit 1
    while [ $# -gt 0 ]; do [ "$1" = "-mountpoint" ] && { mnt="$2"; break; }; shift; done
    cp -R "$FAKE_APP_SRC" "$mnt/Yam Display.app" ;;
  *) : ;;
esac
exit 0
"""


def make_yam_app(path: Path, version: str, marker: str = "") -> Path:
    (path / "Contents").mkdir(parents=True)
    (path / "Contents" / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleShortVersionString": version, "CFBundleIdentifier": "com.yamstu.yamdisplaymac",
        "CFBundleExecutable": "YamDisplayTestStandIn"}))
    (path / "Contents" / "marker").write_text(marker or version)
    return path


def make_hub_app(path: Path) -> Path:
    runtime = path / RUNTIME
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b"#!/bin/sh\n")
    runtime.chmod(0o755)
    return path


class YamDisplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="yamdisplay-"))
        self.addCleanup(lambda: (subprocess.run(["chmod", "-R", "u+w", str(self.tmp)], capture_output=True),
                                 shutil.rmtree(self.tmp, ignore_errors=True)))
        self.kit = self.tmp / "kit"
        self.extras = self.kit / "Extras"
        self.extras.mkdir(parents=True)
        shutil.copy(EXTRAS_SRC / COMMAND, self.extras / COMMAND)
        self.applications = self.tmp / "Applications"
        self.applications.mkdir()
        self.live = self.applications / "Yam Display.app"
        self.log = self.tmp / "hdiutil.log"
        self.hdiutil = self.tmp / "fake-hdiutil"
        self.hdiutil.write_text(FAKE_HDIUTIL)
        self.hdiutil.chmod(0o755)
        self.shipped = self.tmp / "shipped" / "Yam Display.app"
        self.shipped.parent.mkdir()

    # --- helpers
    def put_dmg(self, name: str = "YamDisplay2.4.8.4.dmg", app_version: str = "2.4.8.4") -> None:
        (self.extras / name).write_bytes(b"not a real image; the stand-in hdiutil never reads it")
        if self.shipped.exists():
            shutil.rmtree(self.shipped)
        make_yam_app(self.shipped, app_version, "from the kit")

    def env(self, **extra: str) -> dict[str, str]:
        return {**os.environ, "TERM": "dumb", "STUDIOHUB_UNATTENDED": "1", "STUDIOHUB_HDIUTIL": str(self.hdiutil),
                "STUDIOHUB_YAMDISPLAY_TARGET": str(self.applications), "FAKE_HDIUTIL_LOG": str(self.log),
                "FAKE_APP_SRC": str(self.shipped), **extra}

    def run_extras(self, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(["/bin/sh", str(self.extras / COMMAND)], env=self.env(**extra),
                              capture_output=True, text=True, timeout=120)

    def attached(self) -> bool:
        return self.log.exists() and "attach" in self.log.read_text()

    def installed_version(self) -> str:
        return plistlib.loads((self.live / "Contents" / "Info.plist").read_bytes())["CFBundleShortVersionString"]

    def leftovers(self) -> list[str]:
        return sorted(p.name for p in self.applications.iterdir() if p.name != "Yam Display.app")

    # --- the standalone command
    def test_a_mac_without_yam_display_gets_it_and_the_installer_is_attached_hidden_read_only_and_detached(self):
        self.put_dmg()
        result = self.run_extras()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.installed_version(), "2.4.8.4")
        self.assertEqual((self.live / "Contents" / "marker").read_text(), "from the kit")
        log = self.log.read_text()
        self.assertRegex(log, r"attach .*-readonly")
        self.assertIn("-nobrowse", log)
        self.assertIn("detach", log)
        self.assertEqual(self.leftovers(), [])
        self.assertIn("Installed Yam Display 2.4.8.4", result.stdout)

    def test_an_older_installed_version_is_updated(self):
        self.put_dmg()
        make_yam_app(self.live, "2.4.7", "old")
        result = self.run_extras()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.installed_version(), "2.4.8.4")
        self.assertEqual((self.live / "Contents" / "marker").read_text(), "from the kit")
        self.assertEqual(self.leftovers(), [])
        self.assertIn("2.4.7 -> 2.4.8.4", result.stdout)

    def test_versions_compare_as_numbers_not_text(self):
        self.put_dmg("YamDisplay2.10.dmg", "2.10")
        make_yam_app(self.live, "2.9", "old")
        self.assertEqual(self.run_extras().returncode, 0)
        self.assertEqual(self.installed_version(), "2.10")

    def test_the_same_version_is_left_alone_without_attaching_anything(self):
        self.put_dmg()
        make_yam_app(self.live, "2.4.8.4", "mine")
        result = self.run_extras()
        self.assertEqual(result.returncode, 0)
        self.assertEqual((self.live / "Contents" / "marker").read_text(), "mine")
        self.assertFalse(self.attached(), "a current Mac must not mount the installer")
        self.assertIn("already installed", result.stdout)

    def test_a_newer_installed_version_is_never_downgraded(self):
        self.put_dmg()
        make_yam_app(self.live, "2.5.0", "newer")
        result = self.run_extras()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.installed_version(), "2.5.0")
        self.assertEqual((self.live / "Contents" / "marker").read_text(), "newer")

    def test_a_file_name_that_overstates_the_version_still_cannot_downgrade(self):
        # The decision that counts is the version inside the app; the name is only a cheap first look.
        self.put_dmg("YamDisplay9.9.9.dmg", "2.4.0")
        make_yam_app(self.live, "2.4.8", "newer")
        result = self.run_extras()
        self.assertEqual(result.returncode, 0)
        self.assertEqual((self.live / "Contents" / "marker").read_text(), "newer")
        self.assertTrue(self.attached())
        self.assertIn("detach", self.log.read_text())

    def test_no_installer_in_the_kit_is_one_quiet_line_and_not_a_failure(self):
        result = self.run_extras()
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.live.exists())
        self.assertFalse(self.attached())
        self.assertIn("no installer in this kit", result.stdout)
        self.assertLessEqual(len([line for line in result.stdout.splitlines() if line.strip()]), 2)

    def test_an_installer_that_will_not_open_fails_plainly_and_keeps_the_installed_app(self):
        self.put_dmg()
        make_yam_app(self.live, "2.4.0", "old")
        result = self.run_extras(FAKE_ATTACH_FAIL="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Yam Display was NOT installed", result.stdout)
        self.assertEqual((self.live / "Contents" / "marker").read_text(), "old")

    def test_an_installer_without_the_app_in_it_fails_and_detaches(self):
        self.put_dmg()
        shutil.rmtree(self.shipped)
        (self.shipped).mkdir()
        (self.shipped / "README").write_text("no app")
        result = self.run_extras()
        self.assertEqual(result.returncode, 1)
        self.assertIn("NOT installed", result.stdout)
        self.assertIn("detach", self.log.read_text())

    def test_a_copy_that_cannot_be_made_leaves_the_installed_app_in_place(self):
        self.put_dmg()
        make_yam_app(self.live, "2.4.0", "old")
        self.applications.chmod(0o555)   # nothing can be created beside the installed app
        try:
            result = self.run_extras()
        finally:
            self.applications.chmod(0o755)
        self.assertEqual(result.returncode, 1)
        self.assertIn("NOT installed", result.stdout)
        self.assertEqual((self.live / "Contents" / "marker").read_text(), "old")
        self.assertEqual(self.leftovers(), [])

    @unittest.skipUnless(Path("/usr/bin/hdiutil").exists(), "needs macOS hdiutil")
    def test_the_real_hdiutil_round_trip_with_a_dmg_built_here(self):
        image_src = self.tmp / "image-src"
        make_yam_app(image_src / "Yam Display.app", "3.0.1", "real")
        subprocess.run(["/usr/bin/hdiutil", "create", "-quiet", "-format", "UDBZ", "-srcfolder", str(image_src),
                        "-volname", "Yam Display", str(self.extras / "YamDisplay3.0.1.dmg")], check=True,
                       capture_output=True, timeout=120)
        env = self.env()
        env.pop("STUDIOHUB_HDIUTIL")
        result = subprocess.run(["/bin/sh", str(self.extras / COMMAND)], env=env, capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.installed_version(), "3.0.1")
        self.assertEqual((self.live / "Contents" / "marker").read_text(), "real")
        mounted = subprocess.run(["/sbin/mount"], capture_output=True, text=True).stdout
        self.assertNotIn("yamdisplay-installer", mounted, "the installer must be detached afterwards")


class LaunchersInstallYamDisplayTests(unittest.TestCase):
    """`1 - Install the app.command` and `Update this Mac.command` run it - and never fail because of it."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="yamdisplay-kit-"))
        self.addCleanup(lambda: (subprocess.run(["chmod", "-R", "u+w", str(self.tmp)], capture_output=True),
                                 shutil.rmtree(self.tmp, ignore_errors=True)))
        self.kit = self.tmp / "kit"
        self.kit.mkdir()
        make_hub_app(self.kit / "Studio Hub.app")
        shutil.copy(ROOT / "install_app.command", self.kit / "1 - Install the app.command")
        shutil.copy(ROOT / "Update this Mac.command", self.kit / "Update this Mac.command")
        (self.kit / "2 - Copy the models.command").write_text("#!/bin/sh\ntouch \"$(dirname \"$0\")/models-ran\"\n")
        self.applications = self.tmp / "Applications"
        self.applications.mkdir()
        self.log = self.tmp / "hdiutil.log"
        self.hdiutil = self.tmp / "fake-hdiutil"
        self.hdiutil.write_text(FAKE_HDIUTIL)
        self.hdiutil.chmod(0o755)
        self.shipped = make_yam_app(self.tmp / "shipped" / "Yam Display.app", "2.4.8.4", "from the kit")

    def with_extras(self) -> None:
        extras = self.kit / "Extras"
        extras.mkdir()
        shutil.copy(EXTRAS_SRC / COMMAND, extras / COMMAND)
        (extras / "YamDisplay2.4.8.4.dmg").write_bytes(b"stand-in")

    def run_script(self, name: str, **extra: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "TERM": "dumb", "STUDIOHUB_INSTALL_TARGET": str(self.applications),
               "STUDIOHUB_HDIUTIL": str(self.hdiutil), "FAKE_HDIUTIL_LOG": str(self.log),
               "FAKE_APP_SRC": str(self.shipped), **extra}
        if name.startswith("1"):
            env["STUDIOHUB_UNATTENDED"] = "1"
        return subprocess.run(["/bin/sh", str(self.kit / name)], env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=180)

    def yam(self) -> Path:
        return self.applications / "Yam Display.app"

    def test_install_app_installs_both(self):
        self.with_extras()
        result = self.run_script("1 - Install the app.command")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.applications / "Studio Hub.app").is_dir())
        self.assertTrue(self.yam().is_dir(), result.stdout)

    def test_install_app_without_extras_says_so_in_one_line_and_installs_the_hub(self):
        result = self.run_script("1 - Install the app.command")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.applications / "Studio Hub.app").is_dir())
        self.assertIn("Yam Display: not in this kit", result.stdout)
        self.assertFalse(self.yam().exists())

    def test_a_failing_yam_display_never_blocks_the_hub_install(self):
        self.with_extras()
        result = self.run_script("1 - Install the app.command", FAKE_ATTACH_FAIL="1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.applications / "Studio Hub.app").is_dir())
        self.assertIn("Yam Display was NOT installed", result.stdout)
        self.assertIn("Installed Studio Hub", result.stdout)
        self.assertFalse(self.yam().exists())

    def test_install_app_stands_aside_when_the_update_script_drives(self):
        self.with_extras()
        result = self.run_script("1 - Install the app.command", STUDIOHUB_YAMDISPLAY_BY_CALLER="1")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.yam().exists())
        self.assertFalse(self.log.exists())

    def test_update_this_mac_installs_yam_display_and_carries_on_to_the_models(self):
        self.with_extras()
        result = self.run_script("Update this Mac.command")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.yam().is_dir(), result.stdout)
        self.assertTrue((self.kit / "models-ran").exists())
        self.assertEqual(self.log.read_text().count("attach"), 1, "Yam Display must be installed once, not twice")

    def test_update_this_mac_still_installs_yam_display_when_the_hub_app_is_already_current(self):
        # The Hub step is skipped for an identical build; Yam Display must not ride on it.
        manifest = json.dumps({"version": "1.2.3", "implementation_commit": "abc1234"})
        make_hub_app(self.applications / "Studio Hub.app")
        for app in (self.kit / "Studio Hub.app", self.applications / "Studio Hub.app"):
            (app / "Contents/Resources/release-manifest.json").write_text(manifest)
        self.with_extras()
        result = self.run_script("Update this Mac.command")
        self.assertIn("already this exact build", result.stdout)
        self.assertTrue(self.yam().is_dir(), result.stdout)

    def test_update_this_mac_reports_a_failed_yam_display_but_finishes_everything_else(self):
        self.with_extras()
        result = self.run_script("Update this Mac.command", FAKE_ATTACH_FAIL="1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.applications / "Studio Hub.app").is_dir())
        self.assertTrue((self.kit / "models-ran").exists())
        self.assertIn("Yam Display was NOT installed (see above)", result.stdout)


class PrepareKitExtrasTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="yamdisplay-prepare-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.assets = self.tmp / "assets"
        self.assets.mkdir()
        self.kit = self.tmp / "vol" / "Video Generation Kit" / "Studio Hub KH"
        self.kit.mkdir(parents=True)
        self.trash = self.tmp / "vol" / "_trash"
        (self.assets / "YamDisplay2.4.8.4.dmg").write_bytes(b"new image" * 1000)
        (self.assets / "YamDisplay2.4.8.3.dmg").write_bytes(b"older image")

    def prepare(self) -> subprocess.CompletedProcess:
        return subprocess.run(["/bin/bash", str(EXTRAS_SRC / "prepare-kit-extras.sh"), str(self.kit), str(self.trash)],
                              env={**os.environ, "KIT_ASSETS": str(self.assets)}, capture_output=True, text=True, timeout=60)

    def test_copies_the_newest_installer_and_the_command_and_checks_sha256(self):
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        extras = self.kit / "Extras"
        # Beside it, the Shops set-up commands (workstation mode, 2026-10-03; `tests/test_shop_kit.py`).
        self.assertEqual(sorted(p.name for p in extras.iterdir()),
                         [COMMAND, "Set Up a Shop Mac.command", "Set Up the Shops Controller.command",
                          "YamDisplay2.4.8.4.dmg"])
        sha = hashlib.sha256((extras / "YamDisplay2.4.8.4.dmg").read_bytes()).hexdigest()
        self.assertIn(f"Extras/YamDisplay2.4.8.4.dmg sha256 {sha}", result.stdout)
        self.assertTrue(os.access(extras / COMMAND, os.X_OK))
        self.assertEqual((extras / COMMAND).read_bytes(), (EXTRAS_SRC / COMMAND).read_bytes())

    def test_an_older_installer_in_extras_is_moved_to_trash_and_other_files_are_left(self):
        (self.kit / "Extras").mkdir()
        (self.kit / "Extras" / "YamDisplay2.4.0.dmg").write_bytes(b"ancient")
        (self.kit / "Extras" / "Something Else.pdf").write_bytes(b"owner's")
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.kit / "Extras" / "YamDisplay2.4.0.dmg").exists())
        self.assertTrue((self.trash / "Extras-retired" / "YamDisplay2.4.0.dmg").exists())
        self.assertTrue((self.kit / "Extras" / "Something Else.pdf").exists())

    def test_it_is_idempotent(self):
        self.assertEqual(self.prepare().returncode, 0)
        self.assertEqual(self.prepare().returncode, 0)
        self.assertEqual(len(list((self.kit / "Extras").iterdir())), 4)

    def test_missing_assets_say_so_and_fail(self):
        for f in self.assets.iterdir():
            f.unlink()
        result = self.prepare()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Extras NOT prepared", result.stdout)
        self.assertFalse((self.kit / "Extras").exists())


class KitDocsMentionIt(unittest.TestCase):
    def test_the_checklist_and_kit_readme_say_where_yam_display_comes_from(self):
        for path in ("qualification/NODE-SETUP-CHECKLIST.md", "packaging/kit-readmes/Studio Hub KH.md"):
            text = (ROOT / path).read_text()
            self.assertIn("Yam Display installs automatically", text, path)
            self.assertIn("Extras/Install Yam Display.command", text, path)


if __name__ == "__main__":
    unittest.main()
