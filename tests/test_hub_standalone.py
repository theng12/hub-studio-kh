"""Standalone Hub configuration contracts independent of the native shell."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class StandaloneHubConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data_dir = self.root / "Node State"
        self.workers_file = self.root / "private-workers.json"
        self.output_roots = [self.root / "image-output", self.root / "voice-output",
                             self.root / "subtitle-output"]
        for root in self.output_roots:
            root.mkdir()
        self.workers_file.write_text(json.dumps([
            {
                "id": "image", "title": "Image Studio", "modality": "image",
                "host": "127.0.0.1", "port": 51101, "machine": "node-a",
                "local": True, "runtime_identity": {"id": "image-runtime"},
                "hub_token": "same-existing-hub-token",
                "managed_output_roots": [str(self.output_roots[0])],
            },
            {
                "id": "voice", "title": "Voice Studio", "modality": "voice",
                "host": "127.0.0.1", "port": 51102, "machine": "node-a",
                "local": True, "runtime_identity": {"id": "voice-runtime"},
                "hub_token": "same-existing-hub-token",
                "managed_output_roots": [str(self.output_roots[1])],
            },
            {
                "id": "subtitle", "title": "Subtitle Studio", "modality": "transcription",
                "host": "127.0.0.1", "port": 51103, "machine": "node-a",
                "local": True, "runtime_identity": {"id": "subtitle-runtime"},
                "hub_token": "same-existing-hub-token",
                "managed_output_roots": [str(self.output_roots[2])],
            },
        ]))
        os.chmod(self.workers_file, 0o600)
        self.data_dir.mkdir()
        token_file = self.data_dir / ".hub_token"
        token_file.write_text("same-existing-hub-token\n")
        os.chmod(token_file, 0o600)
        self.old_environ = os.environ.copy()
        os.environ.update({
            "STUDIOHUB_DATA_DIR": str(self.data_dir),
            "STUDIOHUB_WORKERS_FILE": str(self.workers_file),
            "STUDIOHUB_PACKAGED": "1",
        })

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_environ)
        self.temporary.cleanup()

    def _fresh_config(self):
        import importlib
        from components.hub import standalone_config

        return importlib.reload(standalone_config)

    def test_loads_three_private_dynamic_workers_without_pinokio_defaults(self) -> None:
        config = self._fresh_config()

        workers = config.load_workers()

        self.assertEqual([worker["id"] for worker in workers], ["image", "voice", "subtitle"])
        self.assertEqual([worker["modality"] for worker in workers], ["image", "voice", "transcription"])
        self.assertTrue(all(worker["host"] == "127.0.0.1" for worker in workers))
        self.assertTrue(all(worker["runtime_identity"]["id"].endswith("runtime") for worker in workers))

    def test_rejects_insecure_worker_manifest_before_dispatch(self) -> None:
        self.workers_file.write_text(json.dumps([{
            "id": "voice", "modality": "voice", "host": "0.0.0.0", "port": 47870,
            "machine": "node-a", "local": True,
        }]))
        os.chmod(self.workers_file, 0o600)
        config = self._fresh_config()

        with self.assertRaisesRegex(ValueError, "loopback"):
            config.load_workers()

    def test_rejects_symlink_worker_manifest(self) -> None:
        alternate = self.root / "alternate-workers.json"
        alternate.write_text(self.workers_file.read_text())
        self.workers_file.unlink()
        self.workers_file.symlink_to(alternate)
        config = self._fresh_config()

        with self.assertRaisesRegex(ValueError, "regular file"):
            config.load_workers()

    def test_allows_a_truthful_subset_while_later_engines_are_not_running(self) -> None:
        workers = json.loads(self.workers_file.read_text())
        self.workers_file.write_text(json.dumps([workers[1]]))
        os.chmod(self.workers_file, 0o600)
        config = self._fresh_config()

        self.assertEqual([worker["id"] for worker in config.load_workers()], ["voice"])

    def test_persists_editable_device_defaults_with_effective_values(self) -> None:
        config = self._fresh_config()

        defaults = config.load_device_settings()
        changed = config.save_device_settings(max_bytes=60_000_000_000, retention_days=14)
        restored = config.restore_device_defaults()

        self.assertEqual(defaults["effective"]["max_bytes"], 50_000_000_000)
        self.assertEqual(defaults["effective"]["retention_days"], 30)
        self.assertEqual(changed["effective"], {
            "max_bytes": 60_000_000_000, "retention_days": 14,
            "cleanup_enabled": True, "cleanup_interval_seconds": 300,
            "minimum_free_bytes": 1_000_000_000,
        })
        self.assertEqual(restored["effective"], {
            "max_bytes": 50_000_000_000, "retention_days": 30,
            "cleanup_enabled": True, "cleanup_interval_seconds": 300,
            "minimum_free_bytes": 1_000_000_000,
        })
        self.assertEqual(stat.S_IMODE((self.data_dir / "device_settings.json").stat().st_mode), 0o600)

    def test_rejects_boolean_device_settings(self) -> None:
        config = self._fresh_config()

        with self.assertRaises(ValueError):
            config.save_device_settings(max_bytes=True, retention_days=30)
        with self.assertRaises(ValueError):
            config.save_device_settings(max_bytes=50_000_000_000, retention_days=False)
        with self.assertRaises(ValueError):
            config.save_device_settings(max_bytes=50_000_000_000, retention_days=30, minimum_free_bytes=-1)

    def test_runtime_readiness_keeps_os_ineligibility_separate_from_model_files(self) -> None:
        config = self._fresh_config()

        readiness = config.model_readiness({
            "files_state": "files_verified",
            "minimum_macos": "15.0",
            "runtime_identity": {"id": "voice-runtime", "compatible": True},
        }, macos_version="14.6", chip="Apple M1")

        self.assertEqual(readiness["files_state"], "files_verified")
        self.assertEqual(readiness["runtime_state"], "ready")
        self.assertEqual(readiness["os_state"], "unsupported")
        self.assertEqual(readiness["dispatch_state"], "ineligible")

        for missing_identity in ({}, {"id": "voice-runtime"}, {"compatible": True}):
            incomplete = config.model_readiness({
                "files_state": "files_verified", "minimum_macos": "14.0",
                "runtime_identity": missing_identity,
            }, macos_version="14.6", chip="Apple M1")
            self.assertEqual(incomplete["runtime_state"], "incompatible")
            self.assertEqual(incomplete["dispatch_state"], "ineligible")

    def test_packaged_api_requires_existing_hub_token_on_loopback(self) -> None:
        from fastapi.testclient import TestClient
        from components.hub.standalone import create_app

        app = create_app(start_background=False)
        preview_calls = []
        def capture_preview(**kwargs):
            preview_calls.append(kwargs)
            return {"machine": kwargs["machine"]}
        with patch("components.hub.output_storage.preview", side_effect=capture_preview):
            with TestClient(app) as client:
                denied = client.get("/api/hub/settings")
                accepted = client.get(
                    "/api/hub/settings",
                    headers={"Authorization": "Bearer same-existing-hub-token"},
                )
                preview = client.post(
                    "/api/hub/settings/preview", json={},
                    headers={"Authorization": "Bearer same-existing-hub-token"},
                )

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["effective"]["max_bytes"], 50_000_000_000)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual([row["machine"] for row in preview.json()["machines"]], ["node-a"])
        self.assertEqual(len(preview_calls), 1)
        self.assertEqual(preview_calls[0]["managed_output_roots"],
                         sorted(str(root) for root in self.output_roots))

    def test_packaged_artifact_metadata_never_sends_token_to_foreign_url(self) -> None:
        import asyncio
        from components.hub.backend import broker, peers

        worker = self._fresh_config().load_workers()[1]
        same_origin = "http://127.0.0.1:51102/api/generate/jobs/fixture/audio"
        url, headers = peers.studio_request(worker, same_origin)
        self.assertEqual(url, same_origin)
        self.assertEqual(headers["X-Studio-Token"], "same-existing-hub-token")

        class NoNetwork:
            async def get(self, *args, **kwargs):
                raise AssertionError("credential-bearing request escaped to a foreign URL")

        item = {}
        asyncio.run(broker._cache_voice_artifact_metadata(
            NoNetwork(), item, worker, "https://attacker.invalid/artifact", None, None))
        self.assertIn("private endpoint", item["artifact_metadata_error"])
        with self.assertRaisesRegex(ValueError, "private endpoint"):
            peers.studio_request(worker, "http://user:password@127.0.0.1:51102/artifact")


if __name__ == "__main__":
    unittest.main()
