"""The Hub's visual language is written once, in `native/Design.swift`, and the pages use it.

Before the design pass, thirteen pages each wrote their own `.padding(28)`, fonts came from 22 different
specs (157 of them the 10-point `.caption`), radii were 6, 8, 10 and 12, a page was titled "Image Studio"
under a sidebar row called "Image", and the same state was a green capsule on one page and orange text on
another. The Gateway app is drawn from the same table, so the two apps read as one product.

These pin the structure, not the pixels (`native/render-page.sh` shows those): a page that goes back to its
own margin, size, radius or status colour fails here instead of drifting one fix at a time.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()
DESIGN = (ROOT / "native" / "Design.swift").read_text()
NOTES = (ROOT / "native" / "ReleaseNotes.swift").read_text()
BUILD = (ROOT / "native" / "build.sh").read_text()
SHELL = (ROOT / "native" / "ShellCore.swift").read_text()


def page_body(name: str) -> str:
    marker = f"private var {name}: some View {{"
    body = VIEWS[VIEWS.index(marker) + len(marker):]
    cut = body.find("\n    private ")
    return body if cut < 0 else body[:cut]


class TokenTests(unittest.TestCase):
    def test_the_tokens_are_the_spec(self) -> None:
        for needle in (
            "static let page: CGFloat = 28", "static let xl: CGFloat = 24", "static let l: CGFloat = 16",
            "static let m: CGFloat = 12", "static let s: CGFloat = 8", "static let tight: CGFloat = 6",
            "static let xs: CGFloat = 4", "static let sidebar: CGFloat = 220",
            "static let card: CGFloat = 8", "static let control: CGFloat = 6", "static let sheet: CGFloat = 12",
            "Font.system(size: 26, weight: .semibold)", "Font.system(size: 15, weight: .semibold)",
            "Font.system(size: 13, weight: .semibold)", "Font.system(size: 12)", "Font.system(size: 11)",
            "Font.system(size: 22, weight: .semibold).monospacedDigit()",
        ):
            self.assertIn(needle, DESIGN, f"the design token changed or went missing: {needle}")

    def test_design_is_compiled_everywhere_the_views_are(self) -> None:
        # The lifecycle suite lists its sources by name; a view file that needs Design.swift and a build
        # line that does not name it is a build that fails only on the machine that runs the suite.
        line = next(row for row in BUILD.splitlines() if "StudioViews.swift" in row and "Design.swift" not in row
                    or "StudioViews.swift" in row and "Design.swift" in row)
        self.assertIn("Design.swift", line, "native/build.sh compiles StudioViews.swift without Design.swift")

    def test_release_notes_does_not_depend_on_design(self) -> None:
        # ReleaseNotes.swift is built into the boundary tests on its own, so it carries its own sizes.
        for token in ("HubType", "HubSpace", "HubStatus", "HubRadius"):
            self.assertNotIn(token, NOTES, f"ReleaseNotes.swift is compiled without Design.swift ({token})")


class PageTests(unittest.TestCase):
    PAGES = {
        "overview": "overview", "audioWorkspace": "audio", "imageWorkspace": "image", "musicWorkspace": "music",
        "videoWorkspace": "video", "chatWorkspace": "chat", "transcriptionWorkspace": "transcription",
        "modelsWorkspace": "models", "voicesWorkspace": "voices", "jobsWorkspace": "jobs",
        "connections": "connections", "whatsNew": "whatsNew", "settings": "settings",
    }

    def test_every_page_is_one_page_scaffold_titled_like_its_sidebar_row(self) -> None:
        for name, case in self.PAGES.items():
            with self.subTest(page=name):
                body = page_body(name)
                self.assertIn(f"PageScaffold(title: pageTitle(.{case})", body,
                              f"{name} is not drawn by PageScaffold with the sidebar's own name")
                self.assertNotIn(".padding(28)", body)
                self.assertNotIn("heading(", body)
        # The one function the titles come from is the sidebar's own.
        self.assertIn("private func pageTitle(_ page: StudioPage) -> String { page.title }", VIEWS)
        self.assertNotRegex(VIEWS, r'"(Image|Music|Video|Subtitle|Chat) Studio"', "a page is titled as it was before the sidebar names")

    def test_nothing_writes_its_own_margin_size_radius_or_status_colour(self) -> None:
        self.assertNotIn(".padding(28)", VIEWS)
        for banned in (".font(.caption", ".font(.callout", ".font(.headline", ".font(.title", ".font(.largeTitle",
                       ".fontWeight("):
            self.assertNotIn(banned, VIEWS, f"a hand-picked type size is back: {banned}")
        self.assertNotRegex(VIEWS, r"cornerRadius: (3|4|5|10)\b", "a radius outside 6 / 8 / 12 is back")
        self.assertNotRegex(VIEWS, r"spacing: (3|5|7|9|10|14|18|20|22)\b", "a spacing outside the 4-point grid is back")

    def test_orange_is_a_warning_and_only_through_the_status_vocabulary(self) -> None:
        # Raw `.orange` is allowed in exactly one place: the ring that circles a running job.
        stray = [line.strip() for line in VIEWS.splitlines()
                 if re.search(r"\.orange\b", line) and "HubStatus" not in line and ".pink" not in line]
        self.assertEqual([], stray, "orange is drawn outside HubStatus.warn")

    def test_whats_new_is_semibold_not_bold(self) -> None:
        self.assertNotIn(".bold()", VIEWS)
        self.assertNotIn(".bold()", NOTES, "What's New headings are semibold like every other title")
        self.assertIn("PageScaffold(title: pageTitle(.whatsNew)", page_body("whatsNew"))

    def test_the_sidebar_is_220_and_keeps_its_footer(self) -> None:
        self.assertIn("idealWidth: HubSpace.sidebar, maxWidth: HubSpace.sidebar", VIEWS)
        # The owner accepted the footer: this Mac's name and its group line stay.
        self.assertIn("Text(thisMachineName)", VIEWS)
        self.assertIn("Text(groupLine)", VIEWS)

    def test_scrolling_pages_say_so_and_split_pages_do_not(self) -> None:
        for name in ("overview", "jobsWorkspace", "connections", "whatsNew", "settings"):
            self.assertIn("scrolls: true", page_body(name), f"{name} is a long column and must scroll")
        for name in ("audioWorkspace", "imageWorkspace", "musicWorkspace", "chatWorkspace", "modelsWorkspace"):
            self.assertNotIn("scrolls: true", page_body(name), f"{name} scrolls inside its panes, not as a page")


class FormTests(unittest.TestCase):
    def test_form_labels_sit_above_their_controls_through_one_row(self) -> None:
        picker = VIEWS[VIEWS.index("private func modelPicker"):]
        picker = picker[:picker.index("\n    private var transcriptionWorkspace")]
        self.assertIn('LabeledRow(label: "Model")', picker)
        self.assertIn("struct LabeledRow", DESIGN)
        self.assertIn(".frame(maxWidth: .infinity, alignment: .leading)", DESIGN[DESIGN.index("struct LabeledRow"):],
                      "a form control must start at its label's left edge, not centred in the column")


class RowTests(unittest.TestCase):
    def test_voices_rows_are_action_rows(self) -> None:
        row = VIEWS[VIEWS.index("private func voiceRow"):]
        row = row[:row.index("\n    /// One editor for both adding and editing")]
        self.assertIn("ActionRow {", row)
        self.assertIn("RowActions {", row)
        self.assertNotIn("Spacer()", row, "the actions column is ActionRow's, not a Spacer's guess")

    def test_removed_nodes_rows_are_action_rows(self) -> None:
        row = VIEWS[VIEWS.index("private func removedNodes"):]
        row = row[:row.index("private func removedFacts")]
        self.assertIn("ActionRow {", row)


class ConnectionsTableTests(unittest.TestCase):
    def test_one_table_of_macs(self) -> None:
        connections = page_body("connections")
        self.assertIn("fleetSection", connections)
        self.assertNotIn("Fleet overview", VIEWS, "the second list of the same Macs is back")
        self.assertNotIn("Joined nodes", VIEWS, "the second list of the same Macs is back")
        table = VIEWS[VIEWS.index("private func nodeTable"):]
        table = table[:table.index("private var nodeTableHeader")]
        self.assertIn("NativeFleetRow", VIEWS)
        self.assertIn("NativeFleetRow.rows(overview: workspace.fleetOverview, nodes: workspace.fleetNodes)", VIEWS)
        self.assertIn("windowWidth", table)

    def test_the_table_carries_the_four_facts_the_owner_asked_for(self) -> None:
        cells = VIEWS[VIEWS.index("private func wideNodeRow"):]
        cells = cells[:cells.index("/// The narrow layout")]
        for fact in ("nodeStateCell(row)", "nodeDoingNow(row)", "diskText", "memoryText", "nodeActions(row)"):
            self.assertIn(fact, cells)
        identity = VIEWS[VIEWS.index("private func nodeIdentity"):]
        identity = identity[:identity.index("private func nodeStateCell")]
        self.assertIn("row.startupText", identity, "what starts with the Mac is part of its row")

    def test_disk_and_memory_are_badges_in_the_shared_status_colours(self) -> None:
        cell = VIEWS[VIEWS.index("private func resourceCell"):]
        cell = cell[:cell.index("/// Send here / Automatic")]
        self.assertIn("StatusBadge(", cell)
        self.assertIn("HubStatus(tier)", cell)
        self.assertIn("MeterBar(", cell)


if __name__ == "__main__":
    unittest.main()
