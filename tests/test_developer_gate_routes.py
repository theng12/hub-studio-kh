"""Focused HTTP tests for the seven frozen developer-gate operations.

A small FastAPI app carries the real routes and a test middleware that supplies the actor the
production auth middleware would set. No Hub starts, no network is used, and every attempt
lives in a temporary store.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.hub import developer_gate_contract as contract  # noqa: E402
from components.hub import developer_gate_service as service_mod  # noqa: E402
from components.hub import developer_gates  # noqa: E402
from components.hub import developer_tests  # noqa: E402
from tests.test_developer_gate_service import (  # noqa: E402
    CONTROLLER, NODE, FakeAdmission, FakeMetrics, _fixture, _inventory, _reset_observed,
    fake_runner)

BASE = "/api/hub/fleet/nodes/local/developer-gates"
TOKEN = "fleet-current-token"


class DeveloperGateRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_observed()
        self.temp = tempfile.TemporaryDirectory(prefix="studiohub-gate-routes-")
        self.addCleanup(self.temp.cleanup)
        self.old_identities = developer_tests._identities
        developer_tests._identities = lambda: (NODE, CONTROLLER)
        self.addCleanup(setattr, developer_tests, "_identities", self.old_identities)
        self.old_token = None
        from components.hub.backend import peers
        self.old_token = peers.current_fleet_token
        peers.current_fleet_token = lambda: TOKEN
        self.addCleanup(setattr, peers, "current_fleet_token", self.old_token)
        self.disk = mock.patch.object(
            service_mod.shutil, "disk_usage",
            return_value=types.SimpleNamespace(free=400_000_000_000,
                                               total=500_277_792_768,
                                               used=100_277_792_768))
        self.disk.start()
        self.addCleanup(self.disk.stop)
        authority, self.request, self.raw = _fixture()
        self.service = service_mod.DeveloperGateService(
            root_dir=self.temp.name, authority=authority, runner=fake_runner,
            metrics=FakeMetrics(), admission=FakeAdmission(),
            inventory_provider=lambda node, controller: _inventory(node, controller))

        from fastapi import FastAPI, Request
        from fastapi.testclient import TestClient
        app = FastAPI()

        @app.middleware("http")
        async def actor(request: Request, call_next):
            request.state.fleet_actor = {
                "kind": request.headers.get("X-Test-Actor", "fleet"), "node_id": None}
            return await call_next(request)

        developer_gates.register_routes(app, service=self.service)
        self.app = app
        self.client = TestClient(app)

    def _headers(self, *, token: str | None = TOKEN, actor: str = "fleet",
                 key: str | None = None) -> dict[str, str]:
        headers = {"X-Test-Actor": actor, "Content-Type": "application/json"}
        if token is not None:
            headers["X-Hub-Token"] = token
        if key is not None:
            headers["Idempotency-Key"] = key
        return headers

    def _submit(self, key: str = "key-1", body: dict | None = None,
                *, token: str | None = TOKEN, actor: str = "fleet"):
        return self.client.post(
            BASE, content=contract.canonical_json(body or self.request).encode(),
            headers=self._headers(token=token, actor=actor, key=key))

    def _wait(self, key: str = "key-1", timeout: float = 5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.client.get(
                f"{BASE}/by-key/{key}", headers=self._headers())
            if response.status_code == 200 and response.json()["state"] in (
                    "passed", "failed", "not_run", "cancelled", "interrupted"):
                return response.json()
            time.sleep(0.01)
        self.fail("attempt did not reach a terminal state")

    def test_exactly_the_seven_frozen_operations_are_registered(self) -> None:
        found = set()
        for route in self.app.routes:
            path = str(getattr(route, "path", ""))
            if "/developer-gates" not in path:
                continue
            for method in getattr(route, "methods", set()) or set():
                if method in {"GET", "POST", "PUT"}:
                    found.add((method, path))
        expected = {
            ("GET", "/api/hub/fleet/nodes/{node_id}/developer-gates/capabilities"),
            ("POST", "/api/hub/fleet/nodes/{node_id}/developer-gates"),
            ("GET", "/api/hub/fleet/nodes/{node_id}/developer-gates/by-key/{key}"),
            ("PUT", "/api/hub/fleet/nodes/{node_id}/developer-gates/by-key/{key}/source"),
            ("POST", "/api/hub/fleet/nodes/{node_id}/developer-gates/by-key/{key}/cancel"),
            ("GET", "/api/hub/fleet/nodes/{node_id}/developer-gates/by-key/{key}/result"),
            ("POST", "/api/hub/fleet/nodes/{node_id}/developer-gates/by-key/{key}/ack"),
        }
        self.assertEqual(found, expected)

    def test_only_the_current_fleet_header_authenticates(self) -> None:
        for headers in (
                self._headers(token=None),
                self._headers(token="wrong"),
                self._headers(actor="hub"),
                self._headers(actor="node")):
            response = self.client.get(f"{BASE}/capabilities", headers=headers)
            self.assertEqual(response.status_code, 403, headers)
            self.assertEqual(response.json()["format"], contract.ERROR_FORMAT)

    def test_no_query_is_accepted(self) -> None:
        response = self.client.get(f"{BASE}/capabilities?extra=1",
                                   headers=self._headers())
        self.assertEqual(response.status_code, 400)

    def test_full_operation_sequence_and_original_key_binding(self) -> None:
        accepted = self._submit()
        self.assertEqual(accepted.status_code, 200)
        status = accepted.json()
        self.assertEqual(status["state"], "waiting_source")
        key = "key-1"

        again = self._submit()
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["acceptance"]["attempt_id"],
                         status["acceptance"]["attempt_id"])

        changed = dict(self.request)
        changed["run_id"] = "run-other"
        conflict = self._submit(body=changed)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["reason"]["code"], "conflict")

        source = self.client.put(
            f"{BASE}/by-key/{key}/source", content=self.raw,
            headers=self._headers())
        self.assertEqual(source.status_code, 200)
        terminal = self._wait(key)
        self.assertEqual(terminal["state"], "passed")
        descriptor = terminal["result"]

        result = self.client.get(f"{BASE}/by-key/{key}/result",
                                 headers=self._headers())
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["content-type"], "application/x-tar")
        self.assertEqual(int(result.headers["content-length"]),
                         descriptor["size_bytes"])
        self.assertEqual(__import__("hashlib").sha256(result.content).hexdigest(),
                         descriptor["sha256"])

        ack = {
            "format": contract.ACK_FORMAT, "request_id": "ack-1",
            "acceptance": terminal["acceptance"], "result": descriptor,
        }
        acked = self.client.post(
            f"{BASE}/by-key/{key}/ack",
            content=contract.canonical_json(ack).encode(), headers=self._headers())
        self.assertEqual(acked.status_code, 200)
        self.assertTrue(acked.json()["result_acknowledged"])
        self.assertTrue(acked.json()["workspace_removed"])

        gone = self.client.get(f"{BASE}/by-key/{key}/result",
                               headers=self._headers())
        self.assertEqual(gone.status_code, 404)

    def test_source_with_a_different_body_is_refused(self) -> None:
        self.assertEqual(self._submit().status_code, 200)
        bad = self.raw[:-1] + (b"x" if self.raw[-1:] != b"x" else b"y")
        response = self.client.put(
            f"{BASE}/by-key/key-1/source", content=bad, headers=self._headers())
        self.assertEqual(response.status_code, 400)
        status = self.client.get(f"{BASE}/by-key/key-1",
                                 headers=self._headers()).json()
        self.assertEqual(status["state"], "waiting_source")

    def test_a_node_or_browser_actor_never_reaches_the_service(self) -> None:
        for actor in ("hub", "node"):
            response = self.client.post(
                BASE, content=contract.canonical_json(self.request).encode(),
                headers=self._headers(actor=actor, key="key-1"))
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.service._store.list_attempts(), [])

    def test_an_unsafe_key_is_refused_before_any_relay(self) -> None:
        response = self.client.get(f"{BASE}/by-key/bad%20key",
                                   headers=self._headers())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["reason"]["code"], "invalid_request")

    def test_pinned_source_length_rejects_before_the_body_is_read(self) -> None:
        for headers in (
                {"content-length": "9"},
                {"content-length": "abc"},
                {"transfer-encoding": "chunked"},
                {"content-encoding": "gzip", "content-length": "8"}):
            with self.assertRaises(developer_gates.GateRefused) as caught:
                developer_gates._pinned_length(_FakeRequest(headers), 8)
            self.assertEqual(caught.exception.status, 400, headers)

    def test_source_floor_is_rechecked_before_each_chunk_is_written(self) -> None:
        import asyncio
        request = _FakeRequest(chunks=[b"a", b"b"])
        with mock.patch.object(
                developer_gates.shutil, "disk_usage",
                side_effect=[types.SimpleNamespace(free=100),
                             types.SimpleNamespace(free=0)]):
            with self.assertRaises(developer_gates.GateRefused) as caught:
                asyncio.run(developer_gates._stream_to_temp(
                    request, 2, Path(self.temp.name), reservation=10, minimum=10))
        self.assertEqual(caught.exception.status, 507)
        self.assertIn("10 byte gate floor", caught.exception.detail)
        self.assertTrue(request.read)


class _FakeRequest:
    def __init__(self, headers: dict[str, str] | None = None,
                 chunks: list[bytes] | None = None) -> None:
        self.headers = headers or {}
        self.chunks = chunks if chunks is not None else [b"unexpected"]
        self.url = types.SimpleNamespace(query="")
        self.read = False

    async def stream(self):
        self.read = True
        for chunk in self.chunks:
            yield chunk


class _FakeResponse:
    def __init__(self, headers: dict[str, str], chunks: list[bytes]) -> None:
        self.headers = headers
        self.chunks = chunks
        self.status_code = 200
        self.pulled = 0
        self.closed = False

    async def aiter_raw(self):
        for chunk in self.chunks:
            self.pulled += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class _FakeStream:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response

    async def __aenter__(self) -> _FakeResponse:
        return self.response

    async def __aexit__(self, *_exc) -> None:
        self.response.closed = True


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    def stream(self, method: str, url: str, **_kwargs) -> _FakeStream:
        self.calls.append((method, url))
        return _FakeStream(self.response)

    async def aclose(self) -> None:
        self.closed = True


class DeveloperGateRelayBoundTests(unittest.TestCase):
    node = {"address": "http://peer.invalid"}

    def _run(self, coroutine):
        import asyncio
        return asyncio.run(coroutine)

    def test_json_relay_rejects_a_declared_oversize_before_reading(self) -> None:
        response = _FakeResponse(
            {"Content-Length": str(developer_gates.RESPONSE_CEILING + 1)},
            [b"{}"])
        client = _FakeClient(response)
        with mock.patch("components.hub.backend.fleet_nodes._probe_client",
                        lambda: client):
            with self.assertRaises(developer_gates.GateRefused) as caught:
                self._run(developer_gates._relay_json(
                    self.node, "GET", "/capabilities", _FakeRequest()))
        self.assertEqual(caught.exception.status, 502)
        self.assertEqual(response.pulled, 0)
        self.assertTrue(response.closed)
        self.assertTrue(client.closed)

    def test_json_relay_stops_a_lying_stream_at_its_declared_size(self) -> None:
        chunk = b"x" * (developer_gates.RESPONSE_CEILING // 2 + 1)
        response = _FakeResponse({"Content-Length": "1"}, [chunk] * 10)
        client = _FakeClient(response)
        with mock.patch("components.hub.backend.fleet_nodes._probe_client",
                        lambda: client):
            with self.assertRaises(developer_gates.GateRefused):
                self._run(developer_gates._relay_json(
                    self.node, "GET", "/capabilities", _FakeRequest()))
        self.assertEqual(response.pulled, 1)
        self.assertTrue(response.closed)
        self.assertTrue(client.closed)

    def test_source_relay_rejects_a_declared_oversize_before_reading(self) -> None:
        response = _FakeResponse(
            {"Content-Length": str(developer_gates.RESPONSE_CEILING + 1)},
            [b"{}"])
        client = _FakeClient(response)
        request = _FakeRequest({"Content-Length": "8"})
        with mock.patch("components.hub.backend.fleet_nodes._probe_client",
                        lambda: client):
            with self.assertRaises(developer_gates.GateRefused) as caught:
                self._run(developer_gates._relay_source(
                    self.node, "/by-key/key-1/source", request))
        self.assertEqual(caught.exception.status, 502)
        self.assertEqual(response.pulled, 0)
        self.assertTrue(response.closed)
        self.assertTrue(client.closed)

    def test_result_relay_rejects_an_oversize_declaration_before_yielding(self) -> None:
        response = _FakeResponse(
            {"Content-Length": str(developer_gates.RESULT_CEILING + 1),
             "Content-Type": "application/x-tar"}, [b"tar"])
        client = _FakeClient(response)
        with mock.patch("components.hub.backend.fleet_nodes._probe_client",
                        lambda: client):
            with self.assertRaises(developer_gates.GateRefused) as caught:
                self._run(developer_gates._relay_result(
                    self.node, "/by-key/key-1/result", _FakeRequest()))
        self.assertEqual(caught.exception.status, 502)
        self.assertEqual(response.pulled, 0)
        self.assertTrue(response.closed)
        self.assertTrue(client.closed)

    def test_result_relay_stops_an_overflowing_stream_and_closes_both(self) -> None:
        response = _FakeResponse(
            {"Content-Length": "4", "Content-Type": "application/x-tar"},
            [b"tar0", b"tar1"])
        client = _FakeClient(response)

        async def consume() -> None:
            stream = await developer_gates._relay_result(
                self.node, "/by-key/key-1/result", _FakeRequest())
            async for _chunk in stream.body_iterator:
                pass

        with mock.patch("components.hub.backend.fleet_nodes._probe_client",
                        lambda: client):
            with self.assertRaises(developer_gates.GateRefused):
                self._run(consume())
        self.assertEqual(response.pulled, 2)
        self.assertTrue(response.closed)
        self.assertTrue(client.closed)


if __name__ == "__main__":
    unittest.main()
