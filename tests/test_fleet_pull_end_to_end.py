"""A Shops Mac the controller cannot dial, end to end, through a real controller Hub.

This is the fault the whole channel exists for, reproduced as a test: a Mac reaches its controller
through Tailscale **node sharing**, which can work one way. The Mac joins, and the controller can
then never reach it — every dial times out — so it read `never_connected` for ever. The three Shops
Macs that prompted this were in that position and had read `never_connected` 51 hours after joining;
whether they were running and calling home was never proven, because the controller recorded no
call from them.

A real controller Hub process, over a real socket, and a node that speaks the wire contract the
Swift app speaks (`native/StudioAPI.swift`): it declares an address that **accepts a connection and
never answers**, checks in with its credential, replays the requests it is handed against its own
Hub, and posts the answers back. The controller never once reaches it by dialling — the black hole
counts every connection it swallows.

What is asserted, in the owner's words: it **shows as online**, it **receives a Lite Mode
setting**, it **receives an update offer**, and it **completes a job**.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
import urllib.error
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from test_standalone_connections import HubProcess, free_port  # noqa: E402

NODE = "kt-ksw-imac-blue-16-512-001-hub"
STUDIOS = [{"id": "image", "modality": "image"}]
TRANSCRIPT = {"ok": True, "job_id": "node-job-1", "state": "done",
              "text": "the transcript the Mac produced", "words": []}


class BlackHole:
    """A listener that accepts and never answers — a dial that genuinely times out.

    Not a closed port: a refused connection says "nothing is listening", which is a different
    fault with a different fix. This is the Shops case, where the packets go somewhere and nothing
    ever comes back.
    """

    def __init__(self) -> None:
        self.socket = socket.socket()
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(16)
        self.port = self.socket.getsockname()[1]
        self.accepted = 0
        self._held: list[socket.socket] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "BlackHole":
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _ = self.socket.accept()
            except OSError:
                return
            self.accepted += 1
            self._held.append(connection)   # accepted, and deliberately never answered

    @property
    def address(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self._stop.set()
        for connection in self._held:
            connection.close()
        self.socket.close()


class _NodeHandler(BaseHTTPRequestHandler):
    """The node's own Hub, as far as the commands this controller sends are concerned."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # noqa: D102 - silence the fixture
        pass

    def _send(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            value = json.loads(raw or b"{}")
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    def do_GET(self) -> None:  # noqa: N802 - http.server's spelling
        node = self.server.node
        if self.path.startswith("/api/hub/resources"):
            # The route the controller's probe dials, and the one the peer poll reads. A real
            # node's Hub serves it; this Mac's declared address does not answer at all, so the
            # only way the controller can read it is through the Mac's own call home.
            self._send(200, node.resources())
            return
        if self.path.startswith("/api/hub/fleet/nodes/local/trash"):
            self._send(200, {"ok": True, "entries": [], "bytes": 0})
            return
        self._send(404, {"ok": False, "detail": "no such route on the node"})

    def do_POST(self) -> None:  # noqa: N802 - http.server's spelling
        node = self.server.node
        if self.path.startswith("/api/hub/workstation"):
            body = self._body()
            node.workstation.update(body)
            self._send(200, {"ok": True, "workstation": node.workstation})
            return
        if self.path.startswith("/api/hub/fleet/nodes/local/transcribe"):
            # **The job.** The node runs it on its own worker and answers with the finished
            # result, exactly as it would have answered the controller's dial.
            node.jobs.append({"path": self.path, "bytes": int(self.headers.get("Content-Length") or 0)})
            self._send(200, TRANSCRIPT)
            return
        self._send(404, {"ok": False, "detail": "no such route on the node"})


class NodeDouble:
    """One agent Mac: a Hub surface, a black-hole address, and the app's own check-in loop."""

    def __init__(self, controller_base: str, fleet_token: str, address: str) -> None:
        self.controller = controller_base
        self.fleet_token = fleet_token
        self.address = address
        self.credential: str | None = None
        self.hub_token = "node-own-hub-token"
        self.workstation: dict = {"enabled": False}
        self.jobs: list[dict] = []
        self.offered_release: dict | None = None
        self.checkins = 0
        self.port = free_port()
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _NodeHandler)
        self._server.node = self
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._results: list[dict] = []
        self._seen: list[str] = []
        self._stop = threading.Event()
        self._loop_thread = threading.Thread(target=self._run, daemon=True)

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(self) -> "NodeDouble":
        self._thread.start()
        self.join()
        self._loop_thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._loop_thread.join(timeout=5)
        self._server.shutdown()
        self._server.server_close()

    # ── the wire ───────────────────────────────────────────────────────────
    def _call(self, path: str, payload: dict | None, *, token: str | None = None,
              base: str | None = None, method: str = "POST") -> tuple[int, dict]:
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(f"{base or self.controller}{path}", data=body, method=method)
        request.add_header("Accept", "application/json")
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("X-Hub-Token", token)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read() or b"null") or {}
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw or b"null") or {}
            except ValueError:
                return error.code, {}

    def resources(self) -> dict:
        """What this Mac's Hub answers on the route a controller probes."""
        return {"host": {"chip": "Apple M1", "total_gb": 16.0, "available_gb": 9.0},
                "studios": {"image": {"id": "image", "status": "up"}},
                "version": "0.6.193", "source": "hub",
                "workstation": dict(self.workstation, state="idle"),
                "machine": {"id": NODE, "name": "Shops iMac"}}

    def join(self) -> None:
        status, answer = self._call("/api/hub/fleet/nodes",
                                    {"node_id": NODE, "name": "Shops iMac", "address": self.address,
                                     "studios": STUDIOS}, token=self.fleet_token)
        assert status == 201, (status, answer)
        self.credential = answer["credential"]

    def checkin(self, **extra) -> dict:
        """One authenticated call home, carrying what this Mac is and taking back its work."""
        payload = {
            "node_id": NODE,
            "address": self.address,
            "name": "Shops iMac",
            "version": "0.6.193",
            "studios": STUDIOS,
            "workstation": dict(self.workstation, state="idle"),
            "resources": self.resources(),
            "seen": list(self._seen),
            "results": list(self._results),
        }
        payload.update(extra)
        self._seen = []
        self._results = []
        status, answer = self._call(f"/api/hub/fleet/nodes/{NODE}/checkin", payload,
                                    token=self.credential)
        assert status == 200, (status, answer)
        self.checkins += 1
        return answer

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                answer = self.checkin()
            except Exception:  # noqa: BLE001 - the loop keeps trying, as the app does
                time.sleep(0.1)
                continue
            for command in answer.get("commands") or []:
                self._results.append(self.execute(command))
            time.sleep(0.05)

    def execute(self, command: dict) -> dict:
        """Replay one command on this Mac's own Hub, with this Mac's own credential."""
        self._seen.append(str(command["id"]))
        body = command.get("content_b64")
        data = base64.b64decode(body) if body else b""
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{command['path']}",
                                         data=data or None, method=str(command["method"]))
        for key, value in (command.get("headers") or {}).items():
            request.add_header(key, value)
        request.add_header("X-Hub-Token", self.hub_token)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return {"id": command["id"], "status": response.status,
                        "json": json.loads(response.read() or b"null")}
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                payload = json.loads(raw or b"null")
            except ValueError:
                payload = {"raw": raw.decode(errors="replace")[:200]}
            return {"id": command["id"], "status": error.code, "json": payload}

    def read_release_offer(self) -> tuple[int, dict]:
        """What this Mac's app reads to decide whether an update is offered."""
        return self._call("/api/hub/release", None, token=self.credential, method="GET")


class TheControllerCannotDialItTests(unittest.TestCase):
    """One real controller Hub, one node whose address swallows every connection."""

    def setUp(self) -> None:
        import tempfile
        self.scratch = tempfile.TemporaryDirectory(prefix="studiohub-pull-e2e-")
        self.root = Path(self.scratch.name)
        self.addCleanup(self.scratch.cleanup)
        self.controller = HubProcess(self.root, "controller", "controller-hub-token",
                                     workers=[{"id": "voice", "modality": "voice",
                                               "port": free_port()}])
        self.controller.start()
        self.addCleanup(self.controller.stop)
        self.controller.request("/api/hub/controller", token="controller-hub-token", method="PUT",
                                body=json.dumps({"site_id": "hq-macmini",
                                                 "site_name": "Shops Controller",
                                                 "controller_id": "hq-macmini",
                                                 "role": "controller"}).encode(),
                                headers={"Content-Type": "application/json"})
        self.fleet_token = (self.controller.data / ".fleet_token").read_text().strip()
        self.hole = BlackHole().start()
        self.addCleanup(self.hole.stop)
        self.node = NodeDouble(self.controller.base, self.fleet_token, self.hole.address).start()
        self.addCleanup(self.node.stop)

    # ── helpers ────────────────────────────────────────────────────────────
    def roster(self) -> dict:
        status, body, _ = self.controller.request("/api/hub/fleet/nodes",
                                                  token="controller-hub-token")
        self.assertEqual(200, status, body)
        return body

    def row(self) -> dict:
        for entry in self.roster()["nodes"]:
            if entry["id"] == NODE:
                return entry
        raise AssertionError("the Mac is not on the controller's roster")

    def wait_for(self, predicate, *, seconds: float = 30.0, what: str = ""):
        deadline = time.time() + seconds
        while time.time() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        raise AssertionError(f"timed out waiting for {what or predicate}")

    def _assert_the_declared_address_swallows_a_request(self) -> None:
        """The Mac's address is the black hole: a request to it never comes back."""
        with socket.create_connection(("127.0.0.1", self.hole.port), timeout=5) as probe:
            probe.sendall(b"GET /api/hub/resources?local_only=true HTTP/1.1\r\nHost: node\r\n\r\n")
            probe.settimeout(2.0)
            with self.assertRaises((TimeoutError, socket.timeout)):
                probe.recv(64)
        self.assertGreater(self.hole.accepted, 0)

    # ── the four things the owner asked for ────────────────────────────────
    def test_it_shows_as_online_even_though_the_dial_never_answers(self) -> None:
        self.node.checkin()
        row = self.row()
        self.assertEqual("reachable", row["state"])
        # The reason is kept distinct from a probe, because the two fail in different ways.
        self.assertEqual("check-in", row["via"])
        self.assertIsNone(row["detail"])
        self.assertFalse(row["never_connected"])
        self.assertGreater(row["last_seen_at"], row["joined_at"])
        self.assertEqual(0, self.roster()["summary"]["unreachable"])
        self.assertEqual(1, self.roster()["summary"]["check_in"])
        self._assert_the_declared_address_swallows_a_request()

    def test_it_receives_a_lite_mode_setting(self) -> None:
        self.node.checkin()
        status, body, _ = self.controller.request(f"/api/hub/fleet/nodes/{NODE}/workstation",
                                                  token="controller-hub-token", method="POST",
                                                  body=json.dumps({"enabled": True,
                                                                   "idle_minutes": 10}).encode(),
                                                  headers={"Content-Type": "application/json"})
        # 202, not 200: nothing has happened on the Mac yet, and it is held for it.
        self.assertEqual(202, status, body)
        self.assertTrue(body["queued"])
        applied = self.wait_for(lambda: self.node.workstation.get("enabled") is True,
                                what="the Mac to apply the Lite Mode change")
        self.assertTrue(applied)
        # And the controller's own roster shows it, from the Mac's next call home.
        self.wait_for(lambda: (self.row().get("checkin_workstation") or {}).get("enabled") is True,
                      what="the roster to show Lite Mode on")

    def test_it_receives_an_update_offer(self) -> None:
        self._publish_a_release()
        self.node.checkin()
        status, offer = self.node.read_release_offer()
        self.assertEqual(200, status, offer)
        # The route answers the held release under `release` (the descriptor, plus this
        # controller's own countersignature) and the publisher key the Mac pins.
        descriptor = offer.get("release") or {}
        self.assertEqual("0.6.193", descriptor.get("version"))
        # The offer is signed by the publisher the controller pinned, and the Mac verifies it
        # against the key its controller publishes (`/api/hub/release/identity`).
        self.assertTrue(descriptor.get("signature"))
        self.assertTrue(descriptor.get("controller_signature"))
        self.assertEqual(descriptor.get("publisher_key_id"),
                         (offer.get("trusted_publisher") or {}).get("fingerprint"))

    def test_it_completes_a_job(self) -> None:
        self.node.checkin()
        status, body, _ = self.controller.post_multipart(
            f"/api/hub/fleet/nodes/{NODE}/transcribe",
            {"model": "whisper-small", "language": "km"}, {"file": ("a.wav", b"RIFF", "audio/wav")},
            token="controller-hub-token")
        self.assertEqual(200, status, body)
        self.assertEqual(TRANSCRIPT["text"], body.get("text"))
        self.assertEqual(TRANSCRIPT["job_id"], body.get("job_id"))
        # It really ran on the Mac: the node's own Hub saw the work, and it was never dialled.
        self.assertEqual(1, len(self.node.jobs))
        self.assertGreater(self.node.jobs[0]["bytes"], 0)

    def test_a_work_request_is_answered_rather_than_queued_when_the_mac_takes_it(self) -> None:
        # The same channel carries a read the caller waits for.
        self.node.checkin()
        status, body, _ = self.controller.request(f"/api/hub/fleet/nodes/{NODE}/trash",
                                                  token="controller-hub-token")
        self.assertEqual(200, status, body)
        self.assertEqual([], body.get("entries"))

    # ── the release the controller holds, published the way the Gateway publishes one ──
    def _publish_a_release(self) -> None:
        from components.hub.backend import app_release, ed25519
        seed = secrets.token_bytes(32)
        public = ed25519.public_key(seed)
        artifact = b"the disk image the gateway publishes"
        descriptor = {
            "schema": "studiohub.app-release.v1", "schema_version": 1, "version": "0.6.193",
            "build": "pullfleet", "filename": "StudioHub-0.6.193-pullfleet.dmg",
            "bytes": len(artifact), "sha256": hashlib.sha256(artifact).hexdigest(),
            "published_at": 1790000000, "publisher_id": "gatewaykh",
            "publisher_key_id": app_release.fingerprint(public),
            "files_sha256": hashlib.sha256(b"manifest").hexdigest(),
            "files_count": 1, "files_bytes": 1,
        }
        descriptor["signature"] = app_release.sign(descriptor, seed)
        status, body, _ = self.controller.post_json(
            "/api/hub/release/trust", {"public_key": public.hex(), "publisher_id": "gatewaykh"},
            token="controller-hub-token")
        self.assertEqual(200, status, body)
        status, body, _ = self.controller.post_json(
            "/api/hub/release",
            {"descriptor": descriptor,
             "artifact_base64": base64.b64encode(artifact).decode()},
            token=self.fleet_token)
        self.assertEqual(200, status, body)


if __name__ == "__main__":
    unittest.main()
