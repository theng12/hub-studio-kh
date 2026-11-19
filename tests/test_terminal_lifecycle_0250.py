"""Leases follow their job, and uncertain items end inside their own bound (0.6.250).

Both defects came from production on 1400 (2026-11-19): every candidate Mac was refused
`machine_lease_busy` while its worker reported nothing running, and one Image item stayed
`uncertain` for 60,909 s with no outcome. All peers here are fake rows; no sockets are opened.
"""
import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from components.hub.backend import broker, ledger, peers


STUDIO = {
    "id": "image@node", "modality": "image", "machine": "node-a",
    "host": "127.0.0.1", "port": 51201, "hub_token": "fake-token",
    "runtime_identity": {"id": "fake", "compatible": True},
}


def _uncertain_batch(started_at, *, item_id=0):
    item = {
        "index": item_id, "state": "uncertain", "studio": STUDIO["id"],
        "studio_job_id": "job-1", "machine": STUDIO["machine"],
        "worker_id": STUDIO["id"], "model_revision": "r" * 40,
        "runtime_revision": "a" * 64, "deadline_s": 600.0,
        "execution_started_at": started_at,
        "recovery": {"phase": "uncertain", "reason": "the poll was lost"},
    }
    return {"id": "b-uncertain", "modality": "image", "items": [item]}


class _JobResponse:
    status_code = 200

    def __init__(self, state="done", job_id="job-1"):
        self._job = {"id": job_id, "state": state, "finished_at": time.time(),
                     "bytes": 12, "sha256": "abc", "media_type": "image/png"}

    def json(self):
        return {"job": self._job}


class _Client:
    def __init__(self, response):
        self.response = response

    async def get(self, url, **kwargs):
        return self.response

    async def post(self, url, **kwargs):
        raise AssertionError("a late answer must never submit work")


class TerminalLifecycle0250(unittest.TestCase):
    def setUp(self):
        self.saved = dict(broker.batches)
        broker.batches.clear()

    def tearDown(self):
        broker.batches.clear()
        broker.batches.update(self.saved)
        broker._external_machine_leases.clear()
        broker._external_machine_lease_since.clear()
        broker._machine_protection.clear()

    def test_a_terminal_owner_job_takes_its_machine_lease_with_it(self):
        from components.hub.backend import chat_jobs
        running = {"id": "chat-b", "packs": [{"index": 0, "state": "running"}]}
        with patch.dict(chat_jobs.batches, {running["id"]: running}, clear=False):
            broker.restore_external_machine("local", "chat:chat-b:0")
            self.assertEqual(broker.prune_stale_machine_leases(), [])
            self.assertIn("local", broker._external_machine_leases)
            running["packs"][0]["state"] = "done"
            self.assertEqual(broker.prune_stale_machine_leases(), ["local"])
            self.assertEqual(broker._external_machine_leases, {})

    def test_a_fresh_hub_prunes_leases_it_cannot_justify(self):
        from components.hub.backend import developer_test_admission
        with patch.object(broker.ledger, "load_unfinished_batches", return_value=[]), \
                patch.object(developer_test_admission,
                             "restore_before_production", return_value=[]):
            broker.restore_external_machine("node-a", "chat:missing-batch:0")
            broker.restore_external_machine("node-b", "")
            self.assertEqual(broker.restore_batches(), 0)
        self.assertEqual(broker._external_machine_leases, {})

    def test_lease_busy_with_an_idle_peer_is_named_instead_of_passed_up(self):
        idle = {"current_job": None, "studios": {}, "reachable": True,
                "status": "connected", "auth": True}
        with patch.object(peers, "fresh_snapshot", return_value=idle), \
                patch.object(broker, "busy_machines", return_value={"node-a"}):
            trace = [{"gate": "machine_lease_busy", "machine": "node-a"}]
            self.assertEqual(broker._lease_busy_while_idle_note(trace),
                             "peer says lease-busy while reporting 0 running")
            note = broker._no_candidate_note(
                {"modality": "image"}, [{"prompt": "x"}], STUDIO)
            self.assertEqual(note, "peer says lease-busy while reporting 0 running")

    def test_a_refusal_is_kept_on_the_mac_until_it_takes_work_again(self):
        broker.note_refusal(STUDIO, "INSUFFICIENT_MEMORY", "needs 12 GB, has 3 GB")
        snapshot = broker.machine_protection_snapshot()
        self.assertEqual(snapshot["node-a"]["refusal"]["code"], "INSUFFICIENT_MEMORY")
        self.assertEqual(snapshot["node-a"]["refusal"]["message"], "needs 12 GB, has 3 GB")
        broker._mark_machine_success(STUDIO)
        self.assertNotIn("refusal", broker.machine_protection_snapshot().get("node-a", {}))

    def test_a_lease_nobody_can_disprove_still_ends_at_the_stall_ceiling(self):
        late = time.time() - broker.automatic_recovery.MAX_STALL_S - 1
        broker._external_machine_leases["node-a"] = "peer:someone-else"
        broker._external_machine_lease_since["node-a"] = late
        broker._external_machine_leases["node-b"] = "peer:someone-else"
        broker._external_machine_lease_since["node-b"] = time.time()
        self.assertEqual(broker.prune_stale_machine_leases(), ["node-a"])
        self.assertEqual(set(broker._external_machine_leases), {"node-b"})

    def test_an_unanswered_uncertain_item_fails_retryable_at_its_bound(self):
        now = 1_000_000.0
        batch = _uncertain_batch(now - 3601)
        inside = _uncertain_batch(now - 60, item_id=1)
        inside["id"] = "b-inside"
        broker.batches[batch["id"]] = batch
        broker.batches[inside["id"]] = inside
        with patch.object(ledger, "save_batch"):
            self.assertEqual(broker.expire_uncertain_items(now=now),
                             ["b-uncertain:0"])
        item = batch["items"][0]
        self.assertEqual(item["state"], "error")
        self.assertTrue(item["retryable"])
        self.assertEqual(item["error_code"], "UNCERTAIN_BOUND_EXCEEDED")
        self.assertIn("the poll was lost", item["error"])
        self.assertIn("3600 s", item["error"])
        self.assertIsNone(item["retry_at"])
        self.assertEqual(item["recovery"]["phase"], "reconciliation_timeout")
        self.assertEqual(inside["items"][0]["state"], "uncertain")

    def test_a_late_answer_is_kept_beside_the_bound_verdict(self):
        now = 1_000_000.0
        batch = _uncertain_batch(now - 3601)
        broker.batches[batch["id"]] = batch
        with patch.object(ledger, "save_batch"):
            broker.expire_uncertain_items(now=now)
        answer = _JobResponse()
        with patch.object(broker, "_monitor",
                          return_value=SimpleNamespace(registry=[STUDIO], status={})), \
                patch.object(ledger, "save_batch"):
            result = asyncio.run(broker._reconcile_async_item(
                batch["id"], 0, "image", _Client(answer)))
        self.assertFalse(result["reconciled"])
        self.assertEqual(batch["items"][0]["state"], "error")
        self.assertEqual(batch["items"][0]["late_result"]["studio_job_id"], "job-1")
        self.assertEqual(batch["items"][0]["late_result"]["sha256"], "abc")


if __name__ == "__main__":
    unittest.main()
