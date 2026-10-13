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
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from unittest.mock import patch

TOKEN = "controller-token-0123456789abcdef"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class FakeWorker:
    """A worker that records what the proxy sent it and answers in each studio's shape."""

    def __init__(self):
        self.requests: list[dict] = []
        self.memory_release_busy = False
        # The job-status answer is configurable so a test can drive one job from
        # accepted to terminal without a second stub.
        self.poll_job: dict = {"id": "job-1", "state": "done"}
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
                               json.dumps({"job": dict(outer.poll_job)}).encode())

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self._record(self.rfile.read(length))
                if self.path == "/api/memory/release":
                    if outer.memory_release_busy:
                        self._send(409, "application/json",
                                   b'{"detail":"music generation is active"}')
                    else:
                        self._send(200, "application/json", json.dumps({
                            "mode": "auto", "loaded_models": [], "busy": False,
                            "released": True, "reason": "manual",
                        }).encode())
                elif self.path.endswith("/boom"):
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
        self.server.server_close()


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
        cls.subtitle = FakeWorker()
        cls.music = FakeWorker()
        workers = root / "workers.json"
        workers.write_text(json.dumps([
            {
                "id": "image", "title": "Image Studio", "modality": "image",
                "host": "127.0.0.1", "port": cls.worker.port, "machine": "local", "local": True,
                "runtime_identity": {"id": "r", "compatible": True},
                "hub_token": "worker-token", "managed_output_roots": [str(root / "out")],
            },
            {
                "id": "subtitle", "title": "Subtitle Studio", "modality": "transcription",
                "host": "127.0.0.1", "port": cls.subtitle.port, "machine": "local", "local": True,
                "runtime_identity": {"id": "r", "compatible": True},
                "hub_token": "worker-token", "managed_output_roots": [str(root / "out")],
            },
            {
                "id": "music", "title": "Music Studio", "modality": "music",
                "host": "127.0.0.1", "port": cls.music.port, "machine": "local", "local": True,
                "runtime_identity": {"id": "r", "compatible": True},
                "hub_token": "worker-token", "managed_output_roots": [str(root / "out")],
            },
        ]))
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
            (peers, "SHARED_STUDIO_TOKEN_FILE", data / ".kh_studio_token"),
            (peers, "PREVIOUS_TOKENS_FILE", data / ".fleet_token_previous"),
            (ledger, "DB_FILE", data / "hub.db"),
            (ledger, "PINOKIO_HOME", data),
        ):
            cls.stack.enter_context(patch.object(module, name, value))
        registry._labels_cache = None
        registry._flags_cache = None

    @classmethod
    def tearDownClass(cls) -> None:
        cls.worker.stop()
        cls.subtitle.stop()
        cls.music.stop()
        cls.stack.close()
        os.environ.clear()
        os.environ.update(cls.old)
        cls.temporary.cleanup()

    def setUp(self) -> None:
        # A node holds a physical machine lease while a peer job it accepted is
        # still running; a test that fails midway must not leave that lease to
        # poison the next one.
        self.addCleanup(self.release_peer_leases)

    @staticmethod
    def release_peer_leases() -> None:
        from components.hub import standalone
        from components.hub.backend import broker
        release = getattr(standalone, "_release_peer_lease", None)
        for owner in list(getattr(standalone, "_PEER_LEASES", {})):
            if release is not None:
                release(owner)
        for machine, owner in list(broker._external_machine_leases.items()):
            if str(owner).startswith("peer:"):
                broker.release_external_machine(machine, owner)

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

    def test_remote_memory_control_releases_idle_music_through_the_node_proxy(self) -> None:
        """A controller's release uses Fleet auth outside and the worker token inside."""
        import asyncio

        from components.hub.backend import peers
        from components.hub.backend.memory_control import FleetMemoryControl

        fleet_token = "fleet-controller-token-0123456789"
        peers.set_fleet_token(fleet_token)
        node = self.client()

        class NodeClient:
            async def request(inner_self, method, url, headers=None, json=None, timeout=None):
                parsed = urlsplit(url)
                path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
                return node.request(method, path, headers=headers, json=json)

        remote = {
            "id": "remote-node:music", "title": "Remote Music Studio", "modality": "music",
            "host": "192.0.2.10", "port": 47873, "hub_port": 47873,
            "machine": "remote-node", "local": False, "fleet_node": "remote-node",
            "base_url": "http://192.0.2.10:47873",
        }

        class ControllerMonitor:
            registry = [remote]
            status = {remote["id"]: {"status": "up"}}

        before = len(self.music.requests)
        operation = asyncio.run(FleetMemoryControl(
            ControllerMonitor(), client=NodeClient()).release([remote["id"]]))

        self.assertTrue(operation["ok"], operation)
        self.assertEqual(operation["succeeded"], 1)
        self.assertEqual(operation["results"][0]["result"], "released")
        self.assertEqual(len(self.music.requests), before + 1)
        sent = self.music.requests[-1]
        self.assertEqual(sent["method"], "POST")
        self.assertEqual(sent["path"], "/api/memory/release")
        self.assertEqual(sent["token"], "worker-token")

        self.music.memory_release_busy = True
        try:
            busy = asyncio.run(FleetMemoryControl(
                ControllerMonitor(), client=NodeClient()).release([remote["id"]]))
        finally:
            self.music.memory_release_busy = False
        self.assertFalse(busy["ok"])
        self.assertEqual(busy["results"][0]["result"], "busy")
        self.assertEqual(self.music.requests[-1]["token"], "worker-token")

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

    def test_a_paused_model_is_refused_before_the_worker(self) -> None:
        """The node's own circuit breaker is not the controller's to override.

        A pause is this Mac's memory/integrity decision. The controller's cached
        capability view may still list the model, so the refusal has to happen at
        the proxy, before the worker is asked to generate anything.
        """
        from components.hub.backend import broker

        client = self.client()
        headers = {"X-Hub-Token": TOKEN}
        pause = {("image", "fixture/flux"): {
            "studio": "image", "model": "fixture/flux", "blocked": True,
            "reason": "fixture model pause", "failures": 3,
        }}
        with patch.object(broker, "_model_protection", pause):
            before = len(self.worker.requests)
            response = client.post("/studio/image/api/generate/txt2img",
                                   json={"repo": "fixture/flux", "prompt": "x"},
                                   headers=headers)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json().get("code"), "MODEL_PAUSED")
        self.assertEqual(len(self.worker.requests), before,
                         "a paused model must not reach the worker")

    def test_a_multipart_transcription_names_its_model_for_admission(self) -> None:
        """Subtitle carries the model as a multipart field, not in a JSON body."""
        from components.hub.backend import broker

        client = self.client()
        headers = {"X-Hub-Token": TOKEN}
        pause = {("subtitle", "fixture/whisper"): {
            "studio": "subtitle", "model": "fixture/whisper", "blocked": True,
            "reason": "fixture subtitle pause", "failures": 2,
        }}
        with patch.object(broker, "_model_protection", pause):
            before = len(self.subtitle.requests)
            response = client.post(
                "/studio/transcription/api/transcribe",
                data={"model": "fixture/whisper"},
                files={"file": ("clip.wav", b"RIFF", "application/octet-stream")},
                headers=headers)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json().get("code"), "MODEL_PAUSED")
        self.assertEqual(len(self.subtitle.requests), before,
                         "a paused Subtitle model must not reach the worker")

    def test_a_disabled_studio_is_refused_before_the_worker(self) -> None:
        client = self.client()
        headers = {"X-Hub-Token": TOKEN}
        disabled = client.post("/api/hub/registry/studios/image/enabled",
                               json={"enabled": False}, headers=headers)
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.addCleanup(lambda: client.post("/api/hub/registry/studios/image/enabled",
                                            json={"enabled": True}, headers=headers))
        before = len(self.worker.requests)
        response = client.post("/studio/image/api/generate/txt2img",
                               json={"prompt": "x"}, headers=headers)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json().get("code"), "STUDIO_DISABLED")
        self.assertEqual(len(self.worker.requests), before,
                         "a disabled Studio must not reach the worker")

    def test_a_disabled_machine_is_refused_before_the_worker(self) -> None:
        client = self.client()
        headers = {"X-Hub-Token": TOKEN}
        disabled = client.post("/api/hub/registry/machines/local/enabled",
                               json={"enabled": False}, headers=headers)
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.addCleanup(lambda: client.post("/api/hub/registry/machines/local/enabled",
                                            json={"enabled": True}, headers=headers))
        before = len(self.worker.requests)
        response = client.post("/studio/image/api/generate/txt2img",
                               json={"prompt": "x"}, headers=headers)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json().get("code"), "MACHINE_DISABLED")
        self.assertEqual(len(self.worker.requests), before)

    def test_blocked_output_storage_is_refused_before_the_worker(self) -> None:
        client = self.client()
        headers = {"X-Hub-Token": TOKEN}
        (Path(self.temporary.name) / "out").mkdir(exist_ok=True)
        changed = client.put("/api/hub/settings", json={"minimum_free_bytes": 10**18},
                             headers=headers)
        self.assertEqual(changed.status_code, 200, changed.text)
        self.addCleanup(lambda: client.put("/api/hub/settings",
                                           json={"minimum_free_bytes": 0}, headers=headers))
        before = len(self.worker.requests)
        response = client.post("/studio/image/api/generate/txt2img",
                               json={"prompt": "x"}, headers=headers)
        self.assertEqual(response.status_code, 507, response.text)
        self.assertEqual(response.json().get("code"), "OUTPUT_STORAGE_BLOCKED")
        self.assertEqual(len(self.worker.requests), before,
                         "blocked output storage must not reach the worker")

    def test_a_reserved_machine_is_refused_before_the_worker(self) -> None:
        from components.hub.backend import broker

        client = self.client()
        headers = {"X-Hub-Token": TOKEN}
        self.assertTrue(broker.acquire_external_machine("local", "test:r04-reservation"))
        self.addCleanup(lambda: broker.release_external_machine(
            "local", "test:r04-reservation"))
        before = len(self.worker.requests)
        response = client.post("/studio/image/api/generate/txt2img",
                               json={"prompt": "x"}, headers=headers)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json().get("code"), "MACHINE_BUSY")
        self.assertEqual(len(self.worker.requests), before,
                         "a reserved machine must not reach the worker")

    def test_an_accepted_async_job_holds_the_machine_until_it_is_terminal(self) -> None:
        """One physical heavy-work boundary, even for work the node does not own.

        A peer job is accepted opaquely, so the node cannot queue it. Holding the
        machine lease until the job is observed terminal is what keeps a second
        submission from overlapping on the same Mac - and releasing it on the
        asynchronous POST's return would defeat exactly that.
        """
        from components.hub.backend import broker

        client = self.client()
        headers = {"X-Hub-Token": TOKEN}
        first = client.post("/studio/image/api/generate/txt2img",
                            json={"prompt": "x"}, headers=headers)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertIn("local", broker.busy_machines(),
                      "an accepted asynchronous job must hold the machine")

        second = client.post("/studio/image/api/generate/txt2img",
                             json={"prompt": "y"}, headers=headers)
        self.assertEqual(second.status_code, 409, second.text)
        self.assertEqual(second.json().get("code"), "MACHINE_BUSY")

        self.worker.poll_job = {"id": "job-2", "state": "done"}
        polled = client.get("/studio/image/api/generate/jobs/job-2", headers=headers)
        self.assertEqual(polled.status_code, 200, polled.text)
        self.assertNotIn("local", broker.busy_machines(),
                         "a terminal job must release the machine")

    def test_an_abandoned_peer_lease_expires(self) -> None:
        """A controller that disappears must not reserve this Mac for ever."""
        from components.hub import standalone
        from components.hub.backend import broker

        client = self.client()
        headers = {"X-Hub-Token": TOKEN}
        accepted = client.post("/studio/image/api/generate/txt2img",
                               json={"prompt": "x"}, headers=headers)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertIn("local", broker.busy_machines())

        future = time.monotonic() + standalone._PEER_LEASE_TTL_S + 1
        with patch.object(standalone.time, "monotonic", lambda: future):
            standalone._prune_peer_leases()
        self.assertNotIn("local", broker.busy_machines(),
                         "an expired lease must free the machine")

    def test_a_lease_ends_with_its_job_even_when_nobody_polls_it(self) -> None:
        """Gateway 2026-10-03: nodes refused work with "local is reserved for other work on
        this Mac" while idle. A controller that stopped polling an accepted job (it had rejected
        the peer's own machine id) left the lease to the six-hour TTL. The node asks its own
        worker instead, and frees the Mac as soon as that worker says the job is over."""
        import asyncio
        from components.hub import standalone
        from components.hub.backend import broker
        from components.hub.backend.monitor import StudioMonitor

        client = self.client()
        headers = {"X-Hub-Token": TOKEN}
        accepted = client.post("/studio/image/api/generate/txt2img",
                               json={"prompt": "x"}, headers=headers)
        self.assertEqual(accepted.status_code, 200, accepted.text)

        async def sweep() -> None:
            monitor = StudioMonitor()
            try:
                await standalone._release_finished_peer_leases(monitor)
            finally:
                await monitor._client.aclose()

        original = self.worker.poll_job
        self.addCleanup(setattr, self.worker, "poll_job", original)
        self.worker.poll_job = {"id": "job-2", "state": "running"}
        asyncio.run(sweep())
        self.assertIn("local", broker.busy_machines(), "a running job keeps its machine")
        self.assertEqual(self.worker.requests[-1]["path"], "/api/generate/jobs/job-2")

        self.worker.poll_job = {"id": "job-2", "state": "done"}
        asyncio.run(sweep())
        self.assertNotIn("local", broker.busy_machines(),
                         "a finished job must free the machine without a controller poll")


if __name__ == "__main__":
    unittest.main()
