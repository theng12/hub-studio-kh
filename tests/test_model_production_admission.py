"""Explicit production admission cannot be earned by files or remote availability."""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components import model_packages  # noqa: E402
from components.hub import standalone, standalone_config  # noqa: E402


def manifest(*, production_allowed: object = ...) -> dict:
    value = {
        "schema_version": 1,
        "package_id": "pending-chat",
        "model_id": "owner/pending-chat-4bit",
        "revision": "a" * 40,
        "engine": "chat-mlx",
        "minimum_macos": "14.0",
        "qualification": {"status": "unqualified"},
        "files": [{
            "path": "weights.safetensors",
            "size": 1,
            "sha256": hashlib.sha256(b"x").hexdigest(),
            "urls": ["https://example.test/weights.safetensors"],
        }],
    }
    if production_allowed is not ...:
        value["production_allowed"] = production_allowed
    return value


class ManifestProductionPolicyTests(unittest.TestCase):
    def test_absent_flag_preserves_legacy_admission(self) -> None:
        legacy = manifest()
        model_packages.validate_manifest(legacy)
        self.assertTrue(model_packages.production_allowed(legacy))
        self.assertEqual(legacy["qualification"]["status"], "unqualified")

    def test_present_flag_requires_a_literal_boolean_and_fails_closed(self) -> None:
        for invalid in (None, 0, 1, "false", [], {}):
            with self.subTest(value=invalid):
                candidate = manifest(production_allowed=invalid)
                self.assertFalse(model_packages.production_allowed(candidate))
                with self.assertRaisesRegex(model_packages.PackageError, "production_allowed"):
                    model_packages.validate_manifest(candidate)


class ReadinessProductionPolicyTests(unittest.TestCase):
    def test_verified_supported_candidate_stays_ineligible_when_production_is_held(self) -> None:
        readiness = standalone_config.model_readiness({
            "files_state": "files_verified",
            "minimum_macos": "14.0",
            "runtime_identity": {"id": "chat-runtime", "compatible": True},
            "production_allowed": False,
        }, macos_version="14.6", chip="Apple M2 Max")

        self.assertEqual(readiness["files_state"], "files_verified")
        self.assertEqual(readiness["runtime_state"], "ready")
        self.assertEqual(readiness["os_state"], "ready")
        self.assertEqual(readiness["chip_state"], "ready")
        self.assertFalse(readiness["production_allowed"])
        self.assertEqual(readiness["admission_state"], "production_blocked")
        self.assertIn("qualification", readiness["admission_reason"])
        self.assertEqual(readiness["dispatch_state"], "ineligible")

    def test_legacy_readiness_is_unchanged_when_flag_is_absent(self) -> None:
        readiness = standalone_config.model_readiness({
            "files_state": "files_verified",
            "minimum_macos": "14.0",
            "runtime_identity": {"id": "chat-runtime", "compatible": True},
        }, macos_version="14.6", chip="Apple M2 Max")

        self.assertTrue(readiness["production_allowed"])
        self.assertEqual(readiness["admission_state"], "ready")
        self.assertIsNone(readiness["admission_reason"])
        self.assertEqual(readiness["dispatch_state"], "eligible")

    def test_model_state_passes_the_validated_manifest_policy_to_readiness(self) -> None:
        candidate = manifest(production_allowed=False)
        monitor = SimpleNamespace(registry=[{
            "id": "chat", "modality": "chat", "local": True,
            "runtime_identity": {"id": "chat-runtime", "compatible": True},
        }])
        receipt = {"state": "files_verified", "reason": None}
        with mock.patch.object(model_packages, "preflight", return_value=receipt), \
             mock.patch.object(standalone, "_model_library", return_value=ROOT), \
             mock.patch.object(standalone.broker, "model_block_state", return_value=None), \
             mock.patch.object(standalone.shelved_models, "manifest_shelved", return_value=False), \
             mock.patch.object(standalone.standalone_config, "model_readiness",
                               wraps=standalone_config.model_readiness) as readiness:
            standalone._model_state(candidate, {candidate["package_id"]: candidate}, monitor, {})

        self.assertFalse(readiness.call_args.args[0]["production_allowed"])


class SubmissionProductionPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.candidate = manifest(production_allowed=False)
        self.monitor = SimpleNamespace(
            registry=[{"id": "chat", "modality": "chat", "local": True, "machine": "local"}],
            status={"chat": {"status": "up"}},
        )

    def test_joined_mac_cannot_make_a_pending_candidate_dispatchable(self) -> None:
        state = {"dispatch_state": "ineligible", "production_allowed": False}
        with mock.patch.object(standalone, "_model_catalog",
                               return_value={self.candidate["package_id"]: self.candidate}), \
             mock.patch.object(standalone, "_model_row", return_value=state), \
             mock.patch.object(standalone, "_a_joined_mac_can_run", return_value=True) as joined:
            refusal = standalone._submission_readiness(
                {"model": self.candidate["model_id"], "modality": "chat"}, self.monitor)

        self.assertEqual(refusal["code"], "MODEL_UNQUALIFIED")
        self.assertFalse(refusal["readiness"]["production_allowed"])
        joined.assert_not_called()

    def test_peer_gate_refuses_known_pending_without_a_local_model_library(self) -> None:
        worker = self.monitor.registry[0]
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(standalone, "_model_catalog",
                               return_value={self.candidate["package_id"]: self.candidate}), \
             mock.patch.object(standalone.workstation, "admission_refusal", return_value=None), \
             mock.patch.object(standalone.broker, "model_block_state", return_value=None), \
             mock.patch.object(standalone.broker, "busy_machines", return_value=set()), \
             mock.patch.object(standalone.output_storage, "admission", return_value=None), \
             mock.patch.object(standalone.standalone_config, "load_device_settings",
                               return_value={"effective": {
                                   "max_bytes": 1, "retention_days": 1,
                                   "cleanup_enabled": True, "minimum_free_bytes": 0,
                               }}), \
             mock.patch("components.hub.backend.registry.machine_enabled", return_value=True), \
             mock.patch("components.hub.backend.registry.studio_enabled", return_value=True):
            refusal = standalone._peer_admission_refusal(
                worker, self.monitor, self.candidate["model_id"])

        self.assertEqual(refusal["code"], "MODEL_UNQUALIFIED")


if __name__ == "__main__":
    unittest.main()
