"""The KH Updater kit folder, and the installer a Mac runs out of it.

Studio Hub installs this agent by itself on Hub Macs (`components/hub/backend/khu_agent.py`,
branch `feat/khu-in-hub-20261025`). This is the other path: the same agent, installed from the SSD
kit, for a Mac that runs only Story Studio or only GatewayKH. Whichever of the two runs first
installs and the other must find it already there - so the two are not allowed to produce
*different* installs, and the tree comparison below is what says so rather than a comment.

Everything here runs against scratch folders and a loopback publisher: no test writes to a real
`~/Library/KHUpdater`, calls `launchctl`, mounts a drive or reaches the network. The publisher is a
fixture server on 127.0.0.1 that signs its reply with the same Ed25519 implementation the Hub uses
(`components/hub/backend/ed25519.py`), and `KH_UPDATER_LAUNCHCTL` replaces `launchctl` the way the
Studio Hub kit's own commands let a test replace `hdiutil`.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import plistlib
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packaging"))

from components.hub.backend import ed25519  # noqa: E402 - the fixtures' signatures

KIT_SOURCE = ROOT / "packaging" / "kit-khu"
READMES = ROOT / "packaging" / "kit-readmes"
RESTRUCTURE = ROOT / "packaging" / "kit-restructure.sh"
RELEASE_KIT = ROOT / "packaging" / "release_kit.py"
STAMP_INDEX = ROOT / "packaging" / "kit_stamp_index.py"
COMMAND = "Install KH Updater.command"
INSTALLER = "khu-install.py"
APPS = "apps.json"
PROTECTED = "KH Updater signing key backup"
#: A stand-in for kh-updater's `packaging/build-agent-kit.sh <version> <dir>` output.
AGENT_KIT_FILES = ("VERSION", "bin/khu", "bin/khu-stub", "install-agent.sh",
                   "com.khupdater.agent.plist", "agent-config.example.json")
#: The Hub's own client. It is on `feat/khu-in-hub-20261025` today; the comparison that needs it
#: skips with that reason rather than passing on a fixture of its own.
HUB_CLIENT = ROOT / "components" / "hub" / "backend" / "khu_agent.py"
LAUNCHD_LABEL = "com.khupdater.agent"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_agent_kit_source(root: Path, version: str = "1.0.0") -> Path:
    """The credential-free kit: exactly what kh-updater's own builder produces, and nothing else."""
    kit = root / "agent-kit"
    (kit / "bin").mkdir(parents=True, exist_ok=True)
    (kit / "VERSION").write_text(f"{version}\n")
    for name in ("bin/khu", "bin/khu-stub", "install-agent.sh"):
        path = kit / name
        path.write_bytes(b"#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
    (kit / "com.khupdater.agent.plist").write_bytes(
        plistlib.dumps({"Label": LAUNCHD_LABEL, "ProgramArguments": [], "RunAtLoad": True}))
    (kit / "agent-config.example.json").write_text("{}\n")
    return kit


def write_descriptor(path: Path, port: int, public_b64: str, *, test_authority: bool = False) -> Path:
    path.write_text(json.dumps({
        "feedURL": f"http://127.0.0.1:{port}/release.json",
        "enrolURL": f"http://127.0.0.1:{port}/enrol",
        "publicKey": public_b64,
        "testAuthority": test_authority,
    }, indent=2, sort_keys=True) + "\n")
    return path


def stage_agent_kit(output: Path, kit: Path, descriptor: Path) -> Path:
    """Stage it the way the drive gets it: through `release_kit.py`, never by hand."""
    result = subprocess.run([sys.executable, str(RELEASE_KIT), "--khu-agent", str(kit),
                             "--khu-publisher", str(descriptor), "--output", str(output)],
                            capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise AssertionError(f"release_kit.py refused: {result.stdout}{result.stderr}")
    return output


def kit_folder(root: Path, kit: Path, descriptor: Path) -> Path:
    """A laid-out `KH Updater/` folder, built from this repository's own sources."""
    folder = root / "KH Updater"
    folder.mkdir(parents=True)
    for name in (COMMAND, INSTALLER, APPS):
        shutil.copy(KIT_SOURCE / name, folder / name)
    shutil.copy(READMES / "KH Updater.md", folder / "README.md")
    stage_agent_kit(folder / "khu-agent", kit, descriptor)
    return folder


def scratch_drive(root: Path) -> Path:
    """A drive with the three app folders, a loose installer, and the owner's key backup."""
    drive = root / "KH-STUDIO-TEST"
    (drive / "GatewayKH App" / "installers").mkdir(parents=True)
    (drive / "GatewayKH App" / "installers" / "gateway-kh-0.2.195.dmg").write_bytes(b"dmg")
    (drive / "Studio Hub App").mkdir()
    (drive / "Studio Hub App" / "marker").write_bytes(b"app")
    (drive / "Story Studio KH 1.4.351-arm64.dmg").write_bytes(b"dmg")
    backup = drive / PROTECTED
    backup.mkdir()
    (backup / "khupdater-publisher-private.key").write_bytes(b"PRIVATE-KEY-BYTES\n")
    (backup / "notes.txt").write_text("the owner's own copy of the key\n")
    return drive


def backup_bytes(drive: Path) -> dict[str, bytes]:
    folder = drive / PROTECTED
    return {path.name: path.read_bytes() for path in sorted(folder.iterdir())}


def restructure(drive: Path, *arguments: str, dry: bool = False) -> subprocess.CompletedProcess:
    env = {**os.environ, "DRY": "1" if dry else "0"}
    return subprocess.run(["/bin/bash", str(RESTRUCTURE), *arguments, str(drive)],
                          capture_output=True, text=True, env=env, timeout=300)


def launchctl_stub(root: Path) -> Path:
    """A stand-in `launchctl`: `print` says the job is not loaded, everything else succeeds."""
    path = root / "launchctl"
    path.write_text('#!/bin/sh\necho "$*" >> "$KHU_LAUNCHCTL_LOG"\n'
                    '[ "$1" = "print" ] && exit 1\nexit 0\n')
    path.chmod(0o755)
    return path


def run_command(folder: Path, home: Path, log: Path, *, env: dict | None = None,
                machine: str | None = "fixture-mac", unattended: bool = True,
                stdin: str = "\n") -> subprocess.CompletedProcess:
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home),
                   "TERM": "dumb", "KH_UPDATER_PYTHON": sys.executable,
                   "KH_UPDATER_ALLOW_TEST": "1",
                   "KH_UPDATER_LAUNCHCTL": str(launchctl_stub(folder.parent)),
                   "KHU_LAUNCHCTL_LOG": str(log)}
    if unattended:
        environment["KH_UPDATER_UNATTENDED"] = "1"
    if machine is not None:
        environment["KH_UPDATER_MACHINE"] = machine
    environment.update(env or {})
    return subprocess.run(["/bin/sh", str(folder / COMMAND)], input=stdin, text=True,
                          capture_output=True, env=environment, timeout=180)


def computer_name_default() -> str:
    """What the installer proposes as this Mac's name when no Hub is installed.

    The Hub's own rule (`control_plane._safe_id`) applied to the hostname - written out here
    because it is the *expected* value, which is what a test is for.
    """
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", socket.gethostname().split(".")[0].strip().lower())
    cleaned = cleaned.strip("-._")[:100]
    return cleaned if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,99}", cleaned) else "studiohub"


def snapshot(root: Path, home: Path) -> dict[str, tuple]:
    """Every path under `root`, with its bytes, its link target and its mode.

    The two clients write the same tree into different scratch homes and mint their own enrolment
    nonce, so the home path and any 64-hex nonce are folded to placeholders first - everything else
    is compared exactly.
    """
    tree: dict[str, tuple] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if path.is_symlink():
            tree[relative] = ("link", os.readlink(path), mode)
        elif path.is_dir():
            tree[relative] = ("dir", b"", mode)
        else:
            data = path.read_bytes().replace(str(home).encode(), b"<HOME>")
            if path.name == "enrol-request.json":
                value = json.loads(data)
                value["nonce"] = "<NONCE>"      # minted per run, on purpose
                data = json.dumps(value, indent=2, sort_keys=True).encode()
            tree[relative] = ("file", data, mode)
    return tree


# --------------------------------------------------------------------------- the fixture publisher
class _EnrolServer(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        return

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        request = json.loads(self.rfile.read(length).decode())
        self.server.seen.append(json.loads(json.dumps(request)))
        if self.path != "/enrol":
            self.send_response(404)
            self.end_headers()
            return
        if self.server.window_open is False:
            # The owner has not opened the enrolment window: the publisher refuses, and no signed
            # reply exists to be trusted.
            self.send_response(403)
            self.end_headers()
            return
        payload = {"machine": request["machine"], "nonce": request["nonce"],
                   "feedURL": self.server.feed_url, "publicKey": self.server.public_b64,
                   "issuedAt": int(time.time()), "token": self.server.token}
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        body = json.dumps({"reply": base64.b64encode(raw).decode(),
                           "signature": base64.b64encode(
                               ed25519.sign(self.server.seed, raw)).decode()}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class PublisherFixture:
    """A loopback publisher that signs its replies like the real one."""

    def __init__(self, unittest_case: unittest.TestCase, *, window_open: bool = True):
        self.seed = secrets.token_bytes(32)
        self.public = ed25519.public_key(self.seed)
        self.public_b64 = base64.b64encode(self.public).decode()
        self.token = secrets.token_hex(32)
        server = HTTPServer(("127.0.0.1", 0), _EnrolServer)
        server.seed, server.public_b64, server.token = self.seed, self.public_b64, self.token
        server.window_open = window_open
        server.seen = []
        server.feed_url = f"http://127.0.0.1:{server.server_address[1]}/release.json"
        self.server = server
        self.port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        # Cleanups run last-in-first-out: stop serving, wait for the loop, then close the socket.
        unittest_case.addCleanup(server.server_close)
        unittest_case.addCleanup(thread.join, 5)
        unittest_case.addCleanup(server.shutdown)

    def rotate_key(self) -> None:
        """Make the publisher sign with a key this Mac does not trust."""
        self.server.seed = secrets.token_bytes(32)

    def close_window(self) -> None:
        self.server.window_open = False


# --------------------------------------------------------------------------- the layout on a drive
class DriveLayoutTests(unittest.TestCase):
    """What `kit-restructure.sh` puts in `KH Updater/`, with and without a publisher record.

    Without one there must be **no install file**: an agent installed from a kit with no pinned
    publisher key can never enrol, and it would look from the outside exactly like a Mac that is
    simply not reporting.
    """

    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="khu-drive-"))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.drive = scratch_drive(self.work)
        self.kit = write_agent_kit_source(self.work)

    @property
    def folder(self) -> Path:
        return self.drive / "1 Install Apps" / "KH Updater"

    def test_an_already_reorganized_drive_keeps_the_new_layout(self) -> None:
        apps = self.drive / "1 Install Apps"
        apps.mkdir()
        (self.drive / "Studio Hub App").rename(apps / "Studio Hub")
        (self.drive / "GatewayKH App").rename(apps / "Gateway")
        (apps / "Story Studio").mkdir()
        installer = self.drive / "Story Studio KH 1.4.351-arm64.dmg"
        installer.rename(apps / "Story Studio" / installer.name)
        result = restructure(self.drive)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((apps / "Studio Hub" / "marker").read_bytes(), b"app")
        self.assertTrue((apps / "Studio Hub" / "README.md").is_file())
        self.assertTrue((apps / "Gateway" / "installers" / "gateway-kh-0.2.195.dmg").is_file())
        self.assertFalse((self.drive / "Video Generation Kit").exists())
        self.assertFalse((apps / "Studio Hub KH").exists())

    def test_without_a_publisher_record_there_is_no_install_file_and_it_says_why(self) -> None:
        result = restructure(self.drive)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["README.md"])
        readme = (self.folder / "README.md").read_text()
        self.assertIn("not ready yet", readme)
        self.assertIn("That key has not been created yet", readme)
        self.assertIn("no installer on purpose", readme)
        self.assertIn("the publisher key has not been created yet", result.stdout)

    def test_a_ready_kit_carries_the_installer_the_apps_config_and_the_agent(self) -> None:
        descriptor = write_descriptor(self.work / "publisher.json", 8790, "A" * 43 + "=")
        # First the drive it would be without a publisher key, then the one it becomes: a drive
        # may be laid out early, and becoming ready has to replace what was there.
        self.assertEqual(restructure(self.drive).returncode, 0)
        self.assertIn("not ready yet", (self.folder / "README.md").read_text())
        result = restructure(self.drive, "--agent-kit", str(self.kit),
                             "--publisher-json", str(descriptor))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()),
                         ["Install KH Updater.command", "README.md", "apps.json", "khu-agent",
                          "khu-install.py"])
        self.assertIn("Enrolment needs you at the publisher first",
                      (self.folder / "README.md").read_text())
        self.assertTrue(os.access(self.folder / COMMAND, os.X_OK), "the install file must run")
        self.assertEqual(json.loads((self.folder / APPS).read_text())["apps"][
            "kh.gateway.menubar"], {"app": "gateway", "recipe": "gateway"})
        staged = self.folder / "khu-agent"
        manifest = json.loads((staged / "MANIFEST.json").read_text())
        self.assertEqual(manifest["format"], "studiohub.khu-agent.v1")
        self.assertEqual(manifest["agent_version"], "1.0.0")
        self.assertEqual(sorted(manifest["files"]),
                         sorted((*AGENT_KIT_FILES, "publisher.json")))
        for name, pin in manifest["files"].items():
            self.assertEqual((staged / name).stat().st_size, pin["bytes"], name)
            self.assertEqual(digest(staged / name), pin["sha256"], name)
        self.assertEqual((staged / "publisher.json").read_text(),
                         descriptor.read_text().rstrip("\n") + "\n")

    def test_the_environment_variables_are_the_same_two_inputs(self) -> None:
        descriptor = write_descriptor(self.work / "publisher.json", 8790, "A" * 43 + "=")
        env = {**os.environ, "KHU_AGENT_KIT": str(self.kit),
               "KHU_PUBLISHER_JSON": str(descriptor), "DRY": "0"}
        result = subprocess.run(["/bin/bash", str(RESTRUCTURE), str(self.drive)],
                                capture_output=True, text=True, env=env, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.folder / "khu-agent" / "MANIFEST.json").is_file())

    def test_an_input_that_does_not_exist_leaves_the_folder_without_an_installer(self) -> None:
        (self.work / "publisher.json").write_text("{}\n")
        result = restructure(self.drive, "--agent-kit", str(self.work / "absent"),
                             "--publisher-json", str(self.work / "publisher.json"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("REFUSED: an input named for the KH Updater kit does not exist", result.stdout)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["README.md"])

    def test_losing_the_publisher_record_takes_the_installer_out_of_the_folder(self) -> None:
        descriptor = write_descriptor(self.work / "publisher.json", 8790, "A" * 43 + "=")
        restructure(self.drive, "--agent-kit", str(self.kit), "--publisher-json", str(descriptor))
        result = restructure(self.drive)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["README.md"])
        moved = self.drive / "_trash" / [p.name for p in (self.drive / "_trash").iterdir()
                                         if p.name.startswith("half-configured")][0]
        self.assertEqual(sorted(p.name for p in moved.iterdir()),
                         ["Install KH Updater.command", "apps.json", "khu-agent", "khu-install.py"],
                         "nothing is deleted; it is moved out of the way")

    def test_a_dry_run_changes_nothing_at_all(self) -> None:
        descriptor = write_descriptor(self.work / "publisher.json", 8790, "A" * 43 + "=")
        before = {p.as_posix(): digest(p) for p in sorted(self.drive.rglob("*")) if p.is_file()}
        result = restructure(self.drive, "--agent-kit", str(self.kit),
                             "--publisher-json", str(descriptor), dry=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("DRY: stage", result.stdout)
        after = {p.as_posix(): digest(p) for p in sorted(self.drive.rglob("*")) if p.is_file()}
        self.assertEqual(before, after)

    def test_the_readme_index_is_on_the_drive_where_the_stamper_looks_for_it(self) -> None:
        """`kit_stamp_index.py` stamps `Studio Hub/README.md` and mirrors it to the kit root.

        Those two are the kit index and must carry the KH Updater row on a prepared drive; the Hub's
        own page is a different document beside them.
        """
        restructure(self.drive)
        kit = self.drive / "1 Install Apps"
        index_root = (kit / "README.md").read_text()
        index_hub = (kit / "Studio Hub" / "README.md").read_text()
        self.assertIn("| `KH Updater/` | KH Updater |", index_root)
        self.assertEqual(index_root, index_hub)
        self.assertTrue((kit / "Studio Hub" / "Studio Hub KH.md").read_text()
                        .startswith("# Studio Hub KH — the Macs that run the models"))


class SigningKeyBackupTests(unittest.TestCase):
    """Each drive carries a plain backup of the publisher's signing key. It is **not part of the
    kit**: never moved, copied, renamed, listed in a checksum or a README, or trashed - and a run
    that did any of that fails loudly instead of quietly."""

    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="khu-backup-"))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.drive = scratch_drive(self.work)
        self.kit = write_agent_kit_source(self.work)
        self.descriptor = write_descriptor(self.work / "publisher.json", 8790, "A" * 43 + "=")

    def test_a_dry_run_leaves_it_byte_identical(self) -> None:
        before = backup_bytes(self.drive)
        result = restructure(self.drive, "--agent-kit", str(self.kit),
                             "--publisher-json", str(self.descriptor), dry=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(backup_bytes(self.drive), before)

    def test_a_real_run_leaves_it_byte_identical_and_says_so(self) -> None:
        before = backup_bytes(self.drive)
        result = restructure(self.drive, "--agent-kit", str(self.kit),
                             "--publisher-json", str(self.descriptor))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(backup_bytes(self.drive), before)
        self.assertIn(f"  {PROTECTED}: untouched (it is not part of the kit)", result.stdout)

    def test_the_guard_notices_a_changed_byte(self) -> None:
        """The protection is a measurement, so the measurement has to be able to fail.

        `folder_digest` is taken from the script itself, not re-implemented here: a copy would
        agree with itself and prove nothing.
        """
        script = RESTRUCTURE.read_text()
        start = script.index("folder_digest() {")
        function = script[start:script.index("\n}\n", start) + 3]
        folder = self.drive / PROTECTED

        def measure() -> str:
            result = subprocess.run(["/bin/bash", "-c", f'{function}\nfolder_digest "$1"',
                                     "guard", str(folder)], capture_output=True, text=True,
                                    timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()

        before = measure()
        self.assertNotEqual(before, "absent")
        (folder / "notes.txt").write_text("changed\n")
        self.assertNotEqual(measure(), before, "a changed byte must change the digest")
        (folder / "notes.txt").unlink()
        self.assertNotEqual(measure(), before, "a removed file must change the digest too")

    def test_the_run_compares_the_digest_and_fails_when_it_differs(self) -> None:
        text = RESTRUCTURE.read_text()
        self.assertIn('PROTECTED_BEFORE="$(folder_digest "$PROTECTED")"', text)
        self.assertIn('if [ "$(folder_digest "$PROTECTED")" != "$PROTECTED_BEFORE" ]; then', text)
        self.assertIn("    STATUS=1", text)
        self.assertIn('exit "$STATUS"', text)

    def test_it_is_never_named_in_a_readme_or_a_checksum_on_the_drive(self) -> None:
        restructure(self.drive, "--agent-kit", str(self.kit), "--publisher-json", str(self.descriptor))
        kit = self.drive / "1 Install Apps"
        documents = [p for p in sorted(kit.rglob("*"))
                     if p.is_file() and p.suffix in (".md", ".txt")]
        self.assertTrue(documents, "the drive has no documents to check")
        for path in documents:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            self.assertNotIn(PROTECTED, text, path)
            self.assertNotIn("khupdater-publisher-private.key", text, path)
        for checksum in ("Gateway/SHA256SUMS.txt", "Story Studio/SHA256.txt"):
            text = (kit / checksum).read_text()
            self.assertNotIn(PROTECTED, text, checksum)
            self.assertNotIn("khupdater-publisher-private.key", text, checksum)

    def test_the_repository_does_not_name_it_in_any_kit_document(self) -> None:
        for path in sorted(READMES.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(PROTECTED, text, path)
            self.assertNotIn("khupdater-publisher-private.key", text, path)
        for path in (STAMP_INDEX, RELEASE_KIT, RESTRUCTURE):
            text = path.read_text(encoding="utf-8")
            self.assertIn(PROTECTED, text, f"{path.name} should carry the protection")

    def test_the_stamper_leaves_it_alone_too(self) -> None:
        before = backup_bytes(self.drive)
        result = subprocess.run([sys.executable, str(STAMP_INDEX), str(self.drive)],
                                capture_output=True, text=True, timeout=120)
        self.assertNotEqual(result.returncode, 0, "this scratch drive cannot be stamped")
        self.assertEqual(backup_bytes(self.drive), before)


# --------------------------------------------------------------------------- the installer
class InstallerTests(unittest.TestCase):
    """`Install KH Updater.command`, run for real against a scratch home and a loopback publisher."""

    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="khu-install-"))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.publisher = PublisherFixture(self)
        self.kit = write_agent_kit_source(self.work)
        self.descriptor = write_descriptor(self.work / "publisher.json", self.publisher.port,
                                           self.publisher.public_b64)
        self.folder = kit_folder(self.work, self.kit, self.descriptor)
        # The installer resolves the home it is given (`/var` is a symlink to `/private/var` on
        # macOS), so the expectations below have to name the same path it writes.
        self.home = (self.work / "Home").resolve()
        self.home.mkdir()
        self.log = self.work / "launchctl.log"

    def run_installer(self, **kwargs) -> subprocess.CompletedProcess:
        return run_command(self.folder, self.home, self.log, **kwargs)

    def config(self) -> dict:
        return json.loads((self.home / "Library/KHUpdater/base/config.json").read_text())

    def test_it_installs_enrols_and_starts_the_agent(self) -> None:
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        root = self.home / "Library/KHUpdater"
        self.assertEqual((self.home / "Library/KHUpdater/bin/khu-stub").read_bytes(),
                         (self.kit / "bin/khu-stub").read_bytes())
        current = root / "base/current"
        self.assertTrue(current.is_symlink())
        self.assertEqual(current.resolve(), (root / "base/versions/1.0.0/khu").resolve())
        self.assertEqual((root / "base/versions/1.0.0/khu").read_bytes(),
                         (self.kit / "bin/khu").read_bytes())
        config = self.config()
        self.assertEqual(config["machine"], "fixture-mac")
        self.assertEqual(config["token"], self.publisher.token)
        self.assertEqual(config["feedURL"], self.descriptor_value("feedURL"))
        self.assertEqual(config["publicKey"], self.publisher.public_b64)
        self.assertEqual(config["stateDir"], str(root / "state"))
        self.assertEqual(config["stubBase"], str(root / "base"))
        self.assertEqual(config["intervalMinutes"], 5)
        self.assertFalse(config["once"])
        self.assertEqual(stat.S_IMODE((root / "base/config.json").stat().st_mode), 0o600)
        plist = self.home / "Library/LaunchAgents/com.khupdater.agent.plist"
        job = plistlib.loads(plist.read_bytes())
        self.assertEqual(job["Label"], LAUNCHD_LABEL)
        self.assertEqual(job["ProgramArguments"],
                         [str(root / "bin/khu-stub"), "--base", str(root / "base")])
        self.assertEqual(job["StandardOutPath"], str(self.home / "Library/Logs/khupdater-agent.log"))
        self.assertEqual(stat.S_IMODE(plist.stat().st_mode), 0o600)
        calls = self.log.read_text().splitlines()
        self.assertTrue(any(line.startswith("print gui/") for line in calls), calls)
        self.assertEqual(calls[-1], f"bootstrap gui/{os.getuid()} {plist}")
        self.assertIn("Enrolled as fixture-mac.", result.stdout)
        self.assertIn("Started at login", result.stdout)

    def descriptor_value(self, key: str) -> str:
        return json.loads(self.descriptor.read_text())[key]

    def test_the_config_is_the_one_the_hub_writes_field_for_field(self) -> None:
        """The Hub's enrolment record, exactly: same keys, same order, same encoding.

        `khu_agent.enrol` writes `json.dump` of a dict built in this order, so the bytes of the two
        clients' configs can be compared directly - and a field added on one side and not the other
        is a difference rather than a detail nobody reads.
        """
        (self.home / "Applications/Studio Hub.app/Contents").mkdir(parents=True)
        (self.home / "Applications/Studio Hub.app/Contents/Info.plist").write_bytes(
            plistlib.dumps({"CFBundleIdentifier": "local.studiohub.standalone"}))
        self.run_installer()
        root = self.home / "Library/KHUpdater"
        apps = {"updater": {"bundlePath": "", "recipe": "updater", "automatic": False},
                "hub": {"bundlePath": str((self.home / "Applications/Studio Hub.app").resolve()),
                        "recipe": "hub", "automatic": False,
                        "bundleID": "local.studiohub.standalone"}}
        expected = json.dumps({"feedURL": self.descriptor_value("feedURL"),
                               "publicKey": self.publisher.public_b64, "machine": "fixture-mac",
                               "token": self.publisher.token, "stateDir": str(root / "state"),
                               "stubBase": str(root / "base"), "apps": apps,
                               "intervalMinutes": 5, "once": False})
        self.assertEqual((root / "base/config.json").read_text(), expected)

    def test_it_enrols_under_the_name_it_is_given(self) -> None:
        self.assertEqual(self.run_installer(machine="mac-mini-2").returncode, 0)
        self.assertEqual(self.config()["machine"], "mac-mini-2")

    def test_pressing_return_accepts_the_name_it_proposes(self) -> None:
        """The name is shown and confirmed, not chosen silently: the owner is the one who decides
        what this Mac is called in the publisher's fleet list."""
        result = self.run_installer(machine=None, unattended=False, stdin="\n\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Name for this Mac [", result.stdout)
        self.assertEqual(self.config()["machine"], computer_name_default())
        self.assertRegex(self.config()["machine"], r"^[a-z0-9][a-z0-9._-]{0,99}$")

    def test_on_a_hub_mac_the_proposed_name_is_the_hubs_own(self) -> None:
        """A Hub Mac already has an identity, and the Hub's own auto-install enrols under it. The
        kit must propose the same one, or the two paths would give this Mac two names."""
        voice = self.home / "Applications/Studio Hub.app/Contents/Resources/voice"
        (self.home / "Applications/Studio Hub.app/Contents").mkdir(parents=True)
        (self.home / "Applications/Studio Hub.app/Contents/Info.plist").write_bytes(
            plistlib.dumps({"CFBundleIdentifier": "local.studiohub.standalone"}))
        voice.mkdir(parents=True)
        (voice / "controller_settings.json").write_text(json.dumps({"controller_id": "mac-mini-7"}))
        result = self.run_installer(machine=None, unattended=False, stdin="\n\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Name for this Mac [mac-mini-7]", result.stdout)
        self.assertEqual(self.config()["machine"], "mac-mini-7")

    def test_a_name_that_is_not_an_id_installs_nothing(self) -> None:
        result = self.run_installer(machine="Manu's Mac Mini")
        self.assertEqual(result.returncode, 2)
        self.assertIn("is not a usable name for this Mac", result.stdout)
        self.assertFalse((self.home / "Library/KHUpdater").exists())
        self.assertFalse(self.log.exists(), "launchd was never asked to do anything")

    def test_it_sends_no_credential_with_the_enrolment(self) -> None:
        """Enrolment is authorised by the owner opening the window, not by a secret this Mac sends."""
        self.run_installer()
        self.assertTrue(self.publisher.server.seen, "the publisher fixture saw no request")
        for request in self.publisher.server.seen:
            self.assertEqual(sorted(request), ["machine", "nonce"])

    def test_it_refuses_a_publisher_that_signs_with_another_key(self) -> None:
        self.publisher.rotate_key()
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("publisher signature refused", result.stdout)
        self.assertFalse((self.home / "Library/KHUpdater").exists(),
                         "a refused enrolment leaves no agent behind")

    def test_a_second_run_says_it_is_already_installed_and_changes_nothing(self) -> None:
        self.assertEqual(self.run_installer().returncode, 0)
        root = self.home / "Library/KHUpdater"
        before = snapshot(root, self.home)
        before_plist = (self.home / "Library/LaunchAgents/com.khupdater.agent.plist").read_bytes()
        second = self.run_installer()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("already installed", second.stdout)
        self.assertIn("enrolled as fixture-mac", second.stdout)
        self.assertEqual(snapshot(root, self.home), before)
        self.assertEqual((self.home / "Library/LaunchAgents/com.khupdater.agent.plist").read_bytes(),
                         before_plist)
        self.assertEqual(len(self.publisher.server.seen), 1, "it did not enrol a second time")

    def test_the_window_closed_leaves_nothing_and_says_where_it_went(self) -> None:
        self.publisher.close_window()
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("KH Updater was NOT installed", result.stdout)
        self.assertIn("publisher has not authorised this Mac", result.stdout)
        self.assertFalse((self.home / "Library/KHUpdater").exists())
        self.assertFalse((self.home / "Library/LaunchAgents/com.khupdater.agent.plist").exists())
        buckets = list((self.home / "Library/_trash").glob("khupdater-incomplete-*"))
        self.assertEqual(len(buckets), 1, result.stdout)
        self.assertTrue((buckets[0] / "KHUpdater/base/current").is_symlink(),
                        "what it wrote is kept, whole, in the trash")
        self.assertIn(str(buckets[0]), result.stdout)

    def test_a_mac_with_no_python_is_told_so(self) -> None:
        """The interpreter is looked for at run time, and the order is part of the promise: an
        installed Studio Hub first, the system Python next, the copy on the drive last.

        Pinned in the file rather than run, because the absence of every Python cannot be staged on
        a Mac that has one.
        """
        text = (KIT_SOURCE / COMMAND).read_text()
        self.assertIn("no Python could be found on this Mac", text)
        self.assertIn('"$candidate" -c \'pass\'', text)
        self.assertIn("/usr/bin/python3", text)
        self.assertIn("Studio Hub.app/Contents/Resources/runtimes/voice/bin/python3", text)
        order = [text.index("/Applications/Studio Hub.app"),
                 text.index("/usr/bin/python3"),
                 text.index("../Studio Hub/Studio Hub.app")]
        self.assertEqual(order, sorted(order), "the drive's copy must be the last resort")

    def test_no_publisher_record_refuses_before_anything_is_written(self) -> None:
        (self.folder / "khu-agent/publisher.json").unlink()
        result = self.run_installer()
        self.assertEqual(result.returncode, 1)
        self.assertIn("publisher.json", result.stdout)
        self.assertFalse((self.home / "Library").exists())
        self.assertFalse(self.log.exists())

    def test_no_agent_kit_refuses_too(self) -> None:
        shutil.rmtree(self.folder / "khu-agent")
        result = self.run_installer()
        self.assertEqual(result.returncode, 1)
        self.assertIn("agent kit folder is missing", result.stdout)
        self.assertFalse((self.home / "Library").exists())

    def test_a_tampered_agent_binary_is_refused(self) -> None:
        """The kit carries a checksum for every byte of itself, and the installer reads it."""
        binary = self.folder / "khu-agent/bin/khu"
        binary.write_bytes(b"#!/bin/sh\necho not the agent\n")
        binary.chmod(0o755)
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("agent kit checksum mismatch", result.stdout)
        self.assertFalse((self.home / "Library/KHUpdater").exists())

    def test_a_launchd_that_will_not_start_it_keeps_the_install_and_says_how_to_retry(self) -> None:
        """The same rule as kh-updater's `install-agent.sh` (owner, 2026-10-26).

        A failed start is not a failed install: the agent's files and its launchd job are
        finished, and trashing them would make the person enrol a second time. The run says
        what launchctl answered, keeps everything, and prints the one command that retries.
        """
        stub = self.work / "launchctl-refusing"
        stub.write_text('#!/bin/sh\necho "$*" >> "$KHU_LAUNCHCTL_LOG"\n'
                        'echo "Bootstrap failed: 5: Input/output error" >&2\nexit 1\n')
        stub.chmod(0o755)
        result = self.run_installer(env={"KH_UPDATER_LAUNCHCTL": str(stub)})
        self.assertNotEqual(result.returncode, 0)
        root = self.home / "Library/KHUpdater"
        plist = self.home / "Library/LaunchAgents/com.khupdater.agent.plist"
        self.assertTrue((root / "base/config.json").is_file(), "the enrolment is kept")
        self.assertTrue(plist.is_file(), "the launchd job is kept")
        self.assertIn("KH Updater is installed but launchd did not start it", result.stdout)
        self.assertIn("Input/output error", result.stdout, "launchctl's own reason is shown")
        self.assertIn(f"launchctl bootstrap gui/{os.getuid()} {plist}", result.stdout,
                      "the one command that retries it is named")
        self.assertFalse((self.home / "Library/_trash").exists(),
                         "a finished install is never moved to the trash")
        # The Mac stays enrolled: a second run finds it and changes nothing.
        second = self.run_installer()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("already installed", second.stdout)
        self.assertEqual(len(self.publisher.server.seen), 1, "it did not enrol a second time")

    def test_a_launchd_that_already_has_the_job_loaded_is_left_alone(self) -> None:
        """The rule `khu_agent.Bootstrap.activate` and `install-agent.sh` both use."""
        stub = self.work / "launchctl-loaded"
        stub.write_text('#!/bin/sh\necho "$*" >> "$KHU_LAUNCHCTL_LOG"\n'
                        '[ "$1" = "print" ] && exit 0\nexit 0\n')
        stub.chmod(0o755)
        result = self.run_installer(env={"KH_UPDATER_LAUNCHCTL": str(stub)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("already has com.khupdater.agent loaded", result.stdout)
        self.assertFalse(any(line.startswith("bootstrap ")
                             for line in self.log.read_text().splitlines()),
                         "an already-loaded job is never bootstrapped twice")


# --------------------------------------------------------------------------- the two clients
class BothPathsAgreeTests(unittest.TestCase):
    """The kit's client and Studio Hub's client, compared on what they actually write.

    Studio Hub auto-installs this agent on Hub Macs; the kit installs it on Macs that run only Story
    Studio or only GatewayKH. Both can run on one Mac. If the two produced *similar* layouts, "is
    the agent installed here?" would have two answers - so this runs both against a loopback
    publisher and compares the trees, byte for byte, with the home path and the enrolment nonce
    folded to placeholders.

    The Hub's client is on `feat/khu-in-hub-20261025` today; until it merges, the comparison runs
    with `KH_UPDATER_KHU_AGENT=<path to khu_agent.py>` and skips with that reason otherwise.
    """

    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="khu-both-"))
        self.addCleanup(shutil.rmtree, self.work, True)

    def hub_client(self):
        override = os.environ.get("KH_UPDATER_KHU_AGENT")
        path = Path(override).resolve() if override else HUB_CLIENT
        if not path.is_file():
            self.skipTest(f"{path} is not in this checkout; Studio Hub's client is on "
                          "feat/khu-in-hub-20261025, and KH_UPDATER_KHU_AGENT=<path to "
                          "khu_agent.py> runs this comparison against it")
        if "httpx" not in sys.modules:
            # The Hub's client imports its HTTP library at the top and uses it only in `enrol`.
            # httpx is not installed for the gate, so a transport stand-in carries the request the
            # same way: urllib, no redirects, loopback only. Nothing about the layout depends on it.
            sys.modules["httpx"] = _httpx_shim()
        import importlib.util
        # Loaded by path, so the comparison uses the file it was pointed at rather than whatever
        # `components.hub.backend` this checkout happens to have. The dotted name is the package
        # the module belongs to, which is what makes its own `from . import ed25519` resolve - to
        # this checkout's copy of the curve, the same implementation in both trees.
        spec = importlib.util.spec_from_file_location(
            "components.hub.backend.khu_agent_reference", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def test_the_kit_writes_the_tree_the_hub_writes(self) -> None:
        hub = self.hub_client()
        publisher = PublisherFixture(self)
        kit = write_agent_kit_source(self.work)
        descriptor = write_descriptor(self.work / "publisher.json", publisher.port,
                                      publisher.public_b64)
        folder = kit_folder(self.work, kit, descriptor)

        kit_home = (self.work / "kit-home").resolve()
        kit_home.mkdir()
        result = run_command(folder, kit_home, self.work / "launchctl-kit.log")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        hub_home = (self.work / "hub-home").resolve()
        hub_home.mkdir()
        recorder = _RecordingRun()
        bootstrap = hub.Bootstrap(folder / "khu-agent", hub_home, allow_test=True, run=recorder)
        bootstrap.install()
        asyncio.run(bootstrap.enrol("fixture-mac"))
        bootstrap.activate()

        self.assertEqual(snapshot(hub_home / "Library", hub_home),
                         snapshot(kit_home / "Library", kit_home))

    def test_the_two_detect_the_same_apps(self) -> None:
        hub = self.hub_client()
        home = (self.work / "home").resolve()
        for name, identity in (("Studio Hub", "local.studiohub.standalone"),
                               ("gateway-kh", "kh.gateway.menubar"),
                               ("Story Studio KH", "com.storystudio.kh"),
                               ("Not Ours", "com.example.other")):
            contents = home / "Applications" / f"{name}.app" / "Contents"
            contents.mkdir(parents=True)
            (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": identity}))
        mine = run_import("app_config", home, json.loads((KIT_SOURCE / APPS).read_text())["apps"])
        theirs = hub.app_config(home, home)
        self.assertEqual(mine, theirs)
        self.assertEqual(sorted(theirs), ["gateway", "hub", "story", "updater"])

    def test_the_first_run_leaves_the_same_state_as_the_hub(self) -> None:
        """`install` returns the same word and the same on-disk result for the same inputs."""
        hub = self.hub_client()
        publisher = PublisherFixture(self)
        kit = write_agent_kit_source(self.work)
        descriptor = write_descriptor(self.work / "publisher.json", publisher.port,
                                      publisher.public_b64)
        folder = kit_folder(self.work, kit, descriptor)
        state = run_import("install_state", folder / "khu-agent", self.work / "install-home")
        self.assertEqual(state, "installed")
        self.assertEqual(
            hub.Bootstrap(folder / "khu-agent", self.work / "hub-install-home",
                          allow_test=True, run=_RecordingRun()).install(),
            state)


def run_import(function: str, *arguments):
    """Call one function of the kit's own client in this process, for a comparison."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("khu_install", KIT_SOURCE / INSTALLER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    if function == "app_config":
        return module.app_config(arguments[0], arguments[1])
    if function == "install_state":
        home = Path(arguments[1])
        home.mkdir(parents=True, exist_ok=True)
        return module.Bootstrap(Path(arguments[0]), home, allow_test=True,
                                run=_RecordingRun()).install()
    raise AssertionError(function)


class _RecordingRun:
    """Every launchctl call, recorded and never made."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, *, capture_output=True, timeout=None):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 1 if argv[:2] == ["/bin/launchctl", "print"] else 0,
                                           stdout="", stderr="")


def _httpx_shim() -> types.ModuleType:
    """Just enough `httpx` for the Hub's own `enrol` to reach a loopback publisher."""
    import urllib.error
    import urllib.request

    class _Response:
        def __init__(self, status: int, content: bytes):
            self.status_code, self.content = status, content

        def json(self):
            return json.loads(self.content.decode())

    class _NoRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    class AsyncClient:
        def __init__(self, **kwargs):
            self._opener = urllib.request.build_opener(_NoRedirects,
                                                       urllib.request.ProxyHandler({}))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            request = urllib.request.Request(url, data=json_dump(json).encode(),
                                             headers={"Content-Type": "application/json"},
                                             method="POST")
            try:
                with self._opener.open(request, timeout=10) as response:
                    return _Response(response.status, response.read())
            except urllib.error.HTTPError as error:
                return _Response(error.code, error.read())

    def json_dump(value):
        import json as _json
        return _json.dumps(value)

    module = types.ModuleType("httpx")
    module.AsyncClient = AsyncClient
    return module


# --------------------------------------------------------------------------- the index
class IndexStampTests(unittest.TestCase):
    """The kit index's KH Updater row is derived from the agent kit on the drive, like the other
    two version rows."""

    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="khu-stamp-"))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.drive = scratch_drive(self.work)
        self.kit = write_agent_kit_source(self.work)
        self.descriptor = write_descriptor(self.work / "publisher.json", 8790, "A" * 43 + "=")

    def index(self) -> Path:
        return self.drive / "1 Install Apps" / "Studio Hub" / "README.md"

    def test_the_row_takes_the_version_the_agent_kit_states(self) -> None:
        write_agent_kit_source(self.work, version="2.4.1")
        restructure(self.drive, "--agent-kit", str(self.work / "agent-kit"),
                    "--publisher-json", str(self.descriptor))
        result = subprocess.run([sys.executable, str(STAMP_INDEX), str(self.drive)],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("KH Updater    -> 2.4.1", result.stdout)
        self.assertIn("| 2.4.1 |", self.index().read_text())
        self.assertEqual(self.index().read_text(),
                         (self.drive / "1 Install Apps" / "README.md").read_text())

    def test_a_drive_without_a_staged_agent_leaves_that_row_alone(self) -> None:
        """The row names the artefact, so a drive with no agent kit is described truthfully by
        leaving it: there is no version to state, and one invented here would be a number about a
        build the drive does not carry."""
        restructure(self.drive)
        result = subprocess.run([sys.executable, str(STAMP_INDEX), str(self.drive)],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("KH Updater    -- left alone: no agent kit on this drive", result.stdout)
        self.assertIn("| its `khu-agent/VERSION` |", self.index().read_text())

    def test_a_drive_with_no_kh_updater_folder_at_all_is_left_alone_too(self) -> None:
        restructure(self.drive)
        shutil.rmtree(self.drive / "1 Install Apps" / "KH Updater")
        result = subprocess.run([sys.executable, str(STAMP_INDEX), str(self.drive)],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("KH Updater    -- left alone:", result.stdout)

    def test_an_agent_kit_whose_version_cannot_be_read_is_a_defect(self) -> None:
        """`None` means two things and they are kept apart: nothing here, versus something broken
        here. A drive carrying an agent kit that cannot say what it is must not be stamped."""
        restructure(self.drive, "--agent-kit", str(self.kit),
                    "--publisher-json", str(self.descriptor))
        (self.drive / "1 Install Apps" / "KH Updater" / "khu-agent" / "VERSION").write_text(
            "not a version\n")
        before = self.index().read_bytes()
        result = subprocess.run([sys.executable, str(STAMP_INDEX), str(self.drive)],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("REFUSED: KH Updater: ", result.stdout)
        self.assertIn("does not state a version", result.stdout)
        self.assertEqual(self.index().read_bytes(), before,
                         "a refusal writes nothing: half-stamped is worse than stale")


# --------------------------------------------------------------------------- release_kit's second mode
class AgentKitStagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="khu-stage-"))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.kit = write_agent_kit_source(self.work)

    def stage(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(RELEASE_KIT), *arguments],
                              capture_output=True, text=True, timeout=120)

    def test_it_pins_every_staged_byte(self) -> None:
        descriptor = write_descriptor(self.work / "publisher.json", 8790, "A" * 43 + "=")
        output = self.work / "khu-agent"
        result = self.stage("--khu-agent", str(self.kit), "--khu-publisher", str(descriptor),
                            "--output", str(output))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        staged = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
        self.assertEqual(staged, set(AGENT_KIT_FILES) | {"publisher.json", "MANIFEST.json"})
        self.assertTrue(os.access(output / "bin/khu", os.X_OK))
        self.assertEqual(stat.S_IMODE((output / "VERSION").stat().st_mode), 0o644)

    def test_a_test_authority_descriptor_is_refused(self) -> None:
        descriptor = write_descriptor(self.work / "publisher.json", 8790, "A" * 43 + "=",
                                     test_authority=True)
        result = self.stage("--khu-agent", str(self.kit), "--khu-publisher", str(descriptor),
                            "--output", str(self.work / "khu-agent"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("testAuthority", result.stdout)
        self.assertFalse((self.work / "khu-agent").exists())

    def test_an_extra_file_in_the_agent_kit_is_refused(self) -> None:
        (self.kit / "surprise.txt").write_text("hello\n")
        descriptor = write_descriptor(self.work / "publisher.json", 8790, "A" * 43 + "=")
        result = self.stage("--khu-agent", str(self.kit), "--khu-publisher", str(descriptor),
                            "--output", str(self.work / "khu-agent"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("unexpected surprise.txt", result.stdout)

    def test_an_existing_output_is_refused_rather_than_merged_into(self) -> None:
        descriptor = write_descriptor(self.work / "publisher.json", 8790, "A" * 43 + "=")
        output = self.work / "khu-agent"
        output.mkdir()
        (output / "keep.txt").write_text("mine\n")
        result = self.stage("--khu-agent", str(self.kit), "--khu-publisher", str(descriptor),
                            "--output", str(output))
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to replace an existing agent kit", result.stdout)
        self.assertEqual(sorted(p.name for p in output.iterdir()), ["keep.txt"])


# --------------------------------------------------------------------------- the curve
class Ed25519Tests(unittest.TestCase):
    """The kit ships `verify` and nothing else, so it has to agree with the Hub's own module."""

    def setUp(self) -> None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("khu_install", KIT_SOURCE / INSTALLER)
        self.kit_module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(self.kit_module)

    def test_it_agrees_with_the_hubs_own_verification(self) -> None:
        seed = secrets.token_bytes(32)
        public = ed25519.public_key(seed)
        message = b'{"machine":"mac-mini-2"}'
        signature = ed25519.sign(seed, message)
        self.assertTrue(self.kit_module.ed25519_verify(public, message, signature))
        self.assertTrue(ed25519.verify(public, message, signature))
        self.assertFalse(self.kit_module.ed25519_verify(public, message + b" ", signature))
        self.assertFalse(self.kit_module.ed25519_verify(
            ed25519.public_key(secrets.token_bytes(32)), message, signature))

    def test_it_refuses_the_shapes_it_should_refuse(self) -> None:
        seed = secrets.token_bytes(32)
        public, message = ed25519.public_key(seed), b"x"
        signature = bytearray(ed25519.sign(seed, message))
        # A non-canonical S (>= l) is refused rather than reduced.
        signature[32:] = (2 ** 252 + 27742317777372353535851937790883648493).to_bytes(32, "little")
        self.assertFalse(self.kit_module.ed25519_verify(public, message, bytes(signature)))
        self.assertFalse(self.kit_module.ed25519_verify(public, message, b"short"))
        self.assertFalse(self.kit_module.ed25519_verify(b"", message, bytes(signature)))

    def test_the_kit_ships_no_signing_key_handling(self) -> None:
        """A drive that is handed out carries the verifier; the half that signs stays on the
        publisher's Mac."""
        text = (KIT_SOURCE / INSTALLER).read_text()
        self.assertIn("def ed25519_verify", text)
        self.assertNotIn("def sign", text)
        self.assertNotIn("def public_key", text)
        self.assertNotIn("seed", text)


if __name__ == "__main__":
    unittest.main()
