"""The transport-failure episode, the recovery window, reattachment and the disk gate (2026-11-16).

Why these exist: six production audio jobs ended "connection recovery exhausted" in a 260 ms burst
while the link was healthy again in 0.07 s. The budget that ended them was the **item's lifetime**
(`infra_failures` / `infra_failure_started_at` were never reset), and the reconcile window was a flat
120 s against a 1,747 s measured Breeze section. This file pins the behaviour that replaces both,
plus the two refusal verdicts that spent a Gateway attempt on a Mac with a full trash.

Unit level only: fixture items and batches, a patched ledger write, no model, no network, no audio.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The broker binds DATA_DIR at import; keep every write in a throwaway directory.
_TEMP = tempfile.TemporaryDirectory(prefix="hub-infra-recovery-")
os.environ.setdefault("STUDIOHUB_DATA_DIR", str(Path(_TEMP.name) / "data"))
os.environ.setdefault("STUDIOHUB_WORKERS_FILE", str(Path(_TEMP.name) / "workers.json"))
_DATA = Path(os.environ["STUDIOHUB_DATA_DIR"])
_DATA.mkdir(parents=True, exist_ok=True)
(_DATA / ".hub_token").write_text("fixture-token\n")
os.chmod(_DATA / ".hub_token", 0o600)

from components.hub.backend import broker  # noqa: E402

LOG = logging.getLogger("test.infra-recovery")


def _item(**extra) -> dict:
    item = {"index": 0, "state": "running", "tries": 1, "params": {}, "prompt": "x"}
    item.update(extra)
    return item


def _batch(batch_id: str = "b1", modality: str = "voice", items: list[dict] | None = None) -> dict:
    return {"id": batch_id, "created_at": 1.0, "cancelled": False, "modality": modality,
            "model": "model", "routing": "pool", "shared_params": {}, "items": items or []}


def _studio(machine: str = "node-a") -> dict:
    return {"id": f"voice@{machine}", "machine": machine, "modality": "voice"}


class InfraEpisodeTests(unittest.TestCase):
    """The counters are per consecutive failure, and the verdict carries its evidence."""

    def test_an_expired_failure_does_not_end_the_item(self) -> None:
        """The exact case that killed the six episodes: an old failure, one drop, still alive."""
        item = _item(infra_failures=7, infra_failure_started_at=1_000.0)
        now = 1_000.0 + broker.INFRA_RETRY_WINDOW_S + 60.0

        verdict = broker._record_infra_failure(item, _studio(), "server disconnected", now)

        self.assertEqual(verdict, "retry")
        self.assertEqual(item["state"], "queued")
        self.assertEqual(item["infra_failures"], 1, "a drop after the window starts a new episode")
        self.assertEqual(item["infra_failure_started_at"], now)
        self.assertEqual(item["retry_at"], now + broker.INFRA_RETRY_DELAYS_S[0],
                         "one plain 5 s retry")
        self.assertNotIn("terminal_clause", item)

    def test_eight_failures_in_one_episode_end_the_item_with_their_evidence(self) -> None:
        item = _item()
        now = 5_000.0
        for step in range(broker.MAX_INFRA_TRIES - 1):
            self.assertEqual(
                broker._record_infra_failure(item, _studio(), "server disconnected", now + step * 5.0),
                "retry")
        verdict = broker._record_infra_failure(item, _studio(), "server disconnected", now + 40.0)

        self.assertEqual(verdict, "terminal")
        self.assertEqual(item["state"], "error")
        self.assertIn("connection recovery exhausted", item["error"])
        self.assertEqual(item["terminal_clause"], "max_infra_tries")
        self.assertEqual(item["infra_failures"], broker.MAX_INFRA_TRIES)
        self.assertEqual(item["infra_failure_started_at"], now,
                         "the record names when this episode began")
        self.assertIsNone(item["retry_at"])

    def test_a_success_clears_the_episode(self) -> None:
        item = _item(infra_failures=5, infra_failure_started_at=900.0)
        broker._reset_infra_episode(item)
        self.assertNotIn("infra_failures", item)
        self.assertNotIn("infra_failure_started_at", item)

    def test_the_terminal_evidence_stays_on_the_public_item(self) -> None:
        batch = _batch(items=[])
        item = _item(state="error", error="connection recovery exhausted: x",
                     infra_failures=8, infra_failure_started_at=111.0,
                     terminal_clause="max_infra_tries")
        batch["items"].append(item)

        view = broker.public_item(batch, item)

        self.assertEqual(view["infra_failures"], 8)
        self.assertEqual(view["infra_failure_started_at"], 111.0)
        self.assertEqual(view["terminal_clause"], "max_infra_tries")


class RecoveryWindowTests(unittest.TestCase):
    """The window comes from the lane's own published deadline, never a flat 120 s."""

    def test_the_voice_window_covers_a_real_request(self) -> None:
        budget = broker._recovery_budget_s({"modality": "voice"}, time.time())
        self.assertGreaterEqual(budget, 1_800.0,
                                "the voice lane's published floor is 1,800 s; 120 s abandoned it")

    def test_a_worker_stated_deadline_raises_the_window(self) -> None:
        plain = broker._recovery_budget_s({"modality": "voice"}, time.time())
        stated = broker._recovery_budget_s({"modality": "voice"}, time.time(), {"deadline_s": 7_200.0})
        self.assertGreater(stated, plain)

    def test_a_job_past_its_ceiling_still_gets_the_plain_window(self) -> None:
        budget = broker._recovery_budget_s({"modality": "voice"}, time.time() - 10_000.0)
        self.assertEqual(budget, broker.RECOVERY_WINDOW_S)

    def test_a_silent_worker_ends_sooner_than_the_lane_ceiling(self) -> None:
        self.assertLess(broker.RECOVERY_UNREACHABLE_S, broker.RECOVERY_WINDOW_S)
        self.assertLess(broker.RECOVERY_UNREACHABLE_S, broker._recovery_budget_s(
            {"modality": "voice"}, time.time()))


class RecoveryLoopTests(unittest.TestCase):
    """The long ceiling only holds while the worker says it is still working."""

    STUDIO = {"id": "render@local", "machine": "local", "modality": "render", "local": True,
              "host": "127.0.0.1", "port": 51999, "hub_token": "fixture-token"}

    class _Client:
        def __init__(self, answers):
            self.answers = list(answers)
            self.polls = 0

        async def get(self, url, **kwargs):
            import httpx
            self.polls += 1
            answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
            if answer == "garbled":
                return httpx.Response(200, content=b"not json")
            return httpx.Response(200, json={"job": {"id": "job-1", "state": answer}})

    def _run(self, client) -> object:
        import asyncio
        item = _item(studio_job_id="job-1")
        batch = _batch("loop", modality="render", items=[item])
        with mock.patch.object(broker, "POLL_S", 0.02), \
                mock.patch.object(broker, "RECOVERY_WINDOW_S", 0.05), \
                mock.patch.object(broker, "_record_worker_success",
                                  new=mock.AsyncMock(return_value=True)), \
                mock.patch.object(broker, "_mark_machine_success"):
            return asyncio.run(broker._recover_worker_job(client, batch, item, self.STUDIO, {}, time.time()))

    def test_a_slow_job_outlives_the_short_window_and_is_adopted(self) -> None:
        client = self._Client(["running"] * 12 + ["done"])   # ~0.24 s of "still working" > 0.05 s
        self.assertIs(self._run(client), True)
        self.assertEqual(client.polls, 13, "polled the same job until it finished; nothing resubmitted")

    def test_a_garbled_answer_is_bounded_by_the_short_window_not_the_ceiling(self) -> None:
        started = time.monotonic()
        self.assertIs(self._run(self._Client(["garbled"])), False)
        self.assertLess(time.monotonic() - started, 5.0,
                        "a bad receipt must not be polled for the 1,020 s lane ceiling")


class ReattachTests(unittest.TestCase):
    """A dropped attempt keeps its request id; only a person asking again takes a new one."""

    def setUp(self) -> None:
        broker.batches.clear()
        self.addCleanup(broker.batches.clear)
        self.addCleanup(broker._restored_attempts.clear)

    def test_a_transport_retry_keeps_the_id_so_the_worker_reattaches(self) -> None:
        batch = _batch(items=[])
        item = _item(client_request_id="studiohub:b1:0", studio_job_id="voice-1")
        with mock.patch.object(broker, "_watch_abandoned_attempt") as watch:
            broker._abandon_attempt(None, batch, item, _studio(), {}, 0.0)
        self.assertTrue(watch.called, "the original is still watched for a late result")
        self.assertEqual(item["client_request_id"], "studiohub:b1:0",
                         "a retry after a drop must reattach, not generate a second job")
        self.assertNotIn("studio_job_id", item)

    def test_a_hub_restart_keeps_the_id_too(self) -> None:
        batch = _batch(items=[])
        item = _item(studio="voice@node-a", studio_job_id="voice-1",
                     client_request_id="studiohub:b1:0", run_started=1.0)
        batch["items"].append(item)

        self.assertTrue(broker._rerun_after_restart(batch, item))

        self.assertEqual(item["state"], "queued")
        self.assertEqual(item["client_request_id"], "studiohub:b1:0")

    def test_a_user_retry_takes_a_new_id(self) -> None:
        item = _item(state="error", error="x", tries=3, client_request_id="studiohub:b1:0")
        batch = _batch(items=[item])
        broker.batches["b1"] = batch
        with mock.patch.object(broker.ledger, "save_batch"):
            broker.retry_failed_item("b1", 0)
        self.assertEqual(item["client_request_id"], "studiohub:b1:0:rerun-1",
                         "a person asked for the work again: a new generation, not a reattach")
        self.assertEqual(item["state"], "queued")

    def test_the_bump_is_monotonic(self) -> None:
        item = _item(reruns=2)
        self.assertEqual(broker._bump_rerun_id(_batch(), item), "studiohub:b1:0:rerun-3")


class LateResultTests(unittest.TestCase):
    """A result after terminal is kept beside the verdict and never flips the row."""

    def setUp(self) -> None:
        broker.batches.clear()
        self.addCleanup(broker.batches.clear)

    def test_a_late_result_is_recorded_beside_the_error(self) -> None:
        item = _item(state="error", error="connection recovery exhausted: x")
        batch = _batch("late", items=[item])
        broker.batches["late"] = batch
        job = {"id": "voice-9", "state": "done", "finished_at": 123.0,
               "bytes": 4_567, "sha256": "a" * 64, "media_type": "audio/wav"}

        with mock.patch.object(broker.ledger, "save_batch") as save:
            broker._record_late_result(batch, item, _studio(), job, LOG)

        self.assertEqual(item["state"], "error", "terminal stays terminal")
        self.assertEqual(item["error"], "connection recovery exhausted: x")
        self.assertEqual(item["late_result"]["studio_job_id"], "voice-9")
        self.assertEqual(item["late_result"]["finished_at"], 123.0)
        self.assertEqual(item["late_result"]["bytes"], 4_567)
        self.assertTrue(save.called, "the evidence is durable, not only in memory")

    def test_a_second_late_result_does_not_rewrite_the_first(self) -> None:
        item = _item(state="error")
        batch = _batch("late", items=[item])
        broker.batches["late"] = batch
        with mock.patch.object(broker.ledger, "save_batch"):
            broker._record_late_result(batch, item, _studio(), {"id": "voice-1", "state": "done"}, LOG)
            broker._record_late_result(batch, item, _studio(), {"id": "voice-2", "state": "done"}, LOG)
        self.assertEqual(item["late_result"]["studio_job_id"], "voice-1")


class DiskGateTests(unittest.TestCase):
    """A Mac that says its own disk is stop/red is skipped before any attempt."""

    def test_stop_and_red_are_skipped_with_the_macs_own_numbers(self) -> None:
        for tier in ("stop", "red"):
            decision, note = broker._disk_gate(
                {"disk": {"state": tier, "free_gb": 1.2}, "disk_free_gb": 1.2, "available_gb": 30})
            self.assertEqual(decision, "skip", tier)
            self.assertIn(tier, note)
            self.assertIn("1.2", note)
            self.assertIn("Trash", note)

    def test_amber_green_unknown_and_missing_are_offered(self) -> None:
        for host in ({"disk": {"state": "amber", "free_gb": 20}},
                     {"disk": {"state": "green", "free_gb": 100}},
                     {"disk_free_gb": 3.0}, {}, None):
            self.assertEqual(broker._disk_gate(host)[0], "run", host)

    def test_a_skipped_mac_spends_nothing_and_stays_queued(self) -> None:
        item = _item(state="queued", tries=0, params={"x": 1})
        batch = _batch(items=[item])
        before = dict(item)
        with mock.patch.object(broker, "_host_for_studio",
                               return_value={"disk": {"state": "red", "free_gb": 0.4}}):
            allowed = broker._disk_allows(batch, _studio(), None)
        self.assertFalse(allowed, "a red-disk Mac is not offered")
        self.assertEqual(item, before, "no attempt, no try, no state change")
        self.assertIn("output storage is red", batch["governor_note"])


class RefusalVerdictTests(unittest.TestCase):
    """The same disk refusal gets one verdict on every path; a contract refusal stays terminal."""

    def _refusal(self, status: int, code: str | None, detail: str) -> BaseException:
        error = RuntimeError(f"HTTP {status}: {detail}")
        error.status_code = status
        error.error_code = code
        return error

    def test_the_governors_sentence_is_a_disk_refusal_without_a_code(self) -> None:
        error = self._refusal(507, None,
                              "managed output storage has insufficient free space; "
                              "empty the Trash Folder (/Users/x/Trash Folder) to free space")
        self.assertTrue(broker._is_disk_full_refusal(error))

    def test_status_and_code_are_disk_refusals(self) -> None:
        self.assertTrue(broker._is_disk_full_refusal(self._refusal(507, None, "full")))
        self.assertTrue(broker._is_disk_full_refusal(self._refusal(400, "OUTPUT_STORAGE_BLOCKED", "x")))
        self.assertTrue(broker._is_disk_full_refusal(self._refusal(400, "DISK_FULL", "x")))

    def test_a_reference_contract_refusal_is_not_a_disk_wait(self) -> None:
        error = self._refusal(400, "REFERENCE_AUDIO_CONTRACT_UNAVAILABLE", "no contract installed")
        self.assertFalse(broker._is_disk_full_refusal(error))

    def test_a_disk_refusal_never_becomes_a_reference_rejection(self) -> None:
        item = _item(state="running")
        error = self._refusal(507, None,
                              "managed output storage has insufficient free space; "
                              "empty the Trash Folder (/x) to free space")
        verdict = broker._reference_refusal_verdict(item, error, "insufficient free space")
        self.assertEqual(verdict, "raise", "the handler's disk-full wait owns it")
        self.assertEqual(item.get("error_code"), "OUTPUT_STORAGE_BLOCKED")
        self.assertNotIn("state", {k: v for k, v in item.items() if k == "state" and v == "error"})

    def test_a_reserved_mac_raises_rather_than_failing(self) -> None:
        item = _item()
        error = self._refusal(409, "MACHINE_BUSY", "local is reserved for other work")
        self.assertEqual(broker._reference_refusal_verdict(item, error, "busy"), "raise")
        self.assertEqual(item["error_code"], "MACHINE_BUSY")

    def test_a_real_reference_rejection_stays_terminal(self) -> None:
        item = _item()
        error = self._refusal(400, "REFERENCE_AUDIO_CONTRACT_UNAVAILABLE", "no contract")
        self.assertEqual(broker._reference_refusal_verdict(item, error, "no contract"), "terminal")
        self.assertEqual(item["state"], "error")
        self.assertEqual(item["error_code"], "VOICE_REFERENCE_REJECTED")
        self.assertIn("rejected the reference", item["error"])


if __name__ == "__main__":
    unittest.main()
