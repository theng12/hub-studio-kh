"""The Hub's job deadline is derived per generator, and the same number is published and enforced.

Two things are pinned here, and both exist because the defect they prevent has already happened once
in this repo.

**One: the deadline is per generator, not one flat ceiling.** The owner asked the question that
forced this:

    "if we raise it too high for, say subtitle and it failed, could we find out or we have to wait
     2 hours to findout?"

A single number is either too short for video — the LTX-2.3 runtime's own measured runs on an
M2 Pro 32 GB are 44:38 for q8 HQ at 480x704x97, so a 900 s wall cuts it off a third of the way
through — or so long that a broken subtitle job goes unnoticed for hours. So the deadline is a
property of the modality, and `video` is the only lane that differs today.

**Two: what the Hub publishes is what the Hub enforces.** The old comment promised exactly that
("the deadline a scheduler reads is the one the Hub will actually hold it to"), and it is easy to
break by deriving the published value per *model* while the proxy — which knows the worker but not
the model — derives its own. These tests assert both call sites resolve through one function.

**Chat is the exception, and it is the owner's decision of 2026-10-19** (see
`tests/test_chat_job_deadline.py`): a Chat job's deadline is computed from the job itself, so what
this table publishes for `chat` is the lane's *minimum* and the enforced number is recorded on the
job row. Every other lane still publishes and enforces one number.
"""

from __future__ import annotations

import unittest

from components.hub import standalone
from components.hub.backend import vocabulary


class DeadlineIsPerGeneratorTests(unittest.TestCase):
    def test_video_gets_a_longer_ceiling_than_every_other_lane(self) -> None:
        """Not a membership test on the numbers: a video job is long, so its ceiling must exceed
        every other registered lane's, whatever those numbers become."""
        video = standalone.deadline_floor_seconds("video")
        for modality in vocabulary.WORKER_FOR_MODALITY:
            if modality == "video":
                continue
            self.assertGreater(
                video, standalone.deadline_floor_seconds(modality),
                f"video's deadline must exceed {modality}'s — a video job is minutes-to-an-hour "
                f"and {modality} is seconds-to-minutes")

    def test_voice_covers_the_measured_slowest_mac_tail(self) -> None:
        """night-atlas 2026-10-05: the slowest successful 1,500-character Breeze part took 1,747 s
        on an M1 16 GB, and 5 parts died at the old 1,800 s wall. 2,700 s is 1.55x that tail."""
        self.assertEqual(standalone.deadline_floor_seconds("voice"), 2700.0)
        self.assertGreaterEqual(standalone.deadline_floor_seconds("voice"), 1.5 * 1747)

    def test_an_unmeasured_lane_gets_the_base_floor_and_nothing_more(self) -> None:
        """A lane with no measurement has no evidence to justify more than the floor."""
        self.assertEqual(standalone.deadline_floor_seconds("transcription"),
                         standalone.PEER_PROXY_TIMEOUT_FLOOR_S)

    def test_a_modality_nobody_classified_gets_the_conservative_floor(self) -> None:
        """A short deadline can only cut a job short; falling through to the *longest* floor would
        be the silent-wait failure this design exists to remove. So the default is the base."""
        for unknown in ("something-new", "", None):
            self.assertEqual(standalone.deadline_floor_seconds(unknown),
                             standalone.PEER_PROXY_TIMEOUT_FLOOR_S)

    def test_every_routed_worker_resolves_to_its_own_modalitys_deadline(self) -> None:
        """The join between the two call sites.

        `_model_row` publishes `deadline_floor_seconds(_modality_for_engine(engine))`; the proxy
        enforces `deadline_floor_seconds(modality_for_worker(worker_id))`. Those are different
        lookups, so they can disagree — this asserts they cannot, for every worker the Hub routes.
        """
        self.assertTrue(vocabulary.WORKER_FOR_MODALITY, "the routing table is empty")
        for worker_id, modality in vocabulary.REQUIRED_WORKERS.items():
            self.assertEqual(
                standalone.deadline_floor_seconds(standalone.modality_for_worker(worker_id)),
                standalone.deadline_floor_seconds(modality),
                f"worker {worker_id!r} publishes and enforces different deadlines")

    def test_video_is_declared_durable_because_it_is_asynchronous(self) -> None:
        """A 45-minute job cannot answer a terminal result for the POST, so it must be retained.

        Under `NON_DURABLE` a node dying at minute 40 loses the job with no `uncertain` state and
        no re-dispatch, while the identical failure in the Image lane is retained and requeued.
        """
        from components.hub.backend import broker

        self.assertEqual(broker.MODALITY_RETRY_CLASS["video"],
                         broker.RETRY_CLASS_DURABLE_ASYNC)
        self.assertIn("video", broker.DURABLE_ASYNC_MODALITIES)


if __name__ == "__main__":
    unittest.main()
