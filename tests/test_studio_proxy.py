"""A packaged Hub must be reachable the way the fleet dispatches to a peer.

The controller side of dispatch already exists in the adopted Hub: for a studio that is not on
this machine it sends `POST {peer}/studio/{modality}/{path}`. Nothing was serving that surface,
so a joined node could be listed but never used. These tests drive the real proxy.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "controller-token-0123456789abcdef"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class FakeWorker:
    """A worker that records what the proxy sent it and answers in each studio's shape."""

    def __init__(self):
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _record(self, body: bytes) -> None:
                outer.requests.append({
                    "method": self.command,
                    "path": self.path,
                    "token": self.headers.get("X-Hub-Token"),
                    "content_type": self.headers.get("Content-Type"),
                    "body": body.decode("utf-8", "replace"),
                })

            def _send(self, code: int, content_type: str, payload: bytes,
                      extra: dict | None = None):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                self._record(b"")
                if self.path.endswith("/image"):
                    self._send(200, "image/png", b"\x89PNG\r\n\x1a\nfake",
                               {"X-Content-SHA256": "abc123"})
                else:
                    self._send(200, "application/json",
                               json.dumps({"job": {"id": "job-1", "state": "done"}}).encode())

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self._record(self.rfile.read(length))
                if self.path.endswith("/boom"):
                    self._send(422, "application/json", b'{"detail":"bad request"}')
                else:
                    self._send(200, "application/json",
                               json.dumps({"job": {"id": "job-2", "state": "queued"}}).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def stop(self) -> None:
        self.server.shutdown()


class StudioProxyTests(unittest.TestCase):
    """One data directory for the class.

    The Hub binds its registry, ledger and token paths while importing, so a per-test directory
    would leave later tests pointing at the first test's deleted one. The directory lives as
    long as the class and every test builds its own app against it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="studiohub-proxy-")
        root = Path(cls.temporary.name)
        cls.old = os.environ.copy()
        for name in ("STUDIOHUB_DATA_DIR", "STUDIOHUB_WORKERS_FILE",
                     "STUDIOHUB_PACKAGED", "STUDIOHUB_REQUIRE_AUTH", "STUDIOHUB_MODEL_MANIFESTS",
                     "STUDIOHUB_MODELS_DIR"):
            os.environ.pop(name, None)
        cls.worker = FakeWorker()
        workers = root / "workers.json"
        workers.write_text(json.dumps([{
            "id": "image", "title": "Image Studio", "modality": "image",
            "host": "127.0.0.1", "port": cls.worker.port, "machine": "local", "local": True,
            "runtime_identity": {"id": "r", "compatible": True},
            "hub_token": "worker-token", "managed_output_roots": [str(root / "out")],
        }]))
        os.chmod(workers, 0o600)
        data = root / "Node State"
        data.mkdir()
        (data / ".hub_token").write_text(TOKEN + "\n")
        os.chmod(data / ".hub_token", 0o600)
        os.environ.update({"STUDIOHUB_DATA_DIR": str(data),
                           "STUDIOHUB_WORKERS_FILE": str(workers),
                           "STUDIOHUB_PACKAGED": "1"})
        # The Hub resolves its token, ledger and registry from DATA_DIR at import time, and an
        # earlier suite in the same process has already bound them. Bind them explicitly here
        # rather than depending on import order.
        from contextlib import ExitStack
        from unittest.mock import patch
        from components.hub.backend import auth, ledger, monitor as monitor_module
        from components.hub.backend import peers, registry
        cls.stack = ExitStack()
        for module, name, value in (
            (registry, "DATA_DIR", data),
            (registry, "REGISTRY_FILE", data / "studios.json"),
            (registry, "LABELS_FILE", data / "machine_labels.json"),
            (registry, "FLAGS_FILE", data / "machine_flags.json"),
            (monitor_module, "DATA_DIR", data),
            (auth, "TOKEN_FILE", data / ".hub_token"),
            (auth, "PASSWORD_FILE", data / ".hub_password.json"),
            (auth, "SESSIONS_FILE", data / ".hub_sessions.json"),
            (peers, "FLEET_TOKEN_FILE", data / ".fleet_token"),
            (ledger, "DB_FILE", data / "hub.db"),
            (ledger, "PINOKIO_HOME", data),
        ):
            cls.stack.enter_context(patch.object(module, name, value))
        registry._labels_cache = None
        registry._flags_cache = None

    @classmethod
    def tearDownClass(cls) -> None:
        cls.worker.stop()
        cls.stack.close()
        os.environ.clear()
        os.environ.update(cls.old)
        cls.temporary.cleanup()

    def client(self):
        from fastapi.testclient import TestClient
        from components.hub.standalone import create_app
        app = create_app(start_background=False)
        client = TestClient(app)
        client.__enter__()
        self.addCleanup(lambda: client.__exit__(None, None, None))
        return client

    def test_a_peer_path_reaches_the_local_worker(self) -> None:
        client = self.client()
        headers = {"X-Hub-Token": TOKEN}
        response = client.get("/studio/image/api/catalog", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["job"]["id"], "job-1")
        sent = self.worker.requests[-1]
        # The controller's credential must not be handed to the worker.
        self.assertEqual(sent["token"], "worker-token")
        self.assertEqual(sent["path"], "/api/catalog")

    def test_a_peer_submission_carries_its_body_and_reaches_the_worker(self) -> None:
        client = self.client()
        headers = {"X-Hub-Token": TOKEN}
        response = client.post("/studio/image/api/generate/txt2img",
                               json={"prompt": "a quiet harbour"}, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        sent = self.worker.requests[-1]
        self.assertEqual(sent["method"], "POST")
        self.assertEqual(sent["path"], "/api/generate/txt2img")
        self.assertIn("a quiet harbour", sent["body"])
        self.assertTrue(sent["content_type"].startswith("application/json"))

    def test_an_artifact_comes_back_byte_for_byte_with_its_receipt(self) -> None:
        client = self.client()
        headers = {"X-Hub-Token": TOKEN}
        response = client.get("/studio/image/api/generate/jobs/job-1/image", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"\x89PNG\r\n\x1a\nfake")
        self.assertEqual(response.headers["content-type"], "image/png")
        # The digest the Hub records has to survive the hop, or delivery cannot be verified.
        self.assertEqual(response.headers["x-content-sha256"], "abc123")

    def test_a_worker_error_is_passed_back_rather_than_hidden(self) -> None:
        client = self.client()
        headers = {"X-Hub-Token": TOKEN}
        response = client.post("/studio/image/api/boom", json={}, headers=headers)
        self.assertEqual(response.status_code, 422)
        self.assertIn("bad request", response.text)

    def test_the_proxy_needs_the_credential_like_everything_else(self) -> None:
        client = self.client()
        self.assertEqual(client.get("/studio/image/api/catalog").status_code, 401)
        self.assertEqual(client.get("/studio/nosuchstudio/api/catalog",
                                    headers={"X-Hub-Token": TOKEN}
                                    ).status_code, 404)

    def test_the_fleet_credential_only_reaches_classified_worker_routes(self) -> None:
        from components.hub.backend import peers

        fleet_token = "fleet-controller-token-0123456789"
        peers.set_fleet_token(fleet_token)
        client = self.client()
        headers = {"X-Hub-Token": fleet_token}

        allowed = client.get("/studio/image/api/catalog", headers=headers)
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual(self.worker.requests[-1]["path"], "/api/catalog")

        for method, path in (
            ("GET", "/studio/image/api/settings"),
            ("GET", "/studio/image/api/not-classified"),
            ("PATCH", "/studio/image/api/not-classified"),
        ):
            with self.subTest(method=method, path=path):
                response = client.request(method, path, headers=headers)
                self.assertEqual(response.status_code, 403, response.text)

    def test_a_traversal_or_an_absolute_url_is_refused(self) -> None:
        client = self.client()
        headers = {"X-Hub-Token": TOKEN}
        # Starlette normalises some of these before routing; whatever reaches the handler must
        # not escape the worker it belongs to.
        for path in ("/studio/image/../../etc/passwd", "/studio/image/api//etc/passwd",
                     "/studio/image/http://example.com/x"):
            with self.subTest(path=path):
                response = client.get(path, headers=headers)
                self.assertIn(response.status_code, (400, 404), response.text)


if __name__ == "__main__":
    unittest.main()
