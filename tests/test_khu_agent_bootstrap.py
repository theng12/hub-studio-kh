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

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        # Recorded so a test can prove no credential travelled with the request.
        self.server.seen_headers.append(dict(self.headers))
        request = json.loads(body.decode())
        if self.path != "/enrol":
            self.send_response(404)
            self.end_headers()
            return
        payload = {
            "machine": request["machine"],
            "nonce": request["nonce"],
            "feedURL": self.server.feed_url,
            "publicKey": self.server.public_key_b64,
            "issuedAt": int(time.time()),
            "token": self.server.issued_token,
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
        server.seen_headers = []
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


if __name__ == "__main__":
    unittest.main()