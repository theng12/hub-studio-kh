"""Behavioral coverage for the copied-app launch verifier."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "qualification" / "verify-launch.sh"


SERVICE = r'''#!{python}
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import signal

parser = argparse.ArgumentParser()
parser.add_argument("--data-dir")
parser.add_argument("--models-dir")
parser.add_argument("--ready-file", required=True)
parser.add_argument("--parent-pid")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=0)
args = parser.parse_args()

mode = os.environ.get("FIXTURE_MODE", "healthy")
Path(os.environ["FIXTURE_ARGS_FILE"]).write_text(json.dumps({"data": args.data_dir, "models": args.models_dir}))
Path(os.environ["FIXTURE_PID_FILE"]).write_text(str(os.getpid()))

if mode == "malformed_readiness":
    Path(args.ready_file).write_text("not json")
    signal.pause()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if mode == "http_error":
            self.send_response(500)
            body = b'{"ok":false}'
        elif mode == "malformed_health":
            self.send_response(200)
            body = b'not json'
        elif mode == "ok_false":
            self.send_response(200)
            body = b'{"ok":false}'
        else:
            self.send_response(200)
            body = b'{"ok":true}'
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass

server = HTTPServer((args.host, args.port), Handler)
url = "http://%s:%d" % server.server_address
Path(args.ready_file).write_text(json.dumps({"url": url, "token": "fixture"}))
server.serve_forever()
'''


class VerifyLaunchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="studiohub-verify-launch-")
        self.root = Path(self.folder.name)
        self.app = self.root / "Fixture.app"
        self.library = self.root / "models"
        self.library.mkdir()
        self.pid_file = self.root / "service.pid"
        self._make_app(self.app)

    def tearDown(self):
        if self.pid_file.exists():
            self._assert_service_stopped()
        self.folder.cleanup()

    def _make_app(self, app: Path) -> None:
        binary = app / "Contents" / "MacOS"
        resources = app / "Contents" / "Resources"
        runtime = resources / "runtimes" / "voice" / "bin"
        binary.mkdir(parents=True)
        runtime.mkdir(parents=True)
        (runtime / "python3").symlink_to(sys.executable)
        (resources / "release-manifest.json").write_text(json.dumps({
            "version": "fixture",
            "implementation_commit": "fixture-commit",
            "development_build": True,
        }))
        service = binary / "StudioHubService"
        service.write_text(SERVICE.replace("{python}", sys.executable))
        service.chmod(0o755)

    def _run(self, mode: str = "healthy", *, app: Path | None = None,
             path: str | None = None) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.update({
            "APP": str(app or self.app),
            "LIB": str(self.library),
            "PORT": "0",
            "TMPDIR": str(self.root),
            "FIXTURE_MODE": mode,
            "FIXTURE_PID_FILE": str(self.pid_file),
            "FIXTURE_ARGS_FILE": str(self.root / "service-args.json"),
        })
        if path is not None:
            env["PATH"] = path
        result = subprocess.run(
            ["sh", str(VERIFIER)], env=env, text=True, capture_output=True,
            timeout=20,
        )
        self._assert_service_stopped()
        return result

    def _assert_service_stopped(self) -> None:
        if not self.pid_file.exists():
            return
        pid = int(self.pid_file.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        os.kill(pid, signal.SIGKILL)
        self.fail(f"verifier left service process {pid} running")

    def assert_verifier_failed(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("RESULT: FAIL", result.stdout)
        self.assertNotIn("RESULT: PASS", result.stdout)

    def test_rejects_malformed_readiness_json_and_stops_service(self):
        self.assert_verifier_failed(self._run("malformed_readiness"))

    def test_rejects_malformed_release_manifest(self):
        manifest = self.app / "Contents" / "Resources" / "release-manifest.json"
        manifest.write_text("not json")
        self.assert_verifier_failed(self._run())

    def test_rejects_health_http_error_and_stops_service(self):
        self.assert_verifier_failed(self._run("http_error"))

    def test_rejects_malformed_health_json_and_stops_service(self):
        self.assert_verifier_failed(self._run("malformed_health"))

    def test_rejects_ok_false_and_stops_service(self):
        self.assert_verifier_failed(self._run("ok_false"))

    def test_app_path_with_apostrophe_has_valid_identity_and_health(self):
        quoted_app = self.root / "Owner's Fixture.app"
        self.app.rename(quoted_app)
        result = self._run(app=quoted_app)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("identity: fixture fixture-commit development_build: True", result.stdout)
        self.assertIn("RESULT: PASS", result.stdout)
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_never_hands_the_service_a_real_library(self):
        """A throwaway data dir over a real library retired real weights (2026-10-02; shelving since deleted)."""
        (self.library / "whisper-base" / "rev").mkdir(parents=True)
        (self.library / "whisper-base" / "rev" / "weights.bin").write_text("weights")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = json.loads((self.root / "service-args.json").read_text())
        models = Path(args["models"]).resolve()
        self.assertNotEqual(models, self.library.resolve())
        self.assertTrue(str(models).startswith(str(Path(args["data"]).resolve())), args)
        self.assertEqual([p.name for p in models.iterdir()], [".studiohub"], "the library given must be empty")
        self.assertEqual((self.library / "whisper-base" / "rev" / "weights.bin").read_text(), "weights")

    def test_uses_bundled_python_when_system_python_is_broken(self):
        shim_dir = self.root / "bin"
        shim_dir.mkdir()
        marker = self.root / "system-python-used"
        shim = shim_dir / "python3"
        shim.write_text(
            "#!/bin/sh\n"
            f"touch '{marker}'\n"
            "exit 97\n"
        )
        shim.chmod(0o755)
        result = self._run(path=str(shim_dir) + os.pathsep + os.environ["PATH"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(marker.exists(), "verifier invoked system python3")
        self.assertIn("RESULT: PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
