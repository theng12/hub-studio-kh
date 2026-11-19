"""Idle-capacity status: an old snapshot or wait reason must say how old it is.

The fleet row and the Jobs page used to show a cached fact as a present-state answer. These tests
use fake rows and fake batches only: no Hub, worker, network or native view is started.
"""
from __future__ import annotations

import time
import unittest

from components.hub import standalone
from components.hub.backend import broker, peers


def _fake_row(age_s: float | None, now: float) -> dict:
    return {
        "machine": "node-a",
        "health": "up",
        "current_job": {"job_id": "job-1"},
        **standalone._peer_snapshot_freshness(now, now - age_s if age_s is not None else None,
                                              peers.PEER_TTL_S),
    }


def _fake_batch(note: str, note_at: float) -> dict:
    return {
        "id": "batch-1", "modality": "image", "model": "fixture/model",
        "created_at": note_at - 30, "cancelled": False,
        "items": [{"index": 0, "state": "queued"}],
        "governor_note": note, "governor_note_at": note_at,
    }


class PeerSnapshotRowTests(unittest.TestCase):
    def test_a_stale_snapshot_row_carries_its_age_and_says_it_is_old(self) -> None:
        now = time.time()
        row = _fake_row(peers.PEER_TTL_S + 30, now)
        self.assertEqual(round(peers.PEER_TTL_S + 30, 1), row["snapshot_age_s"])
        self.assertIn("ago", row["snapshot_note"])
        self.assertIn("old snapshot, as of 1 min ago", row["snapshot_note"])

    def test_a_fresh_snapshot_row_does_not_claim_an_age(self) -> None:
        now = time.time()
        row = _fake_row(peers.PEER_TTL_S - 1, now)
        self.assertIsNotNone(row["snapshot_age_s"])
        self.assertIsNone(row["snapshot_note"])


class GovernorNoteTests(unittest.TestCase):
    def test_a_fresh_note_is_returned_as_the_current_wait(self) -> None:
        now = time.time()
        batch = _fake_batch("Waiting for a free machine", now - 1)
        self.assertEqual("Waiting for a free machine",
                         broker.current_governor_note(batch, now))
        self.assertEqual("Waiting for a free machine",
                         broker.batch_summary(batch)["governor_note"])

    def test_a_note_older_than_the_peer_window_is_not_current(self) -> None:
        now = time.time()
        batch = _fake_batch("model is not downloaded", now - peers.PEER_TTL_S - 1)
        self.assertIsNone(broker.current_governor_note(batch, now))
        self.assertIsNone(broker.batch_summary(batch)["governor_note"])

    def test_every_write_stamps_the_note_and_clearing_it_clears_the_stamp(self) -> None:
        batch = {"id": "batch-2"}
        broker.stamp_governor_note(batch, "no candidate")
        self.assertAlmostEqual(time.time(), batch["governor_note_at"], delta=2)
        broker.stamp_governor_note(batch, None)
        self.assertIsNone(batch["governor_note"])
        self.assertIsNone(batch["governor_note_at"])

    def test_a_note_older_than_the_current_attempt_is_not_current(self) -> None:
        now = time.time()
        batch = _fake_batch("an older refusal", now - 1)
        batch["items"][0]["run_started"] = now - 0.5
        self.assertIsNone(broker.current_governor_note(batch, now))


if __name__ == "__main__":
    unittest.main()
