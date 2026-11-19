"""`/api/health` must name the process that answered and the work it holds."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

TOKEN = "same-existing-hub-token"
COMMIT = "abc1234def5678"
EXISTING_KEYS = {"ok", "workers", "role", "bind", "update_draining", "disk",
                 "update_hold_active"}
NEW_KEYS = {"pid", "version", "build", "started_at", "data_dir", "queue"}


class HealthIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(os.path.realpath(self.temporary.name))
        self.data_dir = root / "data"
        self.data_dir.mkdir()
        workers = root / "private-workers.json"
        workers.write_text(json.dumps([{
            "id": "image", "title": "Image Studio", "modality": "image",
            "host": "127.0.0.1", "port": 51101, "machine": "node-a", "local": True,
            "runtime_identity": {"id": "image-runtime"}, "hub_token": TOKEN,
            "managed_output_roots": [str(root / "image-output")],
        }]))
        os.chmod(workers, 0o600)
        token_file = self.data_dir / ".hub_token"
        token_file.write_text(TOKEN + "\n")
        os.chmod(token_file, 0o600)
        manifest = root / "release.json"
        manifest.write_text(json.dumps({"version": "0.6.251",
                                        "implementation_commit": COMMIT}))
        environment = patch.dict(os.environ, {
            "STUDIOHUB_DATA_DIR": str(self.data_dir),
            "STUDIOHUB_WORKERS_FILE": str(workers),
            "STUDIOHUB_PACKAGED": "1",
            "STUDIOHUB_RELEASE_MANIFEST": str(manifest),
        })
        environment.start()
        self.addCleanup(environment.stop)

    def _client(self):
        from fastapi.testclient import TestClient
        from components.hub.standalone import create_app
        client = TestClient(create_app(start_background=False))
        self.addCleanup(client.close)
        return client

    def _health(self, client):
        answer = client.get("/api/health", headers={"Authorization": "Bearer " + TOKEN})
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()

    @staticmethod
    def _batch(batch_id, states):
        return {
            "id": batch_id, "modality": "image", "model": "fixture/model",
            "created_at": time.time(), "cancelled": False, "routing": "pool",
            "items": [{"index": i, "state": state, "prompt": "p", "params": {}}
                      for i, state in enumerate(states)],
        }

    def test_health_names_this_process_and_keeps_the_old_answer(self):
        from components.hub import standalone
        from components.hub.backend import control_plane

        with self._client() as client:
            health = self._health(client)
            self.assertEqual(set(health), EXISTING_KEYS | NEW_KEYS)
            self.assertIs(health["ok"], True)
            self.assertIsInstance(health["workers"], int)
            self.assertEqual(health["role"], control_plane.load_settings()["role"])
            self.assertEqual(health["bind"], standalone._bind_facts())
            self.assertIs(health["update_draining"], False)
            self.assertIsInstance(health["disk"], dict)
            self.assertIs(health["update_hold_active"], False)

            self.assertEqual(health["pid"], os.getpid())
            self.assertEqual(health["version"], "0.6.251")
            self.assertEqual(health["build"], COMMIT[:7])
            self.assertIsInstance(health["started_at"], (int, float))
            self.assertGreater(health["started_at"], 0)
            self.assertLessEqual(health["started_at"], time.time())
            self.assertEqual(health["data_dir"], str(Path(os.environ["STUDIOHUB_DATA_DIR"]).resolve()))

    def test_queue_counts_come_from_the_broker(self):
        from components.hub.backend import broker

        seeded = {
            "b1": self._batch("b1", ["queued", "queued", "running", "uncertain"]),
            "b2": self._batch("b2", ["queued", "running"]),
        }
        with self._client() as client:
            with patch.dict(broker.batches, seeded, clear=True):
                queue = self._health(client)["queue"]
        self.assertEqual(set(queue), {"queued", "running", "uncertain"})
        for key in queue:
            self.assertIsInstance(queue[key], int)
        self.assertEqual(queue, {"queued": 3, "running": 2, "uncertain": 1})


if __name__ == "__main__":
    unittest.main()
