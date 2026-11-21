"""Tests for the KH Updater bootstrap that ships inside the Hub bundle.

The bootstrap installs the per-user KH Updater agent, enrols it with the
publisher and loads the launchd job. None of these tests touch the real
publisher, the real launchd domain or any real fleet Mac. Every publisher
call is answered by a local fixture server, every file operation happens in
a temporary home and every launchctl invocation is replaced with a recording
subprocess stub.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import plistlib
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub.backend import ed25519, khu_agent  # noqa: E402


def _seed() -> tuple[bytes, bytes]:
    seed = secrets.token_bytes(32)
    return seed, ed25519.public_key(seed)


def _sign(seed: bytes, payload: dict) -> dict:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = ed25519.sign(seed, raw)
    return {
        "reply": base64.b64encode(raw).decode(),
        "signature": base64.b64encode(signature).decode(),
    }


def _kit_files(path: Path) -> dict:
    data = path.read_bytes()
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _build_kit(root: Path, publisher: dict, version: str = "1.0.0") -> Path:
    kit = root / "kit"
    (kit / "bin").mkdir(parents=True)
    (kit / "VERSION").write_text(version)
    (kit / "bin/khu").write_bytes(b"#!/bin/sh\nexit 0\n")
    (kit / "bin/khu").chmod(0o755)
    (kit / "bin/khu-stub").write_bytes(b"#!/bin/sh\nexit 0\n")
    (kit / "bin/khu-stub").chmod(0o755)
    (kit / "install-agent.sh").write_bytes(b"#!/bin/sh\nexit 0\n")
    (kit / "com.khupdater.agent.plist").write_bytes(
        b"<?xml version=\"1.0\"?><plist><dict/></plist>")
    (kit / "publisher.json").write_text(json.dumps(publisher))
    (kit / "agent-config.example.json").write_text("{}")
    files = {
        "VERSION": _kit_files(kit / "VERSION"),
        "bin/khu": _kit_files(kit / "bin/khu"),
        "bin/khu-stub": _kit_files(kit / "bin/khu-stub"),
        "install-agent.sh": _kit_files(kit / "install-agent.sh"),
        "com.khupdater.agent.plist": _kit_files(kit / "com.khupdater.agent.plist"),
        "agent-config.example.json": _kit_files(kit / "agent-config.example.json"),
        "publisher.json": _kit_files(kit / "publisher.json"),
    }
    (kit / "MANIFEST.json").write_text(json.dumps({"files": files}))
    return kit


class _EnrolHandler(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        return

    def _record(self):
        # Recorded so a test can prove no credential travelled with a request, and which routes
        # the bootstrap actually used.
        self.server.seen_headers.append(dict(self.headers))
        self.server.seen_paths.append(self.path)

    def do_GET(self):
        self._record()
        if not self.path.startswith("/commands"):
            self.send_response(404)
            self.end_headers()
            return
        # The publisher knows exactly one token; anything else is a Mac it dropped.
        if self.headers.get("authorization") != f"Bearer {self.server.issued_token}":
            self.send_response(401)
            self.end_headers()
            return
        data = b'{"reply": "", "signature": ""}'
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        self._record()
        if self.path == "/status":
            if self.headers.get("authorization") != f"Bearer {self.server.issued_token}":
                self.send_response(401)
                self.end_headers()
                return
            data = b'{"ok": true}'
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        request = json.loads(body.decode())
        if self.path != "/enrol":
            self.send_response(404)
            self.end_headers()
            return
        if self.server.enrol_status != 200:
            self.send_response(self.server.enrol_status)
            self.end_headers()
            return
        payload = {
            "machine": request["machine"],
            "nonce": request["nonce"],
            "feedURL": self.server.feed_url,
            "publicKey": self.server.public_key_b64,
            "issuedAt": int(time.time()),
            "token": self.server.next_token or self.server.issued_token,
        }
        signed = _sign(self.server.seed, payload)
        data = json.dumps(signed).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class _RecordingRun:
    def __init__(self):
        self.calls = []
        self.bootstrap_rc = 0

    def __call__(self, argv, *, capture_output=True, timeout=None):
        self.calls.append(argv)
        rc = 1 if argv[:2] == ["/bin/launchctl", "print"] else self.bootstrap_rc
        return subprocess.CompletedProcess(argv, rc, stdout="", stderr="")


class KhAgentKitTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="khu-agent-"))
        self.seed, self.public = _seed()
        self.public_b64 = base64.b64encode(self.public).decode()
        self.publisher = {
            "feedURL": "http://100.64.0.1:8790/release.json",
            "enrolURL": "http://100.64.0.1:8790/enrol",
            "publicKey": self.public_b64,
            "testAuthority": True,
        }

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def test_verify_kit_accepts_a_well_formed_kit(self):
        kit = _build_kit(self.work, self.publisher)
        manifest = khu_agent.verify_kit(kit, allow_test=True)
        self.assertEqual(set(manifest["files"]), khu_agent.KIT_FILES)

    def test_verify_kit_refuses_a_tampered_binary(self):
        kit = _build_kit(self.work, self.publisher)
        (kit / "bin/khu").write_bytes(b"#!/bin/sh\nexit 1\n")
        with self.assertRaises(ValueError):
            khu_agent.verify_kit(kit, allow_test=True)

    def test_verify_kit_refuses_an_extra_file(self):
        kit = _build_kit(self.work, self.publisher)
        (kit / "surprise.txt").write_text("hi")
        with self.assertRaises(ValueError):
            khu_agent.verify_kit(kit, allow_test=True)

    def test_verify_kit_accepts_the_shared_icon_when_the_manifest_pins_it(self):
        kit = _build_kit(self.work, self.publisher)
        (kit / "AppIcon.icns").write_bytes(b"icns-icon")
        manifest_path = kit / "MANIFEST.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"]["AppIcon.icns"] = _kit_files(kit / "AppIcon.icns")
        manifest_path.write_text(json.dumps(manifest))
        verified = khu_agent.verify_kit(kit, allow_test=True)
        self.assertIn("AppIcon.icns", verified["files"])

    def test_verify_kit_refuses_a_shared_icon_the_manifest_does_not_pin(self):
        kit = _build_kit(self.work, self.publisher)
        (kit / "AppIcon.icns").write_bytes(b"icns-icon")
        with self.assertRaises(ValueError):
            khu_agent.verify_kit(kit, allow_test=True)

    def test_verify_kit_refuses_a_symlink(self):
        kit = _build_kit(self.work, self.publisher)
        target = self.work / "elsewhere"
        target.write_text("x")
        (kit / "bin/khu").unlink()
        (kit / "bin/khu").symlink_to(target)
        with self.assertRaises(ValueError):
            khu_agent.verify_kit(kit, allow_test=True)

    def test_publisher_config_refuses_non_tailscale_address_in_production(self):
        bad = {**self.publisher, "feedURL": "http://127.0.0.1:8790/release.json"}
        with self.assertRaises(ValueError):
            khu_agent.publisher_config(bad, allow_test=False)

    def test_publisher_config_refuses_a_test_authority_in_production(self):
        bad = {**self.publisher, "testAuthority": True}
        with self.assertRaises(ValueError):
            khu_agent.publisher_config(bad, allow_test=False)

    def test_publisher_config_refuses_mismatched_origins(self):
        bad = {**self.publisher,
               "enrolURL": "http://100.64.0.2:8790/enrol"}
        with self.assertRaises(ValueError):
            khu_agent.publisher_config(bad, allow_test=True)

    # A build staged for the public carries the pinned key and **no address** (owner, 2026-11-18):
    # the DMG anyone downloads must not name this estate's publisher host.
    def test_a_public_build_takes_the_address_from_the_updater_and_keeps_the_key(self):
        shipped = {**self.publisher, "feedURL": "", "enrolURL": ""}
        resolved = khu_agent.publisher_config(
            shipped, allow_test=True,
            authority={"feedURL": "https://mirror.example/kh/release.json",
                       "enrolURL": "https://mirror.example/kh/enrol",
                       "publicKey": self.publisher["publicKey"]})
        self.assertEqual(resolved["feedURL"], "https://mirror.example/kh/release.json")
        self.assertEqual(resolved["enrolURL"], "https://mirror.example/kh/enrol")
        self.assertEqual(resolved["publicKey"], self.publisher["publicKey"])

    def test_a_public_build_refuses_to_enrol_without_an_updater_address(self):
        shipped = {**self.publisher, "feedURL": "", "enrolURL": ""}
        with self.assertRaisesRegex(ValueError, "KH Updater"):
            khu_agent.publisher_config(shipped, allow_test=True)

    def test_an_updater_config_trusting_another_key_is_refused(self):
        shipped = {**self.publisher, "feedURL": "", "enrolURL": ""}
        with self.assertRaisesRegex(ValueError, "another publisher"):
            khu_agent.publisher_config(
                shipped, allow_test=True,
                authority={"feedURL": "https://mirror.example/kh/release.json",
                           "enrolURL": "https://mirror.example/kh/enrol",
                           "publicKey": "AAAA"})

    def test_plain_http_to_a_public_host_is_refused(self):
        shipped = {**self.publisher, "feedURL": "", "enrolURL": ""}
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            khu_agent.publisher_config(
                shipped, allow_test=True,
                authority={"feedURL": "http://example.com/release.json",
                           "enrolURL": "http://example.com/enrol"})


class KhAgentBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="khu-agent-home-"))
        self.home = self.work / "Home"
        self.home.mkdir()
        self.seed, self.public = _seed()
        self.public_b64 = base64.b64encode(self.public).decode()
        self.issued_token = secrets.token_hex(32)
        # A canary that must never be transmitted: nothing in this flow sends a credential.
        self.canary_secret = "fixture-fleet-credential"
        server = HTTPServer(("127.0.0.1", 0), _EnrolHandler)
        server.seed = self.seed
        server.public_key_b64 = self.public_b64
        port = server.server_address[1]
        # The feed URL in the reply must match what's in the kit's publisher.json.
        server.feed_url = f"http://127.0.0.1:{port}/release.json"
        server.issued_token = self.issued_token
        server.next_token = None
        server.enrol_status = 200
        server.seen_headers = []
        server.seen_paths = []
        self.server = server
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.thread.start()
        self.publisher = {
            "feedURL": f"http://127.0.0.1:{port}/release.json",
            "enrolURL": f"http://127.0.0.1:{port}/enrol",
            "publicKey": self.public_b64,
            "testAuthority": True,
        }
        self.kit = _build_kit(self.work, self.publisher)

    def tearDown(self):
        self.server.shutdown()
        shutil.rmtree(self.work, ignore_errors=True)

    def _recorder(self):
        recorder = _RecordingRun()
        return recorder

    def test_fresh_install_writes_version_links_and_config(self):
        recorder = self._recorder()
        bootstrap = khu_agent.Bootstrap(self.kit, self.home, allow_test=True, run=recorder)
        self.assertEqual(bootstrap.install(), "installed")
        base = self.home / "Library/KHUpdater/base"
        current = base / "current"
        self.assertTrue(current.is_symlink())
        self.assertEqual(current.resolve(), (base / "versions/1.0.0/khu").resolve())
        self.assertTrue((base / "versions/1.0.0/khu").is_file())
        self.assertTrue((self.home / "Library/KHUpdater/bin/khu-stub").is_file())
        asyncio.run(bootstrap.enrol("fixture-mac"))
        config = json.loads((base / "config.json").read_text())
        self.assertEqual(config["machine"], "fixture-mac")
        self.assertEqual(config["token"], self.issued_token)
        self.assertEqual(config["feedURL"], self.publisher["feedURL"])
        self.assertEqual(config["publicKey"], self.public_b64)
        self.assertFalse(config["once"])
        self.assertEqual((base / "config.json").stat().st_mode & 0o777, 0o600)
        bootstrap.activate()
        self.assertEqual(recorder.calls[-1][:3],
                         ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}"])

    def test_already_current_returns_current_and_leaves_state(self):
        recorder = self._recorder()
        bootstrap = khu_agent.Bootstrap(self.kit, self.home, allow_test=True, run=recorder)
        self.assertEqual(bootstrap.install(), "installed")
        self.assertEqual(bootstrap.install(), "current")

    def test_newer_installed_returns_newer_and_does_not_downgrade(self):
        recorder = self._recorder()
        # Install the newer version (2.0.0) first
        newer_kit = _build_kit(self.work / "newer", self.publisher, version="2.0.0")
        bootstrap_newer = khu_agent.Bootstrap(newer_kit, self.home, allow_test=True, run=recorder)
        self.assertEqual(bootstrap_newer.install(), "installed")
        # Now try to install the older version (1.0.0)
        bootstrap_older = khu_agent.Bootstrap(self.kit, self.home, allow_test=True, run=recorder)
        self.assertEqual(bootstrap_older.install(), "newer")
        # Verify that 2.0.0 is still the current version (no downgrade)
        current = (self.home / "Library/KHUpdater/base/current").resolve()
        self.assertIn("2.0.0", str(current))

    def test_enrol_request_carries_no_credential(self):
        """The Hub's fleet token is the group's shared secret and must not travel here.

        The publisher cannot verify it and has no need for it; enrolment is authorised on
        the publisher by an owner-opened time window, and the reply's signature is what
        this Mac actually trusts. Asserted on the wire, not on a helper's return value.
        """
        recorder = self._recorder()
        bootstrap = khu_agent.Bootstrap(self.kit, self.home, allow_test=True, run=recorder)
        bootstrap.install()
        asyncio.run(bootstrap.enrol("fixture-mac"))
        self.assertTrue(self.server.seen_headers, "the publisher fixture saw no request")
        for headers in self.server.seen_headers:
            lowered = {name.lower() for name in headers}
            self.assertNotIn("x-hub-token", lowered)
            self.assertNotIn("authorization", lowered)
            self.assertNotIn("cookie", lowered)

    def test_enrol_refuses_a_publisher_with_the_wrong_key(self):
        recorder = self._recorder()
        bootstrap = khu_agent.Bootstrap(self.kit, self.home, allow_test=True, run=recorder)
        bootstrap.install()
        other_seed, other_public = _seed()
        self.server.seed = other_seed
        with self.assertRaises(ValueError):
            asyncio.run(bootstrap.enrol("fixture-mac"))

    def test_enrol_refuses_a_reply_for_another_machine(self):
        recorder = self._recorder()
        bootstrap = khu_agent.Bootstrap(self.kit, self.home, allow_test=True, run=recorder)
        bootstrap.install()
        original = self.server.issued_token

        class _WrongMachineHandler(_EnrolHandler):
            def do_POST(handler_self):
                length = int(handler_self.headers.get("content-length", "0"))
                body = handler_self.rfile.read(length)
                request = json.loads(body.decode())
                payload = {
                    "machine": "some-other-mac",
                    "nonce": request["nonce"],
                    "feedURL": handler_self.server.feed_url,
                    "publicKey": handler_self.server.public_key_b64,
                    "issuedAt": int(time.time()),
                    "token": original,
                }
                signed = _sign(handler_self.server.seed, payload)
                data = json.dumps(signed).encode()
                handler_self.send_response(200)
                handler_self.send_header("content-type", "application/json")
                handler_self.send_header("content-length", str(len(data)))
                handler_self.end_headers()
                handler_self.wfile.write(data)

        self.server.shutdown()
        self.server = HTTPServer(("127.0.0.1", 0), _WrongMachineHandler)
        self.server.seed = self.seed
        self.server.public_key_b64 = self.public_b64
        self.server.feed_url = self.publisher["feedURL"]
        self.server.issued_token = original
        port = self.server.server_address[1]
        self.publisher["enrolURL"] = f"http://127.0.0.1:{port}/enrol"
        (self.kit / "publisher.json").write_text(json.dumps(self.publisher))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        with self.assertRaises(ValueError):
            asyncio.run(bootstrap.enrol("fixture-mac"))

    def test_token_never_appears_in_log_output(self):
        recorder = self._recorder()
        bootstrap = khu_agent.Bootstrap(self.kit, self.home, allow_test=True, run=recorder)
        bootstrap.install()
        asyncio.run(bootstrap.enrol("fixture-mac"))
        config_bytes = (self.home / "Library/KHUpdater/base/config.json").read_bytes()
        self.assertIn(self.issued_token.encode(), config_bytes)
        for call in recorder.calls:
            joined = " ".join(call).encode()
            self.assertNotIn(self.issued_token.encode(), joined)
            self.assertNotIn(self.canary_secret.encode(), joined)

    def test_check_once_returns_waiting_when_publisher_is_offline(self):
        self.server.shutdown()
        recorder = self._recorder()
        bootstrap = khu_agent.Bootstrap(self.kit, self.home, allow_test=True, run=recorder)
        answer = asyncio.run(khu_agent.check_once(bootstrap, "fixture-mac"))
        self.assertEqual(answer["state"], "waiting")
        self.assertNotIn(self.issued_token, answer["detail"])
        self.assertNotIn(self.canary_secret, answer["detail"])

    def test_a_rejected_token_is_dropped_and_the_mac_enrols_again(self):
        """A Mac removed from the publisher must not keep a dead token forever."""
        recorder = self._recorder()
        bootstrap = khu_agent.Bootstrap(self.kit, self.home, allow_test=True, run=recorder)
        bootstrap.install()
        self.assertEqual(asyncio.run(bootstrap.enrol("fixture-mac")), "enrolled")
        config_file = self.home / "Library/KHUpdater/base/config.json"
        old_token = json.loads(config_file.read_text())["token"]
        self.assertEqual(old_token, self.issued_token)
        # The publisher dropped this Mac and would hand out a different token on re-enrolment.
        new_token = secrets.token_hex(32)
        self.server.issued_token = new_token
        self.server.next_token = new_token

        self.assertEqual(asyncio.run(bootstrap.enrol("fixture-mac")), "re-enrolled")
        self.assertFalse(bootstrap.token_rejected,
                         "once the fresh config is written, this Mac is not a rejected one")
        fresh = json.loads(config_file.read_text())
        self.assertEqual(fresh["token"], new_token)
        self.assertEqual(fresh["machine"], "fixture-mac")
        self.assertEqual(fresh["feedURL"], self.publisher["feedURL"])
        self.assertEqual(fresh["publicKey"], self.public_b64)
        # The dead config is archived, not silently overwritten or deleted.
        archived = list((self.home / "Library/KHUpdater/_trash").glob("*/config.json"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(json.loads(archived[0].read_text())["token"], old_token)
        # A third run sees a token the publisher still honours: no re-enrol loop.
        self.assertEqual(asyncio.run(bootstrap.enrol("fixture-mac")), "current")
        self.assertEqual(json.loads(config_file.read_text())["token"], new_token)

        # Re-enrolment goes through the same reply checks: an unverified reply writes nothing.
        self.server.issued_token = secrets.token_hex(32)
        self.server.next_token = self.server.issued_token
        self.server.seed, _ = _seed()
        with self.assertRaises(ValueError):
            asyncio.run(bootstrap.enrol("fixture-mac"))
        self.assertFalse(config_file.exists())

    def test_an_unreachable_publisher_never_discards_a_config(self):
        recorder = self._recorder()
        bootstrap = khu_agent.Bootstrap(self.kit, self.home, allow_test=True, run=recorder)
        bootstrap.install()
        asyncio.run(bootstrap.enrol("fixture-mac"))
        config_file = self.home / "Library/KHUpdater/base/config.json"
        before = config_file.read_bytes()
        self.server.shutdown()
        self.server.server_close()

        self.assertEqual(asyncio.run(bootstrap.enrol("fixture-mac")), "current")
        self.assertEqual(config_file.read_bytes(), before)
        self.assertFalse(bootstrap.token_rejected)

    def test_a_rejected_token_with_no_window_waits_and_says_so(self):
        recorder = self._recorder()
        bootstrap = khu_agent.Bootstrap(self.kit, self.home, allow_test=True, run=recorder)
        bootstrap.install()
        asyncio.run(bootstrap.enrol("fixture-mac"))
        config_file = self.home / "Library/KHUpdater/base/config.json"
        old_token = json.loads(config_file.read_text())["token"]
        # The publisher dropped this Mac and no enrolment window is open.
        self.server.issued_token = secrets.token_hex(32)
        self.server.enrol_status = 403

        with self.assertRaises(ValueError):
            asyncio.run(bootstrap.enrol("fixture-mac"))
        self.assertTrue(bootstrap.token_rejected)
        archived = list((self.home / "Library/KHUpdater/_trash").glob("*/config.json"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(json.loads(archived[0].read_text())["token"], old_token)

        # A later check reports plainly that the publisher no longer knows this Mac. The same
        # bootstrap keeps the flag, which is what the Hub's long-running loop holds.
        answer = asyncio.run(khu_agent.check_once(bootstrap, "fixture-mac"))
        self.assertEqual(answer["state"], "waiting")
        self.assertIs(answer["tokenRejected"], True)
        self.assertIs(khu_agent.STATUS["tokenRejected"], True)
        self.assertNotIn(old_token, answer["detail"])
        self.assertNotIn(self.server.issued_token, answer["detail"])
        self.assertNotIn(self.canary_secret, answer["detail"])

    def test_check_once_surfaces_a_re_enrolment(self):
        recorder = self._recorder()
        bootstrap = khu_agent.Bootstrap(self.kit, self.home, allow_test=True, run=recorder)
        bootstrap.install()
        asyncio.run(bootstrap.enrol("fixture-mac"))
        old_token = self.issued_token
        new_token = secrets.token_hex(32)
        self.server.issued_token = new_token
        self.server.next_token = new_token

        answer = asyncio.run(khu_agent.check_once(bootstrap, "fixture-mac"))
        self.assertEqual(answer["state"], "current")
        self.assertIs(answer["tokenRejected"], True)
        self.assertIs(khu_agent.STATUS["tokenRejected"], True)
        self.assertNotIn(old_token, answer["detail"])
        self.assertNotIn(new_token, answer["detail"])
        self.assertIn("enrolled again", answer["detail"])

    def test_a_re_enrolled_mac_that_fails_to_activate_is_not_reported_as_rejected(self):
        """The token is good again; a launchd failure is a different problem and must say so."""
        recorder = self._recorder()
        bootstrap = khu_agent.Bootstrap(self.kit, self.home, allow_test=True, run=recorder)
        bootstrap.install()
        asyncio.run(bootstrap.enrol("fixture-mac"))
        new_token = secrets.token_hex(32)
        self.server.issued_token = new_token
        self.server.next_token = new_token
        recorder.bootstrap_rc = 1  # launchd refuses the job

        answer = asyncio.run(khu_agent.check_once(bootstrap, "fixture-mac"))
        self.assertEqual(answer["state"], "waiting")
        self.assertIs(answer["tokenRejected"], False)
        self.assertNotIn("no longer recognises", answer["detail"])
        self.assertNotIn(new_token, answer["detail"])

    def test_the_stale_probe_is_a_read_that_cannot_make_a_dead_mac_look_alive(self):
        """The probe must not touch the record it asks about.

        A `POST /status` probe would refresh the publisher's `lastSeen`, so a Mac whose agent
        died would look alive on the owner's page for ever. The probe is `GET /commands` with
        no `capabilities` parameter, which also leaves the capabilities the real agent
        advertised alone.
        """
        recorder = self._recorder()
        bootstrap = khu_agent.Bootstrap(self.kit, self.home, allow_test=True, run=recorder)
        bootstrap.install()
        asyncio.run(bootstrap.enrol("fixture-mac"))
        self.server.seen_paths.clear()
        self.server.seen_headers.clear()

        self.assertEqual(asyncio.run(bootstrap.enrol("fixture-mac")), "current")
        probes = list(zip(self.server.seen_paths, self.server.seen_headers))
        self.assertEqual(len(probes), 1, probes)
        path, headers = probes[0]
        self.assertTrue(path.startswith("/commands?"), path)
        self.assertIn("machine=fixture-mac", path)
        self.assertNotIn("capabilities", path)
        self.assertEqual(headers.get("authorization"), f"Bearer {self.issued_token}")
        self.assertNotIn("/status", self.server.seen_paths)

    def test_a_good_check_reports_no_rejected_token(self):
        recorder = self._recorder()
        bootstrap = khu_agent.Bootstrap(self.kit, self.home, allow_test=True, run=recorder)
        answer = asyncio.run(khu_agent.check_once(bootstrap, "fixture-mac"))
        self.assertEqual(answer["state"], "installed")
        self.assertIs(answer["tokenRejected"], False)
        self.assertIs(khu_agent.STATUS["tokenRejected"], False)

    def test_enrol_request_sends_no_origin_or_referer_header(self):
        """The publisher refuses /enrol when either is present: that is a browser, not the agent."""
        recorder = self._recorder()
        bootstrap = khu_agent.Bootstrap(self.kit, self.home, allow_test=True, run=recorder)
        bootstrap.install()
        asyncio.run(bootstrap.enrol("fixture-mac"))
        enrol_headers = [headers for path, headers in zip(self.server.seen_paths, self.server.seen_headers)
                         if path == "/enrol"]
        self.assertTrue(enrol_headers, "the publisher fixture saw no /enrol request")
        for headers in enrol_headers:
            lowered = {name.lower() for name in headers}
            self.assertNotIn("origin", lowered)
            self.assertNotIn("referer", lowered)


class TheAgentTableGivesHubItsOwnAttempts(unittest.TestCase):
    """Hub's row is `automatic: true`, so a failed Hub download gets a second attempt.

    Measured on the fleet 2026-11-20: nine Macs carried a Hub `download_failed` for 12.6 h to 3.2
    days. The transfer resumes a `.part` (`Agent.swift:159`), but the agent's sweep only works apps
    its table marks automatic, and the publisher's bulk path excludes Hub - with this row off,
    nothing ever asked again. Bundles are plist fixtures in a temporary home; nothing is installed.
    """

    def test_hub_is_automatic_and_the_other_apps_keep_their_setting(self):
        with tempfile.TemporaryDirectory(prefix="hub-agent-table-") as temp:
            home = Path(temp)
            for name, identity in (("Studio Hub", "local.studiohub.standalone"),
                                   ("gateway-kh", "kh.gateway.menubar"),
                                   ("Story Studio KH", "com.storystudio.kh")):
                contents = home / "Applications" / f"{name}.app" / "Contents"
                contents.mkdir(parents=True)
                (contents / "Info.plist").write_bytes(
                    plistlib.dumps({"CFBundleIdentifier": identity}))
            table = khu_agent.app_config(home, home)
            self.assertIs(table["hub"]["automatic"], True)
            self.assertEqual(table["hub"]["recipe"], "hub")
            for app in ("gateway", "story", "updater"):
                self.assertIs(table[app]["automatic"], False, app)

    def test_relay_is_managed_before_it_is_installed_and_after(self):
        """Relay joins Hub's automatic path (owner, 2026-11-21), and 0.3.33 is on no Mac.

        A row that only appeared when the bundle was found would never install it: the agent only
        works apps its table lists, so the entry has to be written with the feed's recipe and an
        empty path, and the found bundle replaces it on the Macs that already have one.
        """
        with tempfile.TemporaryDirectory(prefix="hub-agent-relay-") as temp:
            home = Path(temp)
            table = khu_agent.app_config(home, home)
            self.assertEqual(table["relay"], {"bundlePath": "", "recipe": "app-dmg",
                                              "automatic": True})
            contents = home / "Applications/Relay Studio KH.app/Contents"
            contents.mkdir(parents=True)
            (contents / "Info.plist").write_bytes(
                plistlib.dumps({"CFBundleIdentifier": "kh.relay.desktop"}))
            found = khu_agent.app_config(home, home)["relay"]
            self.assertEqual(found["recipe"], "app-dmg")
            self.assertIs(found["automatic"], True)
            self.assertEqual(found["bundleID"], "kh.relay.desktop")
            self.assertTrue(found["bundlePath"].endswith("Relay Studio KH.app"))

    def test_an_enrolled_mac_gains_the_relay_row_without_losing_its_own_choices(self):
        """The table is written at enrolment, so an old config has to be added to.

        Every fleet Mac was enrolled before Relay existed. Reconciling keeps each saved row
        byte-for-byte (an owner's Automatic choice is not re-decided) and writes the file only
        when there is something new.
        """
        with tempfile.TemporaryDirectory(prefix="hub-agent-reconcile-") as temp:
            home = Path(temp)
            bootstrap = khu_agent.Bootstrap(Path(temp) / "kit", home)
            bootstrap.base.mkdir(parents=True)
            config_file = bootstrap.base / "config.json"
            saved = {"feedURL": "http://publisher.example", "publicKey": "A" * 43 + "=",
                     "machine": "fixture-mac", "token": "t" * 40,
                     "stateDir": str(bootstrap.root / "state"), "stubBase": str(bootstrap.base),
                     "apps": {"updater": {"bundlePath": "", "recipe": "updater", "automatic": False},
                              "hub": {"bundlePath": "/Applications/Hub Studio KH.app",
                                      "recipe": "hub", "automatic": False}},
                     "intervalMinutes": 5, "once": False}
            config_file.write_text(json.dumps(saved))
            self.assertIs(bootstrap.reconcile_apps(), True)
            table = json.loads(config_file.read_text())
            self.assertEqual(table["apps"]["hub"], saved["apps"]["hub"])
            self.assertEqual(table["apps"]["updater"], saved["apps"]["updater"])
            self.assertEqual(table["apps"]["relay"],
                             {"bundlePath": "", "recipe": "app-dmg", "automatic": True})
            self.assertEqual({k: table[k] for k in saved if k != "apps"},
                             {k: saved[k] for k in saved if k != "apps"})
            self.assertIs(bootstrap.reconcile_apps(), False, "a second pass writes nothing")
            config_file.write_text("[]")
            with self.assertRaises(ValueError):
                bootstrap.reconcile_apps()


if __name__ == "__main__":
    unittest.main()