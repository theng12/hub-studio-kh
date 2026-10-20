"""A local Image or Voice job whose own stage counter stops moving is stopped, and says so.

`backend/automatic_recovery.py` is this Hub's policy for bounded local Image/Voice recovery: a
measured stall budget per modality, a durable record of what it has already decided, and an
`eligible` verdict. Nothing called it, so a local job whose progress froze was only *flagged*
(`stall.py` deliberately never acts on its own) while the lane deadline the Hub publishes is the
*caller's* to poll against — and a job this Mac's own page submitted has no such caller. That is
the gap `broker._stop_a_stalled_local_job` fills.

Two things are pinned here beyond the stop itself:

* the observer reads the field the **worker** publishes. It used to name `current_step` for Image,
  which no worker in this app has ever written (`components/image/app/backend/generation.py`
  publishes `denoise_step`), so `stage_evidence` was false for every Image job and the whole
  observer could never fire. A signature that reads a field nobody writes is not a conservative
  policy, it is an inert one;
* the budgets are the module's measured ones, not a shorter window borrowed from Chat. Voice's
  progress moves only at long-form **section** boundaries, and one measured Breeze section took
  1,747 s on an M1 — a five-minute window would stop healthy narration.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import time
import unittest

from components.hub.backend import automatic_recovery, broker

#: Image Studio's own step counters, as its worker publishes them.
IMAGE_STAGE = ("denoise_step", "denoise_steps")
#: Voice Studio's own long-form section counters.
VOICE_STAGE = ("chunk_index", "chunk_total")


def _job(**over) -> dict:
    """One running worker job, as the Hub reads it back from `/api/generate/jobs/{id}`."""
    job = {"id": "job-1", "state": "running", "progress": 0.4,
           "denoise_step": 4, "denoise_steps": 16,
           "chunk_index": 2, "chunk_total": 9,
           "started_at": time.time() - 8 * 3600.0}
    job.update(over)
    return job


class LocalStallStopTests(unittest.TestCase):
    """The caller, driven through the real policy in `automatic_recovery`."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.previous_recovery = broker._RECOVERY
        self.addCleanup(setattr, broker, "_RECOVERY", self.previous_recovery)
        broker._RECOVERY = automatic_recovery.AutomaticRecoveryState(
            Path(self.temporary.name) / "recovery.json")
        self.previous_save = broker.ledger.save_batch
        self.addCleanup(setattr, broker.ledger, "save_batch", self.previous_save)
        broker.ledger.save_batch = lambda _batch: None
        self.cancels: list[dict] = []
        self.cancel_taken = True

        async def _cancel(_client, item):
            self.cancels.append(dict(item))
            return self.cancel_taken

        self.previous_cancel = broker._signal_worker_cancel
        self.addCleanup(setattr, broker, "_signal_worker_cancel", self.previous_cancel)
        broker._signal_worker_cancel = _cancel

    def _watch(self, studio_id: str, modality: str, jobs: list[dict], *, end: float,
               span: float = 3800.0) -> None:
        """Feed the observer one job per poll, ending just before ``end``.

        `MAX_OBSERVATION_GAP_S` is five minutes, so a job that has been frozen for hours has to be
        seen repeatedly inside that gap — which is what the real poll loop (every 2 s) does.
        """
        start = end - span
        step = 200.0
        for index in range(int(span / step)):
            broker._recovery().observe(studio_id, modality, jobs[index % len(jobs)],
                                       now=start + index * step)
        # One last poll well inside `MAX_OBSERVATION_GAP_S` of the caller's own clock, which is
        # the poll the caller under test performs.
        broker._recovery().observe(studio_id, modality, jobs[-1], now=end - 60.0)

    def _stop(self, modality: str, job: dict, *, studio: dict | None = None) -> bool:
        studio = studio or {"id": "image-1", "modality": modality, "machine": "local"}
        item = {"index": 0, "state": "running", "studio": studio["id"], "studio_job_id": job["id"]}
        batch = {"id": "b1", "modality": modality, "items": [item]}
        return asyncio.run(broker._stop_a_stalled_local_job(None, batch, item, studio, job)), item

    def test_the_signature_reads_the_field_the_image_worker_publishes(self) -> None:
        self.assertEqual(automatic_recovery.progress_fields("image"), IMAGE_STAGE)
        self.assertEqual(automatic_recovery.progress_fields("voice"), VOICE_STAGE)
        frozen = _job()
        seen = broker._recovery().observe("image-1", "image", frozen, now=time.time())
        self.assertTrue(seen["stage_evidence"],
                        "an Image job's own denoise counters are the evidence this needs")

    def test_a_local_image_job_that_stopped_moving_is_stopped_with_a_plain_reason(self) -> None:
        frozen = _job()
        self._watch("image-1", "image", [dict(frozen) for _ in range(20)], end=time.time())
        stopped, item = self._stop("image", frozen)
        self.assertTrue(stopped, "an hour of no denoise step is a stalled Image job")
        self.assertEqual(item["error_code"], "STALLED")
        self.assertIn("no image progress", item["error"])
        self.assertIn("minute", item["error"], "the reason says how long in words, not seconds")
        self.assertEqual(item["state"], "error")
        self.assertTrue(self.cancels, "the worker was never told to stop")
        row = broker._recovery().snapshot()["jobs"][0]
        self.assertEqual(row["phase"], "cancel_requested")
        self.assertEqual(row["reason"], item["error"])

    def test_a_local_voice_job_that_stopped_moving_is_stopped_the_same_way(self) -> None:
        frozen = _job()
        self._watch("voice-1", "voice", [dict(frozen) for _ in range(34)], end=time.time(),
                    span=7400.0)
        stopped, item = self._stop("voice", frozen,
                                   studio={"id": "voice-1", "modality": "voice", "machine": "local"})
        self.assertTrue(stopped, "two hours of no new section is a stalled Voice job")
        self.assertIn("no voice progress", item["error"])
        self.assertIn("hour", item["error"])

    def test_a_job_that_is_still_moving_is_left_alone(self) -> None:
        moving = [dict(_job(), denoise_step=step) for step in range(20)]
        self._watch("image-1", "image", moving, end=time.time())
        stopped, item = self._stop("image", moving[-1])
        self.assertFalse(stopped, "a job that keeps moving is a job that keeps working")
        self.assertEqual(item["state"], "running")
        self.assertEqual(self.cancels, [])

    def test_a_job_that_has_not_reached_the_budget_is_left_alone(self) -> None:
        """The window is the module's measured budget, not the poll: ten minutes is not a stall."""
        frozen = _job(started_at=time.time() - 600.0)
        self._watch("image-1", "image", [dict(frozen) for _ in range(20)], end=time.time())
        stopped, _item = self._stop("image", frozen)
        self.assertFalse(stopped)

    def test_a_worker_that_does_not_take_the_stop_is_tried_again(self) -> None:
        """The item is only ended once the worker took the stop signal.

        Ending it on a signal that never landed would report a dead job while a live one still
        holds the Mac — and the durable record would then say `cancel_requested`, which the policy
        reads as protected and never retries.
        """
        self.cancel_taken = False
        frozen = _job()
        self._watch("image-1", "image", [dict(frozen) for _ in range(20)], end=time.time())
        stopped, item = self._stop("image", frozen)
        self.assertFalse(stopped)
        self.assertEqual(item["state"], "running")
        self.assertNotIn("error_code", item)
        self.assertTrue(self.cancels, "the worker was still told to stop")
        self.assertNotEqual(broker._recovery().snapshot()["jobs"][0]["phase"], "cancel_requested",
                            "a stop that was not taken is not recorded as one")

    def test_a_nodes_job_is_the_nodes_to_stop(self) -> None:
        frozen = _job()
        self._watch("node-a:image", "image", [dict(frozen) for _ in range(20)], end=time.time())
        stopped, item = self._stop(
            "image", frozen,
            studio={"id": "node-a:image", "modality": "image", "machine": "node-a", "local": False})
        self.assertFalse(stopped, "a peer's job is reconciled by the peer, not stopped from here")
        self.assertEqual(self.cancels, [])


if __name__ == "__main__":
    unittest.main()
