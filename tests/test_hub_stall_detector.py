"""The stall detector: silence while holding a job, and the boundaries where it must stay quiet.

The cases that matter are as much about *not* firing as about firing. A detector that flags a
healthy worker between two snapshots, or a job that has not started yet, is worse than no detector:
it teaches the operator to ignore the flag.
"""

from __future__ import annotations

import unittest

from components.hub.backend import stall


class StallDetectorTests(unittest.TestCase):
    NOW = 1_000_000.0

    def test_an_active_job_on_a_silent_studio_is_stalled(self) -> None:
        silent_for = stall.stall_seconds("running", self.NOW - 600, self.NOW)
        self.assertIsNotNone(silent_for)
        self.assertAlmostEqual(silent_for, 600.0)

    def test_an_active_job_on_a_reporting_studio_is_not_stalled(self) -> None:
        self.assertIsNone(stall.stall_seconds("running", self.NOW - 5, self.NOW))

    def test_a_cancelling_job_counts_too(self) -> None:
        """A cancel that never lands is the same silence, and the operator is waiting on it."""
        self.assertTrue(stall.is_stalled("cancel_requested", self.NOW - 600, self.NOW))

    def test_a_queued_job_is_never_stalled(self) -> None:
        """Nothing is running yet, so silence is the expected state, not a fault."""
        self.assertFalse(stall.is_stalled("queued", self.NOW - 100_000, self.NOW))

    def test_every_terminal_state_is_never_stalled(self) -> None:
        """The lane contract's terminal vocabulary, and `uncertain` beside it: nothing to wait for."""
        for state in ("done", "error", "cancelled", "uncertain"):
            self.assertFalse(stall.is_stalled(state, self.NOW - 100_000, self.NOW),
                             f"{state} is not an active state")

    def test_a_studio_that_never_reported_is_not_stalled(self) -> None:
        """Absence of a receipt is not staleness; a worker restarting would otherwise be flagged
        on its first polls, which is exactly when the operator should not be alarmed."""
        for never in (None, 0, "not-a-number"):
            self.assertFalse(stall.is_stalled("running", never, self.NOW), f"receipt={never!r}")

    def test_the_boundary_is_not_a_stall_and_one_second_past_it_is(self) -> None:
        self.assertFalse(stall.is_stalled("running", self.NOW - stall.STALL_AFTER_S, self.NOW))
        self.assertTrue(
            stall.is_stalled("running", self.NOW - stall.STALL_AFTER_S - 1, self.NOW))

    def test_the_threshold_is_far_above_any_plausible_report_interval(self) -> None:
        """A guard on the constant, not on a call: this must never be a per-snapshot judgement.

        The Hub polls studio health every 5 seconds (`monitor.POLL_INTERVAL_S`), so anything near
        that would flag healthy workers continuously. The bar is set well clear of it on purpose.
        """
        from components.hub.backend import monitor

        self.assertGreater(stall.STALL_AFTER_S, monitor.POLL_INTERVAL_S * 10,
                           "the stall threshold must be far above the poll interval")

    def test_an_active_state_set_is_not_empty_and_matches_the_lane_vocabulary(self) -> None:
        """The two states a lane contract publishes as `active`. If a lane ever adds one, this
        fails by name rather than silently not watching it."""
        self.assertEqual(stall.ACTIVE_STATES, frozenset({"running", "cancel_requested"}))


if __name__ == "__main__":
    unittest.main()
