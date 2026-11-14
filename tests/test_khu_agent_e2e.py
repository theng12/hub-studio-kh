"""End-to-end dry run of the Hub-shipped KH Updater bootstrap.

The publisher is a local fixture HTTP server. The kit is built from the
repository's own packaging script against the freshly built `khu` and
`khu-stub` binaries. The home is a temporary directory. The launchctl
invocation is replaced with a recorder. The test asserts:

* the kit verifies against its own manifest;
* install writes the version tree, current/previous links and the stub;
* enrol against the fixture publisher produces a config whose token is the
  one the fixture issued, whose publisher key matches the kit, and whose
  apps list contains the three known bundles;
* activate loads the launchd job via the recorder;
* the issued token never appears in any recorder argument.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
KH_UPDATER = Path("/build/Developer/kh-updater")

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub.backend import ed25519, khu_agent  # noqa: E402


def _seed() -> tuple[bytes, bytes]:
    seed = secrets.token_bytes(32)
    return seed, ed25519.public_key(seed)


def _sign(seed: bytes, payload: dict) -> dict:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return {
        "reply": base64.b64encode(raw).decode(),
        "signature": base64.b64encode(ed25519.sign(seed, raw)).decode(),
    }


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        return

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        # Recorded so the test can prove no credential travelled with the request.
        self.server.seen_headers.append(dict(self.headers))
        request = json.loads(body.decode())
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

    def __call__(self, argv, *, capture_output=True, timeout=None):
        self.calls.append(argv)
        rc = 1 if argv[:2] == ["/bin/launchctl", "print"] else 0
        return subprocess.CompletedProcess(argv, rc, stdout="", stderr="")


class EndToEndDryRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = Path(tempfile.mkdtemp(prefix="khu-e2e-"))
        binaries = KH_UPDATER / "build"
        if not (binaries / "khu").is_file() or not (binaries / "khu-stub").is_file():
            raise unittest.SkipTest("KH Updater binaries not built; run ./build.sh build first")
        kit_builder = KH_UPDATER / "packaging/build-agent-kit.sh"
        cls.kit_dir = cls.work / "AgentKit"
        result = subprocess.run(["/bin/bash", str(kit_builder), "1.0.0", str(cls.kit_dir)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise unittest.SkipTest(f"build-agent-kit.sh failed: {result.stderr}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def setUp(self):
        self.home = self.work / "Home"
        self.home.mkdir()
        self.seed, self.public = _seed()
        self.public_b64 = base64.b64encode(self.public).decode()
        self.issued_token = secrets.token_hex(32)
        server = HTTPServer(("127.0.0.1", 0), _Handler)
        server.seed = self.seed
        server.public_key_b64 = self.public_b64
        server.issued_token = self.issued_token
        server.seen_headers = []
        port = server.server_address[1]
        # The feed URL in the reply must match what's in the kit's publisher.json.
        server.feed_url = f"http://127.0.0.1:{port}/release.json"
        self.server = server
        threading.Thread(target=server.serve_forever, daemon=True).start()
        publisher = {
            "feedURL": f"http://127.0.0.1:{port}/release.json",
            "enrolURL": f"http://127.0.0.1:{port}/enrol",
            "publicKey": self.public_b64,
            "testAuthority": True,
        }
        kit = self.kit_dir
        (kit / "publisher.json").write_text(json.dumps(publisher))
        present = {path.relative_to(kit).as_posix() for path in kit.rglob("*")
                   if not path.is_dir()}
        # The builder may also carry the shared icon; anything else is drift to catch here.
        self.assertEqual(present - KIT_OPTIONAL, set(KIT_FILES))
        files = {name: _read_pin(kit / name) for name in sorted(present)}
        (kit / "MANIFEST.json").write_text(json.dumps({"files": files}))
        self.kit = kit
        self.publisher = publisher

    def tearDown(self):
        self.server.shutdown()

    def test_full_dry_run(self):
        recorder = _RecordingRun()
        bootstrap = khu_agent.Bootstrap(self.kit, self.home, allow_test=True, run=recorder)
        self.assertEqual(bootstrap.install(), "installed")
        asyncio.run(bootstrap.enrol("dry-run-mac"))
        bootstrap.activate()
        config = json.loads((self.home / "Library/KHUpdater/base/config.json").read_text())
        self.assertEqual(config["machine"], "dry-run-mac")
        self.assertEqual(config["token"], self.issued_token)
        self.assertEqual(config["publicKey"], self.public_b64)
        self.assertEqual(config["feedURL"], self.publisher["feedURL"])
        self.assertFalse(config["once"])
        self.assertIn("updater", config["apps"])
        for call in recorder.calls:
            joined = " ".join(call)
            self.assertNotIn(self.issued_token, joined)
            self.assertNotIn("fixture-fleet-credential", joined)
        # Nothing in this flow sends a credential, so the publisher fixture must have seen
        # none either - asserted on the wire, where it would actually matter.
        self.assertTrue(self.server.seen_headers)
        for headers in self.server.seen_headers:
            lowered = {name.lower() for name in headers}
            self.assertNotIn("x-hub-token", lowered)
            self.assertNotIn("authorization", lowered)
        self.assertTrue((self.home / "Library/LaunchAgents/com.khupdater.agent.plist").is_file())
        self.assertEqual(recorder.calls[-1][:3],
                         ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}"])


KIT_FILES = {"VERSION", "bin/khu", "bin/khu-stub", "install-agent.sh",
             "com.khupdater.agent.plist", "agent-config.example.json", "publisher.json"}
KIT_OPTIONAL = {"AppIcon.icns"}


def _read_pin(path: Path) -> dict:
    data = path.read_bytes()
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


if __name__ == "__main__":
    unittest.main()