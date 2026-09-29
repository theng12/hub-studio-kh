"""The four things every studio job list has to show, checked by shape.

**The owner's report, 2026-09-29:** *"audio generation tab only shows a few and i can't scroll it
about. like there's more stuff but it's stuck there. and i think the same goes to the other
generators as well."*

He was right about both halves. Each studio's page wrapped its list in a `ScrollView` — and then
handed that scroll view `.prefix(5)`, so five rows were all it ever had. **A list capped at five
cannot be scrolled by definition**, and the panel looked like it was refusing to move rather than
like it had nothing more to show. The same five-line mistake was in **Image, Music, Video, Audio and
Subtitle** — the cap was copied along with the layout, which is why he saw it on more than one page.

The cap was the first of four defects in the same five panels, and each later one was the same
shape: a thing written once per panel, so five copies had to be fixed together.

- **A row had no boundary.** Every panel drew its rows straight onto the workspace background, so
  the owner could not see where one finished — *"they all in the same black background"*. The Jobs
  page had drawn a `Divider()` between rows all along. The division now lives in `RecentRows`, which
  every panel renders through, so no panel can be the one without it.
- **The transport appeared and disappeared.** It was drawn only while a clip was loaded, so pressing
  Play pushed the whole list down and Stop pulled it back — the row being read moved under the
  pointer. It is now always on screen and inert until something is loaded.
- **No row carried a generation date.** Six row builders each hand-wrote the same "status · model"
  line and the date was written into none of them. That line is now `jobMetaLine`, in one place, and
  it renders the date.

The lesson the tests keep is the one the fixes were made for: **check the invariant, not the
membership.** "Every panel draws a divider" is a test that catches the sixth panel. "The Image panel
draws a divider" is a test that catches nothing.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWS = ROOT / "native" / "StudioViews.swift"
API = ROOT / "native" / "StudioAPI.swift"

#: The job collections each studio page renders, and the label it heads them with.
LISTS = {
    "imageJobs": "Recent images",
    "musicJobs": "Recent music",
    "videoJobs": "Recent video",
    "audioJobs": "Recent audio",
    "subtitleJobs": "Recent subtitles",
}

#: The row builder each of those panels draws its jobs with, by its full declaration. The Audio
#: page's row is `jobRow(_:compact:)` — there are two `jobRow`s, and a pattern that matched the
#: first would have checked the dispatcher, which draws no rows and needs no date.
ROW_FOR = {
    "imageJobs": "imageJobRow(_ job: NativeJob)",
    "musicJobs": "musicJobRow(_ job: NativeJob)",
    "videoJobs": "videoJobRow(_ job: NativeJob)",
    "audioJobs": "jobRow(_ job: NativeJob, compact: Bool)",
    "subtitleJobs": "subtitleJobRow(_ job: NativeJob)",
}


class TheRecentListsAreNotCappedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = VIEWS.read_text(encoding="utf-8")

    def test_no_studio_list_is_sliced_to_a_fixed_number(self) -> None:
        for collection in LISTS:
            with self.subTest(collection=collection):
                found = re.search(rf"{collection}\.prefix\(\s*\d+\s*\)", self.source)
                if found:
                    # Name the slice, not the file: an assertion that prints 9,000 lines of Swift
                    # is a failure nobody reads.
                    self.fail(
                        f"{collection} is capped with {found.group(0)!r} — a capped list cannot "
                        "scroll, and the panel then looks stuck rather than empty",
                    )

    def test_every_studio_still_shows_its_list(self) -> None:
        # The other way to pass the test above is to stop rendering the list at all. Each panel
        # now hands its collection to the shared row list rather than to its own `LazyVStack`,
        # which is what puts the divider between the rows.
        for collection in LISTS:
            with self.subTest(collection=collection):
                if f"RecentRows(items: workspace.{collection})" not in self.source:
                    self.fail(f"{collection} is no longer listed on its page")

    def test_the_lists_still_scroll(self) -> None:
        # A long list that is not in a scroller is a clipped list.
        self.assertGreaterEqual(self.source.count("ScrollView"), len(LISTS),
                                "fewer scroll views than studio lists — one of them cannot scroll")


class EveryRowIsSeparatedTest(unittest.TestCase):
    """*"i think we should have a divider or a line between them."* — the owner, 2026-09-29."""

    def setUp(self) -> None:
        self.source = VIEWS.read_text(encoding="utf-8")

    def test_the_shared_row_list_draws_a_divider_between_rows(self) -> None:
        body = re.search(r"private struct RecentRows<.*?\n\}", self.source, re.S)
        self.assertIsNotNone(body, "RecentRows is gone — the five panels have no shared row list")
        self.assertIn("Divider()", body.group(0),
                      "RecentRows draws its rows with nothing between them, which is the flat "
                      "black background the owner reported")

    def test_the_divider_is_inside_the_per_row_loop(self) -> None:
        # A divider *after* the loop is one line under the last row, not a boundary between rows.
        body = re.search(r"private struct RecentRows<.*?\n\}", self.source, re.S)
        assert body is not None
        loop = re.search(r"ForEach\(items\) \{.*?\n            \}", body.group(0), re.S)
        self.assertIsNotNone(loop, "RecentRows no longer iterates its items")
        assert loop is not None
        self.assertIn("Divider()", loop.group(0),
                      "the divider is outside the row loop, so it separates nothing")

    def test_no_two_panels_draw_their_own_row_list(self) -> None:
        # The drift this replaced: five private `LazyVStack { ForEach(workspace.…Jobs) }` copies.
        # Any one of them coming back is a panel that will miss the next fix.
        for collection in LISTS:
            with self.subTest(collection=collection):
                hand_rolled = re.search(
                    rf"LazyVStack[^{{]*\{{[^}}]*ForEach\(workspace\.{collection}\)",
                    self.source, re.S)
                if hand_rolled:
                    self.fail(f"{collection} is drawn by a private LazyVStack again — it will not "
                              "get the divider, or whatever the next fix is")


class TheTransportIsAlwaysOnScreenTest(unittest.TestCase):
    """*"the audio scrubber is appearing and disappearing … it shouldn't move the list."*"""

    def setUp(self) -> None:
        self.source = VIEWS.read_text(encoding="utf-8")

    def test_the_transport_is_not_gated_on_a_loaded_clip(self) -> None:
        # The exact shape of the bug: `if let job = loadedAudioJob, player.hasAudio { … }`. Anything
        # that hides the control is the layout shift, however it is spelled.
        hidden = re.search(r"if[^\n{]*loadedAudioJob[^\n{]*\{[^\n]*audioTransport", self.source)
        if hidden:
            self.fail(f"the transport is shown conditionally again ({hidden.group(0).strip()!r}) — "
                      "it must be drawn whether or not a clip is loaded")

    def test_the_transport_is_drawn_from_the_panel(self) -> None:
        self.assertIn("audioTransport(loadedAudioJob)", self.source,
                      "the Audio panel no longer draws the transport unconditionally")

    def test_the_transport_accepts_no_clip(self) -> None:
        # Drawing it unconditionally only works if it can be drawn with nothing loaded.
        self.assertIn("audioTransport(_ job: NativeJob?)", self.source,
                      "audioTransport no longer takes an optional job, so it cannot be drawn "
                      "before the first clip is played")

    def test_the_inert_transport_says_why(self) -> None:
        # A row of greyed-out controls with no stated reason is the state this project keeps
        # having to fix. When nothing is loaded, the panel says how to load something.
        self.assertIn("Choose Play on a clip to load it here.", self.source,
                      "the empty transport is disabled with nothing saying why")


class EveryRowCarriesItsDateTest(unittest.TestCase):
    """*"there's is no generation date so i don't know when each of them was generated on."*"""

    def setUp(self) -> None:
        self.views = VIEWS.read_text(encoding="utf-8")
        self.api = API.read_text(encoding="utf-8")

    def test_no_row_builder_hand_writes_the_meta_line(self) -> None:
        # Six copies of `stateBadge(job.state) + model` is how the date came to be in none of them.
        # Exactly one occurrence means one builder, and every row goes through it.
        occurrences = self.views.count("stateBadge(job.state)")
        self.assertEqual(
            1, occurrences,
            f"{occurrences} rows hand-write their status/model line; the date lands on whichever "
            "of them is edited, which is how it landed on none")

    def test_the_meta_line_renders_the_date(self) -> None:
        line = re.search(r"private func jobMetaLine\(.*?\n    \}", self.views, re.S)
        self.assertIsNotNone(line, "jobMetaLine is gone — the rows have no shared meta line")
        assert line is not None
        self.assertIn("generatedAtLabel", line.group(0),
                      "the shared row line does not render a generation date")

    def test_every_row_builder_uses_the_meta_line(self) -> None:
        # Not just the five the panels draw: the Chat row and the Jobs page draw these too, and a
        # row without the shared line is a row without a date.
        for signature in sorted(set(ROW_FOR.values()) | {"chatJobRow(_ job: NativeJob)"}):
            with self.subTest(builder=signature):
                body = re.search(rf"private func {re.escape(signature)} -> some View \{{.*?\n    \}}",
                                 self.views, re.S)
                self.assertIsNotNone(body, f"{signature} is gone")
                assert body is not None
                self.assertIn("jobMetaLine(job)", body.group(0),
                              f"{signature} does not draw the shared meta line, so its rows have "
                              "no generation date")

    def test_the_job_states_when_it_was_made(self) -> None:
        # The label itself: built from the Hub's own stamp, and absent when there is not one —
        # a job with no time is a different fact from a job made long ago.
        label = re.search(r"var generatedAtLabel: String\? \{.*?\n    \}", self.api, re.S)
        self.assertIsNotNone(label, "NativeJob.generatedAtLabel is gone")
        assert label is not None
        self.assertIn("createdAt", label.group(0),
                      "the date is not read from the job's own Hub stamp")
        self.assertIn("return nil", label.group(0),
                      "a job with no stamp would be dated anyway")


if __name__ == "__main__":
    unittest.main()
