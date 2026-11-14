"""Every page's acceptance record carries the owner's three completeness checks.

Owner rule (2026-11-13, "Completeness, not just correctness"): before a page's gate it needs a task
list with one end-to-end test per task, a live-data capability cross-check, and a named
standard-practice comparison with present / deliberately absent / missing. This pins the record so
a page cannot be accepted with only a visual review.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "docs" / "tasks" / "PAGE-ACCEPTANCE-0.6.234.md").read_text()

PAGES = [
    "Overview", "Image", "Audio", "Subtitle (Transcription)", "Music", "Chat", "Models",
    "Set up this Mac", "Voices", "Jobs", "Renders", "Connections", "Settings", "What's New",
]


def section(title: str) -> str:
    marker = f"## {title}\n"
    start = DOC.index(marker) + len(marker)
    end = DOC.find("\n## ", start)
    return DOC[start:end if end >= 0 else len(DOC)]


class PageAcceptanceTests(unittest.TestCase):
    def test_every_page_has_the_three_checks(self) -> None:
        for page in PAGES:
            with self.subTest(page=page):
                body = section(page)
                self.assertIn("- Tasks:", body, f"{page} has no task list with end-to-end tests")
                self.assertIn("End-to-end:", body, f"{page} names no end-to-end test")
                self.assertIn("- Capabilities:", body, f"{page} has no capability cross-check")
                self.assertIn("Cross-check:", body, f"{page} does not say where live data is read")
                self.assertIn("- Standard:", body, f"{page} names no reference product")
                flat = " ".join(body.split())
                for word in ("Present:", "Deliberately absent:", "Missing:"):
                    self.assertIn(word, flat, f"{page} does not state {word[:-1].lower()}")

    def test_the_retired_page_is_recorded_and_not_navigation(self) -> None:
        body = section("Retired — Video")
        self.assertIn("Not a navigation page", body)
        self.assertIn("retired", body.lower())

    def test_the_record_names_the_live_producers_not_a_second_list(self) -> None:
        # The cross-check must read what the producers publish, never a page-local enumeration.
        self.assertIn("NativeModel", DOC)
        self.assertIn("NativeFleetRow", DOC)
        self.assertIn("HistoryTab.allCases", DOC)
        self.assertIn("derived from `StudioFunction`", DOC)


if __name__ == "__main__":
    unittest.main()
