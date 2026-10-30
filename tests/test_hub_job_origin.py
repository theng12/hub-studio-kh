"""Who asked, and who cancelled, as the Hub itself knows it (0.6.212).

A controller's operator could not tell its own job from its fleet's, and every cancelled job said
"Cancelled by user" whether Story, the Gateway or this Mac asked. Origin is stamped at the door
from the credential (`auth.presented_owner_token`), never from the body, and is absent on batches
written before 0.6.212 so the app can show nothing rather than a guess.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.hub.backend import broker, transcription_jobs  # noqa: E402

MODEL = "fixture/image"


def submit(**extra) -> str:
    submitted = broker.submit_batch({
        "modality": "image", "model": MODEL, "items": [{"prompt": "one"}], **extra,
    })
    assert "batch_id" in submitted, submitted
    return submitted["batch_id"]


class OriginTests(unittest.TestCase):
    def test_the_summary_publishes_the_origin_the_door_stamped(self) -> None:
        batch_id = submit(origin="gateway")
        self.addCleanup(broker.batches.pop, batch_id, None)
        self.assertEqual("gateway", broker.batches[batch_id]["origin"])
        self.assertEqual("gateway", broker.batch_summary(broker.batches[batch_id])["origin"])

    def test_a_batch_written_before_this_field_publishes_nothing(self) -> None:
        batch_id = submit()
        self.addCleanup(broker.batches.pop, batch_id, None)
        self.assertIsNone(broker.batch_summary(broker.batches[batch_id])["origin"])

    @staticmethod
    def subtitle_batch(**extra) -> dict:
        return {"id": "t1", "model": "fixture/subtitle", "created_at": 1.0, "cancelled": False,
                "items": [{"index": 0, "item_id": "clip-1", "filename": "clip.wav",
                           "state": "done", "tries": 1}], **extra}

    def test_transcription_publishes_it_too(self) -> None:
        self.assertEqual("this-mac",
                         transcription_jobs.summary(self.subtitle_batch(origin="this-mac"))["origin"])
        self.assertIsNone(transcription_jobs.summary(self.subtitle_batch())["origin"],
                          "an older batch makes no claim")


class CancellationSentenceTests(unittest.TestCase):
    def test_the_sentence_names_only_a_source_the_hub_saw(self) -> None:
        self.assertEqual("Cancelled from this Mac", broker.cancelled_text({"cancelled_by": "this-mac"}))
        self.assertEqual("Cancelled from the Gateway", broker.cancelled_text({"cancelled_by": "gateway"}))
        self.assertEqual("Cancelled", broker.cancelled_text({}))
        self.assertEqual("Cancelled", broker.cancelled_text({"cancelled_by": "someone"}))

    def test_a_cancel_records_its_source_and_says_it_on_every_item_it_queued(self) -> None:
        batch_id = submit()
        self.addCleanup(broker.batches.pop, batch_id, None)
        result = asyncio.run(broker.cancel_batch(batch_id, source="gateway"))
        assert result is not None
        batch = broker.batches[batch_id]
        self.assertEqual("gateway", batch["cancelled_by"])
        self.assertEqual(["Cancelled from the Gateway"],
                         [item["error"] for item in batch["items"]])
        self.assertEqual("cancelled", batch["items"][0]["state"])


if __name__ == "__main__":
    unittest.main()
