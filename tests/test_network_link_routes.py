"""The link block reaches a controller: on `/api/hub/resources` and on every `/api/hub/fleet/overview` row.

`test_network_link.py` pins what the Mac reads. This pins the two routes a controller actually
calls — the block travelling on the host snapshot a node answers with, and on the overview row the
Fleet page draws — because a block only `host_stats()` can see would never leave the Mac.
"""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class ConnectionPayloadRouteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(os.path.realpath(self.temporary.name))
        self.data_dir = self.root / "hub"
        self.data_dir.mkdir()
        output = self.root / "image-output"
        output.mkdir()
        workers = self.root / "private-workers.json"
        workers.write_text(json.dumps([{
            "id": "image", "title": "Image Studio", "modality": "image", "host": "127.0.0.1",
            "port": 51101, "machine": "local", "local": True,
            "runtime_identity": {"id": "image-runtime"},
            "hub_token": "same-existing-hub-token", "managed_output_roots": [str(output)]}]))
        os.chmod(workers, 0o600)
        token = self.data_dir / ".hub_token"
        token.write_text("same-existing-hub-token\n")
        os.chmod(token, 0o600)
        environment = patch.dict(os.environ, {"STUDIOHUB_DATA_DIR": str(self.data_dir),
                                              "STUDIOHUB_WORKERS_FILE": str(workers),
                                              "STUDIOHUB_PACKAGED": "1"})
        environment.start()
        self.addCleanup(environment.stop)

    def _client(self, stack):
        from fastapi.testclient import TestClient
        from components.hub.backend import auth, ledger, monitor as monitor_module, registry
        from components.hub.standalone import create_app
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
            stack.enter_context(patch.object(module, name, value))
        registry._labels_cache = None
        registry._flags_cache = None
        return TestClient(create_app(start_background=False))

    def test_resources_publishes_the_link_on_the_host_snapshot(self):
        from components.hub.backend import network_link
        measured = {"status": "measured", "reason": None, "interface": "en0", "kind": "ethernet",
                    "speed_mbps": 100, "duplex": "full",
                    "media": "autoselect (100baseTX <full-duplex>)", "autoselect": True,
                    "link_up": True,
                    "wifi": {"interface": "en1", "up": True, "rate_mbps": 432},
                    "sampled_at": 1793000000.0}
        with ExitStack() as stack, \
                patch.object(network_link, "status", return_value=measured):
            client = self._client(stack)
            answer = client.get("/api/hub/resources",
                                headers={"Authorization": "Bearer same-existing-hub-token"}).json()
        link = answer["host"]["connection"]
        self.assertEqual(link["kind"], "ethernet")
        self.assertEqual(link["speed_mbps"], 100)
        self.assertEqual(link["duplex"], "full")
        self.assertEqual(link["wifi"]["rate_mbps"], 432)

    def test_the_overview_row_carries_the_link(self):
        from components.hub.backend import network_link
        measured = {"status": "measured", "reason": None, "interface": "en0", "kind": "ethernet",
                    "speed_mbps": 1000, "duplex": "full",
                    "media": "autoselect (1000baseT <full-duplex>)", "autoselect": True,
                    "link_up": True, "wifi": {"interface": "en1", "up": False, "rate_mbps": None},
                    "sampled_at": 1793000000.0}
        with ExitStack() as stack, \
                patch.object(network_link, "status", return_value=measured):
            client = self._client(stack)
            answer = client.get("/api/hub/fleet/overview",
                                headers={"Authorization": "Bearer same-existing-hub-token"}).json()
        rows = [row for row in answer["machines"] if row.get("connection") is not None]
        self.assertTrue(rows, "no fleet overview row carried the connection block")
        self.assertEqual(rows[0]["connection"]["speed_mbps"], 1000)

    def test_an_unmeasured_link_travels_with_its_reason(self):
        from components.hub.backend import network_link
        with ExitStack() as stack, patch.object(
                network_link, "status",
                return_value=network_link.unmeasured("This Mac's routing table could not be read.")):
            client = self._client(stack)
            answer = client.get("/api/hub/resources",
                                headers={"Authorization": "Bearer same-existing-hub-token"}).json()
        link = answer["host"]["connection"]
        self.assertEqual(link["status"], "unmeasured")
        self.assertIsNone(link["speed_mbps"])
        self.assertIn("routing table", link["reason"])


if __name__ == "__main__":
    unittest.main()
