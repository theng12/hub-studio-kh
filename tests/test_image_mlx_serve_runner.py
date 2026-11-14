from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

from components.image.app.backend import mlx_serve_runner


def _png(width: int, height: int) -> str:
    import io
    stream = io.BytesIO()
    Image.new("RGB", (width, height), (31, 47, 63)).save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode()


def _fake_server(path: Path, payload: dict, *, delay: float = 0.0,
                 version: str = "26.10.1", ignore_term: bool = False) -> None:
    source = f'''import json, os, sys, time
args = sys.argv[1:]
if args == ["--version"]:
    print("mlx-serve {version}")
    raise SystemExit(0)
if {ignore_term!r}:
    import signal
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
from http.server import BaseHTTPRequestHandler, HTTPServer
port = int(args[args.index("--port") + 1])
key_name = args[args.index("--api-key-env") + 1]
key = os.environ[key_name]
model = args[args.index("--model") + 1]
observed_path = model + "/observed.json"
open(observed_path, "w").write(json.dumps({{"argv": args, "env": sorted(os.environ), "pid": os.getpid()}}))
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args): pass
    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer " + key:
            self.send_response(401); self.end_headers(); return
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def do_POST(self):
        if self.headers.get("Authorization") != "Bearer " + key:
            self.send_response(401); self.end_headers(); return
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        observed = json.loads(open(observed_path).read())
        observed["request"] = request
        open(observed_path, "w").write(json.dumps(observed))
        time.sleep({delay!r})
        body = json.dumps({payload!r}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
HTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''
    Path(str(path) + ".py").write_text(source)
    path.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$0.py" "$@"\n')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class MlxServeRunnerTests(unittest.TestCase):
    def test_version_fixture_uses_the_exact_spaced_interpreter(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            interpreter = root / "Owner's Python Runtime" / "python3"
            interpreter.parent.mkdir()
            interpreter.symlink_to(sys.executable)
            binary = root / "Owner's mlx-serve"
            with patch.object(sys, "executable", str(interpreter)):
                _fake_server(binary, {"data": []})
            result = subprocess.run([str(binary), "--version"], capture_output=True,
                                    text=True, env={"PATH": "/nonexistent"}, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "mlx-serve 26.10.1")

    def test_worker_shutdown_kills_a_sigterm_ignoring_active_leaf(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            weights = root / "weights"
            weights.mkdir()
            binary = root / "mlx-serve"
            _fake_server(binary, {"data": [{"b64_json": _png(1024, 1024)}]},
                         delay=30.0, ignore_term=True)
            cancel = threading.Event()
            errors: list[Exception] = []

            def generate() -> None:
                try:
                    with patch.object(mlx_serve_runner, "runtime_binary", return_value=binary):
                        mlx_serve_runner.run_image(weights, {
                            "prompt": "wait", "width": 1024, "height": 1024,
                            "steps": 16, "guidance": 1.0, "seed": 42,
                        }, root / "output.png", cancel, timeout_s=30)
                except Exception as exc:
                    errors.append(exc)

            thread = threading.Thread(target=generate)
            thread.start()
            observed_path = weights / "observed.json"
            deadline = time.monotonic() + 5
            while not observed_path.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(observed_path.exists(), "native leaf did not start")
            pid = json.loads(observed_path.read_text())["pid"]
            try:
                mlx_serve_runner.stop_active_leaf(timeout_s=0.2)
            finally:
                cancel.set()
                try:
                    os.kill(pid, 9)
                except ProcessLookupError:
                    pass
                thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertTrue(errors)
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_request_pixel_budget_is_enforced_before_starting_the_leaf(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            weights = root / "weights"
            weights.mkdir()
            with patch.object(mlx_serve_runner, "runtime_problem", return_value=None), \
                 patch.object(mlx_serve_runner.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(mlx_serve_runner.MlxServeFailure, "pixel limit"):
                    mlx_serve_runner.run_image(weights, {
                        "prompt": "too large", "width": 2048, "height": 1024,
                        "steps": 16, "guidance": 1.0, "seed": 42,
                    }, root / "output.png", threading.Event(), timeout_s=10)
            popen.assert_not_called()

    def test_readiness_rejects_an_unpinned_native_version(self) -> None:
        with TemporaryDirectory() as temporary:
            binary = Path(temporary) / "mlx-serve"
            _fake_server(binary, {"data": []}, version="26.10.2")
            with patch.object(mlx_serve_runner, "runtime_binary", return_value=binary):
                self.assertIn("requires mlx-serve 26.10.1",
                              mlx_serve_runner.runtime_problem())

    def test_private_leaf_returns_only_the_verified_requested_png(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            weights = root / "weights"
            weights.mkdir()
            binary = root / "mlx-serve"
            _fake_server(binary, {"data": [{"b64_json": _png(1024, 1024)}]})
            destination = root / "output.png"
            with patch.object(mlx_serve_runner, "runtime_binary", return_value=binary), \
                 patch.dict(os.environ, {
                     "HF_TOKEN": "must-not-leak", "STUDIOHUB_HUB_TOKEN": "must-not-leak",
                     "DYLD_INSERT_LIBRARIES": "/must/not/leak.dylib",
                 }, clear=False):
                mlx_serve_runner.run_image(weights, {
                    "prompt": "a river at dawn", "width": 1024, "height": 1024,
                    "steps": 16, "guidance": 1.0, "seed": 42,
                }, destination, threading.Event(), timeout_s=10)

            with Image.open(destination) as image:
                self.assertEqual((image.format, image.size), ("PNG", (1024, 1024)))
            observed = json.loads((weights / "observed.json").read_text())
            self.assertNotIn("must-not-leak", " ".join(observed["argv"]))
            self.assertNotIn("HF_TOKEN", observed["env"])
            self.assertNotIn("STUDIOHUB_HUB_TOKEN", observed["env"])
            self.assertNotIn("DYLD_INSERT_LIBRARIES", observed["env"])
            self.assertIn("--api-key-env", observed["argv"])
            self.assertIn("--api-key-strict", observed["argv"])
            self.assertNotIn("--os-reserve-gib", observed["argv"],
                             "keep the pinned runtime automatic reserve for each RAM tier")
            self.assertNotIn("--skip-mem-preflight", observed["argv"])
            self.assertNotIn(destination.read_bytes().hex()[:32], " ".join(observed["argv"]))
            retained = list((root / "_trash").rglob("mlx-serve.log"))
            self.assertEqual(len(retained), 1, "native diagnostics must remain recoverable")
            self.assertEqual(observed["request"]["steps"], 16)
            self.assertEqual(observed["request"]["guidance_scale"], 1.0)
            self.assertNotIn("num_inference_steps", observed["request"])
            self.assertNotIn("guidance", observed["request"])

    def test_artifact_urls_are_refused(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            weights = root / "weights"
            weights.mkdir()
            binary = root / "mlx-serve"
            _fake_server(binary, {"data": [{
                "url": "http://127.0.0.1:1/not-allowed.png",
                "b64_json": _png(1024, 1024),
            }]})
            with patch.object(mlx_serve_runner, "runtime_binary", return_value=binary):
                with self.assertRaisesRegex(mlx_serve_runner.MlxServeFailure, "artifact URL"):
                    mlx_serve_runner.run_image(weights, {
                        "prompt": "x", "width": 1024, "height": 1024,
                        "steps": 16, "guidance": 1.0, "seed": 42,
                    }, root / "output.png", threading.Event(), timeout_s=10)

    def test_wrong_dimensions_are_refused_without_publishing(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            weights = root / "weights"
            weights.mkdir()
            binary = root / "mlx-serve"
            _fake_server(binary, {"data": [{"b64_json": _png(1280, 720)}]})
            destination = root / "output.png"
            with patch.object(mlx_serve_runner, "runtime_binary", return_value=binary):
                with self.assertRaisesRegex(mlx_serve_runner.MlxServeFailure, "dimensions"):
                    mlx_serve_runner.run_image(weights, {
                        "prompt": "x", "width": 1024, "height": 1024,
                        "steps": 16, "guidance": 1.0, "seed": 42,
                    }, destination, threading.Event(), timeout_s=10)
            self.assertFalse(destination.exists())

    def test_running_cancel_terminates_the_leaf_and_discards_late_output(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            weights = root / "weights"
            weights.mkdir()
            binary = root / "mlx-serve"
            _fake_server(binary, {"data": [{"b64_json": _png(1024, 1024)}]}, delay=30.0)
            destination = root / "output.png"
            cancel = threading.Event()
            timer = threading.Timer(0.5, cancel.set)
            timer.start()
            try:
                with patch.object(mlx_serve_runner, "runtime_binary", return_value=binary):
                    with self.assertRaises(mlx_serve_runner.MlxServeCancelled):
                        mlx_serve_runner.run_image(weights, {
                            "prompt": "wait", "width": 1024, "height": 1024,
                            "steps": 16, "guidance": 1.0, "seed": 42,
                        }, destination, cancel, timeout_s=10)
            finally:
                timer.cancel()
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
