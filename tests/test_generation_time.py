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

    # The four row styles, by their exact signatures. `jobRow(_:for:)` is deliberately not one of
    # them: it is the dispatcher that routes a job to the style its function uses, so asserting a
    # suffix inside it would be asserting that a `switch` renders text.
    ROW_STYLES = (
        "private func imageJobRow(_ job: NativeJob) -> some View",
        "private func jobRow(_ job: NativeJob, compact: Bool) -> some View",
        "private func musicJobRow(_ job: NativeJob) -> some View",
        "private func subtitleJobRow(_ job: NativeJob) -> some View",
    )

    def test_every_job_row_shows_it(self) -> None:
        """One suffix, four row styles, both pages: the generator and Jobs cannot disagree."""
        self.assertEqual(
            VIEWS.count("job.generationSuffix"), len(self.ROW_STYLES),
            f"each of the {len(self.ROW_STYLES)} row styles must show the generation time once")
        for row in self.ROW_STYLES:
            with self.subTest(row=row):
                self.assertIn(row, VIEWS, f"{row} is gone; update this test with the new styles")
                body = VIEWS[VIEWS.index(row):]
                body = body[:body.index("\n    private func", 10)]
                self.assertIn("generationSuffix", body, f"{row} must show the generation time")

    def test_the_dispatcher_routes_every_function_with_a_job_history_to_a_row(self) -> None:
        """`jobRow(_:for:)` is the router; a function it drops loses its history silently.

        B29 made this switch exhaustive by function, and the Jobs page iterates
        `workspace.jobHistories` - which is built from `hasBatchHistory`. So every function with a
        history has to land on a real row here, and the ones without must be an explicit, commented
        `EmptyView()` rather than a missing case.
        """
        dispatcher = VIEWS[VIEWS.index("private func jobRow(_ job: NativeJob, for function"):
                            ]
        dispatcher = dispatcher[:dispatcher.index("\n    private func", 10)]
        for style in ("imageJobRow(job)", "musicJobRow(job)", "subtitleJobRow(job)"):
            with self.subTest(style=style):
                self.assertIn(style, dispatcher, f"the dispatcher no longer routes to {style}")
        self.assertIn("jobRow(job, compact:", dispatcher,
                      "the audio row is the compact style and must be routed to")
        self.assertIn("EmptyView()", dispatcher,
                      "a function with no batch history must say so explicitly")

    def test_a_clip_length_is_never_mistaken_for_the_generation_time(self) -> None:
        """They are different numbers and both are useful: 4.6 s of audio, 52 s to make it."""
        facts = API[API.index("var assetFacts: String"):]
        facts = facts[:facts.index("struct NativeDownload")]
        self.assertIn("durationSeconds", facts, "the media length stays in the facts line")
        self.assertNotIn("generationSeconds", facts,
                         "the generation time is its own line, not folded into the facts")


if __name__ == "__main__":
    unittest.main()
