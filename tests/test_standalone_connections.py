"""ST-10.PORTABLE / ST-10.3 — the Hub as an independent client sees it.

GenStudio and a location controller reach a Hub with an address, a port and an existing
authentication header. Nothing else: no browser login, no cloud domain, no shared database
and no shared filesystem. This drives a real Hub process over a real socket with a plain
HTTP client that has no cookie jar, and proves the identity a controller stores survives an
address change and a credential rotation.

The Hub resolves its token, ledger and registry paths at import time, so the whole exercise
runs in a child process with the packaged environment already set — the same reason
``test_hub_voice_references`` does.
"""
from __future__ import annotations

from hashlib import sha256
import io
import json
import secrets
import shutil
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
import urllib.error
import urllib.parse
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_SERVER = r'''
import os
import sys
from pathlib import Path

root = Path(os.environ["STUDIOHUB_PROJECT_ROOT"])
# `-I` ignores PYTHONPATH, so the two roots this server needs are added explicitly.
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "packaging"))

from components.hub.standalone import app
from voice_worker import run_server

run_server(app, 0, Path(os.environ["STUDIOHUB_READY_FILE"]),
           "0.4.0", None, None, token=os.environ["STUDIOHUB_FIXTURE_TOKEN"],
           host=os.environ.get("STUDIOHUB_FIXTURE_HOST", "127.0.0.1"))
'''


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class HubProcess:
    """One real Hub process bound to a loopback port, started from a chosen state root."""

    def __init__(self, root: Path, name: str, token: str, data: Path | None = None,
                 host: str = "127.0.0.1", joined: list[dict] | None = None,
                 workers: list[dict] | None = None, env: dict | None = None):
        self.root = root
        self.token = token
        self.host = host
        self.joined = joined or []
        self.override_workers = workers
        self.extra_env = env or {}
        self.data = data or (root / name / "state")
        self.data.mkdir(parents=True, exist_ok=True)
        token_file = self.data / ".hub_token"
        token_file.write_text(token + "\n")
        os.chmod(token_file, 0o600)
        if self.joined:
            # A node that joined earlier, so the controller builds it into its registry at
            # startup rather than only knowing about it in memory.
            fleet = self.data / "fleet_nodes.json"
            fleet.write_text(json.dumps({
                row["node_id"]: {"id": row["node_id"], "name": row["name"],
                                 "address": row["address"], "role": "agent",
                                 "studios": row["studios"], "joined_at": 1.0,
                                 "updated_at": 1.0}
                for row in self.joined}))
            os.chmod(fleet, 0o600)
        self.workers = root / name / "workers.json"
        self.workers.parent.mkdir(parents=True, exist_ok=True)
        descriptors = [{
            "id": row["id"], "title": f"{row['id'].title()} Studio",
            "modality": row["modality"], "host": "127.0.0.1", "port": row["port"],
            "machine": "local", "local": True, "hub_token": token,
            "runtime_identity": {"id": "fixture-runtime", "compatible": True},
            "managed_output_roots": [str(self.root / name / f"{row['id']}-outputs")],
        } for row in (self.override_workers
                      or [{"id": "voice", "modality": "voice", "port": free_port()}])]
        self.workers.write_text(json.dumps(descriptors))
        os.chmod(self.workers, 0o600)
        self.ready = self.data / "ready.json"
        # The packaged app reports the version from its release manifest, so the fixture
        # provides one rather than letting the Hub answer "unknown".
        self.release = self.root / name / "release-manifest.json"
        self.release.write_text(json.dumps({"version": "0.4.0", "components": {}}))
        self.script = root / name / "serve.py"
        self.script.parent.mkdir(parents=True, exist_ok=True)
        self.script.write_text(_SERVER)
        self.process: subprocess.Popen | None = None
        self.base = ""

    def start(self, timeout: float = 120.0) -> str:
        environment = {**os.environ,
                       "STUDIOHUB_PROJECT_ROOT": str(PROJECT_ROOT),
                       "STUDIOHUB_READY_FILE": str(self.ready),
                       "STUDIOHUB_FIXTURE_TOKEN": self.token,
                       "STUDIOHUB_FIXTURE_HOST": self.host,
                       "STUDIOHUB_DATA_DIR": str(self.data),
                       "STUDIOHUB_WORKERS_FILE": str(self.workers),
                       "STUDIOHUB_PACKAGED": "1",
                       "STUDIOHUB_RELEASE_MANIFEST": str(self.release),
                       # Model an off-box caller: loopback auto-authentication is off, so
                       # the address, port and authentication header must be enough.
                       "STUDIOHUB_REQUIRE_AUTH": "1",
                       "PYTHONPATH": str(PROJECT_ROOT),
                       **self.extra_env}
        self.log = open(self.root / f"{self.script.parent.name}.log", "wb")
        self.process = subprocess.Popen(
            [sys.executable, "-I", "-B", str(self.script)], cwd=str(self.data),
            env=environment, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise AssertionError(
                    f"the Hub exited during startup:\n"
                    f"{(self.root / f'{self.script.parent.name}.log').read_text()[-3000:]}")
            try:
                self.base = json.loads(self.ready.read_text())["url"].rstrip("/")
                return self.base
            except (OSError, ValueError, KeyError):
                time.sleep(0.3)
        raise AssertionError("the Hub never became ready")

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.log.close()

    def post_multipart(self, path: str, fields: dict, files: dict, *, token: str):
        boundary = "----studiohubfixture"
        body = io.BytesIO()
        for key, value in fields.items():
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
            body.write(str(value).encode())
            body.write(b"\r\n")
        for key, (name, content, media) in files.items():
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="{key}"; '
                       f'filename="{name}"\r\n'.encode())
            body.write(f"Content-Type: {media}\r\n\r\n".encode())
            body.write(content)
            body.write(b"\r\n")
        body.write(f"--{boundary}--\r\n".encode())
        request = urllib.request.Request(f"{self.base}{path}", data=body.getvalue(),
                                         method="POST")
        request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        request.add_header("Accept", "application/json")
        request.add_header("X-Hub-Token", token)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, json.loads(response.read() or b"null"), response
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw or b"null"), error
            except ValueError:
                return error.code, {"raw": raw.decode(errors="replace")[:300]}, error

    def artifact(self, path: str, *, token: str) -> tuple[int, bytes]:
        """Fetch raw bytes. An artifact is not JSON, and parsing it as such fails on its magic
        number rather than on anything real."""
        request = urllib.request.Request(f"{self.base}{path}")
        request.add_header("X-Hub-Token", token)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def post_json(self, path: str, payload: dict, *, token: str | None = None):
        body = json.dumps(payload).encode()
        request = urllib.request.Request(f"{self.base}{path}", data=body, method="POST")
        request.add_header("Accept", "application/json")
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("X-Hub-Token", token)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read() or b"null"), response
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw or b"null"), error
            except ValueError:
                return error.code, {"raw": raw.decode(errors="replace")[:300]}, error

    def request(self, path: str, *, token: str | None = None, method: str = "GET",
                headers: dict | None = None, use_bearer: bool = False, body: bytes | None = None):
        request = urllib.request.Request(f"{self.base}{path}", data=body, method=method)
        request.add_header("Accept", "application/json")
        if token:
            if use_bearer:
                request.add_header("Authorization", f"Bearer {token}")
            else:
                request.add_header("X-Hub-Token", token)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            # No cookie jar and no redirect handler: an independent client exactly as a
            # controller would be.
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read() or b"null"), response
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw or b"null"), error
            except ValueError:
                return error.code, {"raw": raw.decode(errors="replace")[:300]}, error


class IndependentClientTests(unittest.TestCase):
    """One Hub per class; every call comes from a plain HTTP client over loopback.

    The Hub authenticates loopback callers automatically, so this fixture sets
    ``STUDIOHUB_REQUIRE_AUTH`` to model the case that actually matters for a controller: the
    request arrives from off-box, and the address, the port and the authentication header
    are the only things the caller has.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="studiohub-connections-")
        cls.root = Path(cls.temporary.name)
        cls.token = "connection-fixture-token-0123456789"
        cls.hub = HubProcess(cls.root, "first", cls.token)
        cls.hub.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.hub.stop()
        cls.temporary.cleanup()

    def test_no_browser_session_is_required(self) -> None:
        """The credential header alone is enough; nothing about this needs a login page."""
        status, payload, _ = self.hub.request("/api/hub/controller", token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(payload["settings"]["role"], "standalone")
        status, _, _ = self.hub.request("/api/hub/controller", token=self.token,
                                        use_bearer=True)
        self.assertEqual(status, 200, "the bearer form must keep working")
        # And a stale or forged session cookie cannot stand in for the credential.
        status, _, _ = self.hub.request(
            "/api/hub/controller",
            headers={"Cookie": "kh_hub_token=not-a-real-session"})
        self.assertEqual(status, 401)

    def test_without_a_credential_the_hub_refuses_private_state(self) -> None:
        status, _, _ = self.hub.request("/api/hub/controller")
        self.assertEqual(status, 401)
        status, _, _ = self.hub.request("/api/hub/memory/policy")
        self.assertEqual(status, 401)
        # Version and health are public by design and carry no private state.
        status, health, _ = self.hub.request("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])

    def test_identity_and_capability_contracts_are_served(self) -> None:
        status, identity, _ = self.hub.request("/api/hub/controller", token=self.token)
        self.assertEqual(status, 200)
        settings = identity["settings"]
        self.assertEqual(settings["role"], "standalone")
        self.assertTrue(settings["site_id"])
        self.assertTrue(settings["controller_id"])
        self.assertTrue(identity["readiness"]["ready"])
        self.assertEqual(identity["readiness"]["global_authority"], "genstudio")
        self.assertIn("host", identity["capacity"])

        status, version, _ = self.hub.request("/api/version", token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(version["version"], "0.4.0")
        self.assertIn("studiohub-mac@", version["source"])

        status, capabilities, _ = self.hub.request("/api/hub/capabilities", token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(capabilities["schema"], "studiohub.site-capabilities")
        self.assertEqual(capabilities["site_id"], settings["site_id"])
        self.assertEqual(capabilities["authority"]["global"], "genstudio")
        # The final-render lane is a worker row of its own, present even with no qualified
        # authority: it reports the machine honestly instead of vanishing from the page.
        self.assertEqual([row["service_id"] for row in capabilities["workers"]],
                         ["voice", "hub-final-render"])
        self.assertEqual([row["physical_machine_id"] for row in capabilities["machines"]],
                         ["local"])

    def test_a_node_can_join_a_controller_and_is_reported_honestly(self) -> None:
        """One app is the controller; another joins it by address."""
        # Joining is private surface, like everything else that is not health or version.
        status, _, _ = self.hub.request("/api/hub/fleet/nodes", method="POST",
                                        headers={"Content-Type": "application/json"},
                                        body=json.dumps({"node_id": "n", "name": "N",
                                                         "address": "http://127.0.0.1:9"}).encode())
        self.assertEqual(status, 401)

        joined = {"node_id": "studio-2", "name": "Studio 2",
                  "address": "http://127.0.0.1:9"}
        status, payload, _ = self.hub.post_json("/api/hub/fleet/nodes", joined, token=self.token)
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["node"]["id"], "studio-2")

        status, listing, _ = self.hub.request("/api/hub/fleet/nodes", token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in listing["nodes"]], ["studio-2"])
        # Nothing is listening on that address, and that must read as unreachable rather than
        # as a node that joined successfully and is therefore available.
        self.assertEqual(listing["nodes"][0]["state"], "unreachable")
        self.assertEqual(listing["summary"]["reachable"], 0)

        # A public address is refused: a join must never point this Mac at the internet.
        status, refused, _ = self.hub.post_json(
            "/api/hub/fleet/nodes",
            {"node_id": "bad", "name": "Bad", "address": "http://93.184.216.34:80"},
            token=self.token)
        self.assertEqual(status, 400)
        self.assertIn("private or Tailscale", refused["detail"])

        status, _, _ = self.hub.request("/api/hub/fleet/nodes/studio-2", method="DELETE",
                                        token=self.token)
        self.assertEqual(status, 200)
        _, listing, _ = self.hub.request("/api/hub/fleet/nodes", token=self.token)
        self.assertEqual(listing["summary"]["total"], 0)

    def test_a_joined_node_enters_the_registry_without_a_restart(self) -> None:
        """A node's studios must be routable the moment it joins, not after a restart.

        The registry is loaded once at startup, so without an explicit reload a freshly
        joined node was listed as reachable while no work was ever sent to it.
        """
        joined = {"node_id": "studio-9", "name": "Studio 9",
                  "address": "http://127.0.0.1:9",
                  "studios": [{"id": "music", "modality": "music"},
                              {"id": "image", "modality": "image"}]}
        status, payload, _ = self.hub.post_json("/api/hub/fleet/nodes", joined,
                                                token=self.token)
        self.assertEqual(status, 201, payload)
        status, workers, _ = self.hub.request("/api/hub/workers", token=self.token)
        self.assertEqual(status, 200)
        remote = {row["id"]: row for row in workers["workers"]
                  if row.get("machine") == "studio-9"}
        self.assertEqual(set(remote), {"studio-9:image", "studio-9:music"})
        self.assertEqual(remote["studio-9:music"]["modality"], "music")

        # Leaving takes it straight back out of the registry too.
        status, _, _ = self.hub.request("/api/hub/fleet/nodes/studio-9", method="DELETE",
                                        token=self.token)
        self.assertEqual(status, 200)
        _, workers, _ = self.hub.request("/api/hub/workers", token=self.token)
        self.assertFalse([row for row in workers["workers"]
                          if row.get("machine") == "studio-9"])

    def test_a_fleet_credential_can_work_but_not_own_the_mac(self) -> None:
        """The credential a controller is given must be narrower than the Hub's own."""
        fleet_file = self.hub.data / ".fleet_token"
        fleet = fleet_file.read_text().strip()
        self.assertTrue(len(fleet) >= 24, "the packaged Hub must own a fleet credential")
        # It is an owner-only secret, like every other credential here.
        self.assertEqual(fleet_file.stat().st_mode & 0o077, 0)
        self.assertNotEqual(fleet, self.token, "the fleet credential is its own scope")

        # It can do the work a controller exists for.
        status, payload, _ = self.hub.request("/api/hub/capabilities", token=fleet)
        self.assertEqual(status, 200)
        status, listing, _ = self.hub.request("/api/hub/jobs", token=fleet)
        self.assertEqual(status, 200)

        # It cannot change this Mac's settings or delete its things.
        status, refused, _ = self.hub.post_json(
            "/api/hub/settings/preview", {"max_bytes": 1}, token=fleet)
        self.assertEqual(status, 403)
        self.assertIn("fleet credential", refused["detail"])

        status, _, _ = self.hub.request(
            "/api/hub/settings", method="PUT", token=fleet,
            headers={"Content-Type": "application/json"},
            body=json.dumps({"max_bytes": 1}).encode())
        self.assertEqual(status, 403)
        status, _, _ = self.hub.request(
            "/api/hub/controller", method="PUT", token=fleet,
            headers={"Content-Type": "application/json"},
            body=json.dumps({"role": "controller"}).encode())
        self.assertEqual(status, 403)
        status, _, _ = self.hub.request(
            "/api/hub/model-packages/whisper-turbo/verify", method="POST", token=fleet)
        self.assertEqual(status, 403)
        status, _, _ = self.hub.request(
            "/api/hub/memory/release", method="POST", token=fleet)
        self.assertEqual(status, 403)

        # A node joins with the fleet token, exactly as the app's Join field does.
        status, joined, _ = self.hub.post_json(
            "/api/hub/fleet/nodes",
            {"node_id": "fleet-joined", "name": "Joined with the fleet token",
             "address": "http://127.0.0.1:9"}, token=fleet)
        self.assertEqual(status, 201, joined)
        # But it cannot evict another node: membership is the controller owner's decision.
        status, refused, _ = self.hub.request("/api/hub/fleet/nodes/fleet-joined",
                                              method="DELETE", token=fleet)
        self.assertEqual(status, 403)
        status, _, _ = self.hub.request("/api/hub/fleet/nodes/fleet-joined",
                                        method="DELETE", token=self.token)
        self.assertEqual(status, 200)

        # The Hub's own credential keeps the access it always had.
        status, _, _ = self.hub.request("/api/hub/settings", token=self.token)
        self.assertEqual(status, 200)

    def test_a_mac_that_joins_another_controller_answers_only_its_new_one(self) -> None:
        """Re-pointing a Mac at a new controller must not leave the old one with a key.

        A controller is replaced by promoting a node and pointing the others at it. A node
        adopts the new controller's credential as part of joining, so the credential the dead
        controller holds stops working - which is what makes the replacement complete rather
        than a second controller that can still drive this Mac.
        """
        with tempfile.TemporaryDirectory(prefix="studiohub-repoint-") as root:
            directory = Path(root)
            node = HubProcess(directory, "node", "node-repoint-token-0123456789ab")
            node.start()
            try:
                old = HubProcess(directory, "old-controller",
                                 "old-controller-token-0123456789")
                old.start()
                try:
                    new = HubProcess(directory, "new-controller",
                                     "new-controller-token-0123456789")
                    new.start()
                    try:
                        old_fleet = (old.data / ".fleet_token").read_text().strip()
                        new_fleet = (new.data / ".fleet_token").read_text().strip()
                        self.assertNotEqual(old_fleet, new_fleet)

                        # The node has not adopted either yet, so neither controller can drive it.
                        for foreign in (old_fleet, new_fleet):
                            status, _, _ = node.request("/api/hub/capabilities", token=foreign)
                            self.assertEqual(status, 401, "an unadopted credential must not work")

                        # Joining the new controller is what hands it the key, and it uses the
                        # node's own Hub token - which is the owner's decision on this Mac.
                        status, adopted, _ = node.post_json("/api/hub/fleet/adopt",
                                                            {"token": new_fleet}, token=node.token)
                        self.assertEqual(status, 200, adopted)
                        status, _, _ = node.request("/api/hub/capabilities", token=new_fleet)
                        self.assertEqual(status, 200, "the new controller must be able to work here")
                        status, refused, _ = node.request("/api/hub/capabilities", token=old_fleet)
                        self.assertEqual(status, 401,
                                         "the replaced controller's credential must stop working")
                        # And the node's own owner still has full control.
                        status, _, _ = node.request("/api/hub/settings", token=node.token)
                        self.assertEqual(status, 200)
                    finally:
                        new.stop()
                finally:
                    old.stop()
            finally:
                node.stop()

    def test_a_mac_that_leaves_a_group_stops_carrying_its_credential(self) -> None:
        """Leaving is the action for a Mac that is being retired, sold or moved.

        A node holds the controller's token - one token per controller, copied to every joining
        Mac - so the thing that must not travel out of the door with it is that credential. What
        makes leaving real is that the Hub mints a fresh local one there and then: the old
        controller's token stops working here, so it cannot dispatch to this Mac even while its
        roster still lists it.
        """
        with tempfile.TemporaryDirectory(prefix="studiohub-leave-") as root:
            directory = Path(root)
            node = HubProcess(directory, "node", "node-leave-token-0123456789abcd")
            node.start()
            try:
                controller = HubProcess(directory, "controller", "controller-leave-token-0123456789")
                controller.start()
                try:
                    borrowed = (controller.data / ".fleet_token").read_text().strip()
                    status, adopted, _ = node.post_json("/api/hub/fleet/adopt",
                                                        {"token": borrowed}, token=node.token)
                    self.assertEqual(status, 200, adopted)
                    status, _, _ = node.request("/api/hub/capabilities", token=borrowed)
                    self.assertEqual(status, 200, "the controller must be able to work here first")

                    # A controller must not be able to un-join a node: leaving is the Mac owner's
                    # decision, so the borrowed credential is refused.
                    status, refused, _ = node.post_json("/api/hub/fleet/leave", {}, token=borrowed)
                    self.assertEqual(status, 403, refused)

                    status, left, _ = node.post_json("/api/hub/fleet/leave", {}, token=node.token)
                    self.assertEqual(status, 200, left)
                    self.assertTrue(left.get("left"))

                    status, _, _ = node.request("/api/hub/capabilities", token=borrowed)
                    self.assertEqual(status, 401,
                                     "the group's credential must stop working on a Mac that left")
                    fresh = (node.data / ".fleet_token").read_text().strip()
                    self.assertNotEqual(fresh, borrowed, "leaving must mint, not merely forget")
                    status, settings, _ = node.request("/api/hub/controller", token=node.token)
                    self.assertEqual(status, 200, settings)
                    # The credential origin is served by the controller read, which is where the
                    # app takes it from - the capabilities snapshot carries the role only.
                    self.assertEqual(settings["settings"]["fleet_credential_origin"], "minted")
                    self.assertEqual(settings["settings"]["role"], "standalone",
                                     "a Mac that left must be able to take work on its own again")
                    # And this Mac's own token still has full control of it.
                    status, _, _ = node.request("/api/hub/settings", token=node.token)
                    self.assertEqual(status, 200)
                finally:
                    controller.stop()
            finally:
                node.stop()

    def test_the_hub_address_is_loopback_only(self) -> None:
        """There is no cloud-domain assumption to make; the app talks to a private address."""
        host = self.hub.base.split("://", 1)[1].split(":", 1)[0]
        self.assertEqual(host, "127.0.0.1")
        self.assertIn("127.0.0.1", self.hub.base)


class FleetDispatchTests(unittest.TestCase):
    """A controller must be able to send real work to a joined node.

    Everything up to here was proven in pieces. This runs two real Hubs: a node whose Image
    studio is a recording stub, and a controller that was told about it. A job submitted to the
    controller has to come out the other side at the stub.
    """

    def test_a_job_submitted_to_the_controller_runs_on_the_node(self) -> None:
        """Almost there, and the remaining step is precise.

        Five things were missing before this and each failed **silently**: the node did not
        serve the surface a controller dispatches to, was not in the controller's registry,
        served no peer resource snapshot, did not share the fleet credential, and - the last
        one found - the controller's dispatcher reached the node's catalogue and reported
        "'fixture/flux' not in node-a:image's catalog" while the aggregate catalogue plainly
        listed it as cached.

        What is known: the node is polled for /api/health and /api/catalog on the right
        interval, its peer snapshot is trusted (dispatch_ready passed), and the aggregate view
        has the model. What is not yet established is why monitor.scheduling_catalog - which
        fails closed unless the studio's status is "up" and its catalogue observation carries
        no error - returns nothing for a studio it is demonstrably polling.

        Kept as a test rather than a note because everything it exercises is real, and it will
        say so the moment the last condition is understood.
        """
        # Cleanups unwind last-in-first-out, so the temporary directory is registered first and
        # removed last - after both Hubs have stopped writing into it.
        temporary = tempfile.TemporaryDirectory(prefix="studiohub-dispatch-")
        self.addCleanup(temporary.cleanup)
        worker = StubImageWorker()
        self.addCleanup(worker.stop)
        directory = Path(temporary.name)
        node = HubProcess(directory, "node", "node-fixture-token-0123456789ab",
                          workers=[{"id": "image", "modality": "image", "port": worker.port}])
        node.start()
        self.addCleanup(node.stop)
        node_url = urllib.parse.urlsplit(node.base)

        # The controller's packaged catalog is the trusted boundary for what it will accept, so
        # this Mac needs the same model installed - which is how the fleet is actually set up:
        # every Mac is copied from the same library.
        manifests, library = tiny_flux_install(directory / "controller-models")
        # The controller runs an Image studio but does not have the model installed, so the
        # only place this job can run is the node. This is the case the fleet exists for.
        controller_worker = StubImageWorker(catalog=False)
        self.addCleanup(controller_worker.stop)
        controller = HubProcess(
            directory, "controller", "controller-fixture-token-0123456789",
            workers=[{"id": "image", "modality": "image", "port": controller_worker.port}],
            env={"STUDIOHUB_MODEL_MANIFESTS": str(manifests),
                 "STUDIOHUB_MODELS_DIR": str(library)},
            joined=[{"node_id": "node-a", "name": "Node A",
                     # The controller must reach the node at the address the node bound.
                     "address": f"http://127.0.0.1:{node_url.port}",
                     "studios": [{"id": "image", "modality": "image"}]}])
        controller.start()
        self.addCleanup(controller.stop)

        # Joining a fleet means adopting its credential: until the node shares it, the
        # controller presents a token the node does not recognise and is answered 401.
        status, controller_fleet, _ = controller.request("/api/hub/fleet/nodes",
                                                         token=controller.token)
        fleet_value = (controller.data / ".fleet_token").read_text().strip()
        status, adopted, _ = node.post_json("/api/hub/fleet/adopt",
                                            {"token": fleet_value}, token=node.token)
        self.assertEqual(status, 200, adopted)
        # A fleet credential must not be able to do this to another Mac: membership is the
        # owner's decision, so it takes the owner's credential.
        status, refused, _ = node.post_json("/api/hub/fleet/adopt",
                                            {"token": "another-fleet-token-0123456789"},
                                            token=fleet_value)
        self.assertEqual(status, 403, refused)

        if True:
            status, submitted, _ = controller.post_json(
                "/api/hub/jobs",
                {"modality": "image", "model": FLUX_MODEL_ID,
                 "clientRequestId": "fleet-dispatch-1",
                 "items": [{"prompt": "a quiet harbour", "params": {}}]},
                token=controller.token)
            self.assertEqual(status, 200, submitted)
            batch = submitted.get("batch_id")
            self.assertIsNotNone(batch, submitted)

            # The job has to reach the node's studio, not sit queued on the controller.
            deadline = time.time() + 45
            while time.time() < deadline and not worker.submissions:
                time.sleep(0.5)
            # The controller's own log is the only place some of these failures are visible:
            # several of them produced no governor note at all.
            if not worker.submissions:
                text = (controller.root / "controller.log").read_text(errors="replace")
                print("CONTROLLER LOG TAIL:", text[-1500:])
            self.assertTrue(worker.submissions,
                            "the controller never dispatched to the joined node; "
                            f"governor said: {self.governor(controller, batch)}; "
                            f"the controller's own studio got {len(controller_worker.submissions)}; "
                            f"catalog: {self.catalog(controller)}; node studio saw: {worker.seen[:8]}")
            # And it must not have run it locally as well.
            self.assertEqual(controller_worker.submissions, [],
                             "a job must run in one place, not two")
            self.assertIn("/api/generate/txt2img", worker.submissions[0]["path"])
            self.assertIn("a quiet harbour", worker.submissions[0]["body"])
            # The node's worker must be given the node's own credential to accept, never the
            # controller's.
            self.assertEqual(worker.submissions[0]["token"], node.token)


    def test_a_node_refuses_stale_capability_work_its_owner_disabled(self) -> None:
        """The controller's cache is not authority over another Mac's own decision.

        A controller learns a node is reachable with its Image studio and keeps
        that picture until the next poll. If the node's owner disables the studio
        in between, the node - not the controller's stale snapshot - has to be the
        one that refuses, before its worker is asked to generate anything.
        """
        temporary = tempfile.TemporaryDirectory(prefix="studiohub-stale-")
        self.addCleanup(temporary.cleanup)
        worker = StubImageWorker()
        self.addCleanup(worker.stop)
        directory = Path(temporary.name)
        node = HubProcess(directory, "node", "node-fixture-token-0123456789ab",
                          workers=[{"id": "image", "modality": "image", "port": worker.port}])
        node.start()
        self.addCleanup(node.stop)
        node_url = urllib.parse.urlsplit(node.base)
        manifests, library = tiny_flux_install(directory / "controller-models")
        controller_worker = StubImageWorker(catalog=False)
        self.addCleanup(controller_worker.stop)
        controller = HubProcess(
            directory, "controller", "controller-fixture-token-0123456789",
            workers=[{"id": "image", "modality": "image", "port": controller_worker.port}],
            env={"STUDIOHUB_MODEL_MANIFESTS": str(manifests),
                 "STUDIOHUB_MODELS_DIR": str(library)},
            joined=[{"node_id": "node-a", "name": "Node A",
                     "address": f"http://127.0.0.1:{node_url.port}",
                     "studios": [{"id": "image", "modality": "image"}]}])
        controller.start()
        self.addCleanup(controller.stop)
        fleet_value = (controller.data / ".fleet_token").read_text().strip()
        node.post_json("/api/hub/fleet/adopt", {"token": fleet_value}, token=node.token)

        # Wait until the controller has a positive, cached picture of the node.
        node_row = None
        deadline = time.time() + 45
        while time.time() < deadline:
            status, overview, _ = controller.request("/api/hub/fleet/overview",
                                                     token=controller.token)
            rows = {row["machine"]: row for row in (overview or {}).get("machines", [])}
            node_row = rows.get("node-a")
            if node_row and node_row.get("reachable"):
                break
            time.sleep(0.5)
        self.assertIsNotNone(node_row, "the controller never listed the joined node")
        self.assertTrue(node_row.get("reachable"),
                        "the controller never cached the node as reachable")

        # The node's own owner disables the studio; the controller's cache is now stale.
        disabled = node.post_json("/api/hub/registry/studios/image/enabled",
                                  {"enabled": False}, token=node.token)
        self.assertEqual(disabled[0], 200, disabled[1])
        before = len(worker.submissions)
        status, payload, _ = node.post_json(
            "/studio/image/api/generate/txt2img",
            {"repo": FLUX_MODEL_ID, "prompt": "must not run"},
            token=fleet_value)
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload.get("code"), "STUDIO_DISABLED", payload)
        self.assertEqual(len(worker.submissions), before,
                         "a stale controller capability must not override the node's disable")

    def test_a_node_refuses_peer_work_when_its_output_storage_is_full(self) -> None:
        """Storage refusal is this Mac's own decision, enforced at the peer boundary.

        The controller can still see the node and its model, but the node's output
        policy has no room, so the node must refuse before its worker is asked.
        """
        temporary = tempfile.TemporaryDirectory(prefix="studiohub-storage-")
        self.addCleanup(temporary.cleanup)
        worker = StubImageWorker()
        self.addCleanup(worker.stop)
        directory = Path(temporary.name)
        (directory / "node" / "image-outputs").mkdir(parents=True, exist_ok=True)
        node = HubProcess(directory, "node", "node-fixture-token-0123456789ab",
                          workers=[{"id": "image", "modality": "image", "port": worker.port}])
        node.start()
        self.addCleanup(node.stop)
        node_url = urllib.parse.urlsplit(node.base)
        manifests, library = tiny_flux_install(directory / "controller-models")
        controller_worker = StubImageWorker(catalog=False)
        self.addCleanup(controller_worker.stop)
        controller = HubProcess(
            directory, "controller", "controller-fixture-token-0123456789",
            workers=[{"id": "image", "modality": "image", "port": controller_worker.port}],
            env={"STUDIOHUB_MODEL_MANIFESTS": str(manifests),
                 "STUDIOHUB_MODELS_DIR": str(library)},
            joined=[{"node_id": "node-a", "name": "Node A",
                     "address": f"http://127.0.0.1:{node_url.port}",
                     "studios": [{"id": "image", "modality": "image"}]}])
        controller.start()
        self.addCleanup(controller.stop)
        fleet_value = (controller.data / ".fleet_token").read_text().strip()
        node.post_json("/api/hub/fleet/adopt", {"token": fleet_value}, token=node.token)

        changed = node.request(
            "/api/hub/settings", method="PUT", token=node.token,
            headers={"Content-Type": "application/json"},
            body=json.dumps({"minimum_free_bytes": 10**18}).encode())
        self.assertEqual(changed[0], 200, changed[1])
        before = len(worker.submissions)
        status, payload, _ = node.post_json(
            "/studio/image/api/generate/txt2img",
            {"repo": FLUX_MODEL_ID, "prompt": "must not run"},
            token=fleet_value)
        self.assertEqual(status, 507, payload)
        self.assertEqual(payload.get("code"), "OUTPUT_STORAGE_BLOCKED", payload)
        self.assertEqual(len(worker.submissions), before,
                         "blocked output storage must not reach the node's worker")

    def test_the_controllers_artifact_comes_back_from_the_node(self) -> None:
        """The work runs on the node; the operator must still be able to collect it.

        Dispatch is proven and stays green. This is the step after it, and it does not work yet:
        the controller polls the node's job, the node answers `done`, and the controller leaves
        its own item `running` - so the artifact route answers 404 for a job that finished on
        the node in under a second.

        What is ruled out by driving it: the poll does happen, on the right URL, through the
        node's proxy; the node's completion is now a full receipt (output URL, path, media type,
        digest, timestamp, and the machine it ran on - the Hub rejects a completion missing any
        of them, which is correct and was the previous failure). What is left is why a valid
        `done` does not finalise the item, given the same code path finalises a local job.

        The owner has since said the artifact lives on the node and is pulled only when
        requested, so the controller proxying it - rather than copying it - is the right shape.
        This test asserts exactly that, and is skipped until the item finalises.
        """
        worker = StubImageWorker(machine="node-a")
        self.addCleanup(worker.stop)
        temporary = tempfile.TemporaryDirectory(prefix="studiohub-artifact-")
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        node = HubProcess(directory, "node", "node-fixture-token-0123456789ab",
                          workers=[{"id": "image", "modality": "image", "port": worker.port}])
        node.start()
        self.addCleanup(node.stop)
        node_url = urllib.parse.urlsplit(node.base)
        manifests, library = tiny_flux_install(directory / "controller-models")
        controller_worker = StubImageWorker(catalog=False)
        self.addCleanup(controller_worker.stop)
        controller = HubProcess(
            directory, "controller", "controller-fixture-token-0123456789",
            workers=[{"id": "image", "modality": "image", "port": controller_worker.port}],
            env={"STUDIOHUB_MODEL_MANIFESTS": str(manifests),
                 "STUDIOHUB_MODELS_DIR": str(library)},
            joined=[{"node_id": "node-a", "name": "Node A",
                     "address": f"http://127.0.0.1:{node_url.port}",
                     "studios": [{"id": "image", "modality": "image"}]}])
        controller.start()
        self.addCleanup(controller.stop)
        node.post_json("/api/hub/fleet/adopt",
                       {"token": (controller.data / ".fleet_token").read_text().strip()},
                       token=node.token)

        _, submitted, _ = controller.post_json(
            "/api/hub/jobs",
            {"modality": "image", "model": FLUX_MODEL_ID,
             "clientRequestId": "fleet-artifact-1",
             "items": [{"prompt": "a quiet harbour", "params": {}}]},
            token=controller.token)
        batch = submitted["batch_id"]

        deadline = time.time() + 45
        artifact = None
        while time.time() < deadline:
            status, artifact = controller.artifact(
                f"/api/hub/jobs/{batch}/items/0/artifact", token=controller.token)
            if status == 200:
                break
            artifact = None
            time.sleep(0.5)
        if artifact is None:
            # The controller's own log is the only place some of these are visible.
            print("CONTROLLER LOG TAIL:",
                  (controller.root / "controller.log").read_text(errors="replace")[-1200:])
        self.assertIsNotNone(artifact, "the controller never served the artifact the node "
                                       f"produced; job: {self.governor(controller, batch)}; "
                                       f"the node's studio was asked for: {worker.seen[:14]}")
        self.assertEqual(artifact, FLUX_PNG,
                         "the controller must return the node's bytes unchanged")

    @staticmethod
    def tail(controller) -> str:
        import re as re_module
        text = (controller.root / "controller.log").read_text(errors="replace")
        lines = [line for line in text.splitlines() if "sched-debug" in line]
        # A distinct set of reasons, so the message stays readable.
        return " | ".join(sorted(set(re_module.sub(r"obs=.*", "", line)
                                      for line in lines))[:4])

    @staticmethod
    def catalog(controller) -> str:
        status, payload, _ = controller.request("/api/hub/catalog", token=controller.token)
        rows = []
        if status == 200:
            rows = [(row.get("hub_studio") or row.get("studio"), row.get("repo"),
                     row.get("cache")) for row in (payload.get("models") or [])]
        # The controller's own view of each studio: whether the monitor considers it up, which
        # is the first thing the scheduling view checks.
        status, caps, _ = controller.request("/api/hub/capabilities", token=controller.token)
        workers = []
        if status == 200:
            workers = [(row.get("service_id"), row.get("physical_machine_id"),
                        row.get("online"), row.get("ready")) for row in caps.get("workers", [])]
        return f"models={rows!r} workers={workers!r}"

    @staticmethod
    def governor(controller, batch: str) -> str:
        status, payload, _ = controller.request(f"/api/hub/jobs/{batch}", token=controller.token)
        if status != 200:
            return f"(job unreadable: {status})"
        items = [{key: item.get(key) for key in
                  ("state", "studio", "error", "tries", "duration_s")}
                 for item in (payload.get("items") or [])]
        return (f"note={payload.get('governor_note')!r} state={payload.get('state')!r} "
                f"items={items!r}")


FLUX_MODEL_ID = "fixture/flux"
WHISPER_MODEL_ID = "fixture/whisper"
QWEN_MODEL_ID = "fixture/qwen"
# A one-pixel PNG, with the digest the stub reports for it, so the controller can verify the
# artifact it fetches rather than accepting whatever comes back.
FLUX_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d4944415478da63f8cfc000000301010018dd8db0"
    "0000000049454e44ae426082")
FLUX_SHA = sha256(FLUX_PNG).hexdigest()


def tiny_install(root: Path, manifest_name: str, model_id: str,
                 package_id: str) -> tuple[Path, Path]:
    """One verified package and the manifest catalog that names it.

    The controller refuses a job for a model outside its packaged catalog, so a real dispatch
    test needs a real - if tiny - installation rather than a fixture that only looks like one.
    """
    import json as json_module
    from hashlib import sha256
    from components import model_packages

    original = json_module.loads((PROJECT_ROOT / f"model-manifests/{manifest_name}.json").read_text())
    original["model_id"] = model_id
    original["package_id"] = package_id
    source = root / "source"
    for entry in original["files"]:
        value = (entry["path"] + "\n").encode()
        # A manifest may name where the file came from as well as where it is installed; the
        # whisper manifests have only the installed path.
        target = source / (entry.get("source_path") or entry["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
        entry["size"] = len(value)
        entry["sha256"] = sha256(value).hexdigest()
    model_packages.validate_manifest(original)
    library = root / "library"
    library.mkdir(parents=True, exist_ok=True)
    model_packages.import_folder(original, source, library)
    manifests = root / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    (manifests / f"{package_id}.json").write_text(json_module.dumps(original))
    return manifests, library


def tiny_flux_install(root: Path) -> tuple[Path, Path]:
    return tiny_install(root, "flux2-klein-4b-mlx-4bit", FLUX_MODEL_ID, "fixture-flux")


def tiny_whisper_install(root: Path) -> tuple[Path, Path]:
    return tiny_install(root, "whisper-turbo", WHISPER_MODEL_ID, "fixture-whisper")


# A short WAV, with the digest the stub reports for it, so the controller can verify what it
# fetches rather than accepting whatever comes back.
VOICE_WAV = (b"RIFF" + (36 + 8).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
             + (1).to_bytes(2, "little") + (1).to_bytes(2, "little")
             + (24000).to_bytes(4, "little") + (48000).to_bytes(4, "little")
             + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
             + b"data" + (8).to_bytes(4, "little") + b"\x00" * 8)
VOICE_SHA = sha256(VOICE_WAV).hexdigest()


class StubVoiceWorker:
    """A recording stand-in for a node's Voice studio.

    A clone is dispatched to the reference endpoint rather than the preset one, and like an
    image job it is accepted and then polled.
    """

    def __init__(self, *, catalog: bool = True, machine: str = "local") -> None:
        self.submissions: list[dict] = []
        self.seen: list[str] = []
        self.has_catalog = catalog
        self.machine = machine
        self.job_params: dict = {}
        # A real, stateful voice library: the voice routes are the point of this stub, and an
        # app that adds a voice must be able to see it afterwards.
        self.voices: dict[str, dict] = {}
        self.voice_edits: list[tuple[str, bytes]] = []
        self.voice_deletes: list[str] = []
        self.raw_bodies: list[str] = []
        # The clip bytes this worker stored, keyed by voice id.
        self.voice_audio: dict[str, bytes] = {}
        # Voices installed by a controller's shared-voice fan-out, with the timings they carried.
        self.fleet_syncs: list[dict] = []
        # A worker that ignores the timings field, to prove the controller notices.
        self.drop_timings = False
        outer = self

        def voice_payload() -> dict:
            return {"voices": [dict(row) for row in outer.voices.values()]}

        def new_voice(name: str, transcript: str) -> dict:
            identifier = f"voice-{len(outer.voices) + 1}"
            row = {
                "id": identifier, "name": name, "language": "en", "gender": "n",
                "notes": "", "license": "self-owned", "source_url": None,
                "permission_acknowledged": True, "has_transcript": bool(transcript),
                "transcript": transcript, "audio_extension": ".wav",
                "duration_seconds": 1.0, "sample_rate": 16000, "channels": 1,
                "fleet_managed": False, "audio_sha256": VOICE_SHA,
                "created_at": time.time(), "audio_url": f"/api/voices/{identifier}/audio",
                "audio_filename": "reference.wav",
            }
            outer.voices[identifier] = row
            return row

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code: int, payload: dict) -> None:
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                outer.seen.append(self.path)
                if self.path.endswith("/api/health"):
                    self._send(200, {"ok": True, "generation": {
                        "available": True, "busy": False, "queued": 0, "running": 0}})
                elif self.path.endswith("/api/catalog"):
                    models = [{
                        "repo": QWEN_MODEL_ID, "model_id": QWEN_MODEL_ID,
                        "label": "Qwen fixture", "family": "qwen3-tts",
                        "capabilities": ["tts", "voice-cloning"], "languages": ["en"],
                        "runtime_ready": True, "cache": {"state": "cached"},
                        "reference_audio": {"declared": True, "supported": True,
                                            "transcript": "required"},
                    }] if outer.has_catalog else []
                    self._send(200, {"models": models})
                elif self.path == "/api/voices":
                    self._send(200, voice_payload())
                elif self.path.startswith("/api/voices/") and self.path.endswith("/audio"):
                    # The worker returns the clip it was given, so a test that plays a voice
                    # hears the same bytes that were uploaded.
                    identifier = self.path.split("/")[3] if len(self.path.split("/")) > 3 else ""
                    stored = outer.voice_audio.get(identifier, VOICE_WAV)
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Content-Length", str(len(stored)))
                    self.send_header("X-Content-SHA256", VOICE_SHA)
                    self.end_headers()
                    self.wfile.write(stored)
                elif self.path.endswith("/audio"):
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Content-Length", str(len(VOICE_WAV)))
                    self.send_header("X-Content-SHA256", VOICE_SHA)
                    self.end_headers()
                    self.wfile.write(VOICE_WAV)
                elif "/api/generate/jobs/" in self.path:
                    self._send(200, {"job": {
                        "id": "job-v1", "state": "done", "progress": 1.0,
                        "params": outer.job_params,
                        "worker_id": "voice-fixture", "machine_id": outer.machine,
                        "bytes": len(VOICE_WAV), "sha256": VOICE_SHA,
                        "media_type": "audio/wav",
                        "output_url": "/api/generate/jobs/job-v1/audio",
                        "artifact_url": "/api/generate/jobs/job-v1/audio",
                        "output_path": "/tmp/job-v1.wav",
                        "generated_at": time.time(), "finished_at": time.time(),
                    }})
                else:
                    self._send(200, {})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                outer.seen.append(self.path)
                if "/api/generate/txt2speech" in self.path:
                    # A clone posts the request as JSON inside a form field, so the text is
                    # somewhere in the body rather than being the body.
                    body = raw.decode("utf-8", "replace")
                    marker = 'name="request_json"'
                    if marker in body:
                        after = body.split(marker, 1)[1]
                        request_json = after.split("\r\n\r\n", 1)[-1].split(
                            "\r\n--", 1
                        )[0].strip()
                        outer.job_params = json.loads(request_json)
                    outer.submissions.append({
                        "path": self.path, "body": body,
                        "token": self.headers.get("X-Hub-Token"),
                    })
                    self._send(200, {"job": {"id": "job-v1", "state": "queued",
                                             "worker_id": "voice-fixture"}})
                elif self.path == "/api/voices":
                    body = raw.decode("utf-8", "replace")
                    outer.raw_bodies.append(body)
                    # The fields arrive as multipart form parts; the transcript is the one
                    # this test reads back, so it is what gets carried out of the body.
                    transcript = ""
                    marker = 'name="transcript"'
                    if marker in body:
                        after = body.split(marker, 1)[1]
                        transcript = after.split("\r\n\r\n", 1)[-1].split("\r\n--", 1)[0].strip()
                    name = "Imported voice"
                    marker = 'name="name"'
                    if marker in body:
                        after = body.split(marker, 1)[1]
                        name = after.split("\r\n\r\n", 1)[-1].split("\r\n--", 1)[0].strip()
                    row = new_voice(name, transcript)
                    # Store the file part so the audio route has something real to return.
                    clip = b""
                    if 'filename="' in body:
                        head = body.split('filename="', 1)[1]
                        if "\r\n\r\n" in head:
                            clip = head.split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0].encode(
                                "utf-8", "surrogateescape")
                    outer.voice_audio[row["id"]] = clip or VOICE_WAV
                    self._send(201, {"voice": row})
                else:
                    self._send(200, {"ok": True})

            def do_PUT(self):
                """A controller installing one shared voice: the receive end of the fan-out."""
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                outer.seen.append(self.path)
                if "/fleet-sync" not in self.path:
                    self._send(404, {"detail": "not found"})
                    return
                body = raw.decode("utf-8", "replace")
                identifier = self.path.split("/api/voices/", 1)[1].split("/", 1)[0]

                def field(name: str) -> str:
                    marker = f'name="{name}"'
                    if marker not in body:
                        return ""
                    after = body.split(marker, 1)[1]
                    return after.split("\r\n\r\n", 1)[-1].split("\r\n--", 1)[0].strip()

                clip = b""
                if b'filename="' in raw:
                    head = raw.split(b'filename="', 1)[1]
                    if b"\r\n\r\n" in head:
                        clip = head.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0]
                segments = json.loads(field("transcript_segments_json") or "[]")
                transcript = field("transcript")
                sha = field("audio_sha256")
                outer.fleet_syncs.append({
                    "id": identifier, "sha256": sha, "segments": segments,
                    "transcript": transcript, "name": field("name"),
                    "clip": clip or VOICE_WAV,
                })
                row = {
                    "id": identifier, "name": field("name"), "language": field("language"),
                    "gender": field("gender"), "notes": "", "license": field("license"),
                    "source_url": None, "permission_acknowledged": True,
                    "has_transcript": bool(transcript), "transcript": transcript,
                    "audio_extension": ".wav", "duration_seconds": 1.0,
                    "sample_rate": 16000, "channels": 1, "fleet_managed": True,
                    "audio_sha256": sha, "created_at": time.time(),
                    "audio_url": f"/api/voices/{identifier}/audio",
                    "audio_filename": "reference.wav",
                }
                outer.voices[identifier] = row
                outer.voice_audio[identifier] = clip or VOICE_WAV
                # The count is answered back on purpose: a node that silently drops the timings
                # accepts the voice and cannot clone with it, and only this number catches that.
                stored = 0 if outer.drop_timings else len(segments)
                previous = outer.fleet_syncs[-1] if len(outer.fleet_syncs) > 1 else None
                unchanged = previous is not None and previous["sha256"] == sha
                self._send(200, {"voice": row, "sync": {
                    "status": "current" if unchanged else "created",
                    "sha256": sha, "segments": stored}})

            def do_PATCH(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                outer.seen.append(self.path)
                outer.voice_edits.append((self.path, raw))
                identifier = self.path.rsplit("/", 1)[-1]
                if identifier in outer.voices:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                    outer.voices[identifier].update({
                        key: value for key, value in payload.items()
                        if key in ("name", "language", "gender", "license", "notes", "transcript")
                    })
                    if "transcript" in payload:
                        outer.voices[identifier]["has_transcript"] = bool(payload["transcript"])
                    self._send(200, {"voice": outer.voices[identifier]})
                else:
                    self._send(404, {"detail": "not found"})

            def do_DELETE(self):
                outer.seen.append(self.path)
                # The removal carries the expected hash as a query string, and the identifier has
                # to be read from the path without it.
                path, _, query = self.path.partition("?")
                identifier = path.split("/api/voices/", 1)[1].split("/", 1)[0]
                expected = urllib.parse.parse_qs(query).get("audio_sha256", [""])[0]
                outer.voice_deletes.append(identifier)
                removed = outer.voices.pop(identifier, None)
                outer.voice_audio.pop(identifier, None)
                if removed is None:
                    self._send(404, {"detail": f"managed voice {identifier} not found"})
                else:
                    # The real worker answers with the id and the hash it removed, and the
                    # controller verifies both rather than trusting a 200.
                    self._send(200, {"deleted": identifier,
                                     "sha256": expected or removed.get("audio_sha256")})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def stop(self) -> None:
        self.server.shutdown()


class StubSubtitleWorker:
    """A recording stand-in for a node's Subtitle studio.

    Transcription is synchronous over one request, unlike an image job: the controller posts the
    audio and the answer is the transcript, so there is no poll to follow.
    """

    def __init__(self, *, catalog: bool = True, machine: str = "local") -> None:
        self.transcriptions: list[dict] = []
        self.seen: list[str] = []
        self.has_catalog = catalog
        self.machine = machine
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code: int, payload: dict) -> None:
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                outer.seen.append(self.path)
                if self.path.endswith("/api/health"):
                    self._send(200, {"ok": True, "transcription": {
                        "available": True, "busy": False, "queued": 0, "running": 0}})
                elif self.path.endswith("/api/catalog"):
                    models = [{
                        "repo": WHISPER_MODEL_ID, "model_id": WHISPER_MODEL_ID,
                        "label": "Whisper fixture", "engine": "subtitle-mlx",
                        "languages": "Multilingual", "supports_word_timestamps": True,
                        "cached": True, "cache": {"state": "cached"},
                    }] if outer.has_catalog else []
                    self._send(200, {"models": models, "default_model": WHISPER_MODEL_ID})
                elif self.path.endswith("/api/transcribe/availability"):
                    # A studio without the model must not advertise it here either: this is the
                    # endpoint the transcription dispatcher reads to choose a studio, so a stub
                    # that hid the model from its catalogue but offered it here would send work
                    # to a machine that cannot run it.
                    models = [{"repo": WHISPER_MODEL_ID, "label": "Whisper fixture",
                               "engine": "subtitle-mlx", "cached": True}] if outer.has_catalog else []
                    self._send(200, {"available": True, "default_model": WHISPER_MODEL_ID,
                                     "models": models})
                else:
                    self._send(200, {})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                outer.seen.append(self.path)
                if self.path.endswith("/api/transcribe"):
                    outer.transcriptions.append({
                        "path": self.path, "bytes": len(raw),
                        "token": self.headers.get("X-Hub-Token"),
                    })
                    self._send(200, {
                        "srt": "1\n00:00:00,000 --> 00:00:01,250\nthe quick brown fox\n",
                        "text": "the quick brown fox", "language": "en", "duration": 1.25,
                        "model": WHISPER_MODEL_ID,
                        "studio_task_id": "stt-1", "elapsed_seconds": 1.0,
                    })
                else:
                    self._send(200, {"ok": True})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def stop(self) -> None:
        self.server.shutdown()


class StubImageWorker:
    """A recording stand-in for a studio.

    `catalog` decides whether this studio reports the model as installed. A controller that has
    the model itself will use its own studio, which is correct - so the controller's studio here
    reports an empty catalog, exactly like a Mac where the model was never verified.
    """

    def __init__(self, *, catalog: bool = True, machine: str = "local",
                 hold: bool = False) -> None:
        self.submissions: list[dict] = []
        self.seen: list[str] = []
        self.cancels: list[dict] = []
        self.has_catalog = catalog
        # The Hub refuses a completion whose machine identity is not the one it dispatched to,
        # which is what stops a node claiming work that ran somewhere else.
        self.machine = machine
        # A job that stays running until it is cancelled, so cancellation has something to act
        # on rather than a job that finished before anyone could stop it.
        self.hold = hold
        self.cancelled = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code: int, payload: dict) -> None:
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                outer.seen.append(self.path)
                if self.path.endswith("/api/health"):
                    # A studio the monitor cannot see as healthy is never dispatched to, so the
                    # stub has to answer health the way a real studio does.
                    self._send(200, {"ok": True, "generation": {
                        "available": True, "busy": False, "queued": 0, "running": 0}})
                elif self.path.endswith("/api/catalog"):
                    models = [{
                        "repo": FLUX_MODEL_ID, "model_id": FLUX_MODEL_ID, "label": "FLUX fixture",
                        "family": "flux2-klein", "capabilities": ["txt2img"],
                        "runtime_ready": True, "memory_eligible": True,
                        "cache": {"state": "cached"},
                    }] if outer.has_catalog else []
                    self._send(200, {"models": models})
                elif self.path.endswith("/image"):
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(FLUX_PNG)))
                    self.send_header("X-Content-SHA256", FLUX_SHA)
                    self.end_headers()
                    self.wfile.write(FLUX_PNG)
                elif "/api/generate/jobs/" in self.path:
                    if outer.hold and not outer.cancelled:
                        self._send(200, {"job": {"id": "job-1", "state": "running",
                                                 "progress": 0.4,
                                                 "worker_id": "image-fixture",
                                                 "machine_id": outer.machine}})
                        return
                    if outer.cancelled:
                        self._send(200, {"job": {"id": "job-1", "state": "cancelled",
                                                 "worker_id": "image-fixture",
                                                 "machine_id": outer.machine}})
                        return
                    # A completion receipt has to be complete: the Hub rejects a job that
                    # claims done without the output URL, path, media type and a timestamp,
                    # which is the behaviour that keeps a half-written file from being treated
                    # as a result.
                    self._send(200, {"job": {
                        "id": "job-1", "state": "done", "progress": 1.0,
                        "worker_id": "image-fixture",
                        "machine_id": outer.machine,
                        "bytes": len(FLUX_PNG), "sha256": FLUX_SHA,
                        "media_type": "image/png",
                        "output_url": "/api/generate/jobs/job-1/image",
                        "artifact_url": "/api/generate/jobs/job-1/image",
                        "output_path": "/tmp/job-1.png",
                        "generated_at": time.time(),
                        "finished_at": time.time(),
                    }})
                else:
                    self._send(200, {"job": {"id": "job-1", "state": "running"}})

            def do_DELETE(self):
                outer.seen.append(self.path)
                if "/api/generate/jobs/" in self.path:
                    outer.cancels.append({"path": self.path,
                                          "token": self.headers.get("X-Hub-Token")})
                    outer.cancelled = True
                    self._send(200, {"job": {"id": "job-1", "state": "cancelled"}})
                else:
                    self._send(200, {"ok": True})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                if self.path.endswith("/api/generate/txt2img"):
                    try:
                        parsed = json.loads(raw or b"{}")
                    except ValueError:
                        parsed = {}
                    outer.submissions.append({
                        "path": self.path, "body": raw.decode("utf-8", "replace"),
                        "token": self.headers.get("X-Hub-Token"), "request": parsed,
                    })
                    # A real worker identifies itself at submit and keeps that identity at
                    # every poll; the Hub refuses a status that changes it.
                    self._send(200, {"job": {"id": "job-1", "state": "queued",
                                             "worker_id": "image-fixture"}})
                else:
                    self._send(200, {"job": {"id": "job-1", "state": "done"}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def stop(self) -> None:
        self.server.shutdown()


class FleetVoiceDispatchTests(unittest.TestCase):
    """Voice is the third studio, and a clone is what the fleet exists to move around."""

    def test_a_voice_clone_submitted_to_the_controller_runs_on_the_node(self) -> None:
        worker = StubVoiceWorker(machine="node-a")
        self.addCleanup(worker.stop)
        temporary = tempfile.TemporaryDirectory(prefix="studiohub-voice-fleet-")
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        node = HubProcess(directory, "node", "node-fixture-token-0123456789ab",
                          workers=[{"id": "voice", "modality": "voice", "port": worker.port}])
        node.start()
        self.addCleanup(node.stop)
        node_url = urllib.parse.urlsplit(node.base)

        # A Qwen clone declares a Whisper dependency: the Voice worker runs its own quality
        # check on the reference clip, so the controller must have that installed too.
        models_root = directory / "controller-models"
        # The dependency is declared by package id, so this package must carry that id
        # rather than a fixture name.
        tiny_install(models_root, "whisper-turbo", WHISPER_MODEL_ID, "whisper-turbo")
        manifests, library = tiny_install(
            models_root, "qwen3-tts-12hz-0-6b-base-8bit", QWEN_MODEL_ID, "fixture-qwen")
        # The controller's own Voice studio has no model, so the only place this can run is
        # the node.
        controller_worker = StubVoiceWorker(catalog=False)
        self.addCleanup(controller_worker.stop)
        controller = HubProcess(
            directory, "controller", "controller-fixture-token-0123456789",
            workers=[{"id": "voice", "modality": "voice", "port": controller_worker.port}],
            env={"STUDIOHUB_MODEL_MANIFESTS": str(manifests),
                 "STUDIOHUB_MODELS_DIR": str(library)},
            joined=[{"node_id": "node-a", "name": "Node A",
                     "address": f"http://127.0.0.1:{node_url.port}",
                     "studios": [{"id": "voice", "modality": "voice"}]}])
        controller.start()
        self.addCleanup(controller.stop)
        controller_fleet = (controller.data / ".fleet_token").read_text().strip()
        status, adopted, _ = node.post_json("/api/hub/fleet/adopt",
                                            {"token": controller_fleet}, token=node.token)
        self.assertEqual(status, 200, adopted)

        # A clone needs a reference clip, and it goes through the same Hub contract the app's
        # picker uses - which is what makes a pushed clone and a local one the same thing.
        status, staged, _ = controller.post_multipart(
            "/api/hub/voice-references", {"transcript": "exact words", "language": "en",
                                          "display_name": "Yim Mengly"},
            {"audio": ("reference.wav", VOICE_WAV, "audio/wav")}, token=controller.token)
        self.assertEqual(status, 201, staged)

        status, submitted, _ = controller.post_json(
            "/api/hub/jobs",
            {"modality": "voice", "model": QWEN_MODEL_ID,
             "clientRequestId": "fleet-voice-1",
             "voice_reference_asset_id": staged["asset_id"],
             "items": [{"text": "exact words", "params": {}}]},
            token=controller.token)
        self.assertEqual(status, 200, submitted)
        batch = submitted["batch_id"]

        deadline = time.time() + 60
        while time.time() < deadline and not worker.submissions:
            time.sleep(0.5)
        self.assertTrue(worker.submissions,
                        "the controller never sent the voice job to the node; "
                        f"job: {self.voice_state(controller, batch)}; "
                        f"node saw: {worker.seen[:10]}")
        # The node's own credential, never the controller's.
        self.assertEqual(worker.submissions[0]["token"], node.token)
        self.assertIn("txt2speech", worker.submissions[0]["path"])

        deadline = time.time() + 45
        audio = b""
        while time.time() < deadline:
            status, audio = controller.artifact(
                f"/api/hub/jobs/{batch}/items/0/artifact", token=controller.token)
            if status == 200:
                break
            audio = b""
            time.sleep(0.5)
        self.assertEqual(audio, VOICE_WAV,
                         "the controller must return the node's audio unchanged; "
                         f"job: {self.voice_state(controller, batch)}")
        # The row says which voice spoke it (owner, 2026-10-24): the name it was staged under.
        status, job, _ = controller.request(f"/api/hub/jobs/{batch}", token=controller.token)
        self.assertEqual(job["items"][0].get("voice"),
                         {"name": "Yim Mengly", "id": staged["asset_id"]})

    @staticmethod
    def voice_state(controller, batch: str) -> str:
        status, payload, _ = controller.request(f"/api/hub/jobs/{batch}", token=controller.token)
        if status != 200:
            return f"(unreadable: {status})"
        return (f"note={payload.get('governor_note')!r} "
                f"items={payload.get('items')!r}")


class FleetSharedVoiceTests(unittest.TestCase):
    """Adding a voice once has to put it on every Mac the controller owns.

    The owner asked for exactly this: fifty to a hundred nodes, no hand-copying, a default-on
    toggle in the gateway. The gateway talks only to controllers, so this fan-out is the Hub's
    half - and the failure that matters is not a push that fails, it is a push that succeeds
    everywhere and produces a voice that cannot clone on any of them.
    """

    TRANSCRIPT = "A short line that was transcribed."
    SEGMENTS = [{"start_seconds": 0.0, "end_seconds": 1.4, "text": "A short line"},
                {"start_seconds": 1.4, "end_seconds": 3.1, "text": "that was transcribed."}]

    def _fleet(self):
        worker = StubVoiceWorker(machine="node-a")
        self.addCleanup(worker.stop)
        temporary = tempfile.TemporaryDirectory(prefix="studiohub-shared-voice-")
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        node = HubProcess(directory, "node", "node-shared-token-0123456789ab",
                          workers=[{"id": "voice", "modality": "voice", "port": worker.port}])
        node.start()
        self.addCleanup(node.stop)
        node_url = urllib.parse.urlsplit(node.base)
        controller_worker = StubVoiceWorker()
        self.addCleanup(controller_worker.stop)
        controller = HubProcess(
            directory, "controller", "controller-shared-token-0123456789",
            workers=[{"id": "voice", "modality": "voice", "port": controller_worker.port}],
            joined=[{"node_id": "node-a", "name": "Node A",
                     "address": f"http://127.0.0.1:{node_url.port}",
                     "studios": [{"id": "voice", "modality": "voice"}]}])
        controller.start()
        self.addCleanup(controller.stop)
        # Adopting the credential the app hands the operator: the controller's FLEET token, not its
        # Hub token. The distinction is the whole point - it is the value the controller presents
        # back to this Mac, and it is narrow, so a node's owner cannot administer the controller.
        controller_fleet = (controller.data / ".fleet_token").read_text().strip()
        self.assertNotEqual(controller_fleet, controller.token,
                            "the credential a node adopts must not be the controller's own token")
        status, adopted, _ = node.post_json("/api/hub/fleet/adopt",
                                            {"token": controller_fleet}, token=node.token)
        self.assertEqual(status, 200, adopted)
        # A node that has registered is not yet a node the controller will push to: the peer
        # status poll has to see it first, or the fan-out correctly reports it as offline.
        deadline = time.time() + 45
        while time.time() < deadline:
            status, listing, _ = controller.request("/api/hub/shared-voices", token=controller.token)
            if status == 200 and any(row["machine"] == "node-a" and row["reachable"]
                                     for row in listing.get("targets", [])):
                break
            time.sleep(0.5)
        else:
            self.fail(f"the controller never saw the joined node as reachable: "
                      f"status={status}, targets={listing.get('targets', [])!r}, "
                      f"node requests={worker.seen[-12:]!r}")
        return worker, controller_worker, controller

    @staticmethod
    def _push(hub, *, audio=VOICE_WAV, transcript=TRANSCRIPT, segments=SEGMENTS,
              voice_id="", wait="true"):
        digest = sha256(audio).hexdigest()
        segments = [] if segments is None else segments
        return hub.post_multipart(
            f"/api/hub/shared-voices?wait={wait}",
            {"name": "Todd", "language": "en", "gender": "m", "license": "self-owned",
             "transcript": transcript, "transcript_segments": json.dumps(segments),
             "voice_id": voice_id or digest[:12], "permission_acknowledged": "true"},
            {"audio": ("reference.wav", audio, "audio/wav")}, token=hub.token)

    def test_a_pushed_voice_arrives_on_the_node_with_its_timings(self) -> None:
        worker, controller_worker, controller = self._fleet()
        expected_id = sha256(VOICE_WAV).hexdigest()[:12]

        status, pushed, _ = self._push(controller)
        self.assertEqual(status, 201, pushed)
        self.assertEqual(pushed["status"], "created")
        self.assertEqual(pushed["voice"]["id"], expected_id,
                         "a caller that names the clip by its hash keeps that id")
        self.assertTrue(pushed["voice"]["has_segments"])
        self.assertEqual(pushed["voice"]["segment_count"], 2)
        self.assertNotIn("transcript_segments", pushed["voice"],
                         "status reads must not carry every voice's timings")

        # The node's own Voice studio got the clip, the transcript and the timings.
        self.assertEqual(len(worker.fleet_syncs), 1, f"node saw: {worker.seen[:8]}")
        delivered = worker.fleet_syncs[0]
        self.assertEqual(delivered["id"], expected_id)
        self.assertEqual(delivered["segments"], self.SEGMENTS)
        self.assertEqual(delivered["transcript"], self.TRANSCRIPT)
        self.assertEqual(delivered["clip"], VOICE_WAV)
        # And the controller's own studio got it too: the fleet includes the Mac you are on.
        self.assertEqual(len(controller_worker.fleet_syncs), 1)

        # Every target is reported, which is what lets the gateway tell the truth per location.
        by_machine = {row["machine"]: row for row in pushed["voice"]["targets"]}
        self.assertEqual(sorted(by_machine), ["local", "node-a"])
        self.assertEqual(by_machine["node-a"]["status"], "synced")
        self.assertEqual(by_machine["node-a"]["segments"], 2)

    def test_re_pushing_the_same_clip_refreshes_it_rather_than_adding_one(self) -> None:
        worker, _controller_worker, controller = self._fleet()
        first_status, first, _ = self._push(controller)
        second_status, second, _ = self._push(controller)
        self.assertEqual(first_status, 201, first)
        self.assertEqual(second_status, 200, second)
        self.assertFalse(second["created"])
        self.assertEqual(second["voice"]["id"], first["voice"]["id"])
        status, listing, _ = controller.request("/api/hub/shared-voices", token=controller.token)
        self.assertEqual(status, 200, listing)
        self.assertEqual(len(listing["voices"]), 1, "a re-push must not fork the voice")
        # The node is told again - that is how a node that missed the first push catches up - and
        # the install is idempotent, so the same id and bytes come back as "current".
        self.assertEqual([row["remote_action"] for row in second["voice"]["targets"]
                          if row["machine"] == "node-a"], ["current"])

    def test_a_transcript_without_timings_is_refused_before_it_reaches_a_node(self) -> None:
        """The whole point: a voice that would install everywhere and clone nowhere is not stored."""
        worker, _controller_worker, controller = self._fleet()
        status, refused, _ = self._push(controller, segments=None)
        self.assertEqual(status, 400, refused)
        self.assertIn("transcript_segments", json.dumps(refused))
        self.assertEqual(worker.fleet_syncs, [], "nothing may be pushed")
        status, listing, _ = controller.request("/api/hub/shared-voices", token=controller.token)
        self.assertEqual(listing["voices"], [])

    def test_removing_a_voice_tombstones_it_across_the_fleet(self) -> None:
        worker, controller_worker, controller = self._fleet()
        _status, pushed, _ = self._push(controller)
        voice_id = pushed["voice"]["id"]

        status, deleted, _ = controller.request(f"/api/hub/shared-voices/{voice_id}",
                                                method="DELETE", token=controller.token)
        self.assertEqual(status, 200, deleted)
        self.assertEqual(deleted["deletion"]["id"], voice_id)
        self.assertEqual(deleted["deletion"]["sync"]["deleted"], 2,
                         "both the controller's own studio and the node must drop it")
        self.assertEqual(worker.voice_deletes, [voice_id])
        self.assertEqual(controller_worker.voice_deletes, [voice_id])
        status, listing, _ = controller.request("/api/hub/shared-voices", token=controller.token)
        self.assertEqual(listing["voices"], [], "the voice itself is gone")
        # A finished removal is not an outstanding one, so the listing has nothing left to report.
        self.assertEqual(listing["deletions"], [])

    def test_a_node_that_drops_the_timings_is_reported_as_a_failure(self) -> None:
        """A node that answers 200 after losing the timings must not read as synchronized.

        Nothing else catches this shape: the clip and the transcript are both there, the node is
        healthy, and the refusal only appears when somebody tries to clone with that voice.
        """
        worker, _controller_worker, controller = self._fleet()
        # The exact behaviour of a worker that ignores the field: it accepts the voice, keeps the
        # clip and the transcript, and reports that it stored no timings.
        worker.drop_timings = True
        status, pushed, _ = self._push(controller)
        self.assertEqual(status, 201, pushed)
        by_machine = {row["machine"]: row for row in pushed["voice"]["targets"]}
        self.assertEqual(by_machine["node-a"]["status"], "failed")
        self.assertIn("timings", by_machine["node-a"]["message"])
        self.assertEqual(worker.fleet_syncs[0]["segments"], self.SEGMENTS,
                         "the controller sent them; the node is what dropped them")

    def test_a_credential_this_controller_does_not_know_is_refused(self) -> None:
        """Pushing a voice is fleet administration, and only a credential this Mac issued may do it.

        Note what this does *not* claim: with one token per controller - the owner's decision - a
        node holds the same value the controller shows, so a node's owner can administer the
        controller. The allowlist narrows what the controller may do to a node, not the reverse.
        """
        _worker, _controller_worker, controller = self._fleet()
        digest = sha256(VOICE_WAV).hexdigest()
        status, refused, _ = controller.post_multipart(
            "/api/hub/shared-voices",
            {"name": "Todd", "language": "en", "gender": "m", "license": "self-owned",
             "transcript": self.TRANSCRIPT, "transcript_segments": json.dumps(self.SEGMENTS),
             "voice_id": digest[:12], "permission_acknowledged": "true"},
            {"audio": ("reference.wav", VOICE_WAV, "audio/wav")},
            token="a-credential-this-mac-never-issued-0123456789")
        self.assertIn(status, (401, 403), refused)


class FleetNodeDeathTests(unittest.TestCase):
    """A node that stops answering must not leave a job waiting on it."""

    def test_a_node_that_dies_mid_job_requeues_it_rather_than_waiting(self) -> None:
        worker = StubImageWorker(machine="node-a", hold=True)
        temporary = tempfile.TemporaryDirectory(prefix="studiohub-death-")
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        node = HubProcess(directory, "node", "node-fixture-token-0123456789ab",
                          workers=[{"id": "image", "modality": "image", "port": worker.port}])
        node.start()
        self.addCleanup(node.stop)
        node_url = urllib.parse.urlsplit(node.base)
        manifests, library = tiny_flux_install(directory / "controller-models")
        # The controller has no model of its own, so the node is the only place this can run.
        controller_worker = StubImageWorker(catalog=False)
        self.addCleanup(controller_worker.stop)
        controller = HubProcess(
            directory, "controller", "controller-fixture-token-0123456789",
            workers=[{"id": "image", "modality": "image", "port": controller_worker.port}],
            env={"STUDIOHUB_MODEL_MANIFESTS": str(manifests),
                 "STUDIOHUB_MODELS_DIR": str(library)},
            joined=[{"node_id": "node-a", "name": "Node A",
                     "address": f"http://127.0.0.1:{node_url.port}",
                     "studios": [{"id": "image", "modality": "image"}]}])
        controller.start()
        self.addCleanup(controller.stop)
        fleet = (controller.data / ".fleet_token").read_text().strip()
        status, adopted, _ = node.post_json("/api/hub/fleet/adopt",
                                            {"token": fleet}, token=node.token)
        self.assertEqual(status, 200, adopted)

        status, submitted, _ = controller.post_json(
            "/api/hub/jobs",
            {"modality": "image", "model": FLUX_MODEL_ID,
             "clientRequestId": "fleet-death-1",
             "items": [{"prompt": "a quiet harbour", "params": {}}]},
            token=controller.token)
        self.assertEqual(status, 200, submitted)
        batch = submitted["batch_id"]

        deadline = time.time() + 60
        while time.time() < deadline and not worker.submissions:
            time.sleep(0.5)
        self.assertTrue(worker.submissions, "the job never reached the node")

        # The node stops answering with the job still running - the case the owner described:
        # the Mac is gone, and whatever it was doing died with it.
        worker.stop()

        # The item must come back to the queue rather than sit waiting on a machine that is
        # never coming back, and it must not need a person to unstick it.
        deadline = time.time() + 120
        state = ""
        while time.time() < deadline:
            state = self.death_state(controller, batch)
            if "'queued'" in state or "uncertain" in state:
                break
            time.sleep(2)
        self.assertNotIn("uncertain", state,
                         "a dead node must be regenerated, not reconciled: " + state)
        self.assertIn("'queued'", state,
                      "a dead node must put the job back in the queue: " + state)

    @staticmethod
    def death_state(controller, batch: str) -> str:
        status, payload, _ = controller.request(f"/api/hub/jobs/{batch}", token=controller.token)
        if status != 200:
            return f"(unreadable: {status})"
        items = payload.get("items") or []
        return (f"note={payload.get('governor_note')!r} "
                f"states={[i.get('state') for i in items]!r} "
                f"errors={[i.get('error') for i in items]!r}")


class FleetPinTests(unittest.TestCase):
    """Automatic by default, but the operator can name the Mac when it matters."""

    def _fleet(self, directory: Path, worker, controller_worker):
        node = HubProcess(directory, "node", "node-fixture-token-0123456789ab",
                          workers=[{"id": "image", "modality": "image", "port": worker.port}])
        node.start()
        self.addCleanup(node.stop)
        node_url = urllib.parse.urlsplit(node.base)
        manifests, library = tiny_flux_install(directory / "controller-models")
        controller = HubProcess(
            directory, "controller", "controller-fixture-token-0123456789",
            workers=[{"id": "image", "modality": "image", "port": controller_worker.port}],
            env={"STUDIOHUB_MODEL_MANIFESTS": str(manifests),
                 "STUDIOHUB_MODELS_DIR": str(library)},
            joined=[{"node_id": "node-a", "name": "Node A",
                     "address": f"http://127.0.0.1:{node_url.port}",
                     "studios": [{"id": "image", "modality": "image"}]}])
        controller.start()
        self.addCleanup(controller.stop)
        fleet = (controller.data / ".fleet_token").read_text().strip()
        status, adopted, _ = node.post_json("/api/hub/fleet/adopt",
                                            {"token": fleet}, token=node.token)
        self.assertEqual(status, 200, adopted)
        return node, controller

    def test_a_machine_pin_beats_the_automatic_choice(self) -> None:
        # Both Macs have the model, so only the pin can decide where this goes.
        worker = StubImageWorker(machine="node-a", hold=True)
        self.addCleanup(worker.stop)
        controller_worker = StubImageWorker(machine="local", hold=True)
        self.addCleanup(controller_worker.stop)
        temporary = tempfile.TemporaryDirectory(prefix="studiohub-pin-")
        self.addCleanup(temporary.cleanup)
        node, controller = self._fleet(Path(temporary.name), worker, controller_worker)

        status, submitted, _ = controller.post_json(
            "/api/hub/jobs",
            {"modality": "image", "model": FLUX_MODEL_ID,
             "clientRequestId": "fleet-pin-1", "routing": "machine:node-a",
             "items": [{"prompt": "a quiet harbour", "params": {}}]},
            token=controller.token)
        self.assertEqual(status, 200, submitted)
        batch = submitted["batch_id"]

        deadline = time.time() + 60
        while time.time() < deadline and not worker.submissions:
            time.sleep(0.5)
        self.assertTrue(worker.submissions,
                        "a machine pin did not reach the named Mac; "
                        f"job: {self.pin_state(controller, batch)}")
        self.assertEqual(controller_worker.submissions, [],
                         "a machine pin must not run on the Mac that was not named")

    def test_pinning_this_mac_keeps_the_work_here(self) -> None:
        worker = StubImageWorker(machine="node-a", hold=True)
        self.addCleanup(worker.stop)
        controller_worker = StubImageWorker(machine="local", hold=True)
        self.addCleanup(controller_worker.stop)
        temporary = tempfile.TemporaryDirectory(prefix="studiohub-pin-local-")
        self.addCleanup(temporary.cleanup)
        node, controller = self._fleet(Path(temporary.name), worker, controller_worker)

        status, submitted, _ = controller.post_json(
            "/api/hub/jobs",
            {"modality": "image", "model": FLUX_MODEL_ID,
             "clientRequestId": "fleet-pin-2", "routing": "machine:local",
             "items": [{"prompt": "a quiet harbour", "params": {}}]},
            token=controller.token)
        self.assertEqual(status, 200, submitted)
        batch = submitted["batch_id"]

        deadline = time.time() + 60
        while time.time() < deadline and not controller_worker.submissions:
            time.sleep(0.5)
        self.assertTrue(controller_worker.submissions,
                        f"pinning this Mac did not keep the work here; "
                        f"job: {self.pin_state(controller, batch)}")
        self.assertEqual(worker.submissions, [], "pinning this Mac must not use a node")

    def test_pinning_a_machine_that_is_not_here_is_refused_with_the_list(self) -> None:
        worker = StubImageWorker(machine="node-a", hold=True)
        self.addCleanup(worker.stop)
        controller_worker = StubImageWorker(machine="local", hold=True)
        self.addCleanup(controller_worker.stop)
        temporary = tempfile.TemporaryDirectory(prefix="studiohub-pin-bad-")
        self.addCleanup(temporary.cleanup)
        node, controller = self._fleet(Path(temporary.name), worker, controller_worker)

        status, refused, _ = controller.post_json(
            "/api/hub/jobs",
            {"modality": "image", "model": FLUX_MODEL_ID,
             "clientRequestId": "fleet-pin-3", "routing": "machine:not-a-mac",
             "items": [{"prompt": "a quiet harbour", "params": {}}]},
            token=controller.token)
        # Refused at submission, naming what does exist, rather than queued forever.
        self.assertEqual(status, 400, refused)
        detail = json.dumps(refused)
        self.assertIn("not-a-mac", detail)
        self.assertIn("node-a", detail)

    @staticmethod
    def pin_state(controller, batch: str) -> str:
        status, payload, _ = controller.request(f"/api/hub/jobs/{batch}", token=controller.token)
        if status != 200:
            return f"(unreadable: {status})"
        return (f"note={payload.get('governor_note')!r} "
                f"items={payload.get('items')!r}")


class FleetCancelTests(unittest.TestCase):
    """Stopping work on another Mac, from the Mac that asked for it."""

    def test_cancelling_on_the_controller_stops_the_node(self) -> None:
        worker = StubImageWorker(machine="node-a", hold=True)
        self.addCleanup(worker.stop)
        temporary = tempfile.TemporaryDirectory(prefix="studiohub-cancel-")
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        node = HubProcess(directory, "node", "node-fixture-token-0123456789ab",
                          workers=[{"id": "image", "modality": "image", "port": worker.port}])
        node.start()
        self.addCleanup(node.stop)
        node_url = urllib.parse.urlsplit(node.base)
        manifests, library = tiny_flux_install(directory / "controller-models")
        controller_worker = StubImageWorker(catalog=False)
        self.addCleanup(controller_worker.stop)
        controller = HubProcess(
            directory, "controller", "controller-fixture-token-0123456789",
            workers=[{"id": "image", "modality": "image", "port": controller_worker.port}],
            env={"STUDIOHUB_MODEL_MANIFESTS": str(manifests),
                 "STUDIOHUB_MODELS_DIR": str(library)},
            joined=[{"node_id": "node-a", "name": "Node A",
                     "address": f"http://127.0.0.1:{node_url.port}",
                     "studios": [{"id": "image", "modality": "image"}]}])
        controller.start()
        self.addCleanup(controller.stop)
        controller_fleet = (controller.data / ".fleet_token").read_text().strip()
        status, adopted, _ = node.post_json("/api/hub/fleet/adopt",
                                            {"token": controller_fleet}, token=node.token)
        self.assertEqual(status, 200, adopted)

        status, submitted, _ = controller.post_json(
            "/api/hub/jobs",
            {"modality": "image", "model": FLUX_MODEL_ID,
             "clientRequestId": "fleet-cancel-1",
             "items": [{"prompt": "a quiet harbour", "params": {}}]},
            token=controller.token)
        self.assertEqual(status, 200, submitted)
        batch = submitted["batch_id"]

        deadline = time.time() + 60
        while time.time() < deadline and not worker.submissions:
            time.sleep(0.5)
        self.assertTrue(worker.submissions, "the job never reached the node to be cancelled")

        status, _, _ = controller.request(f"/api/hub/jobs/{batch}", method="DELETE",
                                          token=controller.token)
        self.assertIn(status, (200, 202), f"the controller refused the cancel: {status}")

        deadline = time.time() + 45
        while time.time() < deadline and not worker.cancels:
            time.sleep(0.5)
        self.assertTrue(worker.cancels,
                        "the node kept working after the controller was told to stop it; "
                        f"job: {self.cancel_state(controller, batch)}")
        # And the node's own credential, not the controller's.
        self.assertEqual(worker.cancels[0]["token"], node.token)

    @staticmethod
    def cancel_state(controller, batch: str) -> str:
        status, payload, _ = controller.request(f"/api/hub/jobs/{batch}", token=controller.token)
        if status != 200:
            return f"(unreadable: {status})"
        return (f"note={payload.get('governor_note')!r} "
                f"items={payload.get('items')!r}")


class FleetSubtitleDispatchTests(unittest.TestCase):
    """Subtitle is a different dispatcher from Image, so it needs its own proof."""

    def test_a_subtitle_job_submitted_to_the_controller_runs_on_the_node(self) -> None:
        worker = StubSubtitleWorker(machine="node-a")
        self.addCleanup(worker.stop)
        temporary = tempfile.TemporaryDirectory(prefix="studiohub-subtitle-fleet-")
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        node = HubProcess(directory, "node", "node-fixture-token-0123456789ab",
                          workers=[{"id": "subtitle", "modality": "transcription",
                                    "port": worker.port}])
        node.start()
        self.addCleanup(node.stop)
        node_url = urllib.parse.urlsplit(node.base)

        # The controller needs the model installed too: its packaged catalog is the boundary it
        # accepts submissions against, and this is how the fleet is actually set up.
        manifests, library = tiny_whisper_install(directory / "controller-models")
        # Its own Subtitle studio has no model, so the only place this work can run is the node.
        controller_worker = StubSubtitleWorker(catalog=False)
        self.addCleanup(controller_worker.stop)
        controller = HubProcess(
            directory, "controller", "controller-fixture-token-0123456789",
            workers=[{"id": "subtitle", "modality": "transcription",
                      "port": controller_worker.port}],
            env={"STUDIOHUB_MODEL_MANIFESTS": str(manifests),
                 "STUDIOHUB_MODELS_DIR": str(library)},
            joined=[{"node_id": "node-a", "name": "Node A",
                     "address": f"http://127.0.0.1:{node_url.port}",
                     "studios": [{"id": "subtitle", "modality": "transcription"}]}])
        controller.start()
        self.addCleanup(controller.stop)
        controller_fleet = (controller.data / ".fleet_token").read_text().strip()
        status, adopted, _ = node.post_json("/api/hub/fleet/adopt",
                                            {"token": controller_fleet}, token=node.token)
        self.assertEqual(status, 200, adopted)

        audio = b"RIFF" + b"\0" * 4096
        status, submitted, _ = controller.post_multipart(
            "/api/hub/transcription/jobs",
            {"model": WHISPER_MODEL_ID, "item_ids": "fleet-subtitle-1", "episode": "fleet"},
            {"files": ("clip.wav", audio, "audio/wav")},
            token=controller.token)
        self.assertEqual(status, 200, submitted)
        batch = submitted.get("batch_id")
        self.assertIsNotNone(batch, submitted)

        deadline = time.time() + 60
        while time.time() < deadline and not worker.transcriptions:
            time.sleep(0.5)
        self.assertTrue(worker.transcriptions,
                        "the controller never sent the subtitle job to the node; "
                        f"job: {self.subtitle_state(controller, batch)}; "
                        f"node saw: {worker.seen[:10]}")
        # Again the node's own credential, never the controller's.
        self.assertEqual(worker.transcriptions[0]["token"], node.token)

        # And the transcript comes back to the operator through the controller.
        deadline = time.time() + 45
        transcript = ""
        while time.time() < deadline:
            status, body = controller.artifact(
                f"/api/hub/transcription/jobs/{batch}/items/0/artifact",
                token=controller.token)
            if status == 200:
                transcript = body.decode("utf-8", "replace")
                break
            time.sleep(0.5)
        self.assertIn("the quick brown fox", transcript,
                      "the controller never served the transcript; "
                      f"job: {self.subtitle_state(controller, batch)}")

    @staticmethod
    def subtitle_state(controller, batch: str) -> str:
        status, payload, _ = controller.request(
            f"/api/hub/transcription/jobs/{batch}", token=controller.token)
        if status != 200:
            return f"(unreadable: {status})"
        return (f"status={payload.get('status')!r} note={payload.get('governor_note')!r} "
                f"items={payload.get('items')!r}")


class ControllerRegistryTests(unittest.TestCase):
    """A controller must hold a joined node as something it can dispatch to."""

    def test_a_joined_node_appears_as_remote_studios(self) -> None:
        with tempfile.TemporaryDirectory(prefix="studiohub-controller-") as root:
            directory = Path(root)
            hub = HubProcess(directory, "controller", "controller-fixture-token-0123456789",
                             joined=[{"node_id": "mac-mini-2", "name": "Studio 2",
                                      "address": "http://192.168.1.21:47873",
                                      "studios": [{"id": "image", "modality": "image"},
                                                  {"id": "voice", "modality": "voice"},
                                                  {"id": "subtitle",
                                                   "modality": "transcription"}]}])
            hub.start()
            try:
                status, payload, _ = hub.request("/api/hub/workers", token=hub.token)
                self.assertEqual(status, 200)
                rows = {row["id"]: row for row in payload["workers"]}
                # The controller's own worker, plus one entry per studio on the joined node.
                self.assertIn("voice", rows)
                self.assertEqual(rows["voice"]["machine"], "local")
                remote = {key: value for key, value in rows.items() if ":" in key}
                self.assertEqual(sorted(remote),
                                 ["mac-mini-2:image", "mac-mini-2:subtitle", "mac-mini-2:voice"])
                self.assertEqual(remote["mac-mini-2:image"]["modality"], "image")
                self.assertEqual(remote["mac-mini-2:subtitle"]["modality"], "transcription")
                # The machine identity is the node, not this Mac - that is what routes a
                # dispatch to it over the peer path.
                self.assertEqual(remote["mac-mini-2:voice"]["machine"], "mac-mini-2")
            finally:
                hub.stop()

    def test_the_overview_shows_every_machine_in_one_row(self) -> None:
        """One place to see the fleet: is each Mac there, what is it running, how fast is it."""
        with tempfile.TemporaryDirectory(prefix="studiohub-overview-") as root:
            directory = Path(root)
            hub = HubProcess(directory, "controller", "controller-fixture-token-0123456789",
                             joined=[{"node_id": "mac-mini-2", "name": "Studio 2",
                                      "address": "http://192.168.1.21:47873",
                                      "studios": [{"id": "image", "modality": "image"},
                                                  {"id": "subtitle",
                                                   "modality": "transcription"}]}])
            hub.start()
            try:
                status, payload, _ = hub.request("/api/hub/fleet/overview", token=hub.token)
                self.assertEqual(status, 200)
                rows = {row["machine"]: row for row in payload["machines"]}
                # This Mac is one row, and the joined node is another - one per machine, not
                # one per studio, however many studios each of them runs.
                self.assertEqual(sorted(rows), ["local", "mac-mini-2"])
                self.assertEqual(payload["summary"]["total"], 2)

                here = rows["local"]
                self.assertTrue(here["reachable"])
                status, controller, _ = hub.request("/api/hub/controller", token=hub.token)
                self.assertEqual(status, 200)
                self.assertEqual(here["label"], controller["settings"]["machine_name"])
                self.assertEqual(here["controller_id"], controller["settings"]["controller_id"])
                self.assertTrue(here["version"])
                # The facts that decide whether work can run here at all.
                self.assertIsNotNone(here["disk_free_gb"])
                self.assertIsNotNone(here["memory_gb"])
                self.assertTrue(here["chip"])

                # The node has not reported in, and must not be shown as if it had.
                there = rows["mac-mini-2"]
                self.assertFalse(there["reachable"])
                self.assertIsNone(there["disk_free_gb"])
                self.assertEqual(there["jobs"], 0)
                self.assertIsNone(there["average_seconds"],
                                  "a machine with no measured work must not report a zero")
            finally:
                hub.stop()

    def test_a_node_with_no_studios_is_not_dispatchable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="studiohub-controller-") as root:
            directory = Path(root)
            hub = HubProcess(directory, "controller", "controller-fixture-token-0123456789",
                             joined=[{"node_id": "empty", "name": "Empty",
                                      "address": "http://192.168.1.22:47873", "studios": []}])
            hub.start()
            try:
                _, payload, _ = hub.request("/api/hub/workers", token=hub.token)
                self.assertEqual([row["id"] for row in payload["workers"] if ":" in row["id"]], [])
            finally:
                hub.stop()


class IdentityScopeTests(unittest.TestCase):
    """ST-10.PORTABLE/ST-10.3: the identity a controller stores is not the address.

    Each case owns its processes so it can stop, change one thing, and start the Hub again
    against the same state root — which is the only way to prove stability rather than
    assume it.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-identity-")
        self.root = Path(self.temporary.name)
        self.hub: HubProcess | None = None
        self.addCleanup(self.temporary.cleanup)

    def tearDown(self) -> None:
        if self.hub is not None:
            self.hub.stop()

    def start(self, name: str, token: str, data=None) -> HubProcess:
        hub = HubProcess(self.root, name, token, data=data)
        hub.start()
        return hub

    def test_identity_survives_an_address_change(self) -> None:
        token = "identity-fixture-token-0123456789ab"
        first = self.start("first", token)
        _, before, _ = first.request("/api/hub/controller", token=token)
        first.stop()

        second = self.start("second", token, data=first.data)
        self.hub = second
        self.assertNotEqual(second.base, first.base, "the fixture must bind a new address")
        _, after, _ = second.request("/api/hub/controller", token=token)

        for field in ("site_id", "controller_id", "machine_name"):
            self.assertEqual(before["settings"][field], after["settings"][field])
        self.assertEqual(before["capacity"]["site_id"], after["capacity"]["site_id"])

    def test_rotating_the_credential_refuses_the_old_one_and_keeps_the_state(self) -> None:
        original = "rotation-fixture-token-0123456789"
        rotated = "rotation-fixture-token-9876543210"
        token_file = self.root / "first" / "state" / ".hub_token"

        first = self.start("first", original)
        _, before, _ = first.request("/api/hub/controller", token=original)
        _, history_before, _ = first.request("/api/hub/jobs", token=original)
        first.stop()

        # Rotate only the credential, in place; the state root is untouched.
        token_file.write_text(rotated + "\n")
        os.chmod(token_file, 0o600)
        self.hub = self.start("second", rotated, data=first.data)

        status, _, _ = self.hub.request("/api/hub/controller", token=original)
        self.assertEqual(status, 401, "a rotated-away credential must stop working")
        status, after, _ = self.hub.request("/api/hub/controller", token=rotated)
        self.assertEqual(status, 200)
        _, history_after, _ = self.hub.request("/api/hub/jobs", token=rotated)

        # Losing the credential loses nothing else: identity and the job ledger survive.
        self.assertEqual(before["settings"]["site_id"], after["settings"]["site_id"])
        self.assertEqual(before["settings"]["controller_id"], after["settings"]["controller_id"])
        self.assertEqual(history_before, history_after,
                         "rotating a credential must not disturb accepted work")


def reachable_second_address() -> str | None:
    """A real non-loopback IPv4 address on this Mac, if it has one."""
    import socket as socket_module
    probe = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_DGRAM)
    try:
        # No packets are sent; this only asks the routing table which local address it would use.
        probe.connect(("8.8.8.8", 53))
        address = probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()
    return address if address and not address.startswith("127.") else None


class FleetSubtitlePinTests(unittest.TestCase):
    """A Subtitle machine pin has to mean the same thing it means for Image and Voice.

    Subtitle has its own dispatcher, so it needed its own routing check: a pin accepted at the
    door but ignored by the scheduler would look like it worked while the work ran elsewhere.
    """

    def test_a_subtitle_pin_names_a_machine_and_a_bad_pin_is_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="studiohub-subtitle-pin-") as root:
            directory = Path(root)
            # The controller has to accept the model before it will route it anywhere, so the
            # fleet has the model installed on the controller exactly as it would in reality.
            manifests, library = tiny_whisper_install(directory / "controller-models")
            hub = HubProcess(directory, "controller", "controller-fixture-token-0123456789",
                             workers=[{"id": "subtitle", "modality": "transcription",
                                       "port": free_port()}],
                             env={"STUDIOHUB_MODEL_MANIFESTS": str(manifests),
                                  "STUDIOHUB_MODELS_DIR": str(library)},
                             joined=[{"node_id": "node-a", "name": "Node A",
                                      "address": "http://192.168.1.21:47873",
                                      "studios": [{"id": "subtitle",
                                                   "modality": "transcription"}]}])
            hub.start()
            try:
                audio = b"RIFF" + b"\0" * 4096
                status, payload, _ = hub.post_multipart(
                    "/api/hub/transcription/jobs",
                    {"model": WHISPER_MODEL_ID, "item_ids": "pin-1", "episode": "pin",
                     "routing": "machine:node-a"},
                    {"files": ("clip.wav", audio, "audio/wav")},
                    token=hub.token)
                self.assertEqual(status, 200, payload)
                status, batch, _ = hub.request(
                    f"/api/hub/transcription/jobs/{payload['batch_id']}", token=hub.token)
                self.assertEqual(status, 200, batch)
                # The pin is remembered on the batch, because the dispatcher chooses a Studio
                # later - at dispatch time, not at the door.
                self.assertEqual(batch.get("routing"), "machine:node-a", batch)

                # A machine that is not in the fleet is refused at submission, exactly as the
                # Image and Voice paths refuse it, rather than queued forever.
                status, refused, _ = hub.post_multipart(
                    "/api/hub/transcription/jobs",
                    {"model": WHISPER_MODEL_ID, "item_ids": "pin-2", "episode": "pin2",
                     "routing": "machine:not-a-mac"},
                    {"files": ("clip.wav", audio, "audio/wav")},
                    token=hub.token)
                self.assertEqual(status, 400, refused)
                self.assertIn("not-a-mac", str(refused))

                # And a value that is not a routing instruction at all is a plain error.
                status, refused, _ = hub.post_multipart(
                    "/api/hub/transcription/jobs",
                    {"model": WHISPER_MODEL_ID, "item_ids": "pin-3", "episode": "pin3",
                     "routing": "somewhere-nice"},
                    {"files": ("clip.wav", audio, "audio/wav")},
                    token=hub.token)
                self.assertEqual(status, 400, refused)
                self.assertIn("routing must be", str(refused))
            finally:
                hub.stop()


class VoiceLibraryTests(unittest.TestCase):
    """The voice library as the app uses it: add, list, edit, play, remove.

    The library itself predates this app - it lives in the Voice worker and predates the
    native shell - and the app is only a window onto it. What these tests pin is the seam:
    that the Hub proxies the voice routes with the Hub token, that a clip uploaded through
    the Hub is what the list returns, and that editing changes metadata without touching the
    stored audio.
    """

    def setUp(self) -> None:
        self.worker = StubVoiceWorker()
        self.addCleanup(self.worker.stop)
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-voices-")
        self.addCleanup(self.temporary.cleanup)
        self.hub = HubProcess(
            Path(self.temporary.name), "controller", "voice-library-token-0123456789",
            workers=[{"id": "voice", "modality": "voice", "port": self.worker.port}])
        self.hub.start()
        self.addCleanup(self.hub.stop)

    def test_the_app_can_add_list_edit_and_remove_a_voice(self) -> None:
        # 1. Nothing saved yet, and the route answers rather than 404s: an empty library is a
        #    valid state, not an error the operator has to interpret.
        status, listing, _ = self.hub.request(
            "/studio/voice/api/voices", token=self.hub.token)
        self.assertEqual(status, 200, listing)
        self.assertEqual(listing.get("voices"), [])

        # 2. Add a voice with its clip and its transcript. The transcript is not optional for
        #    a clone: without it the engine cannot align the audio to words.
        audio = b"RIFF" + b"\0" * 2048
        status, added, _ = self.hub.post_multipart(
            "/studio/voice/api/voices",
            {"name": "Narrator", "language": "en", "gender": "f", "license": "self-owned",
             "notes": "studio booth", "transcript": "The exact words in the clip",
             "permission_acknowledged": "true"},
            {"audio": ("narrator.wav", audio, "audio/wav")},
            token=self.hub.token)
        self.assertEqual(status, 201, added)
        voice = added["voice"]
        self.assertEqual(voice["name"], "Narrator")
        self.assertEqual(voice["transcript"], "The exact words in the clip")
        self.assertTrue(voice["has_transcript"])
        self.assertEqual(voice["license"], "self-owned")
        # The clip was uploaded, not referenced: the worker received the multipart body with
        # the file part in it, under the field name the voice route expects.
        self.assertTrue(self.worker.voices)
        self.assertIn('name="audio"; filename="narrator.wav"', self.worker.raw_bodies[-1])
        self.assertIn('name="transcript"', self.worker.raw_bodies[-1])

        # 3. It is in the list the app reads.
        status, listing, _ = self.hub.request(
            "/studio/voice/api/voices", token=self.hub.token)
        self.assertEqual(status, 200)
        self.assertEqual([row["name"] for row in listing["voices"]], ["Narrator"])

        # 4. Play: the clip comes back through the Hub as bytes for the app to hear. Read with
        #    the binary reader - the app uses its own binary path here, and parsing a WAV as
        #    JSON fails on its magic number rather than on anything real.
        # The worker reports `/api/voices/<id>/audio`; the app reaches it through the Hub's
        # `/studio/voice` proxy, and so does this test - asking the Hub for the worker path
        # directly is the mistake that produced a 404 here.
        status, payload = self.hub.artifact(
            f"/studio/voice{voice['audio_url']}", token=self.hub.token)
        self.assertEqual(status, 200)
        self.assertEqual(payload, audio)

        # 5. Edit changes metadata only. The route is PATCH, which the Hub proxy has to carry.
        status, edited, _ = self.hub.request(
            f"/studio/voice/api/voices/{voice['id']}", method="PATCH", token=self.hub.token,
            headers={"Content-Type": "application/json"},
            body=json.dumps({"name": "Narrator Two", "language": "en", "gender": "f",
                             "license": "self-owned", "notes": "",
                             "transcript": "Corrected words in the clip"}).encode())
        self.assertEqual(status, 200, edited)
        self.assertEqual(edited["voice"]["name"], "Narrator Two")
        self.assertEqual(edited["voice"]["transcript"], "Corrected words in the clip")
        self.assertEqual(self.worker.voice_edits[-1][0],
                         f"/api/voices/{voice['id']}")

        # 6. Remove it.
        status, removed, _ = self.hub.request(
            f"/studio/voice/api/voices/{voice['id']}", method="DELETE", token=self.hub.token)
        self.assertEqual(status, 200, removed)
        self.assertEqual(self.worker.voice_deletes, [voice["id"]])
        status, listing, _ = self.hub.request(
            "/studio/voice/api/voices", token=self.hub.token)
        self.assertEqual(listing["voices"], [])

    def test_a_fleet_credential_may_read_voices_but_not_create_them(self) -> None:
        """Reading the library is how a controller shows what a node can clone; writing is
        the node owner's business, like every other part of that Mac's state."""
        from components.hub.backend.auth import fleet_path_allowed
        self.assertTrue(fleet_path_allowed("GET", "/studio/voice/api/voices"))
        self.assertTrue(fleet_path_allowed("PATCH", "/studio/voice/api/voices/voice-1"))
        self.assertFalse(fleet_path_allowed("POST", "/studio/voice/api/voices"))
        self.assertFalse(fleet_path_allowed("DELETE", "/studio/voice/api/voices/voice-1"))


class MultiAddressTests(unittest.TestCase):
    """A Mac must be able to serve its LAN and its Tailscale address at the same time.

    Binding one address was a false choice: the operator would have to pick between a local
    network and the tailnet. Both can be served at once, and loopback is always among them so
    the app on that Mac can always reach its own Hub.
    """

    def test_the_hub_answers_on_every_address_it_was_given(self) -> None:
        second = reachable_second_address()
        if second is None:
            self.skipTest("this Mac has no non-loopback IPv4 address to bind")
        with tempfile.TemporaryDirectory(prefix="studiohub-multibind-") as root:
            directory = Path(root)
            hub = HubProcess(directory, "multi", "multi-address-fixture-token-1234",
                             host=f"127.0.0.1,{second}")
            hub.start()
            try:
                # The ready record must still point at loopback, which is the address the app
                # on this Mac uses regardless of what else was bound.
                self.assertIn("127.0.0.1", hub.base)
                status, version, _ = hub.request("/api/version", token=hub.token)
                self.assertEqual(status, 200)
                # And the second address answers too, with the same credential.
                response = urllib.request.urlopen(
                    urllib.request.Request(f"http://{second}:{urllib.parse.urlsplit(hub.base).port}/api/version",
                                           headers={"X-Hub-Token": hub.token}), timeout=15)
                self.assertEqual(response.status, 200)
            finally:
                hub.stop()


class LeavingAndRemovalAreRealTest(unittest.TestCase):
    """A2 — "leave easily" and "simple node removal", both of which failed 100% before.

    "Leave this group" sent `DELETE /api/hub/fleet/nodes/<own id>` with the Fleet credential and
    was refused 403 by the allowlist, so the app reported success *and* told the operator the
    controller still listed the Mac (B2). And a removal on the controller did not stick,
    because the node still held the group credential and re-joined on its next start (B3).
    """

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="studiohub-leave-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _controller(self, *, controller_id: str = "controller-mac",
                    joined: list[dict] | None = None) -> "HubProcess":
        hub = HubProcess(self.root, "controller", token=secrets.token_urlsafe(24),
                         joined=joined or [])
        # The controller's own identity, so "this Mac's own id" is a value the test knows.
        (hub.data / "controller_settings.json").write_text(json.dumps({
            "version": 2, "role": "controller", "site_id": "site-a", "site_name": "Site A",
            "controller_id": controller_id, "database_mode": "off",
            "parent_controller_url": None, "fleet_credential_origin": "minted",
        }))
        os.chmod(hub.data / "controller_settings.json", 0o600)
        hub.start()
        self.addCleanup(hub.stop)
        return hub

    def test_a_node_may_leave_by_removing_itself(self) -> None:
        # **The controller's id and the node's id are deliberately different here.** The first
        # version of this test gave them the same value, so it passed while the real check
        # compared the path id against the *controller's* own identity — and a node leaving its
        # controller was refused 403. Found by a live two-Hub probe, not by that test.
        hub = self._controller(controller_id="controller-mac")
        fleet = (hub.data / ".fleet_token").read_text().strip()
        status, payload, _ = hub.post_json(
            "/api/hub/fleet/nodes",
            {"node_id": "studio-2", "name": "Studio 2", "address": "http://127.0.0.1:9"},
            token=fleet)
        self.assertEqual(status, 201, payload)
        # **The credential comes back once, and only here.**
        credential = payload["credential"]
        self.assertTrue(credential)
        self.assertNotIn(credential,
                         (hub.data / "fleet_nodes.json").read_text(encoding="utf-8"))
        # The group Fleet token cannot remove anything: it identifies nobody, so there is no
        # honest way to tell "this Mac is leaving" from "this Mac is evicting its neighbour".
        status, _, _ = hub.request("/api/hub/fleet/nodes/studio-2", method="DELETE",
                                   token=fleet)
        self.assertEqual(status, 403)
        # Leaving is this Mac's own decision: its own id, with the credential it was issued.
        status, body, _ = hub.request("/api/hub/fleet/nodes/studio-2", method="DELETE",
                                      token=credential)
        self.assertEqual(status, 200, body)
        _, listing, _ = hub.request("/api/hub/fleet/nodes", token=hub.token)
        self.assertEqual(listing["summary"]["total"], 0)
        # **Forgotten, not evicted.** A Mac that chose to leave may join again; only the operator's
        # removal is remembered.
        self.assertEqual(listing["summary"]["removed"], 0)
        status, again, _ = hub.post_json(
            "/api/hub/fleet/nodes",
            {"node_id": "studio-2", "name": "Studio 2", "address": "http://127.0.0.1:9"},
            token=fleet)
        self.assertEqual(status, 201, again)

    def test_a_node_asks_whether_it_is_still_listed_and_a_rename_follows(self) -> None:
        hub = self._controller(controller_id="controller-mac")
        fleet = (hub.data / ".fleet_token").read_text().strip()
        _, joined, _ = hub.post_json(
            "/api/hub/fleet/nodes",
            {"node_id": "mac-1102-x", "name": "mac-1102",
             "address": "http://127.0.0.1:9"}, token=fleet)
        _, other, _ = hub.post_json(
            "/api/hub/fleet/nodes",
            {"node_id": "studio-3", "name": "Studio 3", "address": "http://127.0.0.1:9"},
            token=fleet)
        own = joined["credential"]
        status, body, _ = hub.request("/api/hub/fleet/nodes/mac-1102-x/status", token=own)
        self.assertEqual((status, body["state"]), (200, "member"))
        # A node may not ask about another Mac.
        status, _, _ = hub.request("/api/hub/fleet/nodes/studio-3/status", token=own)
        self.assertEqual(status, 403)
        # Its label reaches the controller's roster when it asks; nobody else may set it.
        hub.request("/api/hub/fleet/nodes/mac-1102-x/status?name=mac-1501", token=own)
        hub.request("/api/hub/fleet/nodes/studio-3/status?name=hijacked", token=fleet)
        _, listing, _ = hub.request("/api/hub/fleet/nodes", token=hub.token)
        names = {row["id"]: row["name"] for row in listing["nodes"]}
        self.assertEqual(names, {"mac-1102-x": "mac-1501", "studio-3": "Studio 3"})
        # The operator removes it: the Mac, holding only the group token now, hears `removed`.
        hub.request("/api/hub/fleet/nodes/mac-1102-x", method="DELETE", token=hub.token)
        status, body, _ = hub.request("/api/hub/fleet/nodes/mac-1102-x/status", token=fleet)
        self.assertEqual((status, body["state"]), (200, "removed"))
        self.assertIn("removed this Mac on", body["detail"])
        status, body, _ = hub.request("/api/hub/fleet/nodes/nobody/status", token=fleet)
        self.assertEqual((status, body["state"]), (200, "unknown"))
        self.assertEqual(other["node"]["id"], "studio-3")

    def test_a_node_may_not_remove_a_different_machine(self) -> None:
        hub = self._controller(controller_id="studio-2")
        fleet = (hub.data / ".fleet_token").read_text().strip()
        hub.post_json("/api/hub/fleet/nodes",
                      {"node_id": "studio-3", "name": "Studio 3",
                       "address": "http://127.0.0.1:9"}, token=fleet)
        status, body, _ = hub.request("/api/hub/fleet/nodes/studio-3", method="DELETE",
                                      token=fleet)
        self.assertEqual(status, 403, body)
        self.assertIn("only the Mac that holds it", str(body))
        # The controller owner still may, because the roster is his.
        status, _, _ = hub.request("/api/hub/fleet/nodes/studio-3", method="DELETE",
                                   token=hub.token)
        self.assertEqual(status, 200)

    def test_a_removed_node_is_refused_by_name_and_can_be_re_admitted(self) -> None:
        hub = self._controller()
        fleet = (hub.data / ".fleet_token").read_text().strip()
        hub.post_json("/api/hub/fleet/nodes",
                      {"node_id": "sold-mac", "name": "Sold Mac",
                       "address": "http://127.0.0.1:9"}, token=fleet)
        status, _, _ = hub.request("/api/hub/fleet/nodes/sold-mac", method="DELETE",
                                   token=hub.token)
        self.assertEqual(status, 200)
        # It does not come back on its own, even holding the group Fleet token — which is
        # exactly what a Mac that was switched off during the removal would do on restart.
        status, refused, _ = hub.post_json(
            "/api/hub/fleet/nodes",
            {"node_id": "sold-mac", "name": "Sold Mac", "address": "http://127.0.0.1:9"},
            token=fleet)
        self.assertEqual(status, 403, refused)
        self.assertIn("removed this Mac on", str(refused))
        self.assertIn("re-admit it from Connections", str(refused))
        # The removal is published, with the date, so the operator can act on it.
        _, listing, _ = hub.request("/api/hub/fleet/nodes", token=hub.token)
        self.assertEqual(listing["summary"]["removed"], 1)
        self.assertEqual(listing["removed"][0]["id"], "sold-mac")
        # Re-admitting is a deliberate owner action, and only then may it join again.
        status, body, _ = hub.post_json("/api/hub/fleet/nodes/sold-mac/readmit", {},
                                        token=hub.token)
        self.assertEqual(status, 200, body)
        status, again, _ = hub.post_json(
            "/api/hub/fleet/nodes",
            {"node_id": "sold-mac", "name": "Sold Mac", "address": "http://127.0.0.1:9"},
            token=fleet)
        self.assertEqual(status, 201, again)
        self.assertTrue(again.get("credential"), "a re-admitted Mac gets a fresh credential")

    def test_a_removed_nodes_own_credential_stops_working(self) -> None:
        hub = self._controller()
        fleet = (hub.data / ".fleet_token").read_text().strip()
        _, joined, _ = hub.post_json(
            "/api/hub/fleet/nodes",
            {"node_id": "sold-mac", "name": "Sold Mac", "address": "http://127.0.0.1:9"},
            token=fleet)
        node_credential = joined["credential"]
        # Its own credential authenticates the routes a node needs ...
        status, _, _ = hub.request("/api/hub/fleet/nodes", token=node_credential)
        self.assertEqual(status, 200)
        # ... and cannot reach the routes the controller's own credential governs.
        status, _, _ = hub.request("/api/hub/settings", token=node_credential)
        self.assertEqual(status, 403)
        hub.request("/api/hub/fleet/nodes/sold-mac", method="DELETE", token=hub.token)
        # Refused, and told why: a bare 401 was indistinguishable from a controller that lost its
        # roster, so the Mac kept showing "connected" after the owner removed it.
        status, body, _ = hub.request("/api/hub/fleet/nodes", token=node_credential)
        self.assertEqual(status, 403, "a revoked node credential must stop authenticating")
        self.assertIn("removed this Mac on", str(body))

    def test_a_node_cannot_re_admit_itself(self) -> None:
        hub = self._controller()
        fleet = (hub.data / ".fleet_token").read_text().strip()
        _, joined, _ = hub.post_json(
            "/api/hub/fleet/nodes",
            {"node_id": "sold-mac", "name": "Sold Mac", "address": "http://127.0.0.1:9"},
            token=fleet)
        hub.request("/api/hub/fleet/nodes/sold-mac", method="DELETE", token=hub.token)
        # A revoked credential is not a credential any more, so it is refused as unauthenticated
        # (401) rather than as a valid-but-forbidden one. Either way it is refused, which is the
        # property that matters: undoing an eviction is never the removed Mac's decision.
        status, _, _ = hub.post_json("/api/hub/fleet/nodes/sold-mac/readmit", {},
                                     token=joined["credential"])
        self.assertIn(status, (401, 403))
        # And the group Fleet token, which is still valid, is refused by the allowlist: re-admit is
        # in neither credential's scope.
        status, _, _ = hub.post_json("/api/hub/fleet/nodes/sold-mac/readmit", {}, token=fleet)
        self.assertEqual(status, 403)
        _, listing, _ = hub.request("/api/hub/fleet/nodes", token=hub.token)
        self.assertEqual(listing["summary"]["removed"], 1, "it is still removed")


class RotateAndHandOverTest(unittest.TestCase):
    """A4 and A3 — the owner's decision A, proved between two real Hubs on this Mac.

    `peers.sync_fleet_token` existed with **no caller and no route** (B5), and a promotion used
    to mint a new Fleet token that left every node holding a dead credential — the only fix
    being a human typing address and token into each of thirteen Macs (B4). These run a real
    controller and a real node, so the numbers are the ones a rollout would produce.
    """

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="studiohub-handover-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _hub(self, name: str, *, controller_id: str | None = None,
             role: str = "standalone") -> "HubProcess":
        hub = HubProcess(self.root, name, token=secrets.token_urlsafe(24))
        (hub.data / "controller_settings.json").write_text(json.dumps({
            "version": 2, "role": role, "site_id": f"site-{name}", "site_name": name,
            "controller_id": controller_id or f"{name}-hub", "database_mode": "off",
            "parent_controller_url": None, "fleet_credential_origin": "minted",
        }))
        os.chmod(hub.data / "controller_settings.json", 0o600)
        hub.start()
        self.addCleanup(hub.stop)
        return hub

    def _join(self, controller: "HubProcess", node: "HubProcess",
              node_id: str) -> dict:
        fleet = (controller.data / ".fleet_token").read_text().strip()
        address = node.base
        # A node is in `remote_studios()` — and therefore in the push — only for the studios it
        # reported at join. Saying it runs voice is what makes it a dispatchable peer.
        status, payload, _ = controller.post_json(
            "/api/hub/fleet/nodes",
            {"node_id": node_id, "name": node_id, "address": address,
             "studios": [{"id": "voice", "modality": "voice"}]}, token=fleet)
        self.assertEqual(status, 201, payload)
        # The node adopts the controller's credential and records whose it is — the same call
        # the controller makes when it rotates or hands over.
        node_fleet = (node.data / ".fleet_token").read_text().strip()
        response = urllib.request.Request(
            f"{node.base}/api/hub/fleet", method="POST",
            data=json.dumps({"token": fleet, "controller_url": address}).encode(),
            headers={"Content-Type": "application/json", "X-Hub-Token": node_fleet})
        with urllib.request.urlopen(response, timeout=15) as answer:
            self.assertEqual(answer.status, 200)
        return payload

    def test_rotating_the_fleet_token_retires_the_old_one_on_every_node(self) -> None:
        controller = self._hub("controller", role="controller")
        node = self._hub("node-b")
        self._join(controller, node, "node-b")
        old = (controller.data / ".fleet_token").read_text().strip()

        status, body, _ = controller.post_json("/api/hub/fleet", {}, token=controller.token)
        self.assertEqual(status, 200, body)
        self.assertTrue(body.get("rotated"))
        self.assertEqual(body["total"], 1, "the one joined node is in the push")
        self.assertEqual(body["verified"], 1, body)
        new = body["token"]
        self.assertNotEqual(new, old)
        self.assertEqual((controller.data / ".fleet_token").read_text().strip(), new)
        # The node holds the new credential and no longer accepts the old one.
        self.assertEqual((node.data / ".fleet_token").read_text().strip(), new)
        for token, expected in ((old, 401), (new, 200)):
            request = urllib.request.Request(
                f"{node.base}/api/hub/resources?local_only=true",
                headers={"X-Hub-Token": token})
            try:
                with urllib.request.urlopen(request, timeout=15) as answer:
                    self.assertEqual(answer.status, expected)
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, expected)

    def test_a_node_that_was_off_for_a_rotation_is_named_and_not_stranded(self) -> None:
        controller = self._hub("controller", role="controller")
        fleet = (controller.data / ".fleet_token").read_text().strip()
        controller.post_json(
            "/api/hub/fleet/nodes",
            {"node_id": "asleep-mac", "name": "Asleep", "address": "http://127.0.0.1:9",
             "studios": [{"id": "voice", "modality": "voice"}]}, token=fleet)
        status, body, _ = controller.post_json("/api/hub/fleet", {}, token=controller.token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["verified"], 0)
        new = body["token"]
        # The controller switched over, but still holds what the sleeping Mac holds.
        previous = json.loads((controller.data / ".fleet_token_previous").read_text())
        self.assertEqual(previous["nodes"]["asleep-mac"]["token"], fleet)
        # The app can say how many nodes are still on the previous token, and which.
        _, listing, _ = controller.request("/api/hub/fleet/nodes", token=controller.token)
        self.assertEqual(listing["summary"]["on_previous_token"], 1)
        self.assertTrue(listing["nodes"][0]["on_previous_token"])
        # The old token is a node-scope key for the window, nothing more; the new one is the real one.
        for token, route, expected in ((fleet, "/api/hub/fleet/nodes", 200),
                                       (fleet, "/api/hub/startup", 403),
                                       (new, "/api/hub/startup", 200)):
            status, _, _ = controller.request(route, token=token)
            self.assertEqual(status, expected, (route, token == new))

    def test_handing_over_moves_every_node_and_retires_the_old_controller(self) -> None:
        old_controller = self._hub("old-controller", role="controller")
        new_controller = self._hub("new-controller", controller_id="promoted-node")
        node = self._hub("node-b")
        self._join(old_controller, node, "node-b")
        new_token = (new_controller.data / ".fleet_token").read_text().strip()

        status, body, _ = old_controller.post_json(
            "/api/hub/fleet/handover",
            {"controller_url": new_controller.base, "token": new_token},
            token=old_controller.token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["total"], 1, body)
        self.assertEqual(body["verified"], 1,
                         "every node must follow with no manual step: " + json.dumps(body))
        self.assertEqual(body["handed_over_to"], new_controller.base)
        # **The node followed.** It now reports to the new controller, holding its credential.
        self.assertEqual((node.data / ".fleet_token").read_text().strip(), new_token)
        settings = json.loads((node.data / "controller_settings.json").read_text())
        self.assertEqual(settings["parent_controller_url"], new_controller.base)
        self.assertEqual(settings["fleet_credential_origin"], "adopted")
        # **The old controller is retired**, and says who it handed over to.
        _, controller_state, _ = old_controller.request("/api/hub/controller",
                                                        token=old_controller.token)
        retired = controller_state["settings"]
        self.assertEqual(retired["handed_over_to"], new_controller.base)
        self.assertTrue(retired["handed_over_at"])
        # Retired means it refuses new work rather than serving two controllers at once.
        # "Retired" is a fact the Hub publishes, not a label: `accepting_customer_jobs` is what
        # every remote submission path and the Gateway's routing read.
        self.assertFalse(controller_state["capacity"]["accepting_customer_jobs"],
                         "a handed-over controller must stop accepting dispatch")

    def test_a_handover_names_the_nodes_that_could_not_be_reached(self) -> None:
        # The honest limit: a Mac that is off cannot be moved automatically, and the answer
        # must name it rather than reporting a clean success. Its fallback is SSH over Tailscale.
        controller = self._hub("controller", role="controller")
        fleet = (controller.data / ".fleet_token").read_text().strip()
        controller.post_json(
            "/api/hub/fleet/nodes",
            {"node_id": "asleep-mac", "name": "Asleep", "address": "http://127.0.0.1:9",
             "studios": [{"id": "voice", "modality": "voice"}]},
            token=fleet)
        status, body, _ = controller.post_json(
            "/api/hub/fleet/handover",
            {"controller_url": "http://127.0.0.1:47873",
             "token": secrets.token_urlsafe(24)},
            token=controller.token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["verified"], 0)
        self.assertEqual(list(body["machines"]), ["asleep-mac"])
        self.assertFalse(body["machines"]["asleep-mac"]["ok"])
        self.assertIn(body["machines"]["asleep-mac"]["status"],
                      {"unreachable", "failed", "manual"})

    def test_a_public_controller_address_is_refused_for_a_handover(self) -> None:
        controller = self._hub("controller", role="controller")
        status, body, _ = controller.post_json(
            "/api/hub/fleet/handover",
            {"controller_url": "http://93.184.216.34:47873", "token": secrets.token_urlsafe(24)},
            token=controller.token)
        self.assertEqual(status, 400, body)


if __name__ == "__main__":
    unittest.main()
