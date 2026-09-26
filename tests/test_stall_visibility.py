"""A stall the Hub can see, the operator must be able to see too.

WHAT WAS WRONG, AND WHY IT IS THE SH-3 SHAPE. `stall.py` was written, shipped and tested (9 tests)
so that a worker going quiet is **visible** rather than merely survivable — the owner's own question
separated it from a timeout: *"if we raise it too high … and it failed, could we find out or we have
to wait 2 hours to findout?"* The Hub has published `stalled_seconds` on every job row since 0.6.70
(`broker.batch_summary`) and on every fleet machine row.

**The app read none of it.** `HubJobSummary` decoded two fields, `id` and `modality`, so the number
arrived on the wire and was dropped on the floor: a stalled job was visible to a scheduler and
invisible to the person watching the page. That is the same defect `test_below_tier_warning.py`
exists for — *"a two-ended invariant does not see a hole in the middle"* — where both ends were true
and the sentence could not travel.

Three links, and all three are pinned here, because any one of them alone looks like it works:

1. **the producer** — `broker.batch_summary` states `stalled_seconds` while a job is active, and
   **both** job routes serve it (`/api/hub/jobs` lists summaries and `/api/hub/jobs/{id}` is
   `_summary(batch)`, which is that same function plus `items`);
2. **the wire** — the native decodables name the field, so `convertFromSnakeCase` actually maps it;
3. **the screen** — a render site reads it, and says the one thing it must say.

**What this file cannot catch.** It reads Swift and Python as text: it proves the field is *named* in
each place, not that the view draws correctly, and a rename in the app is caught only because the
assertions are narrow. There is no rendering test for a SwiftUI row in this suite, so the geometry
and the wording on screen are verified by eye, not here.

**The rule the wording carries.** `stall.py`: *"What it must never do is kill the job. A stall
becomes visible."* So the sentence promises nothing about the job ending, and this test refuses a
version that implies it does.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BROKER = (ROOT / "components" / "hub" / "backend" / "broker.py").read_text()
STANDALONE = (ROOT / "components" / "hub" / "standalone.py").read_text()
API = (ROOT / "native" / "StudioAPI.swift").read_text()
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()
STALL = (ROOT / "components" / "hub" / "backend" / "stall.py").read_text()


def _swift_block(source: str, header: str) -> str:
    """The `{...}` body of a Swift declaration, by brace matching from its opening brace."""
    start = source.index(header)
    open_brace = source.index("{", start)
    depth = 0
    for index in range(open_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace:index + 1]
    raise AssertionError(f"unbalanced braces after {header!r}")


class TheHubPublishesItTest(unittest.TestCase):
    def test_a_batch_summary_states_how_long_it_has_been_silent(self) -> None:
        self.assertRegex(
            BROKER, r'"stalled_seconds":\s*_batch_stalled_seconds\(',
            "the batch summary no longer publishes `stalled_seconds`, so there is nothing for the "
            "app to read and a stalled job is invisible again")

    def test_both_job_routes_serve_the_summary(self) -> None:
        # `/api/hub/jobs` lists them; `/api/hub/jobs/{id}` returns `_summary(batch)`, which is
        # `broker.batch_summary` plus `items`. Pinned so a future detail payload cannot quietly
        # become a second shape without this field.
        self.assertRegex(
            STANDALONE, r"def _summary\(batch[^)]*\)[^:]*:\s*\n\s*return \{\*\*broker\.batch_summary\(",
            "the job detail is no longer the batch summary plus items; if it became its own shape, "
            "check that it still carries stalled_seconds")


class TheAppDecodesItTest(unittest.TestCase):
    def test_the_wire_structs_name_the_field(self) -> None:
        # `convertFromSnakeCase` is set on this decoder, so the Swift name is the whole mapping.
        self.assertIn("keyDecodingStrategy = .convertFromSnakeCase", API,
                      "the decoder no longer converts snake_case, so `stalledSeconds` will not "
                      "match `stalled_seconds` and this field silently decodes to nil forever")
        for header in ("private struct HubJobSummary: Decodable",
                       "private struct HubJobDetail: Decodable"):
            with self.subTest(struct=header.split()[-2]):
                body = _swift_block(API, header)
                self.assertIn("stalledSeconds", body,
                              f"{header} does not decode `stalledSeconds`; the Hub publishes it and "
                              f"the app drops it, which is the defect this file exists for")

    def test_the_row_model_carries_it(self) -> None:
        body = _swift_block(API, "struct NativeJob: Identifiable, Equatable")
        self.assertIn("stalledSeconds", body,
                      "NativeJob has no `stalledSeconds`, so the decoded value has nowhere to go")

    def test_the_mapping_passes_it_through(self) -> None:
        body = _swift_block(API, "private static func mapHubJob(")
        self.assertIn("stalledSeconds:", body,
                      "mapHubJob builds the row without the stall, so the number dies between the "
                      "wire struct and NativeJob")


class TheOperatorCanSeeItTest(unittest.TestCase):
    def test_a_render_site_reads_it(self) -> None:
        self.assertRegex(
            VIEWS, r"job\.stalledSeconds",
            "no view reads `job.stalledSeconds`, so the app can decode a stall and still show "
            "nothing — the silent middle link")

    def test_the_sentence_promises_nothing_about_the_job_ending(self) -> None:
        # `stall.py`: "What it must never do is kill the job. A stall becomes visible." A row that
        # reads as an imminent cancellation would be a different and false claim.
        self.assertRegex(
            VIEWS, r"not cancelled",
            "the stall row no longer says the job is still held; a stall is not a timeout and the "
            "screen must not read like one")
        self.assertIn("What it must never do is kill the job", STALL,
                      "stall.py's own contract — that a stall is surfaced and never acted on "
                      "destructively — has been reworded; the UI wording is pinned to it")

    def test_the_render_site_guard_is_not_vacuous(self) -> None:
        # A guard on this file itself: if the accessor is renamed, `test_a_render_site_reads_it`
        # would fail — this makes the *reason* explicit rather than leaving a bare regex.
        self.assertTrue(re.search(r"func stallNote\(", VIEWS),
                        "the stall render helper was renamed or removed; update this file and the "
                        "sites together rather than deleting the check")


if __name__ == "__main__":
    unittest.main()
