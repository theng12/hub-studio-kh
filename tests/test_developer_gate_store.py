"""Unit tests for developer_gate_store.

Tests durable attempt store, identity reconciliation, capacity accounting,
safe path boundaries, result spooling, exact-match ACK before spool removal,
and guarded disposable scratch cleanup without external dependencies.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from components.hub.developer_gate_store import (
    DeveloperGateStore,
    GateStoreCapacityError,
    GateStoreConflictError,
    GateStoreError,
    GateStoreNotFoundError,
    GateStorePathError,
    GateStoreStateError,
    canonical_json_bytes,
    compute_request_sha256,
)


class TestDeveloperGateStore(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="gate_store_test_")
        self.root = Path(self.temp_dir)
        self.store = DeveloperGateStore(
            root_dir=self.root,
            max_workspace_bytes=1000,
            max_result_spool_bytes=2000,
        )
        self.sample_req = canonical_json_bytes({"gate": "test-gate", "run_id": "run-1"})
        self.sample_hash = compute_request_sha256(self.sample_req)

    def tearDown(self) -> None:
        self.store.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_strict_positive_config_rejected(self) -> None:
        with self.assertRaises(GateStoreCapacityError):
            DeveloperGateStore(root_dir=self.root / "t1", max_workspace_bytes=0, max_result_spool_bytes=100)
        with self.assertRaises(GateStoreCapacityError):
            DeveloperGateStore(root_dir=self.root / "t2", max_workspace_bytes=100, max_result_spool_bytes=-5)
        with self.assertRaises(GateStoreCapacityError):
            DeveloperGateStore(root_dir=self.root / "t3", max_workspace_bytes=True, max_result_spool_bytes=100)

    def test_dual_store_exclusive_lock_fails_second(self) -> None:
        with self.assertRaises(GateStoreConflictError):
            DeveloperGateStore(
                root_dir=self.root,
                max_workspace_bytes=1000,
                max_result_spool_bytes=2000,
            )

    def test_accept_and_reconcile_same_identity(self) -> None:
        rec1 = self.store.accept_attempt(
            controller_id="ctrl-1",
            node_id="node-1",
            idempotency_key="key-abc",
            canonical_request_bytes=self.sample_req,
            requested_workspace_bytes=100,
        )
        self.assertEqual(rec1["acceptance"]["controller_id"], "ctrl-1")
        self.assertEqual(rec1["acceptance"]["node_id"], "node-1")
        self.assertEqual(rec1["acceptance"]["idempotency_key"], "key-abc")
        self.assertEqual(rec1["acceptance"]["request_sha256"], self.sample_hash)
        self.assertEqual(rec1["state"], "waiting_source")
        self.assertEqual(self.store.active_workspace_bytes, 100)

        # Idempotent reconciliation
        rec2 = self.store.accept_attempt(
            controller_id="ctrl-1",
            node_id="node-1",
            idempotency_key="key-abc",
            canonical_request_bytes=self.sample_req,
            requested_workspace_bytes=100,
        )
        self.assertEqual(rec1["acceptance"]["attempt_id"], rec2["acceptance"]["attempt_id"])
        self.assertEqual(self.store.active_workspace_bytes, 100)

    def test_accept_conflict_different_request_bytes(self) -> None:
        self.store.accept_attempt(
            controller_id="ctrl-1",
            node_id="node-1",
            idempotency_key="key-abc",
            canonical_request_bytes=self.sample_req,
            requested_workspace_bytes=100,
        )
        diff_req = canonical_json_bytes({"gate": "test-gate-diff", "run_id": "run-2"})
        with self.assertRaises(GateStoreConflictError):
            self.store.accept_attempt(
                controller_id="ctrl-1",
                node_id="node-1",
                idempotency_key="key-abc",
                canonical_request_bytes=diff_req,
            )

    def test_accept_conflict_mismatched_hash(self) -> None:
        with self.assertRaises(GateStoreConflictError):
            self.store.accept_attempt(
                controller_id="ctrl-1",
                node_id="node-1",
                idempotency_key="key-bad-hash",
                canonical_request_bytes=self.sample_req,
                request_sha256="0" * 64,
            )

    def test_safe_id_validation_and_path_safety(self) -> None:
        bad_ids = ["../traversal", "slash/id", "back\\slash", "col:on", "id with spaces", ""]
        for bad in bad_ids:
            with self.assertRaises(GateStorePathError):
                self.store.accept_attempt(
                    controller_id="ctrl-1",
                    node_id="node-1",
                    idempotency_key=bad,
                    canonical_request_bytes=self.sample_req,
                )

    def test_symlink_boundaries_refused(self) -> None:
        rec = self.store.accept_attempt(
            controller_id="ctrl-1",
            node_id="node-1",
            idempotency_key="key-sym",
            canonical_request_bytes=self.sample_req,
        )
        att_id = rec["acceptance"]["attempt_id"]
        ws = self.store.get_workspace_path(att_id)
        # Replace workspace with symlink to /tmp
        shutil.rmtree(ws)
        os.symlink("/tmp", str(ws))
        with self.assertRaises(GateStorePathError):
            self.store.get_workspace_path(att_id)

        # Symlink in root directory is rejected upon store creation
        symlink_root = self.root / "sym_root"
        os.symlink(str(self.root), str(symlink_root))
        with self.assertRaises(GateStorePathError):
            DeveloperGateStore(root_dir=symlink_root, max_workspace_bytes=100, max_result_spool_bytes=100)

    def test_aggregate_workspace_capacity(self) -> None:
        self.store.accept_attempt(
            controller_id="ctrl-1",
            node_id="node-1",
            idempotency_key="key-1",
            canonical_request_bytes=self.sample_req,
            requested_workspace_bytes=800,
        )
        self.assertEqual(self.store.active_workspace_bytes, 800)

        # Overflow 1000 max_workspace_bytes
        req2 = canonical_json_bytes({"gate": "g2"})
        with self.assertRaises(GateStoreCapacityError):
            self.store.accept_attempt(
                controller_id="ctrl-1",
                node_id="node-1",
                idempotency_key="key-2",
                canonical_request_bytes=req2,
                requested_workspace_bytes=300,
            )

    def test_persist_result_archive_stream_and_verification(self) -> None:
        rec = self.store.accept_attempt(
            controller_id="ctrl-1",
            node_id="node-1",
            idempotency_key="key-res",
            canonical_request_bytes=self.sample_req,
            reserved_spool_bytes=50,
        )
        att_id = rec["acceptance"]["attempt_id"]
        archive_data = b"fake tar archive contents"
        exp_sha = hashlib.sha256(archive_data).hexdigest()
        exp_size = len(archive_data)

        desc = self.store.persist_result_archive(
            attempt_id=att_id,
            stream_or_bytes=io.BytesIO(archive_data),
            expected_sha256=exp_sha,
            expected_size=exp_size,
            max_bytes=100,
        )
        self.assertEqual(desc["sha256"], exp_sha)
        self.assertEqual(desc["size_bytes"], exp_size)
        self.assertEqual(desc["media_type"], "application/x-tar")
        self.assertEqual(self.store.active_spool_bytes, exp_size)

        tar_path = self.store.get_result_path(att_id)
        self.assertIsNotNone(tar_path)
        self.assertTrue(tar_path.is_file())

        # Cap limit exceeded
        rec2 = self.store.accept_attempt(
            controller_id="ctrl-1",
            node_id="node-1",
            idempotency_key="key-res2",
            canonical_request_bytes=canonical_json_bytes({"g": 2}),
        )
        with self.assertRaises(GateStoreCapacityError):
            self.store.persist_result_archive(
                attempt_id=rec2["acceptance"]["attempt_id"],
                stream_or_bytes=b"x" * 10,
                max_bytes=5,
            )

    def test_ack_case_sensitive_and_exact_keys(self) -> None:
        rec = self.store.accept_attempt(
            controller_id="Ctrl-1",
            node_id="Node-1",
            idempotency_key="Key-Ack",
            canonical_request_bytes=self.sample_req,
        )
        att_id = rec["acceptance"]["attempt_id"]
        desc = self.store.persist_result_archive(
            attempt_id=att_id,
            stream_or_bytes=b"sample output archive",
        )

        # Case change in controller_id must be refused
        bad_acc = copy.deepcopy(rec["acceptance"])
        bad_acc["controller_id"] = "ctrl-1"
        with self.assertRaises(GateStoreConflictError):
            self.store.ack_result(att_id, bad_acc, desc)

        # Extra key in acceptance must be refused
        bad_acc_extra = copy.deepcopy(rec["acceptance"])
        bad_acc_extra["extra"] = "forbidden"
        with self.assertRaises(GateStoreConflictError):
            self.store.ack_result(att_id, bad_acc_extra, desc)

        # Extra key in result descriptor must be refused
        bad_desc_extra = copy.deepcopy(desc)
        bad_desc_extra["extra"] = "forbidden"
        with self.assertRaises(GateStoreConflictError):
            self.store.ack_result(att_id, rec["acceptance"], bad_desc_extra)

        # Exact match succeeds
        ack_res = self.store.ack_result(
            attempt_id_or_key=att_id,
            acceptance=rec["acceptance"],
            result_descriptor=desc,
        )
        self.assertTrue(ack_res["result_acknowledged"])
        self.assertIsNone(self.store.get_result_path(att_id))

    def test_fault_fsync_failure_prevents_memory_and_spool_mutation(self) -> None:
        rec = self.store.accept_attempt(
            controller_id="ctrl-1",
            node_id="node-1",
            idempotency_key="key-fault",
            canonical_request_bytes=self.sample_req,
        )
        att_id = rec["acceptance"]["attempt_id"]
        desc = self.store.persist_result_archive(att_id, b"fault test data")

        # Mock atomic_write_json to fail on ACK persistence
        with mock.patch("components.hub.developer_gate_store.atomic_write_json", side_effect=OSError("Disk write failed")):
            with self.assertRaises(OSError):
                self.store.ack_result(att_id, rec["acceptance"], desc)

        # Verify in-memory state was NOT mutated to acknowledged, and spool was NOT removed
        current = self.store.get_attempt(att_id)
        self.assertFalse(current["result_acknowledged"])
        self.assertIsNotNone(self.store.get_result_path(att_id))

    def test_normal_scratch_removal_rules(self) -> None:
        rec = self.store.accept_attempt(
            controller_id="ctrl-1",
            node_id="node-1",
            idempotency_key="key-clean",
            canonical_request_bytes=self.sample_req,
            requested_workspace_bytes=250,
        )
        att_id = rec["acceptance"]["attempt_id"]
        ws = self.store.get_workspace_path(att_id)
        test_file = ws / "test.txt"
        test_file.write_text("hello")

        valid_release = {
            "confirmed": True,
            "observed_at": "2026-11-08T12:00:00Z",
            "owned_processes_remaining": 0,
            "proof": "owned-process-identities-exited",
        }

        # Cannot clean before durable result ACK
        with self.assertRaises(GateStoreStateError):
            self.store.cleanup_workspace(att_id, valid_release)

        # Persist and ACK result
        desc = self.store.persist_result_archive(att_id, b"result data")
        self.store.ack_result(att_id, rec["acceptance"], desc)

        # Invalid process release proof
        bad_release = dict(valid_release)
        bad_release["confirmed"] = False
        with self.assertRaises(GateStoreError):
            self.store.cleanup_workspace(att_id, bad_release)

        # Successful cleanup with disk sample callback
        sample_cb = lambda: {
            "observed_at": "2026-11-08T12:01:00Z",
            "free_bytes": 5000,
            "workspace_bytes": 0,
            "result_spool_bytes": 0,
            "shared_cache_bytes": 0,
        }
        cleaned = self.store.cleanup_workspace(att_id, valid_release, sample_disk_callback=sample_cb)
        self.assertTrue(cleaned["workspace_removed"])
        self.assertTrue(cleaned["resource_release_confirmed"])
        self.assertIsNotNone(cleaned["cleanup_after"])
        self.assertFalse(ws.exists())
        self.assertEqual(self.store.active_workspace_bytes, 0)

        with self.assertRaises(GateStoreNotFoundError):
            self.store.get_workspace_path(att_id)

    def test_confirmed_orphan_without_spool_refuses_deletion(self) -> None:
        rec = self.store.accept_attempt(
            controller_id="ctrl-1",
            node_id="node-1",
            idempotency_key="key-no-spool",
            canonical_request_bytes=self.sample_req,
            requested_workspace_bytes=300,
        )
        att_id = rec["acceptance"]["attempt_id"]
        ws = self.store.get_workspace_path(att_id)
        # Attempt has NO result archive spooled yet
        confirmed = {
            "confirmed": True,
            "observed_at": "2026-11-08T12:00:00Z",
            "owned_processes_remaining": 0,
            "proof": "never_started",
        }
        # Wire r2 section 8: Must refuse scratch deletion without durable result
        held = self.store.reconcile_orphan(att_id, confirmed)
        self.assertEqual(held["state"], "recovery_hold")
        self.assertFalse(held["workspace_removed"])
        self.assertTrue(ws.exists())

    def test_orphan_reconciliation_with_spool_preserves_spool_and_cleans_workspace(self) -> None:
        rec = self.store.accept_attempt(
            controller_id="ctrl-1",
            node_id="node-1",
            idempotency_key="key-orphan-spool",
            canonical_request_bytes=self.sample_req,
            requested_workspace_bytes=300,
        )
        att_id = rec["acceptance"]["attempt_id"]
        ws = self.store.get_workspace_path(att_id)
        self.store.persist_result_archive(att_id, b"unacked orphan result")

        confirmed = {
            "confirmed": True,
            "observed_at": "2026-11-08T12:00:00Z",
            "owned_processes_remaining": 0,
            "proof": "never_started",
        }
        reconciled = self.store.reconcile_orphan(att_id, confirmed, outcome="interrupted")
        self.assertEqual(reconciled["state"], "interrupted")
        self.assertTrue(reconciled["workspace_removed"])
        self.assertFalse(ws.exists())

        # CRITICAL: Spool file preserved!
        spool_path = self.store.get_result_path(att_id)
        self.assertIsNotNone(spool_path)
        self.assertTrue(spool_path.is_file())

    def test_state_transitions_and_terminal_requirements(self) -> None:
        rec = self.store.accept_attempt(
            controller_id="ctrl-1",
            node_id="node-1",
            idempotency_key="key-trans",
            canonical_request_bytes=self.sample_req,
        )
        att_id = rec["acceptance"]["attempt_id"]

        # Valid transition: waiting_source -> waiting_idle
        s1 = self.store.update_state(att_id, "waiting_idle")
        self.assertEqual(s1["state"], "waiting_idle")

        # Invalid backwards transition from terminal or illegal jump
        with self.assertRaises(GateStoreStateError):
            self.store.update_state(att_id, "passed")  # requires result + release

        desc = self.store.persist_result_archive(att_id, b"result data")
        self.store.update_state(att_id, "running")
        # Terminal state passed requires confirmed release
        with self.assertRaises(GateStoreStateError):
            self.store.update_state(att_id, "passed", resource_release_confirmed=False)

        # Succeeded with release
        s_term = self.store.update_state(att_id, "passed", resource_release_confirmed=True)
        self.assertEqual(s_term["state"], "passed")

        # Terminal state cannot transition to running
        with self.assertRaises(GateStoreStateError):
            self.store.update_state(att_id, "running")

    def test_corrupt_journal_fails_closed_at_init(self) -> None:
        corrupt_root = self.root / "corrupt_store"
        s = DeveloperGateStore(root_dir=corrupt_root, max_workspace_bytes=1000, max_result_spool_bytes=1000)
        s.accept_attempt(
            controller_id="c", node_id="n", idempotency_key="k", canonical_request_bytes=self.sample_req
        )
        s.close()

        # Write corrupt JSON to attempts/
        bad_file = corrupt_root / "attempts" / "bad.json"
        bad_file.write_text("{corrupt-json", encoding="utf-8")

        with self.assertRaises(GateStoreError):
            DeveloperGateStore(root_dir=corrupt_root, max_workspace_bytes=1000, max_result_spool_bytes=1000)

    def test_deep_copy_prevents_stored_record_mutation(self) -> None:
        rec = self.store.accept_attempt(
            controller_id="ctrl-1",
            node_id="node-1",
            idempotency_key="key-mutation",
            canonical_request_bytes=self.sample_req,
        )
        att_id = rec["acceptance"]["attempt_id"]
        # Mutate returned dictionary
        rec["acceptance"]["controller_id"] = "mutated-ctrl"

        # Verify internal store was NOT modified
        stored = self.store.get_attempt(att_id)
        self.assertEqual(stored["acceptance"]["controller_id"], "ctrl-1")

    def test_crash_recovery_and_accounting(self) -> None:
        rec1 = self.store.accept_attempt(
            controller_id="ctrl-1",
            node_id="node-1",
            idempotency_key="key-recov1",
            canonical_request_bytes=self.sample_req,
            requested_workspace_bytes=150,
            reserved_spool_bytes=50,
        )
        att1 = rec1["acceptance"]["attempt_id"]
        self.store.persist_result_archive(att1, b"tarball 1")

        rec2 = self.store.accept_attempt(
            controller_id="ctrl-1",
            node_id="node-1",
            idempotency_key="key-recov2",
            canonical_request_bytes=canonical_json_bytes({"g": "two"}),
            requested_workspace_bytes=250,
        )

        active_ws_before = self.store.active_workspace_bytes
        active_sp_before = self.store.active_spool_bytes

        # Close current store to release file lock
        self.store.close()

        # Re-initialize store from same directory (simulating process restart)
        restarted_store = DeveloperGateStore(
            root_dir=self.root,
            max_workspace_bytes=1000,
            max_result_spool_bytes=2000,
        )
        self.assertEqual(restarted_store.active_workspace_bytes, active_ws_before)
        self.assertEqual(restarted_store.active_spool_bytes, active_sp_before)

        att1_loaded = restarted_store.get_attempt_by_key("key-recov1")
        self.assertEqual(att1_loaded["acceptance"]["attempt_id"], att1)
        self.assertIsNotNone(att1_loaded["result"])
        self.assertEqual(restarted_store.get_attempt_by_id(rec2["acceptance"]["attempt_id"])["acceptance"]["attempt_id"], rec2["acceptance"]["attempt_id"])
        restarted_store.close()

    def test_get_status_schema_structure(self) -> None:
        rec = self.store.accept_attempt(
            controller_id="ctrl-1",
            node_id="node-1",
            idempotency_key="key-status",
            canonical_request_bytes=self.sample_req,
        )
        att_id = rec["acceptance"]["attempt_id"]
        status = self.store.get_status(att_id)

        required_keys = {
            "format", "acceptance", "state", "reason", "accepted_at",
            "updated_at", "resource_release_confirmed", "result",
            "result_acknowledged", "workspace_removed", "inventory",
            "acknowledged_result", "acknowledged_at", "cleanup_after",
        }
        self.assertTrue(required_keys.issubset(status.keys()))
        self.assertEqual(status["format"], "studio.developer-gate.status.v1")


if __name__ == "__main__":
    unittest.main()
