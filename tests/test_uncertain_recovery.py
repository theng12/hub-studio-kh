"""An uncertain transcription must not hold its Mac forever.

"Uncertain" exists because a lost response might mean the worker is still transcribing, and two
transcripts of one recording would be wasteful. But nothing ever resolved it: `retry_batch` skips an
item with `manual_action_required`, `remove_batch` and `clear_terminal` refuse a batch that is not
terminal, no HTTP route exposes any of the three, and `_eligible_studios` excludes the machine that
holds one - from image, voice and subtitle alike. One lost response took a Mac out of the fleet for
good, and the tests asserted that dead end rather than a way out of it.

The owner's rule: recover after a set time frame, because transcription is cheap enough to redo.
"""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub.backend import transcription_jobs as jobs  # noqa: E402


def _batch(state: str, *, age_seconds: float, manual: bool = True) -> dict:
    now = time.time()
    return {
        "id": "stt-batch", "created_at": now - age_seconds - 60, "cancelled": False,
        "governor_note": None,
        "items": [{
            "index": 0, "state": state, "finished_at": now - age_seconds,
            "manual_action_required": manual, "output_path": "/tmp/audio-out.wav",
            "partial_output_path": None, "studio_job_id": "worker-job",
        }],
    }


class UncertainRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(jobs.batches)
        self.saved_save = jobs._save
        self.saved_calls = []
        jobs._save = lambda batch: self.saved_calls.append(batch["id"])

    def tearDown(self):
        jobs.batches.clear()
        jobs.batches.update(self.saved)
        jobs._save = self.saved_save

    def _run(self, batch):
        jobs.batches.clear()
        jobs.batches[batch["id"]] = batch
        return jobs.recover_stale_uncertain()

    def test_a_stale_uncertain_transcription_is_queued_again(self):
        batch = _batch("uncertain", age_seconds=jobs.UNCERTAIN_RECOVERY_SECONDS + 60)
        self.assertEqual(self._run(batch), ["stt-batch"])
        item = batch["items"][0]
        self.assertEqual(item["state"], "queued", "it has to be tried again")
        self.assertFalse(item["manual_action_required"], "and stop asking for a decision")
        self.assertIsNone(item["output_path"], "a half-written transcript is not kept")
        self.assertEqual(item["retry_at"], 0, "it is due now, not after a backoff")
        self.assertIn("again", batch["governor_note"])
        self.assertEqual(self.saved_calls, ["stt-batch"], "and the change must be durable")

    def test_a_fresh_uncertain_transcription_is_left_alone(self):
        """Still inside the window: the worker may genuinely be working."""
        batch = _batch("uncertain", age_seconds=30)
        self.assertEqual(self._run(batch), [])
        self.assertEqual(batch["items"][0]["state"], "uncertain")
        self.assertTrue(batch["items"][0]["manual_action_required"])

    def test_a_running_or_done_transcription_is_never_touched(self):
        for state in ("running", "done", "error", "queued"):
            batch = _batch(state, age_seconds=jobs.UNCERTAIN_RECOVERY_SECONDS * 10, manual=False)
            self.assertEqual(self._run(batch), [], state)
            self.assertEqual(batch["items"][0]["state"], state)

    def test_the_window_is_a_real_time_frame(self):
        self.assertGreaterEqual(jobs.UNCERTAIN_RECOVERY_SECONDS, 300)
        self.assertLessEqual(jobs.UNCERTAIN_RECOVERY_SECONDS, 60 * 60)


if __name__ == "__main__":
    unittest.main()
