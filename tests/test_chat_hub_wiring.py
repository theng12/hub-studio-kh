"""Hub wiring for the local Chat Studio.

Covers the additive Hub changes that let a packaged chat worker be dispatched and observed:
the `chat` modality is a valid activity studio, a chat completion is admitted as work, the
app has a native-facing local chat route, and that route refuses honestly when no Chat
worker is registered.

It also covers what that route leaves behind: a Chat-page completion is recorded as a
finished one-item Chat job (listed by `GET /api/hub/jobs`, summarised by
`broker.batch_summary`, answer and all, and still there after a Hub restart), and the machine
lease a proxied request holds is released when its caller disconnects.
"""
from __future__ import annotations

import asyncio
import hashlib
import http.server
import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import httpx

from components.hub import standalone
from components.hub.backend import activity

MODEL = "mlx-community/Qwen3.5-9B-4bit"

# A default that is not a value: it lets a test say "the worker stated nothing at all", which is
# a different response body from "the worker stated null" - and both must record `None`.
_MISSING = object()


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


class _StubWorker:
    """A real loopback server that records what the Hub sent it and answers under test control.

    ``hold`` keeps the response until the test releases it, so a caller can be cancelled while
    the worker still has the request; ``status``/``payload``/``raw``/``content_type`` make the
    worker refuse or answer in any shape the test needs.
    """

    def __init__(self, *, hold: bool = False, status: int = 200,
                 payload: dict | None = None, raw: bytes | None = None,
                 content_type: str = "application/json") -> None:
        self.requests: list[dict] = []
        self.hold = hold
        self.status = status
        self.payload = payload
        self.raw = raw
        self.content_type = content_type
        self.seen = threading.Event()
        self.release = threading.Event()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length)
                outer.requests.append({"path": self.path, "method": self.command,
                                       "token": self.headers.get("x-hub-token"),
                                       "body": body})
                outer.seen.set()
                if outer.hold:
                    outer.release.wait(15)
                reply = outer.payload if outer.payload is not None else {
                    "id": "chatcmpl-stub", "object": "chat.completion",
                    "model": MODEL,
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": "stub reply"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                }
                encoded = outer.raw if outer.raw is not None else json.dumps(reply).encode()
                self.send_response(outer.status)
                self.send_header("content-type", outer.content_type)
                self.send_header("content-length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_POST(self) -> None:  # noqa: N802 (stdlib naming)
                self._answer()

            def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
                self._answer()

            def log_message(self, *args) -> None:
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        # A held response must be let go before the server can stop, and a test that failed
        # midway must not park a handler thread for the rest of the session.
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


# The chat-route tests were written against the name this stub had before it grew a hold
# switch; one alias keeps their intent readable without a second server class.
_StubChatWorker = _StubWorker


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


class _PackagedHub:
    """A packaged Hub app over a disposable data dir and a worker manifest."""

    TOKEN = "chat-wiring-token"

    def build_hub(self, workers: list[dict]) -> None:
        from components.hub.backend import auth, ledger, monitor as monitor_module, registry
        from components.hub.standalone import create_app

        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.data_dir = root / "Node State"
        self.data_dir.mkdir()
        token_file = self.data_dir / ".hub_token"
        token_file.write_text(self.TOKEN + "\n")
        os.chmod(token_file, 0o600)
        self.workers_file = root / "private-workers.json"
        self.workers_file.write_text(json.dumps(workers))
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

    def restart_hub(self):
        """Build the app again over the same data dir, as a Hub restart does."""
        from components.hub.backend import broker as broker_module
        from components.hub.standalone import create_app

        broker_module.batches.clear()
        self.app = create_app(start_background=False)
        return self.app

    def tear_down_hub(self) -> None:
        self.stack.close()
        os.environ.clear()
        os.environ.update(self.old_environ)
        self.temporary.cleanup()

    def owner_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.TOKEN}"}


class ChatCompletionJobTests(_PackagedHub, unittest.TestCase):
    """A Chat-page send leaves a finished one-item job in the Hub's own history.

    The send stays synchronous - the app waits for the whole answer - and it never enters the
    broker queue. What it does now is leave a record, in the shape every other studio's finished
    work takes, so a Chat tab over `/api/hub/jobs` reads the same vocabulary as Image or Voice.
    """

    TOKEN = "chat-page-job-token"
    WORKER_TOKEN = "chat-page-worker-token"
    ANSWER = "Low water is at 04:12; the next high is at 10:26."

    def setUp(self) -> None:
        self.stub = _StubWorker(payload={
            "id": "chatcmpl-page-1", "object": "chat.completion", "model": MODEL,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": self.ANSWER}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        })
        self.addCleanup(self.stub.close)
        self.build_hub([{
            "id": "chat", "title": "Chat Studio", "modality": "chat",
            "host": "127.0.0.1", "port": self.stub.port, "machine": "node-chat", "local": True,
            "runtime_identity": {"id": "chat-runtime"}, "hub_token": self.WORKER_TOKEN,
            "managed_output_roots": [],
        }])
        self.addCleanup(self.tear_down_hub)

    def send(self, client, payload: dict | None = None):
        return client.post(
            "/api/hub/chat/completions", headers=self.owner_headers(),
            json=payload if payload is not None else {
                "model": MODEL, "messages": [{"role": "user", "content": "when is low tide?"}]})

    def test_a_completion_is_a_listable_summarisable_chat_job(self) -> None:
        from fastapi.testclient import TestClient

        with TestClient(self.app) as client:
            sent = self.send(client)
            self.assertEqual(sent.status_code, 200, sent.text)
            listing = client.get("/api/hub/jobs", headers=self.owner_headers()).json()["batches"]
            self.assertEqual(len(listing), 1, listing)
            row = listing[0]
            # `batch_summary`'s own vocabulary: modality, model id, per-state counts, timing.
            self.assertEqual(row["modality"], "chat")
            self.assertEqual(row["model"], MODEL)
            self.assertEqual(row["done"], 1)
            self.assertEqual(row["error"], 0)
            self.assertEqual(row["total"], 1)
            self.assertEqual(row["errors"], [])
            self.assertFalse(row["cancelled"])
            self.assertGreater(row["created_at"], 0)
            self.assertIsNotNone(row["finished_at"])
            self.assertTrue(row["id"])
            detail = client.get(f"/api/hub/jobs/{row['id']}",
                                headers=self.owner_headers()).json()
            item = detail["items"][0]
            self.assertEqual(item["state"], "done")
            self.assertEqual(item["text"], self.ANSWER)
            self.assertEqual(item["prompt"], "when is low tide?")
            self.assertEqual(item["index"], 0)
            self.assertIsNone(item["error"])
            self.assertGreaterEqual(item["runtime_s"], 0)
            self.assertIsNotNone(item["finished_at"])
            self.assertEqual(item["studio"], "chat")
            self.assertEqual(detail["routing"], "studio:chat")
            self.assertEqual(item["usage"]["total_tokens"], 18)
            self.assertEqual(item["media_type"], "text/plain; charset=utf-8")
            self.assertEqual(item["bytes"], len(self.ANSWER.encode("utf-8")))
            self.assertEqual(item["sha256"],
                             hashlib.sha256(self.ANSWER.encode("utf-8")).hexdigest())
            self.assertEqual(item["terminal_result"]["status"], "succeeded")
            # The answer is the artifact: served through the same route every other modality's
            # artifact uses, so the Jobs page can show it and save it without a Chat-only path.
            self.assertEqual(item["artifact_url"], f"/api/hub/jobs/{row['id']}/items/0/artifact")
            artifact = client.get(item["artifact_url"], headers=self.owner_headers())
            self.assertEqual(artifact.status_code, 200, artifact.text)
            self.assertEqual(artifact.text, self.ANSWER)
            self.assertEqual(artifact.headers["content-type"], "text/plain; charset=utf-8")
            self.assertEqual(artifact.headers["x-content-sha256"], item["sha256"])

    def test_the_answer_survives_a_hub_restart(self) -> None:
        from fastapi.testclient import TestClient

        with TestClient(self.app) as client:
            self.assertEqual(self.send(client).status_code, 200)
            batch_id = client.get("/api/hub/jobs", headers=self.owner_headers()).json()["batches"][0]["id"]
        self.restart_hub()
        with TestClient(self.app) as client:
            listing = client.get("/api/hub/jobs", headers=self.owner_headers()).json()["batches"]
            self.assertEqual([batch["id"] for batch in listing], [batch_id], listing)
            self.assertEqual(listing[0]["modality"], "chat")
            detail = client.get(f"/api/hub/jobs/{batch_id}", headers=self.owner_headers()).json()
            self.assertEqual(detail["items"][0]["text"], self.ANSWER)

    def test_a_failed_completion_is_recorded_as_failed_never_as_done(self) -> None:
        from fastapi.testclient import TestClient

        self.stub.status = 500
        self.stub.payload = {"detail": "Qwen3.5-9B 4-bit is not installed in the model library"}
        with TestClient(self.app) as client:
            sent = self.send(client)
            self.assertEqual(sent.status_code, 500)
            listing = client.get("/api/hub/jobs", headers=self.owner_headers()).json()["batches"]
            self.assertEqual(len(listing), 1, listing)
            row = listing[0]
            self.assertEqual((row["done"], row["error"], row["total"]), (0, 1, 1))
            self.assertEqual(row["errors"][0]["item_index"], 0)
            self.assertIn("not installed", row["errors"][0]["message"])
            item = client.get(f"/api/hub/jobs/{row['id']}",
                              headers=self.owner_headers()).json()["items"][0]
            self.assertEqual(item["state"], "error")
            self.assertIsNone(item["text"], "a failure must not carry an answer")
            self.assertIn("not installed", item["error"])
            self.assertEqual(item["error_code"], "CHAT_COMPLETION_FAILED")
            self.assertNotIn("terminal_result", item,
                             "a failed item must not publish a succeeded result")
            # Nothing was produced, so there is no artifact to save.
            self.assertEqual(
                client.get(f"/api/hub/jobs/{row['id']}/items/0/artifact",
                           headers=self.owner_headers()).status_code, 404)

    def test_an_empty_answer_is_a_failure_not_an_empty_completion(self) -> None:
        from fastapi.testclient import TestClient

        self.stub.payload = {"id": "chatcmpl-empty", "model": MODEL,
                             "choices": [{"index": 0, "finish_reason": "stop",
                                          "message": {"role": "assistant", "content": "   "}}]}
        with TestClient(self.app) as client:
            self.assertEqual(self.send(client).status_code, 200)
            item = client.get("/api/hub/jobs", headers=self.owner_headers()).json()["batches"]
            self.assertEqual(item[0]["error"], 1, item)
            detail = client.get(f"/api/hub/jobs/{item[0]['id']}",
                                headers=self.owner_headers()).json()["items"][0]
            self.assertEqual(detail["state"], "error")
            self.assertEqual(detail["error_code"], "CHAT_EMPTY_ANSWER")
            self.assertIsNone(detail["text"])

    def test_a_streamed_completion_is_recorded_with_its_answer(self) -> None:
        from fastapi.testclient import TestClient

        # The proxy buffers, so a `stream: true` caller gets the whole answer in one response -
        # and the record must carry the answer those chunks spell out, not the SSE transcript.
        self.stub.payload = None
        self.stub.content_type = "text/event-stream"
        self.stub.raw = (
            b'data: {"id": "chatcmpl-stream", "choices": [{"delta": {"content": "Phnom"}}]}\n\n'
            b'data: {"id": "chatcmpl-stream", "choices": [{"delta": {"content": " Penh"}}]}\n\n'
            b'data: {"id": "chatcmpl-stream", "choices": [{"delta": {}}], "usage": '
            b'{"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}}\n\n'
            b'data: [DONE]\n\n')
        with TestClient(self.app) as client:
            sent = self.send(client, {"model": MODEL, "stream": True,
                                      "messages": [{"role": "user", "content": "capital?"}]})
            self.assertEqual(sent.status_code, 200, sent.text)
            row = client.get("/api/hub/jobs",
                             headers=self.owner_headers()).json()["batches"][0]
            item = client.get(f"/api/hub/jobs/{row['id']}",
                              headers=self.owner_headers()).json()["items"][0]
        self.assertEqual(item["state"], "done")
        self.assertEqual(item["text"], "Phnom Penh")
        self.assertEqual(item["studio_job_id"], "chatcmpl-stream")
        self.assertEqual(item["usage"]["total_tokens"], 4)

    def test_a_streamed_completion_records_the_final_chunks_generation_time(self) -> None:
        from fastapi.testclient import TestClient

        self.stub.payload = None
        self.stub.content_type = "text/event-stream"
        self.stub.raw = (
            b'data: {"id": "chatcmpl-stream", "choices": [{"delta": {"content": "Phnom"}}]}\n\n'
            b'data: {"id": "chatcmpl-stream", "choices": [{"index": 0, "delta": {}, '
            b'"finish_reason": "stop"}], "usage": '
            b'{"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},'
            b' "studio_hub": {"elapsed_seconds": 4.5}}\n\n'
            b'data: [DONE]\n\n')
        with TestClient(self.app) as client:
            sent = self.send(client, {"model": MODEL, "stream": True,
                                      "messages": [{"role": "user", "content": "capital?"}]})
            self.assertEqual(sent.status_code, 200, sent.text)
            row = client.get("/api/hub/jobs",
                             headers=self.owner_headers()).json()["batches"][0]
            item = client.get(f"/api/hub/jobs/{row['id']}",
                              headers=self.owner_headers()).json()["items"][0]
        self.assertEqual(item["generation_s"], 4.5,
                         "the engine's own measured window did not reach the job record")
        self.assertNotEqual(item["generation_s"], item["runtime_s"],
                            "the streamed record has one measurement where it needs two")
        self.assertEqual(item["usage"]["completion_tokens"], 2)
        self.assertEqual(item["text"], "Phnom")

    def test_the_workers_own_generation_time_is_recorded_beside_the_round_trip(self) -> None:
        from fastapi.testclient import TestClient

        # The round trip is deliberately made much slower than the number the worker states.
        # Deriving `generation_s` from `runtime_s` is what this fails on: on a cold turn the
        # round trip carries the model load, and a rate computed from it would understate the
        # model on exactly the reply the owner is measuring.
        self.stub.hold = True
        threading.Timer(0.4, self.stub.release.set).start()
        self.stub.payload = {
            "id": "chatcmpl-timed", "object": "chat.completion", "model": MODEL,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": self.ANSWER}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            "studio_hub": {"elapsed_seconds": 0.25, "json_valid": None, "repairs": 0},
        }
        with TestClient(self.app) as client:
            sent = self.send(client)
            self.assertEqual(sent.status_code, 200, sent.text)
            row = client.get("/api/hub/jobs",
                             headers=self.owner_headers()).json()["batches"][0]
            item = client.get(f"/api/hub/jobs/{row['id']}",
                              headers=self.owner_headers()).json()["items"][0]
        self.assertEqual(item["generation_s"], 0.25)
        self.assertEqual(item["usage"]["completion_tokens"], 7)
        self.assertGreaterEqual(item["runtime_s"], 0.35)
        self.assertNotEqual(item["generation_s"], item["runtime_s"],
                            "the two numbers are the same fact recorded twice")

    def test_a_completion_that_states_no_generation_time_records_none(self) -> None:
        from fastapi.testclient import TestClient

        with TestClient(self.app) as client:
            self.assertEqual(self.send(client).status_code, 200)
            row = client.get("/api/hub/jobs",
                             headers=self.owner_headers()).json()["batches"][0]
            item = client.get(f"/api/hub/jobs/{row['id']}",
                              headers=self.owner_headers()).json()["items"][0]
            artifact = client.get(item["artifact_url"], headers=self.owner_headers())
        self.assertIsNone(item["generation_s"],
                          "the Hub published a generation time the worker never stated")
        # An absent measurement is not a failed job: the answer is recorded normally and the
        # artifact route still serves it.
        self.assertEqual(item["state"], "done")
        self.assertEqual(item["text"], self.ANSWER)
        self.assertEqual(item["usage"]["completion_tokens"], 7)
        self.assertEqual(item["terminal_result"]["status"], "succeeded")
        self.assertEqual(artifact.status_code, 200, artifact.text)
        self.assertEqual(artifact.text, self.ANSWER)

    def test_a_request_that_names_no_model_is_recorded_under_the_one_that_answered(self) -> None:
        from fastapi.testclient import TestClient

        with TestClient(self.app) as client:
            self.assertEqual(self.send(client, {"messages": [
                {"role": "user", "content": "hi"}]}).status_code, 200)
            row = client.get("/api/hub/jobs", headers=self.owner_headers()).json()["batches"][0]
        self.assertEqual(row["model"], MODEL)

    def test_a_refused_request_leaves_no_job_because_nothing_ran(self) -> None:
        from fastapi.testclient import TestClient

        with TestClient(self.app) as client:
            # A second exclusive machine lease makes the proxy refuse before dispatch.
            from components.hub.backend import broker as broker_module
            self.assertTrue(broker_module.acquire_external_machine("node-chat", "image:other"))
            self.addCleanup(broker_module.release_external_machine, "node-chat", "image:other")
            refused = self.send(client)
            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertEqual(
                client.get("/api/hub/jobs", headers=self.owner_headers()).json()["batches"], [])


class ChatGenerationRecordTests(unittest.TestCase):
    """`generation_s` is the worker's own stated number, or nothing at all.

    The rate the app shows is the stated `usage.completion_tokens` over this number, so a value
    the worker did not state - or stated as something that is not a finite, strictly positive
    number - has to stay absent rather than become a plausible-looking measurement no model
    made. `runtime_s` is the Hub's own round trip and is asserted separately in every case: the
    two are different facts and neither may be derived from the other.
    """

    ANSWER = "Low water is at 04:12."
    RUNTIME_S = 9.5

    def record(self, body: bytes, *, content_type: str = "application/json") -> dict:
        from components.hub.backend import chat_jobs, ledger

        with tempfile.TemporaryDirectory(prefix="studiohub-chat-generation-") as state:
            with patch.object(ledger, "DB_FILE", Path(state) / "hub.db"):
                return chat_jobs.record_page_completion(
                    request_body=(b'{"model": "mlx-community/Qwen3.5-9B-4bit", "messages": '
                                  b'[{"role": "user", "content": "when is low tide?"}]}'),
                    started_at=time.time() - self.RUNTIME_S, runtime_s=self.RUNTIME_S,
                    worker_id="chat", machine="local", status_code=200,
                    content_type=content_type, content=body)

    def buffered(self, elapsed=_MISSING) -> bytes:
        body = {"id": "chatcmpl-1", "model": "mlx-community/Qwen3.5-9B-4bit",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": self.ANSWER}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 7, "total_tokens": 10}}
        if elapsed is not _MISSING:
            body["studio_hub"] = {"elapsed_seconds": elapsed, "json_valid": None, "repairs": 0}
        return json.dumps(body).encode()

    @staticmethod
    def streamed(final: dict | None) -> bytes:
        lines = [b'data: {"id": "chatcmpl-stream", "choices": [{"delta": {"content": "hi"}}]}']
        if final is not None:
            lines.append(b"data: " + json.dumps(final).encode())
        lines.append(b"data: [DONE]")
        return b"\n\n".join(lines) + b"\n\n"

    @staticmethod
    def stream_final(elapsed=_MISSING) -> dict:
        final = {"id": "chatcmpl-stream",
                 "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
        if elapsed is not _MISSING:
            final["studio_hub"] = {"elapsed_seconds": elapsed}
        return final

    def test_a_stated_generation_time_is_recorded_as_stated(self) -> None:
        item = self.record(self.buffered(2.5))["items"][0]
        self.assertEqual(item["generation_s"], 2.5)
        self.assertEqual(item["runtime_s"], self.RUNTIME_S,
                         "the Hub's own round trip was replaced by the worker's number")
        self.assertNotEqual(item["generation_s"], item["runtime_s"])
        self.assertEqual(item["state"], "done")
        self.assertEqual(item["text"], self.ANSWER)
        self.assertEqual(item["usage"]["completion_tokens"], 7)

    def test_a_whole_number_states_the_same_number(self) -> None:
        self.assertEqual(self.record(self.buffered(3))["items"][0]["generation_s"], 3)

    def test_no_stated_generation_time_is_none_and_the_answer_is_still_recorded(self) -> None:
        item = self.record(self.buffered())["items"][0]
        self.assertIsNone(item["generation_s"])
        self.assertEqual(item["state"], "done")
        self.assertEqual(item["text"], self.ANSWER)
        self.assertEqual(item["usage"], {"prompt_tokens": 3, "completion_tokens": 7,
                                         "total_tokens": 10})

    def test_a_nonsense_generation_time_is_not_a_number(self) -> None:
        # `NaN` and `Infinity` are what a worker with a broken clock would actually put on the
        # wire (`json.dumps` writes those tokens), so they are sent as bytes rather than as
        # values a Python caller could have filtered first.
        for stated in ("3.2", -1.0, -0.001, 0, 0.0, float("nan"), float("inf"), float("-inf"),
                       True, False, None, [], {}, 10 ** 400):
            with self.subTest(stated=stated):
                item = self.record(self.buffered(stated))["items"][0]
                self.assertIsNone(
                    item["generation_s"],
                    f"elapsed_seconds={stated!r} became a number the worker never stated")
                self.assertEqual(item["state"], "done")
                self.assertEqual(item["text"], self.ANSWER)
                self.assertEqual(item["usage"]["completion_tokens"], 7)

    def test_a_stated_generation_time_survives_an_empty_answer(self) -> None:
        # The worker did generate and did say how long it took; it produced no text. The item is
        # an error and carries no answer, and the facts the worker stated - usage and generation
        # time - are kept rather than dropped because the outcome was a failure.
        body = json.loads(self.buffered(1.5))
        body["choices"][0]["message"]["content"] = "   "
        item = self.record(json.dumps(body).encode())["items"][0]
        self.assertEqual(item["state"], "error")
        self.assertEqual(item["error_code"], "CHAT_EMPTY_ANSWER")
        self.assertIsNone(item["text"])
        self.assertEqual(item["generation_s"], 1.5)

    def test_a_studio_hub_that_is_not_an_object_states_nothing(self) -> None:
        body = json.loads(self.buffered())
        body["studio_hub"] = "elapsed_seconds: 2.5"
        self.assertIsNone(self.record(json.dumps(body).encode())["items"][0]["generation_s"])

    def test_the_streamed_final_chunk_is_where_the_number_is_read_from(self) -> None:
        item = self.record(self.streamed(self.stream_final(6.25)),
                           content_type="text/event-stream")["items"][0]
        self.assertEqual(item["generation_s"], 6.25)
        self.assertEqual(item["runtime_s"], self.RUNTIME_S)
        self.assertNotEqual(item["generation_s"], item["runtime_s"])
        self.assertEqual(item["text"], "hi")
        self.assertEqual(item["usage"]["completion_tokens"], 1)

    def test_a_stream_that_states_no_generation_time_records_none(self) -> None:
        item = self.record(self.streamed(self.stream_final()),
                           content_type="text/event-stream")["items"][0]
        self.assertIsNone(item["generation_s"])
        self.assertEqual(item["state"], "done")
        self.assertEqual(item["text"], "hi")

    def test_a_stream_that_states_a_nonsense_generation_time_records_none(self) -> None:
        for stated in ("fast", -2, 0, float("nan"), float("inf")):
            with self.subTest(stated=stated):
                item = self.record(self.streamed(self.stream_final(stated)),
                                   content_type="text/event-stream")["items"][0]
                self.assertIsNone(item["generation_s"])
                self.assertEqual(item["state"], "done")
                self.assertEqual(item["text"], "hi")

    def test_a_stream_with_no_final_chunk_states_nothing(self) -> None:
        item = self.record(self.streamed(None),
                           content_type="text/event-stream")["items"][0]
        self.assertIsNone(item["generation_s"])
        self.assertEqual(item["text"], "hi")


class ChatPackUsageEvidenceTests(unittest.TestCase):
    """The Chat lane records token usage that is whole counts and adds up, or records none.

    `usage.completion_tokens` is the numerator of the rate the app shows and `generation_s` the
    denominator, so a usage that is a float, a negative, a bool or a total below its own parts
    is refused at the door rather than recorded as a count - a rate built on it could not be
    true. This is the rule the Hub's dispatched-pack path already enforced; the page path's own
    recording is asserted separately above.
    """

    def _run_pack(self, usage: dict) -> dict:
        from components.hub.backend import chat_jobs, ledger

        payload = {"id": "chatcmpl-pack", "model": MODEL,
                   "choices": [{"index": 0, "finish_reason": "stop",
                                "message": {"role": "assistant", "content": "hello"}}],
                   "usage": usage}

        async def run() -> dict:
            client = httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload)))
            monitor = type("Monitor", (), {"_client": client})()
            studio = {"id": "chat", "modality": "chat", "machine": "local",
                      "hub_token": "worker-token", "host": "127.0.0.1", "port": 1}
            pack = {"index": 0, "pack_id": "pack-1", "scene_ids": ["scene-1"],
                    "messages": [{"role": "user", "content": "hi"}], "params": {},
                    "state": "running", "tries": 1, "studio": "chat",
                    "duration_seconds": None, "error": None, "results": {}, "usage": None,
                    "model_revision": None, "raw_responses": [], "started_at": None,
                    "finished_at": None, "retry_at": None}
            batch = {"id": "batch-1", "model": MODEL, "kind": "completion", "cancelled": False,
                     "created_at": time.time(), "idempotency_key": "k", "packs": [pack]}
            try:
                with tempfile.TemporaryDirectory(prefix="studiohub-chat-pack-") as state:
                    with patch.object(ledger, "DB_FILE", Path(state) / "hub.db"):
                        await chat_jobs._run_pack(monitor, batch, pack, studio, "chat:owner")
            finally:
                await client.aclose()
                chat_jobs.busy_studios.discard("chat")
            return pack

        return asyncio.run(run())

    def test_whole_counts_that_add_up_are_recorded_exactly(self) -> None:
        pack = self._run_pack({"prompt_tokens": 3, "completion_tokens": 7, "total_tokens": 10})
        self.assertEqual(pack["state"], "done")
        self.assertEqual(pack["usage"], {"prompt_tokens": 3, "completion_tokens": 7,
                                         "total_tokens": 10})

    def test_counts_that_are_not_whole_non_negative_numbers_are_refused(self) -> None:
        for usage in ({"prompt_tokens": 3, "completion_tokens": 2.5, "total_tokens": 5.5},
                      {"prompt_tokens": -1, "completion_tokens": 2, "total_tokens": 1},
                      {"prompt_tokens": 3, "completion_tokens": True, "total_tokens": 4},
                      {"prompt_tokens": "3", "completion_tokens": 2, "total_tokens": 5},
                      {"prompt_tokens": 3, "completion_tokens": 2}):
            with self.subTest(usage=usage):
                pack = self._run_pack(usage)
                self.assertIsNone(pack["usage"], "an unusable count was recorded as a fact")
                self.assertEqual(pack["state"], "error")
                self.assertIn("invalid token usage evidence", pack["error"])

    def test_a_total_below_its_own_parts_is_refused(self) -> None:
        pack = self._run_pack({"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 4})
        self.assertIsNone(pack["usage"])
        self.assertEqual(pack["state"], "error")
        self.assertIn("inconsistent token usage evidence", pack["error"])


class PeerLeaseDisconnectTests(_PackagedHub, unittest.TestCase):
    """A cancelled proxied request must not pin a machine for the lease's six-hour TTL.

    Cancellation arrives as `asyncio.CancelledError` - a BaseException - so the proxy's transport
    handler never saw it and neither did the settlement below it. There is no response to ask
    whether work continues, so the route's declared policy decides: a synchronous route has no
    work left to protect, an asynchronous submit may already hold a real job. (Whether a bare
    disconnect cancels the handler is the server's business: uvicorn 0.52.4 finishes a buffered
    handler on a TCP close and cancels it at shutdown, so both shapes exist in production.)
    """

    TOKEN = "peer-disconnect-token"
    WORKER_TOKEN = "peer-disconnect-worker-token"

    def setUp(self) -> None:
        from components.hub.backend import broker as broker_module
        self.broker = broker_module
        self.chat = _StubWorker(hold=True)
        self.image = _StubWorker(hold=True)
        self.addCleanup(self.chat.close)
        self.addCleanup(self.image.close)
        self.build_hub([
            {"id": "chat", "title": "Chat Studio", "modality": "chat",
             "host": "127.0.0.1", "port": self.chat.port, "machine": "node-chat", "local": True,
             "runtime_identity": {"id": "chat-runtime"}, "hub_token": self.WORKER_TOKEN,
             "managed_output_roots": []},
            {"id": "image", "title": "Image Studio", "modality": "image",
             "host": "127.0.0.1", "port": self.image.port, "machine": "node-image", "local": True,
             "runtime_identity": {"id": "image-runtime"}, "hub_token": self.WORKER_TOKEN,
             "managed_output_roots": []},
        ])
        self.addCleanup(self.tear_down_hub)
        self.addCleanup(self.release_leases)

    def release_leases(self) -> None:
        for owner in list(standalone._PEER_LEASES):
            standalone._release_peer_lease(owner)
        for machine, owner in list(self.broker._external_machine_leases.items()):
            if str(owner).startswith("peer:"):
                self.broker.release_external_machine(machine, owner)

    def cancel_mid_flight(self, path: str, payload: dict, seen: threading.Event) -> None:
        """Send one proxied request, wait until the worker holds it, then cancel the caller."""
        async def run() -> None:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://hub") as client:
                task = asyncio.create_task(client.post(
                    path, headers=self.owner_headers(), json=payload))
                await asyncio.get_running_loop().run_in_executor(None, seen.wait, 10)
                self.assertTrue(seen.is_set(), "the worker never received the request")
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        asyncio.run(run())

    def test_every_work_route_declares_what_a_disconnect_does(self) -> None:
        release = standalone.PEER_DISCONNECT_RELEASE
        retain = standalone.PEER_DISCONNECT_RETAIN
        self.assertEqual(standalone._peer_lease_policy("v1/chat/completions"), release)
        self.assertEqual(standalone._peer_lease_policy("api/transcribe"), release)
        self.assertEqual(standalone._peer_lease_policy("api/generate/txt2img"), retain)
        # No lease is taken for anything that creates no work.
        self.assertIsNone(standalone._peer_lease_policy("api/generate/jobs/job-1"))
        self.assertIsNone(standalone._peer_lease_policy("api/generate/jobs/job-1/cancel"))
        self.assertIsNone(standalone._peer_lease_policy("api/catalog"))
        self.assertIsNone(standalone._peer_lease_policy("api/fleet/activity"))
        self.assertTrue(standalone._peer_work_request("POST", "v1/chat/completions"))
        self.assertTrue(standalone._peer_work_request("POST", "api/transcribe"))
        self.assertTrue(standalone._peer_work_request("POST", "api/generate/txt2img"))
        self.assertFalse(standalone._peer_work_request("GET", "api/generate/jobs/job-1"))
        self.assertFalse(standalone._peer_work_request("POST", "api/generate/jobs/job-1/cancel"))
        self.assertFalse(standalone._peer_work_request("POST", "api/generate/jobs/job-1/ack"))
        self.assertFalse(standalone._peer_work_request("POST", "api/catalog"))

    def test_a_cancelled_chat_request_releases_the_machine(self) -> None:
        from components.hub.backend import ledger

        self.cancel_mid_flight(
            "/api/hub/chat/completions",
            {"model": MODEL, "messages": [{"role": "user", "content": "hold this"}]},
            self.chat.seen)
        self.assertNotIn("node-chat", self.broker.busy_machines(),
                         "a cancelled synchronous request left the machine reserved")
        self.assertEqual(standalone._PEER_LEASES, {})
        # The dispatched attempt is on the record, as a failure that names its reason.
        records = ledger.load_finished_batches()
        self.assertEqual(len(records), 1, records)
        item = records[0]["items"][0]
        self.assertEqual(records[0]["modality"], "chat")
        self.assertEqual(item["state"], "error")
        self.assertEqual(item["error_code"], "CHAT_REQUEST_CANCELLED")
        self.assertIsNone(item["text"])

    def test_a_cancelled_async_submit_keeps_its_machine(self) -> None:
        self.cancel_mid_flight(
            "/studio/image/api/generate/txt2img",
            {"repo": "AITRADER/FLUX2-klein-4B-mlx-4bit", "prompt": "a quiet harbour"},
            self.image.seen)
        # The worker may already have accepted a job the Hub never saw acknowledged; releasing
        # the machine here would let a second request overlap the first one that is still running.
        self.assertIn("node-image", self.broker.busy_machines())

    def test_an_accepted_async_job_keeps_and_then_releases_its_machine(self) -> None:
        from fastapi.testclient import TestClient

        self.image.hold = False
        self.image.payload = {"job": {"id": "img-1", "state": "queued"}}
        with TestClient(self.app) as client:
            accepted = client.post("/studio/image/api/generate/txt2img",
                                   headers=self.owner_headers(),
                                   json={"repo": "AITRADER/FLUX2-klein-4B-mlx-4bit"})
            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertIn("node-image", self.broker.busy_machines())
            self.image.payload = {"job": {"id": "img-1", "state": "done"}}
            status = client.get("/studio/image/api/generate/jobs/img-1",
                                headers=self.owner_headers())
            self.assertEqual(status.status_code, 200, status.text)
        self.assertNotIn("node-image", self.broker.busy_machines())


if __name__ == "__main__":
    unittest.main()
