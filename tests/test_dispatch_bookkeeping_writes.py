"""A failed bookkeeping write must not abort a dispatch pass.

The dispatch loop writes as it goes: which studio was tried, why it was refused, a lease that
expired, a note for the operator. Those calls were unwrapped, so a full disk or an unreadable
database raised out of `dispatch_once` - and the effect was not a lost note but a stopped scheduler:
no other job could be dispatched, and the failure surfaced as a scheduler error rather than as "this
Mac cannot write". Found while trying to reproduce the leased-dispatch rollback, which is the same
family of defect one call site along.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub.backend import transcription_jobs as jobs  # noqa: E402

SOURCE = (Path(__file__).resolve().parents[1]
          / "components/hub/backend/transcription_jobs.py").read_text()


class BookkeepingWriteTests(unittest.TestCase):
    def test_a_failing_write_is_reported_and_survived(self):
        with mock.patch.object(jobs, "_save", side_effect=OSError("disk full")):
            self.assertFalse(jobs._save_quietly({"id": "batch-1"}),
                             "a failed write must be reported, not raised")

    def test_a_good_write_still_reports_success(self):
        with mock.patch.object(jobs, "_save") as saved:
            self.assertTrue(jobs._save_quietly({"id": "batch-1"}))
        self.assertTrue(saved.called)

    def test_the_note_paths_go_through_the_quiet_helper(self):
        self.assertIn("_save_quietly(batch)", SOURCE,
                      "the dispatch loop's bookkeeping must not raise")
        # The governor-note writes are the ones the trace showed escaping `dispatch_once`.
        note_blocks = [line for line in SOURCE.splitlines()
                       if "governor_note" in line or "_save_quietly(batch)" in line]
        self.assertTrue(any("_save_quietly(batch)" in line for line in note_blocks))
        self.assertGreaterEqual(SOURCE.count("_save_quietly(batch)"), 5,
                                "every bookkeeping write in the dispatch loop is routed through it")


if __name__ == "__main__":
    unittest.main()
