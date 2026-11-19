"""A job silent past its own modality's ceiling is reported, never cancelled.

Finding 11 (idle-capacity, second sweep): an active row read "working" for ever because
``needs_attention`` only knew about health and recent errors. Fake rows only: no sockets.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub import attention  # noqa: E402
from components.hub.backend import activity, ledger  # noqa: E402

NOW = 1_800_000_000.0
GREEN = {"state": "green", "free_gb": 200.0, "total_gb": 460.0}


def row(modality: str, silent_for: float) -> dict:
    return {"machine": "m", "studio": modality, "state": "running", "modality": modality,
            "job_id": "job-a", "activity_received_at": NOW - silent_for,
            "updated_at": NOW - silent_for, "observed_at": NOW}


class StalledRowAttention(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="hub-stalled-attention-")
        self.addCleanup(temp.cleanup)
        patcher = mock.patch.object(ledger, "DATA_DIR", Path(temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        attention.reset_cache()
        self.addCleanup(attention.reset_cache)

    def report(self, *rows: dict) -> dict:
        return activity.attention_report(list(rows), NOW, disk=GREEN)

    def test_a_job_past_its_modality_ceiling_is_in_the_attention_report(self) -> None:
        stuck = row("image", 72 * 60)
        before = dict(stuck)
        got = self.report(stuck)
        self.assertEqual(got["state"], "attention")
        self.assertEqual(len(got["reasons"]), 1)
        self.assertIn(got["reasons"][0]["code"], attention.REASON_CODES)
        self.assertEqual(got["reasons"][0]["detail"],
                         "Image job running 72 minutes with no progress — check the Mac")
        self.assertEqual(stuck, before, "a report must not touch the job")
        self.assertEqual(stuck["state"], "running")

    def test_a_job_just_under_its_ceiling_is_not_reported(self) -> None:
        self.assertEqual(self.report(row("image", 59 * 60))["reasons"], [])
        self.assertEqual(self.report(row("image", 59 * 60))["state"], "ok")

    def test_each_modality_uses_its_own_ceiling_not_a_shared_number(self) -> None:
        # Voice's measured ceiling is 2 h, so an 83-minute silence is still honest.
        self.assertEqual(self.report(row("voice", 5000))["reasons"], [])
        voice = row("voice", 7300)
        got = self.report(voice)
        self.assertEqual(got["reasons"][0]["detail"],
                         "Voice job running 2 hours with no progress — check the Mac")
        self.assertEqual(voice["state"], "running")

    def test_the_machine_state_says_needs_attention_without_acting_on_the_job(self) -> None:
        statuses = {"image": {"status": "up", "activity_support": "available"}}
        studios = [{"id": "image", "modality": "image", "machine": "m"}]
        stuck = row("image", 2 * 3600)
        state, _, current, _ = activity._state_at("m", statuses, studios, [stuck], [], NOW)
        self.assertEqual(state, "needs_attention")
        self.assertEqual(current["job_id"], "job-a")
        self.assertEqual(stuck["state"], "running")
        self.assertEqual(self.report(stuck)["reasons"][0]["detail"],
                         "Image job running 2 hours with no progress — check the Mac")


class CachedEndpointCarriesTheSameSentence(unittest.TestCase):
    """`/api/hub/attention` builds its word from `standalone._recent_job_state()`.

    The measured report and the fleet row must not disagree about the same Mac, so the cached
    endpoint gets the same sentence rather than only the generic one.
    """

    def test_the_measured_report_names_the_stalled_job(self):
        from components.hub import standalone
        stuck = row("image", 2 * 3600)
        batch = {"id": "b" * 32, "modality": "image", "items": [dict(stuck, state="running",
                                                                   last_progress_at=NOW - 7200)]}
        with mock.patch.dict(standalone.broker.batches, {batch["id"]: batch}, clear=True), \
                mock.patch("time.time", return_value=NOW):
            state, detail = standalone._recent_job_state()
        self.assertEqual(state, "needs_attention")
        self.assertEqual(detail, "Image job running 2 hours with no progress — check the Mac")
        report = attention.measure(disk=GREEN, activity_state=state, now=NOW, activity_detail=detail)
        self.assertEqual(report["reasons"][0]["detail"], detail)

    def test_a_healthy_mac_carries_no_sentence(self):
        from components.hub import standalone
        with mock.patch.dict(standalone.broker.batches, {}, clear=True), \
                mock.patch("time.time", return_value=NOW):
            self.assertEqual(standalone._recent_job_state(), (None, None))


if __name__ == "__main__":
    unittest.main()
