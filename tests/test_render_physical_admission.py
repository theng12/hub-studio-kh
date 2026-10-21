#!/usr/bin/env python3
"""One physical authority across durable final render and existing queues."""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.hub.backend import broker  # noqa: E402
from components.hub.backend.render_executor import RenderExecutor  # noqa: E402
from components.hub.backend.render_jobs import RenderJobRuntime  # noqa: E402
from tests.test_render_jobs import QualificationAuthority, request  # noqa: E402


class RecoveryRuntime:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def recover_incomplete(self):
        self.calls.append("recover")
        return list(self.rows)



class RecoveryBroker:
    def __init__(self):
        self.calls = []

    def restore_external_machine(self, machine, owner):
        self.calls.append((machine, owner))


class CandidateExecutor:
    async def dispatchable_candidate(self, machine):
        return (1.0, "render-job") if machine == "local" else None

    def pending_for_machine(self, machine):
        return machine == "local"


class RenderPhysicalAdmissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        broker.batches.clear()
        broker._external_machine_leases.clear()
        broker.configure_monitor(SimpleNamespace(registry=[], status={}))
        broker.configure_render_executor(None)

    def tearDown(self) -> None:
        broker.configure_render_executor(None)
        broker._external_machine_leases.clear()

    def test_restore_external_machine_never_overwrites_another_physical_owner(self) -> None:
        broker.restore_external_machine("local", "render:job-a")

        self.assertIn("local", broker.busy_machines())
        broker.restore_external_machine("local", "render:job-a")
        with self.assertRaisesRegex(RuntimeError, "already"):
            broker.restore_external_machine("local", "render:job-b")
        self.assertEqual(broker._external_machine_leases["local"], "render:job-a")

    def test_executor_restores_every_durable_compute_hold_before_returning(self) -> None:
        runtime = RecoveryRuntime([
            {"id": "job-a", "machine": "local", "leases": {"compute": True}},
            {"id": "job-b", "machine": "node-b", "leases": {"compute": True}},
        ])
        physical = RecoveryBroker()
        executor = RenderExecutor(
            runtime, assets=None, authority=lambda: None, machine="local",
            broker_api=physical,
        )

        executor.recover_and_restore_physical_holds()

        self.assertEqual(runtime.calls, ["recover"])
        self.assertEqual(physical.calls, [
            ("local", "render:job-a"), ("node-b", "render:job-b"),
        ])

    def test_render_admission_reuses_pause_lite_drain_quarantine_and_exact_owner(self) -> None:
        with mock.patch.object(broker, "machine_enabled", return_value=False):
            self.assertFalse(broker.render_dispatch_allowed("local"))
        with mock.patch.object(broker.peers, "standing_by", return_value=True):
            self.assertFalse(broker.render_dispatch_allowed("local"))
        broker._external_machine_leases["local"] = "render:job-a"
        with mock.patch.object(broker, "machine_enabled", return_value=True), \
                mock.patch.object(broker, "studio_enabled", return_value=True), \
                mock.patch.object(broker, "_machine_blocked", return_value=False), \
                mock.patch.object(broker.peers, "standing_by", return_value=False), \
                mock.patch.object(broker, "busy_machines", return_value={"local"}):
            self.assertTrue(broker.render_dispatch_allowed("local", "render:job-a"))
            self.assertFalse(broker.render_dispatch_allowed("local", "render:job-b"))

    async def test_existing_broker_ranks_eligible_render_and_reserves_it_from_other_lanes(self) -> None:
        candidate = CandidateExecutor()
        broker.configure_render_executor(candidate)

        winner = await broker.next_work("local")

        self.assertEqual(winner, (0, 1.0, "render:render-job", "render"))
        self.assertFalse(broker.acquire_external_machine("local", "running:chat"))
        self.assertTrue(broker.acquire_external_machine("local", "render:render-job"))

    async def test_real_runtime_creation_age_loses_to_older_equal_priority_work(self) -> None:
        root = ROOT / "_trash/render-physical-testroots" / uuid4().hex
        root.mkdir(parents=True)
        authority = QualificationAuthority()
        runtime = RenderJobRuntime(
            root, database_path=root / "hub.db",
            qualification_resolver=authority.resolve,
            free_bytes=lambda _path: 20_000_000_000,
        )
        accepted = runtime.accept(
            "owner-a", "real-runtime-age", request(dispatch_key="real-runtime-age"))
        installed = SimpleNamespace(
            bounds=SimpleNamespace(min_available_memory_bytes=1))
        executor = RenderExecutor(
            runtime, assets=None, authority=lambda: installed,
            machine="local", broker_api=broker, available_memory=lambda: 10,
        )
        broker.configure_render_executor(executor)
        older = time.time() - 60

        with mock.patch.object(broker, "render_dispatch_allowed", return_value=True), \
                mock.patch.object(broker, "priority_order", return_value=[]), \
                mock.patch.object(
                    broker, "_broker_dispatchable_work",
                    return_value=[("existing", older, "older-work")]):
            winner = await broker.next_work("local")

        self.assertEqual(winner, (0, older, "older-work", "existing"))
        self.assertGreater(runtime.dispatchable_jobs()[0]["created_at"], older)
        self.assertEqual(runtime.dispatchable_jobs()[0]["id"], accepted["id"])


if __name__ == "__main__":
    unittest.main()
