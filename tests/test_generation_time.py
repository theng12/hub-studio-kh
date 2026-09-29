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

    # The row styles, by their exact signatures. `jobRow(_:for:)` is deliberately not one of
    # them: it is the dispatcher that routes a job to the style its function uses, so asserting a
    # suffix inside it would be asserting that a `switch` renders text. Video joined the list with
    # its lane (2026-09-26) — the Jobs tab now draws clips like every other Studio's work.
    ROW_STYLES = (
        "private func imageJobRow(_ job: NativeJob) -> some View",
        "private func jobRow(_ job: NativeJob, compact: Bool) -> some View",
        "private func musicJobRow(_ job: NativeJob) -> some View",
        "private func subtitleJobRow(_ job: NativeJob) -> some View",
        "private func chatJobRow(_ job: NativeJob) -> some View",
        "private func videoJobRow(_ job: NativeJob) -> some View",
    )

    def test_every_job_row_shows_it(self) -> None:
        """One suffix, one style per Studio, both pages: the generator and Jobs cannot disagree.

        The suffix used to be written into each row style, and this test counted the copies — six
        rows, six occurrences. That checked *membership*: it passed for as long as nobody added a
        row, and it had to be hand-edited for every fact added beside the suffix. The date went in
        that way, written into no row at all, because six copies is six chances to forget one.

        All six now draw one shared `jobMetaLine`, so the invariant is the opposite: the suffix is
        written **once**, and every row style draws the line that carries it. A seventh row that
        forgets the line fails this test; a seventh row that hand-writes the suffix fails it too.
        """
        self.assertEqual(
            1, VIEWS.count("job.generationSuffix"),
            "job.generationSuffix is written more than once — it belongs to the one shared row "
            "line (`jobMetaLine`), and a second copy is a copy that can drift")
        for row in self.ROW_STYLES:
            with self.subTest(row=row):
                self.assertIn(row, VIEWS, f"{row} is gone; update this test with the new styles")
                body = VIEWS[VIEWS.index(row):]
                body = body[:body.index("\n    private func", 10)]
                self.assertIn("jobMetaLine(job)", body,
                              f"{row} must draw the shared row line, which is where the generation "
                              "time - and the date - are rendered")
        # And the line itself has to keep showing what this file exists for.
        line = VIEWS[VIEWS.index("private func jobMetaLine"):
                     ]
        line = line[:line.index("\n    private func", 10)]
        self.assertIn("generationSuffix", line, "the shared row line must show the generation time")

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
        for style in ("imageJobRow(job)", "musicJobRow(job)", "subtitleJobRow(job)",
                      "chatJobRow(job)", "videoJobRow(job)"):
            with self.subTest(style=style):
                self.assertIn(style, dispatcher, f"the dispatcher no longer routes to {style}")
        self.assertIn("jobRow(job, compact:", dispatcher,
                      "the audio row is the compact style and must be routed to")
        # Every Studio files a batch history now, so there is no `EmptyView()` branch left to
        # require — the dispatcher's own exhaustiveness (no `default:`) is the guarantee instead.
        self.assertNotIn("default:", dispatcher,
                         "the dispatcher must name every Studio, so a new one stops the build")

    def test_a_clip_length_is_never_mistaken_for_the_generation_time(self) -> None:
        """They are different numbers and both are useful: 4.6 s of audio, 52 s to make it."""
        facts = API[API.index("var assetFacts: String"):]
        facts = facts[:facts.index("struct NativeDownload")]
        self.assertIn("durationSeconds", facts, "the media length stays in the facts line")
        self.assertNotIn("generationSeconds", facts,
                         "the generation time is its own line, not folded into the facts")


class DurationFormattingTests(unittest.TestCase):
    """Every duration is exact, and never contradicts itself.

    The owner read the defect off the Jobs page: "when it took seconds. it showed seconds. but if it
    took more than 1 minute. it just shows the minute without the exact seconds." The formatter
    truncated the minutes and rounded the remainder separately, then dropped the seconds when the
    rounded remainder was zero - so 119.7 s read "1 min" and hid 59.7 seconds, and a run finishing
    at 60.4 s read "1 min" as well.

    The exact inputs and the exact strings they must produce live in `tests/native/api_main.swift`,
    where the real `NativeJob.clock` is called - a second implementation here would only agree with
    itself. What is pinned here is the shape that makes the table pass: one rounding, the branch
    taken from it, the seconds never dropped, and hours instead of a 127-minute run.
    """

    CLOCK = API[API.index("static func clock(_ seconds: Double) -> String {"):]
    CLOCK = CLOCK[:CLOCK.index("\n    }")]
    NATIVE = (ROOT / "tests" / "native" / "api_main.swift").read_text()

    def test_the_duration_is_rounded_once_and_the_branch_comes_from_it(self) -> None:
        self.assertNotIn("Int(seconds)", self.CLOCK,
                         "the minutes are truncated again: both parts must come from one rounding")
        self.assertEqual(self.CLOCK.count(".rounded()"), 1,
                         "the duration must be rounded exactly once")
        # The scale is chosen from the same rounded value, so a duration can never be reported at a
        # scale it has not reached (59.97 s once read "60.0 s").
        self.assertIn("if whole < 60", self.CLOCK,
                      "the seconds/minutes branch must read the rounded value, not the raw one")

    def test_the_seconds_are_never_dropped(self) -> None:
        self.assertNotIn("rest == 0 ?", self.CLOCK,
                         "a zero remainder must not swallow the seconds: 1 min 0 s, not 1 min")
        # The minutes branch states both parts, and the hours branch states all three.
        self.assertIn('"\\(minutes) min \\(rest) s"', self.CLOCK)
        self.assertIn('"\\(hours) h \\(minutes) min \\(rest) s"', self.CLOCK,
                      "past an hour the minutes must not run up to 127")

    def test_the_exact_table_runs_against_the_real_formatter(self) -> None:
        """The table is the contract; this is the gate that keeps the table, and its coverage."""
        table = self.NATIVE[self.NATIVE.index("let clockTable: [(Double, String)]"):]
        table = table[:table.index("\n        ]")]
        self.assertIn("NativeJob.clock(seconds) == expected", self.NATIVE,
                      "the table must be compared against the real formatter, not a copy of it")
        # Every boundary the old implementation got wrong, including the one it never reached.
        for probe in ("59.4", "59.9", "59.97", "60.0", "60.4", "119.7", "120.4", "125.0",
                      "3599.6", "3600.4", "7623.0"):
            self.assertIn(f"({probe},", table, f"the exact table no longer covers {probe} s")
        # ...and the property, which is what catches a boundary nobody wrote down.
        self.assertIn("lost <=", self.NATIVE,
                      "the half-second-loss property must be checked across the whole range")

    def test_both_labels_use_the_one_formatter(self) -> None:
        """`took …` and `running …` are the same measurement at different moments."""
        suffix = API[API.index("static func generationSuffix"):]
        suffix = suffix[:suffix.index("\n    }")]
        self.assertEqual(suffix.count("clock("), 2,
                         "both the finished and the running label must use the one formatter")

    def test_a_media_length_is_not_a_second_duration_format(self) -> None:
        """One duration voice, wherever a duration is shown.

        `assetFacts` (a job's clip length) and `NativeVoice.durationLabel` (a saved reference clip)
        each carried their own copy of the old `%.1f min` shape, in the same rows as the run time
        the owner was reading — so a 2 min 5 s clip said "2.1 min" directly above "2 min 5 s". The
        invariant is the shape, not the two call sites: no native file renders minutes to a decimal
        for a duration, and every duration goes through the shared formatter.
        """
        for name in ("StudioAPI.swift", "StudioViews.swift"):
            source = (ROOT / "native" / name).read_text()
            # Comments are stripped, not merely tolerated: the comment explaining this defect has
            # to be allowed to name the shape it removed, and any *code* line that still renders
            # minutes to a decimal is the defect back again.
            code = "\n".join(line for line in source.splitlines()
                             if not line.strip().startswith("//"))
            self.assertNotIn("%.1f min", code,
                             f"{name} has a second duration format; use NativeJob.clock")
        facts = API[API.index("var assetFacts: String {"):]
        facts = facts[:facts.index("\n    }")]
        self.assertIn("Self.clock(durationSeconds)", facts,
                      "a job's own length must go through the one formatter")
        voice = API[API.index("var durationLabel: String {"):]
        voice = voice[:voice.index("\n    }")]
        self.assertIn("NativeJob.clock(durationSeconds)", voice,
                      "a saved clip's length must go through the one formatter")
        # The transport label ("0:07 / 0:30") is a playhead, not a duration report, and keeps the
        # shape every other player uses. Everything else here is a duration.
        self.assertIn("NativeJob.clock(value)", VIEWS,
                      "a measured run on the Overview must use the one formatter")


if __name__ == "__main__":
    unittest.main()
