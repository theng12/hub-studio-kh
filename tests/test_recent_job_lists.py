"""Every studio's "Recent …" list scrolls — none of them is capped.

**The owner's report, 2026-09-29:** *"audio generation tab only shows a few and i can't scroll it
about. like there's more stuff but it's stuck there. and i think the same goes to the other
generators as well."*

He was right about both halves. Each studio's page wrapped its list in a `ScrollView` — and then
handed that scroll view `.prefix(5)`, so five rows were all it ever had. **A list capped at five
cannot be scrolled by definition**, and the panel looked like it was refusing to move rather than
like it had nothing more to show. The same five-line mistake was in **Image, Music, Video, Audio and
Subtitle** — the cap was copied along with the layout, which is why he saw it on more than one page.

Nothing explained the 5. It reads as "recent", but a *recent* list is one the reader can scroll
past; this was a ceiling. The scroll views and `LazyVStack`s underneath are built for long lists
already, so the cap was the only thing standing between him and his own history.

The check is by shape rather than by count: no studio list may pass a fixed slice of its jobs, and
the five pages that show one must all still be showing them.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

VIEWS = Path(__file__).resolve().parents[1] / "native" / "StudioViews.swift"

#: The job collections each studio page renders, and the label it heads them with.
LISTS = {
    "imageJobs": "Recent images",
    "musicJobs": "Recent music",
    "videoJobs": "Recent video",
    "audioJobs": "Recent audio",
    "subtitleJobs": "Recent subtitles",
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
        # The other way to pass the test above is to stop rendering the list at all.
        for collection, _ in LISTS.items():
            with self.subTest(collection=collection):
                if f"ForEach(workspace.{collection})" not in self.source:
                    self.fail(f"{collection} is no longer listed on its page")

    def test_the_lists_still_scroll(self) -> None:
        # A long list that is not in a scroller is a clipped list.
        self.assertGreaterEqual(self.source.count("ScrollView"), len(LISTS),
                                "fewer scroll views than studio lists — one of them cannot scroll")


if __name__ == "__main__":
    unittest.main()
