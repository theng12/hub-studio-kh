"""Hub wiring for the local Chat Studio.

Covers the additive Hub changes that let a packaged chat worker be dispatched and observed:
the `chat` modality is a valid activity studio, a chat completion is admitted as work, the
app has a native-facing local chat route, and that route refuses honestly when no Chat
worker is registered.
"""
from __future__ import annotations

import http.server
import json
import os
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from components.hub import standalone
from components.hub.backend import activity


class ChatHubWiringTests(unittest.TestCase):
    def test_chat_is_a_valid_activity_studio(self) -> None:
        self.assertIn("chat", activity.VALID_STUDIOS)

    def test_chat_completion_is_admitted_as_work(self) -> None:
        self.assertTrue(standalone._peer_work_request("POST", "v1/chat/completions"))
        self.assertFalse(standalone._peer_work_request("GET", "v1/chat/completions"))

    def test_native_route_and_peer_proxy_are_registered(self) -> None:
        paths = {getattr(route, "path", "") for route in standalone.app.routes}
        self.assertIn("/api/hub/chat/completions", paths)
        self.assertIn("/studio/{modality}/{path:path}", paths)


class ChatHubRouteTests(unittest.TestCase):
    TOKEN = "chat-route-test-token"

    def setUp(self) -> None:
        from components.hub.backend import auth, ledger, monitor as monitor_module, registry
        from components.hub.standalone import create_app

        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.data_dir = root / "Node State"
        self.data_dir.mkdir()
        token_file = self.data_dir / ".hub_token"
        token_file.write_text(self.TOKEN + "\n")
        os.chmod(token_file, 0o600)
        # A workers manifest with no Chat worker: the route must say so rather than hang.
        self.workers_file = root / "private-workers.json"
        self.workers_file.write_text(json.dumps([{
            "id": "image", "title": "Image Studio", "modality": "image",
            "host": "127.0.0.1", "port": 51101, "machine": "node-a", "local": True,
            "runtime_identity": {"id": "image-runtime"}, "hub_token": "worker-token",
            "managed_output_roots": [],
        }]))
        os.chmod(self.workers_file, 0o600)
        self.old_environ = os.environ.copy()
        os.environ.update({
            "STUDIOHUB_DATA_DIR": str(self.data_dir),
            "STUDIOHUB_WORKERS_FILE": str(self.workers_file),
            "STUDIOHUB_PACKAGED": "1",
        })
        self.stack = ExitStack()
        for module, name, value in (
            (registry, "DATA_DIR", self.data_dir),
            (registry, "REGISTRY_FILE", self.data_dir / "studios.json"),
            (registry, "LABELS_FILE", self.data_dir / "machine_labels.json"),
            (registry, "FLAGS_FILE", self.data_dir / "machine_flags.json"),
            (monitor_module, "DATA_DIR", self.data_dir),
            (auth, "TOKEN_FILE", self.data_dir / ".hub_token"),
            (auth, "PASSWORD_FILE", self.data_dir / ".hub_password.json"),
            (auth, "SESSIONS_FILE", self.data_dir / ".hub_sessions.json"),
            (ledger, "DB_FILE", self.data_dir / "hub.db"),
            (ledger, "PINOKIO_HOME", self.data_dir),
        ):
            self.stack.enter_context(patch.object(module, name, value))
        registry._labels_cache = None
        registry._flags_cache = None
        self.app = create_app(start_background=False)

    def tearDown(self) -> None:
        self.stack.close()
        os.environ.clear()
        os.environ.update(self.old_environ)
        self.temporary.cleanup()

    def test_requires_the_hub_credential(self) -> None:
        from fastapi.testclient import TestClient

        with TestClient(self.app) as client:
            denied = client.post("/api/hub/chat/completions",
                                 json={"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(denied.status_code, 401)

    def test_refuses_honestly_when_no_chat_worker_is_registered(self) -> None:
        from fastapi.testclient import TestClient

        with TestClient(self.app) as client:
            refused = client.post(
                "/api/hub/chat/completions",
                headers={"Authorization": f"Bearer {self.TOKEN}"},
                json={"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(refused.status_code, 503, refused.text)
        self.assertIn("Chat worker is unavailable", refused.text)


class _StubChatWorker:
    """A real loopback server that answers one OpenAI-shaped completion."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 (stdlib naming)
                length = int(self.headers.get("content-length") or 0)
                self.rfile.read(length)
                outer.requests.append({"path": self.path,
                                       "token": self.headers.get("x-hub-token")})
                body = json.dumps({
                    "id": "chatcmpl-stub", "object": "chat.completion",
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": "stub reply"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                }).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class ChatHubForwardingTests(unittest.TestCase):
    """The Hub route must reach a registered chat worker — not just refuse cleanly."""

    TOKEN = "chat-forward-test-token"
    WORKER_TOKEN = "chat-worker-token"

    def setUp(self) -> None:
        from components.hub.backend import auth, ledger, monitor as monitor_module, registry
        from components.hub.standalone import create_app

        self.stub = _StubChatWorker()
        self.addCleanup(self.stub.close)
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.data_dir = root / "Node State"
        self.data_dir.mkdir()
        token_file = self.data_dir / ".hub_token"
        token_file.write_text(self.TOKEN + "\n")
        os.chmod(token_file, 0o600)
        self.workers_file = root / "private-workers.json"
        self.workers_file.write_text(json.dumps([{
            "id": "chat", "title": "Chat Studio", "modality": "chat",
            "host": "127.0.0.1", "port": self.stub.port, "machine": "node-a", "local": True,
            "runtime_identity": {"id": "chat-runtime"}, "hub_token": self.WORKER_TOKEN,
            "managed_output_roots": [],
        }]))
        os.chmod(self.workers_file, 0o600)
        self.old_environ = os.environ.copy()
        os.environ.update({
            "STUDIOHUB_DATA_DIR": str(self.data_dir),
            "STUDIOHUB_WORKERS_FILE": str(self.workers_file),
            "STUDIOHUB_PACKAGED": "1",
        })
        self.stack = ExitStack()
        for module, name, value in (
            (registry, "DATA_DIR", self.data_dir),
            (registry, "REGISTRY_FILE", self.data_dir / "studios.json"),
            (registry, "LABELS_FILE", self.data_dir / "machine_labels.json"),
            (registry, "FLAGS_FILE", self.data_dir / "machine_flags.json"),
            (monitor_module, "DATA_DIR", self.data_dir),
            (auth, "TOKEN_FILE", self.data_dir / ".hub_token"),
            (auth, "PASSWORD_FILE", self.data_dir / ".hub_password.json"),
            (auth, "SESSIONS_FILE", self.data_dir / ".hub_sessions.json"),
            (ledger, "DB_FILE", self.data_dir / "hub.db"),
            (ledger, "PINOKIO_HOME", self.data_dir),
        ):
            self.stack.enter_context(patch.object(module, name, value))
        registry._labels_cache = None
        registry._flags_cache = None
        self.app = create_app(start_background=False)

    def tearDown(self) -> None:
        self.stack.close()
        os.environ.clear()
        os.environ.update(self.old_environ)
        self.temporary.cleanup()

    def test_forwards_the_completion_and_signs_with_the_worker_token(self) -> None:
        from fastapi.testclient import TestClient

        with TestClient(self.app) as client:
            response = client.post(
                "/api/hub/chat/completions",
                headers={"Authorization": f"Bearer {self.TOKEN}"},
                json={"model": "mlx-community/Qwen3.5-9B-4bit",
                      "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["choices"][0]["message"]["content"], "stub reply")
        self.assertTrue(self.stub.requests, "the Hub never reached the chat worker")
        self.assertEqual(self.stub.requests[-1]["path"], "/v1/chat/completions")
        self.assertEqual(self.stub.requests[-1]["token"], self.WORKER_TOKEN)


if __name__ == "__main__":
    unittest.main()
