"""How long a generation took, shown where the operator looks.

The owner reported this as a missing feature after testing audio, image and transcription on his Mac
Studio: the rows gave the *length of the media* (a 4.6-second clip) and never how long it took to
make (52 seconds). Read as "it doesn't show me how long it takes at all" - and he was right, it
didn't.

The Hub had the number all along: `runtime_s` is written when an item finishes, and a running item
has `run_started`. The app parsed neither. This pins both ends: the payload carries the timing, and
every job row - the generator pages and the Jobs tab use the same three row styles - renders it.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BROKER = (ROOT / "components" / "hub" / "backend" / "broker.py").read_text()
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()
API = (ROOT / "native" / "StudioAPI.swift").read_text()


class GenerationTimeTests(unittest.TestCase):
    def test_the_hub_sends_how_long_a_job_took_and_how_long_it_has_run(self) -> None:
        item = BROKER[BROKER.index('"runtime_s": item.get'):]
        item = item[:item.index('"execution_started_at"')]
        self.assertIn('"runtime_s"', item, "a finished item must carry its runtime")
        # runtime_s is only written when an item completes, so without this a running generation
        # has no duration in the payload at all.
        self.assertIn('"elapsed_s"', item, "a running item must carry its elapsed time")
        self.assertIn('"run_started"', item)

    def test_the_app_parses_both(self) -> None:
        wire = API[API.index("private struct HubJobItem"):]
        wire = wire[:wire.index("private struct HubJobParams")]
        self.assertIn("let runtimeS: Double?", wire)
        self.assertIn("let elapsedS: Double?", wire)
        mapping = API[API.index("private static func mapHubJob"):]
        mapping = mapping[:mapping.index("private static func mapDownload")]
        self.assertIn("generationSeconds: item?.runtimeS", mapping)
        self.assertIn("runningSeconds: item?.elapsedS", mapping)

    def test_every_job_row_shows_it(self) -> None:
        """One suffix, three row styles, both pages: the generator and Jobs cannot disagree."""
        self.assertEqual(VIEWS.count("job.generationSuffix"), 3,
                         "imageJobRow, jobRow and subtitleJobRow must each show it")
        for row in ("private func imageJobRow", "private func jobRow", "private func subtitleJobRow"):
            body = VIEWS[VIEWS.index(row):]
            body = body[:body.index("\n    private func", 10)]
            self.assertIn("generationSuffix", body, f"{row} must show the generation time")

    def test_a_clip_length_is_never_mistaken_for_the_generation_time(self) -> None:
        """They are different numbers and both are useful: 4.6 s of audio, 52 s to make it."""
        facts = API[API.index("var assetFacts: String"):]
        facts = facts[:facts.index("struct NativeDownload")]
        self.assertIn("durationSeconds", facts, "the media length stays in the facts line")
        self.assertNotIn("generationSeconds", facts,
                         "the generation time is its own line, not folded into the facts")


if __name__ == "__main__":
    unittest.main()
