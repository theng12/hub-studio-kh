#!/usr/bin/env python3
"""Focused fake tests for Developer Mode's atomic physical-machine admission."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.hub import developer_tests  # noqa: E402
from components.hub.backend import broker, chat_jobs, developer_test_admission as admission  # noqa: E402
from components.hub.backend import peers, registry, transcription_jobs, update_drain  # noqa: E402


def setting(enabled: bool, verified: bool = True) -> dict:
    return {
        "format": developer_tests.SETTING_FORMAT,
        "enabled": enabled,
        "revision": "rev-test",
        "verified": verified,
        "updated_at": "2026-11-06T00:00:00Z",
        "request_id": None,
        "requests": {},
    }


class DeveloperTestAdmissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [
            mock.patch.dict(os.environ, {
                "STUDIOHUB_DATA_DIR": self.tmp.name,
                "STUDIOHUB_MODELS_DIR": str(Path(self.tmp.name) / "models"),
                "STUDIOHUB_WORKERS_FILE": str(Path(self.tmp.name) / "workers.json"),
            }),
            mock.patch.object(update_drain, "draining", return_value=False),
            mock.patch.object(registry, "machine_enabled", return_value=True),
            mock.patch.object(registry, "function_enabled", return_value=True),
            mock.patch.object(registry, "studio_enabled", return_value=True),
            mock.patch.object(peers, "standing_by", return_value=False),
        ]
        for patch in self.patches:
            patch.start()
        # The real reader resolves its own paths from STUDIOHUB_DATA_DIR; nothing is mocked here.
        self.setting_path = developer_tests._setting_path()
        self.ledger_path = developer_tests._ledger_path()
        self.setting_path.parent.mkdir(parents=True, exist_ok=True)
        self.setting_path.write_text(json.dumps(setting(True)), encoding="utf-8")
        admission._reset()
        broker.batches.clear()
        broker._external_machine_leases.clear()
        broker._preparing_machines.clear()
        broker.configure_monitor(SimpleNamespace(registry=[], status={}))
        broker.configure_render_executor(None)
        chat_jobs.batches.clear()
        transcription_jobs.batches.clear()

    def tearDown(self) -> None:
        admission._reset()
        broker.batches.clear()
        broker._external_machine_leases.clear()
        broker._preparing_machines.clear()
        broker.configure_render_executor(None)
        chat_jobs.batches.clear()
        transcription_jobs.batches.clear()
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def write_setting(self, value: dict) -> None:
        self.setting_path.write_text(json.dumps(value), encoding="utf-8")

    def test_reserve_refuses_missing_off_and_unverified_setting(self) -> None:
        self.setting_path.unlink()
        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve("att-1")
        self.assertEqual(raised.exception.code, "paused")

        self.write_setting(setting(False))
        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve("att-1")
        self.assertEqual(raised.exception.code, "paused")

        self.write_setting(setting(True, verified=False))
        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve("att-1")
        self.assertEqual(raised.exception.code, "unverified")
        self.assertEqual(broker._external_machine_leases, {})

    def test_reserve_is_one_test_at_a_time_and_idempotent_for_its_attempt(self) -> None:
        owner = admission.reserve("att-1")

        self.assertEqual(owner, "developer-test:att-1")
        self.assertEqual(broker._external_machine_leases["local"], owner)
        self.assertEqual(admission.reserve("att-1"), owner)
        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve("att-2")
        self.assertEqual(raised.exception.code, "busy")

    def test_reserve_refuses_while_any_production_is_pending(self) -> None:
        chat_jobs.batches["chat-batch"] = {"packs": [{"state": "queued"}]}
        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve("att-1")
        self.assertEqual(raised.exception.code, "production_busy")

        chat_jobs.batches.clear()
        with broker.preparing_machine("local"):
            with self.assertRaises(admission.AdmissionRefused) as raised:
                admission.reserve("att-1")
        self.assertEqual(raised.exception.code, "production_busy")
        self.assertEqual(broker._external_machine_leases, {})

    def test_reserve_refuses_the_machines_image_function_off(self) -> None:
        with mock.patch.object(registry, "function_enabled", return_value=False):
            with self.assertRaises(admission.AdmissionRefused) as raised:
                admission.reserve("att-1")
        self.assertEqual(raised.exception.code, "paused")
        self.assertEqual(broker._external_machine_leases, {})

    def test_reserve_refuses_lite_mode_standing_by(self) -> None:
        with mock.patch.object(peers, "standing_by", return_value=True):
            with self.assertRaises(admission.AdmissionRefused) as raised:
                admission.reserve("att-1")
        self.assertEqual(raised.exception.code, "paused")

    def test_reserve_refuses_when_every_image_studio_is_off(self) -> None:
        broker.configure_monitor(SimpleNamespace(
            registry=[{"id": "image-1", "modality": "image", "machine": "local"}], status={}))
        with mock.patch.object(registry, "studio_enabled", return_value=False):
            with self.assertRaises(admission.AdmissionRefused) as raised:
                admission.reserve("att-1")
        self.assertEqual(raised.exception.code, "paused")

    def test_reserve_allows_when_an_image_studio_is_on_or_unregistered(self) -> None:
        broker.configure_monitor(SimpleNamespace(
            registry=[{"id": "image-1", "modality": "image", "machine": "local"}], status={}))
        self.assertEqual(admission.reserve("att-1"), "developer-test:att-1")
        admission.release("att-1", confirmed=True)

        broker.configure_monitor(SimpleNamespace(registry=[], status={}))
        with mock.patch.object(registry, "studio_enabled", return_value=False):
            # No image Studio row exists, so there is no owner switch to read: absent stays ON.
            self.assertEqual(admission.reserve("att-2"), "developer-test:att-2")

    def test_production_acquire_signals_stop_and_waits_for_release(self) -> None:
        admission.reserve("att-1")
        self.assertFalse(broker.acquire_external_machine("local", "render:job-a"))
        self.assertTrue(admission.cancel_requested("att-1"))
        self.assertFalse(admission.stage_allowed("att-1"))
        self.assertEqual(admission.active_attempt(), "att-1")

        self.assertTrue(admission.release("att-1", confirmed=True))
        self.assertTrue(broker.acquire_external_machine("local", "render:job-a"))
        self.assertIsNone(admission.active_attempt())

    def test_unconfirmed_release_retains_the_hold(self) -> None:
        admission.reserve("att-1")
        self.assertFalse(admission.release("att-1", confirmed=False))
        self.assertTrue(admission.release_unknown("att-1"))
        self.assertIn("local", broker.busy_machines())
        self.assertEqual(broker._external_machine_leases["local"], "developer-test:att-1")

        self.assertTrue(admission.release("att-1", confirmed=True))
        self.assertNotIn("local", broker.busy_machines())

    def test_off_switch_stops_an_active_test_without_another_event(self) -> None:
        admission.reserve("att-1")
        self.assertFalse(admission.cancel_requested("att-1"))
        self.write_setting(setting(False))
        self.assertTrue(admission.cancel_requested("att-1"))

    def test_restore_before_production_restores_a_running_attempt_hold(self) -> None:
        self.ledger_path.write_text(json.dumps({
            "format": "studio.developer-test.ledger.v1",
            "attempts": {"key-1": {
                "attempt_id": "att-1", "state": "running", "accepted": True,
                "updated_at": "2026-11-06T00:00:00Z",
            }},
        }), encoding="utf-8")

        self.assertEqual(admission.restore_before_production(), ["att-1"])
        self.assertEqual(admission.active_attempt(), "att-1")
        self.assertEqual(broker._external_machine_leases["local"], "developer-test:att-1")
        self.assertIn("local", broker.busy_machines())

    def test_unreadable_ledger_keeps_the_mac_out_of_production(self) -> None:
        self.ledger_path.write_text("{not json", encoding="utf-8")
        self.assertEqual(admission.restore_before_production(), [])
        self.assertEqual(admission.held_machines(), {"local"})
        self.assertIn("local", broker.busy_machines())

    async def test_production_preparation_waits_for_a_test_hold(self) -> None:
        admission.reserve("att-1")
        decision, note = await broker.prepare_machine_memory(
            None, {"machine": "local"}, "model", {})
        self.assertEqual(decision, "wait")
        self.assertIn("Developer Mode test", note)

    def test_prepare_marker_and_reserve_are_atomic_across_threads(self) -> None:
        """A runner thread can never claim the Mac between "no test" and "preparing"."""
        for _ in range(25):
            admission._reset()
            broker._external_machine_leases.clear()
            broker._preparing_machines.clear()
            barrier = threading.Barrier(2)
            decided = threading.Event()
            release_prepare = threading.Event()
            outcome: dict = {}

            def prepare() -> None:
                barrier.wait(2)
                with broker.preparing_machine("local") as preparing:
                    outcome["blocked"] = preparing.blocked
                    decided.set()
                    if not preparing.blocked:
                        release_prepare.wait(2)   # hold the marker across a simulated await

            def claim() -> None:
                barrier.wait(2)
                decided.wait(2)
                try:
                    admission.reserve("att-race")
                    outcome["claimed"] = True
                except admission.AdmissionRefused as exc:
                    outcome["claimed"] = False
                    outcome["code"] = exc.code

            preparing_thread = threading.Thread(target=prepare)
            claim_thread = threading.Thread(target=claim)
            preparing_thread.start()
            claim_thread.start()
            claim_thread.join(5)
            release_prepare.set()
            preparing_thread.join(5)

            if outcome.get("claimed"):
                admission.release("att-race", confirmed=True)
            if outcome["blocked"]:
                # The claim won first; the marker refused to start preparation.
                self.assertTrue(outcome["claimed"])
            else:
                # Preparation held the marker; the claim must have been refused.
                self.assertFalse(outcome["claimed"])
                self.assertEqual(outcome.get("code"), "production_busy")

    def test_no_active_test_changes_nothing_for_production(self) -> None:
        self.assertEqual(admission.held_machines(), set())
        self.assertFalse(admission.cancel_requested("att-1"))
        self.assertTrue(broker.acquire_external_machine("local", "render:job-a"))
        self.assertIn("local", broker.busy_machines())
        broker.release_external_machine("local", "render:job-a")
        self.assertNotIn("local", broker.busy_machines())

    def test_app_gate_reserve_and_release(self) -> None:
        owner = admission.reserve_app_gate("gate-1", resource_safe=True)
        self.assertEqual(owner, "developer-test:gate-1")
        self.assertEqual(admission.active_purpose(), "app_gate")
        self.assertEqual(admission.active_attempt(), "gate-1")
        self.assertEqual(broker._external_machine_leases["local"], owner)
        self.assertEqual(admission.reserve_app_gate("gate-1", resource_safe=True), owner)
        self.assertTrue(admission.release("gate-1", confirmed=True))
        self.assertIsNone(admission.active_attempt())
        self.assertIsNone(admission.active_purpose())
        self.assertNotIn("local", broker._external_machine_leases)

    def test_cross_kind_exclusion(self) -> None:
        admission.reserve("img-1")
        self.assertEqual(admission.active_purpose(), "image")
        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve_app_gate("gate-1", resource_safe=True)
        self.assertEqual(raised.exception.code, "busy")
        admission.release("img-1", confirmed=True)

        admission.reserve_app_gate("gate-2", resource_safe=True)
        self.assertEqual(admission.active_purpose(), "app_gate")
        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve("img-2")
        self.assertEqual(raised.exception.code, "busy")

        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve_app_gate("gate-3", resource_safe=True)
        self.assertEqual(raised.exception.code, "busy")
        admission.release("gate-2", confirmed=True)

    def test_image_behavior_unchanged_by_app_gate_support(self) -> None:
        with mock.patch.object(registry, "function_enabled", return_value=False):
            with self.assertRaises(admission.AdmissionRefused) as raised:
                admission.reserve("img-1")
            self.assertEqual(raised.exception.code, "paused")

        broker.configure_monitor(SimpleNamespace(
            registry=[{"id": "image-1", "modality": "image", "machine": "local"}], status={}))
        with mock.patch.object(registry, "studio_enabled", return_value=False):
            with self.assertRaises(admission.AdmissionRefused) as raised:
                admission.reserve("img-2")
            self.assertEqual(raised.exception.code, "paused")

        owner = admission.reserve("img-3")
        self.assertEqual(owner, "developer-test:img-3")
        self.assertEqual(admission.active_purpose(), "image")
        admission.release("img-3", confirmed=True)

    def test_app_gate_allows_when_image_function_or_studios_are_off(self) -> None:
        with mock.patch.object(registry, "function_enabled", return_value=False):
            owner = admission.reserve_app_gate("gate-1", resource_safe=True)
            self.assertEqual(owner, "developer-test:gate-1")
            admission.release("gate-1", confirmed=True)

        broker.configure_monitor(SimpleNamespace(
            registry=[{"id": "image-1", "modality": "image", "machine": "local"}], status={}))
        with mock.patch.object(registry, "studio_enabled", return_value=False):
            owner = admission.reserve_app_gate("gate-2", resource_safe=True)
            self.assertEqual(owner, "developer-test:gate-2")
            admission.release("gate-2", confirmed=True)

    def test_app_gate_refusal_when_paused_off_or_unverified(self) -> None:
        self.setting_path.unlink()
        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve_app_gate("gate-1", resource_safe=True)
        self.assertEqual(raised.exception.code, "paused")

        self.write_setting(setting(False))
        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve_app_gate("gate-1", resource_safe=True)
        self.assertEqual(raised.exception.code, "paused")

        self.write_setting(setting(True, verified=False))
        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve_app_gate("gate-1", resource_safe=True)
        self.assertEqual(raised.exception.code, "unverified")

        self.write_setting(setting(True))
        with mock.patch.object(registry, "machine_enabled", return_value=False):
            with self.assertRaises(admission.AdmissionRefused) as raised:
                admission.reserve_app_gate("gate-1", resource_safe=True)
            self.assertEqual(raised.exception.code, "paused")

        with mock.patch.object(peers, "standing_by", return_value=True):
            with self.assertRaises(admission.AdmissionRefused) as raised:
                admission.reserve_app_gate("gate-1", resource_safe=True)
            self.assertEqual(raised.exception.code, "paused")

    def test_app_gate_requires_resource_safe_failclosed(self) -> None:
        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve_app_gate("gate-1")
        self.assertEqual(raised.exception.code, "resource_unknown")

        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve_app_gate("gate-1", resource_safe=False)
        self.assertEqual(raised.exception.code, "resource_unknown")

        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve_app_gate("gate-1", resource_safe="false")
        self.assertEqual(raised.exception.code, "resource_unknown")

        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve_app_gate("gate-1", resource_safe=1)
        self.assertEqual(raised.exception.code, "resource_unknown")

        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve_app_gate("gate-1", resource_safe=object())
        self.assertEqual(raised.exception.code, "resource_unknown")

        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve("gate-1", purpose="app_gate")
        self.assertEqual(raised.exception.code, "resource_unknown")

        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve("gate-1", purpose="app_gate", resource_safe=False)
        self.assertEqual(raised.exception.code, "resource_unknown")

        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve("gate-1", purpose="app_gate", resource_safe="truthy_str")
        self.assertEqual(raised.exception.code, "resource_unknown")

        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve("gate-1", purpose="app_gate", resource_safe=1)
        self.assertEqual(raised.exception.code, "resource_unknown")

        self.assertEqual(broker._external_machine_leases, {})

    def test_image_truthy_resource_safe_unaffected(self) -> None:
        owner = admission.reserve("img-truthy-1", resource_safe="truthy")
        self.assertEqual(owner, "developer-test:img-truthy-1")
        admission.release("img-truthy-1", confirmed=True)

        owner = admission.reserve("img-truthy-2", resource_safe=1)
        self.assertEqual(owner, "developer-test:img-truthy-2")
        admission.release("img-truthy-2", confirmed=True)

    def test_app_gate_production_preemption(self) -> None:
        admission.reserve_app_gate("gate-1", resource_safe=True)
        self.assertFalse(broker.acquire_external_machine("local", "render:prod-1"))
        self.assertTrue(admission.cancel_requested("gate-1"))
        self.assertFalse(admission.stage_allowed("gate-1"))

        self.assertTrue(admission.release("gate-1", confirmed=True))
        self.assertTrue(broker.acquire_external_machine("local", "render:prod-1"))
        broker.release_external_machine("local", "render:prod-1")

    def test_app_gate_unknown_release_retains_shared_lease(self) -> None:
        admission.reserve_app_gate("gate-1", resource_safe=True)
        self.assertFalse(admission.release("gate-1", confirmed=False))
        self.assertTrue(admission.release_unknown("gate-1"))
        self.assertEqual(broker._external_machine_leases["local"], "developer-test:gate-1")
        self.assertIn("local", broker.busy_machines())

        self.assertFalse(broker.acquire_external_machine("local", "render:prod-1"))
        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve("img-1")
        self.assertEqual(raised.exception.code, "busy")
        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve_app_gate("gate-2", resource_safe=True)
        self.assertEqual(raised.exception.code, "busy")

        self.assertTrue(admission.release("gate-1", confirmed=True))
        self.assertNotIn("local", broker.busy_machines())
        self.assertFalse(admission.release_unknown("gate-1"))

    def test_app_gate_cancel_requested_on_setting_off_or_node_pause(self) -> None:
        admission.reserve_app_gate("gate-1", resource_safe=True)
        self.assertFalse(admission.cancel_requested("gate-1"))

        self.write_setting(setting(False))
        self.assertTrue(admission.cancel_requested("gate-1"))

        self.write_setting(setting(True))
        self.assertFalse(admission.cancel_requested("gate-1"))

        with mock.patch.object(registry, "function_enabled", return_value=False):
            self.assertFalse(admission.cancel_requested("gate-1"))

        with mock.patch.object(registry, "machine_enabled", return_value=False):
            self.assertTrue(admission.cancel_requested("gate-1"))

        admission.release("gate-1", confirmed=True)

    def test_reserve_invalid_purpose(self) -> None:
        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve("att-1", purpose="unsupported_purpose")
        self.assertEqual(raised.exception.code, "invalid")

    def test_reserve_replay_conflicting_purpose_refuses(self) -> None:
        owner = admission.reserve("att-replay")
        self.assertEqual(admission.active_purpose(), "image")
        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve("att-replay", purpose="app_gate", resource_safe=True)
        self.assertEqual(raised.exception.code, "busy")
        admission.release("att-replay", confirmed=True)

        gate_owner = admission.reserve_app_gate("gate-replay", resource_safe=True)
        self.assertEqual(admission.active_purpose(), "app_gate")
        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve("gate-replay", purpose="image")
        self.assertEqual(raised.exception.code, "busy")
        admission.release("gate-replay", confirmed=True)

    def test_reserve_replay_conflicting_machine_refuses(self) -> None:
        owner = admission.reserve("att-replay", machine="local")
        with self.assertRaises(admission.AdmissionRefused) as raised:
            admission.reserve("att-replay", machine="node-2")
        self.assertEqual(raised.exception.code, "busy")
        admission.release("att-replay", confirmed=True)


if __name__ == "__main__":
    unittest.main()
