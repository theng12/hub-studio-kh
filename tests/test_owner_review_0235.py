"""The owner's live review of 0.6.234, pinned as 0.6.235 source checks.

Each test is the acceptance for one numbered item in the batch. They read the shipped source; the
render/native gates prove the pixels and behaviour where a source assertion cannot.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()
DESIGN = (ROOT / "native" / "Design.swift").read_text()


class SidebarTests(unittest.TestCase):
    def test_selected_row_is_a_quiet_tint_and_accent_bar(self) -> None:
        row = VIEWS[VIEWS.index("private func sidebarRow"):]
        row = row[:row.index("private func pageTitle")]
        self.assertIn("Color.accentColor.opacity(0.12)", row)
        self.assertIn("fill(selected ? Color.accentColor : Color.clear)", row)
        self.assertIn("width: 3, height: 18", row)
        self.assertNotIn("Color.white", row, "the selected row is no longer a white-on-accent block")


class FooterTests(unittest.TestCase):
    def test_pill_names_the_product_version_and_whats_new(self) -> None:
        pill = VIEWS[VIEWS.index("private var footerVersionPill"):]
        pill = pill[:pill.index("private var sidebarList")]
        self.assertIn('Text("Hub Studio KH")', pill)
        self.assertIn("Text(appVersion)", pill)
        self.assertIn('Text("· What\'s New")', pill)


if __name__ == "__main__":
    unittest.main()
