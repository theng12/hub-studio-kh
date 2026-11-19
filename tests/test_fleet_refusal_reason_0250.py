"""A Mac that could not take work says why on its fleet row.

The broker records a refusal (code, the sentence it said, and when). The controller's overview
carries that record to the app with its age, so a silent Mac is no longer indistinguishable from
one that was never offered work. Nothing here decides a threshold: no refusal recorded, no field.
"""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch


class FleetRefusalRowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-fleet-refusal-")
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

    def _local_row(self, stack, snapshot):
        from components.hub.backend import broker
        stack.enter_context(patch.object(broker, "machine_protection_snapshot",
                                         return_value=snapshot))
        answer = self._client(stack).get(
            "/api/hub/fleet/overview",
            headers={"Authorization": "Bearer same-existing-hub-token"}).json()
        return next(row for row in answer["machines"] if row["machine"] == "local")

    def test_a_refusing_mac_carries_the_recorded_sentence_and_its_age(self):
        with ExitStack() as stack:
            row = self._local_row(stack, {"local": {"refusal": {
                "code": "INSUFFICIENT_MEMORY", "message": "needs 12 GB, has 3 GB",
                "at": time.time() - 720}}})
        refusal = row["refusal"]
        self.assertEqual(set(refusal), {"code", "message", "age_s"}, refusal)
        self.assertEqual("INSUFFICIENT_MEMORY", refusal["code"])
        self.assertEqual("needs 12 GB, has 3 GB", refusal["message"], "the sentence was cut")
        self.assertAlmostEqual(720, refusal["age_s"], delta=5)

    def test_a_mac_with_no_recorded_refusal_has_no_refusal_key(self):
        for snapshot in ({}, {"local": {}}, {"other": {"refusal": {
                "code": "INSUFFICIENT_MEMORY", "message": "needs 12 GB, has 3 GB",
                "at": time.time()}}}):
            with self.subTest(snapshot=snapshot), ExitStack() as stack:
                row = self._local_row(stack, snapshot)
            self.assertNotIn("refusal", row, "a clean Mac was given somebody else's refusal")

    def test_an_old_refusal_stays_old_on_every_read(self):
        snapshot = {"local": {"refusal": {
            "code": "INSUFFICIENT_MEMORY", "message": "needs 12 GB, has 3 GB",
            "at": time.time() - 3 * 86_400}}}
        with ExitStack() as stack:
            first = self._local_row(stack, snapshot)["refusal"]["age_s"]
            second = self._local_row(stack, snapshot)["refusal"]["age_s"]
        self.assertGreaterEqual(first, 3 * 86_400 - 5, "an old refusal was read as current")
        self.assertGreaterEqual(second, first, "re-reading the row made an old refusal fresh")


if __name__ == "__main__":
    unittest.main()
