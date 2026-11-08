"""The Hub's health and resources answers carry the `disk` object the Gateway shows."""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class DiskPayloadRouteTests(unittest.TestCase):
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
            "id": "image", "title": "Image Studio", "modality": "image", "host": "127.0.0.1", "port": 51101,
            "machine": "node-a", "local": True, "runtime_identity": {"id": "image-runtime"},
            "hub_token": "same-existing-hub-token", "managed_output_roots": [str(output)]}]))
        os.chmod(workers, 0o600)
        token = self.data_dir / ".hub_token"
        token.write_text("same-existing-hub-token\n")
        os.chmod(token, 0o600)
        environment = patch.dict(os.environ, {"STUDIOHUB_DATA_DIR": str(self.data_dir),
                                              "STUDIOHUB_WORKERS_FILE": str(workers), "STUDIOHUB_PACKAGED": "1"})
        environment.start()
        self.addCleanup(environment.stop)

    def test_health_and_resources_publish_the_disk_object(self):
        from fastapi.testclient import TestClient
        from components.hub.backend import auth, ledger, monitor as monitor_module, registry
        from components.hub.standalone import create_app
        with ExitStack() as stack:
            for module, name, value in (
                (registry, "DATA_DIR", self.data_dir), (registry, "REGISTRY_FILE", self.data_dir / "studios.json"),
                (registry, "LABELS_FILE", self.data_dir / "machine_labels.json"),
                (registry, "FLAGS_FILE", self.data_dir / "machine_flags.json"),
                (monitor_module, "DATA_DIR", self.data_dir), (auth, "TOKEN_FILE", self.data_dir / ".hub_token"),
                (auth, "PASSWORD_FILE", self.data_dir / ".hub_password.json"),
                (auth, "SESSIONS_FILE", self.data_dir / ".hub_sessions.json"),
                (ledger, "DB_FILE", self.data_dir / "hub.db"), (ledger, "PINOKIO_HOME", self.data_dir),
            ):
                stack.enter_context(patch.object(module, name, value))
            registry._labels_cache = None
            registry._flags_cache = None
            owner = {"Authorization": "Bearer same-existing-hub-token"}
            with TestClient(create_app(start_background=False)) as client:
                health = client.get("/api/health", headers=owner).json()
                resources = client.get("/api/hub/resources", headers=owner).json()
        for payload in (health["disk"], resources["disk"], resources["host"]["disk"]):
            self.assertIn(payload["state"], {"green", "amber", "red", "stop"})
            for key in ("free_gb", "total_gb", "trash_gb", "consumers", "top", "swap_used_gb", "last_sweep", "warnings"):
                self.assertIn(key, payload)
        self.assertIsInstance(resources["disk"]["warnings"], list)


    def test_resources_publish_measured_work_volume_and_omit_it_when_absent(self):
        from fastapi.testclient import TestClient
        from components.hub import developer_gate_service
        from components.hub.backend import auth, ledger, monitor as monitor_module, registry
        from components.hub.standalone import create_app
        value = {
            "path": "/Volumes/Work/developer-gates/workspaces",
            "mount_point": "/Volumes/Work", "volume_id": "disk3s5",
            "device_id": "disk0", "physical": True, "free_bytes": 0,
            "total_bytes": 500_277_792_768,
            "observed_at": "2026-11-08T00:00:00Z", "reason": None,
        }
        with ExitStack() as stack:
            for module, name, value_to_set in (
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
                stack.enter_context(patch.object(module, name, value_to_set))
            registry._labels_cache = None
            registry._flags_cache = None
            owner = {"Authorization": "Bearer same-existing-hub-token"}
            with TestClient(create_app(start_background=False)) as client:
                with patch.object(developer_gate_service,
                                  "work_volume_for_resources", return_value=value):
                    published = client.get("/api/hub/resources", headers=owner).json()
                with patch.object(developer_gate_service,
                                  "work_volume_for_resources", return_value=None):
                    omitted = client.get("/api/hub/resources", headers=owner).json()
        self.assertEqual(published["work_volume"], value)
        self.assertNotIn("work_volume", omitted)


if __name__ == "__main__":
    unittest.main()
