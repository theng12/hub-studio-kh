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

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
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
                 host: str = "127.0.0.1"):
        self.root = root
        self.token = token
        self.host = host
        self.data = data or (root / name / "state")
        self.data.mkdir(parents=True, exist_ok=True)
        token_file = self.data / ".hub_token"
        token_file.write_text(token + "\n")
        os.chmod(token_file, 0o600)
        worker_port = free_port()
        self.workers = root / name / "workers.json"
        self.workers.parent.mkdir(parents=True, exist_ok=True)
        self.workers.write_text(json.dumps([{
            "id": "voice", "modality": "voice", "host": "127.0.0.1", "port": worker_port,
            "machine": "local", "local": True, "hub_token": token,
            "runtime_identity": {"id": "fixture-runtime", "compatible": True},
            "managed_output_roots": [str(self.root / name / "outputs")],
        }]))
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
                       "PYTHONPATH": str(PROJECT_ROOT)}
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

    def request(self, path: str, *, token: str | None = None, method: str = "GET",
                headers: dict | None = None, use_bearer: bool = False):
        request = urllib.request.Request(f"{self.base}{path}", method=method)
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
        self.assertEqual([row["service_id"] for row in capabilities["workers"]], ["voice"])
        self.assertEqual([row["physical_machine_id"] for row in capabilities["machines"]],
                         ["local"])

    def test_the_hub_address_is_loopback_only(self) -> None:
        """There is no cloud-domain assumption to make; the app talks to a private address."""
        host = self.hub.base.split("://", 1)[1].split(":", 1)[0]
        self.assertEqual(host, "127.0.0.1")
        self.assertIn("127.0.0.1", self.hub.base)


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


if __name__ == "__main__":
    unittest.main()
