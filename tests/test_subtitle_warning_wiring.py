"""The Subtitle below-tier sentence reaches the page, end to end.

`tests/test_below_tier_warning.py` pins the two ends: the worker publishes `memory_warning` at the
top level of its catalogue row, and some page renders `selected<Studio>Model?.memoryWarning`. What
it cannot see is the link between them, and until this change there was none for Subtitle.

The transcription rows the page uses come from the worker's own availability route
(`/studio/transcription/api/transcribe/availability` -> `manager.availability()`), which carries no
RAM tier. The sentence is published on the worker's `/api/catalog` row, which the app receives
through the Hub aggregate. `combineModels` therefore has to read the catalogue row's warning for a
repo and put it on that repo's transcription model - otherwise the sentence is decoded and dropped,
and the render site can never show it. That is the same shape as the original defect, one layer
further along, so it is pinned here with a fixture that makes the row carry the sentence and then
not carry it at all.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = (ROOT / "native" / "StudioAPI.swift").read_text()
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()
NATIVE = (ROOT / "tests" / "native" / "api_main.swift").read_text()
ENGINE = (ROOT / "components" / "subtitle" / "app" / "backend" / "engine.py").read_text()
MANAGER = (ROOT / "components" / "subtitle" / "app" / "backend" / "manager.py").read_text()


def without_comments(swift: str) -> str:
    return "\n".join(line.split("//", 1)[0] for line in swift.splitlines())


def body(of: str, marker: str, stop: str = "\n    }") -> str:
    text = without_comments(of)
    start = text.index(marker)
    end = text.find(stop, start + len(marker))
    return text[start:end if end >= 0 else len(text)]


class SubtitleWarningWiringTests(unittest.TestCase):
    def test_the_catalogue_rows_warning_is_read_for_the_transcription_models(self) -> None:
        combine = body(API, "private static func combineModels(")
        self.assertIn("let catalogWarnings", combine,
                      "the catalogue rows' warnings must be indexed before the models are built")
        self.assertIn("item.memoryWarning", combine,
                      "the index must come from the row's own published `memory_warning`")
        transcription = combine[combine.index("let transcriptionModels = transcriptionRows.map"):]
        transcription = transcription[:transcription.index("let imageModels =")]
        self.assertIn("catalogWarnings[item.repo]", transcription,
                      "a transcription model must take the warning published for its repo")
        self.assertIn("memoryWarning: memoryWarning,", transcription,
                      "the warning must reach the model the Subtitle page reads")

    def test_the_availability_route_cannot_be_the_source(self) -> None:
        """The reason the wiring above is needed, read from the producer."""
        self.assertIn("'memory_warning'", ENGINE,
                      "the worker no longer publishes the warning on its catalogue row")
        # If this ever becomes false the projection gained the key, and the catalogue lookup is
        # belt-and-braces rather than the only path - worth knowing, so it fails here rather than
        # being discovered by a page that shows nothing.
        self.assertNotIn("'memory_warning'", MANAGER,
                         "the availability route now carries the tier; update this test and decide "
                         "which surface the app should read")

    def test_the_render_site_matches_the_image_pages_shape(self) -> None:
        page = body(VIEWS, "private var transcriptionWorkspace: some View", stop="\n    private var")
        block = page[page.index("if let warning = selectedTranscriptionModel?.memoryWarning"):]
        block = block[:block.index('LabeledRow(label: "Language"')]
        # Every page draws the published sentence through the one `advisory` helper (triangle, warning
        # colour, wraps), so the five pages cannot drift apart one fix at a time.
        self.assertIn("advisory(warning)", block)
        self.assertIn("if let warning", block,
                      "the page must render nothing at all when no warning was published")
        self.assertNotIn("else", block, "a machine at the tier must not get a reassuring message")
        helper = body(VIEWS, "private func advisory", stop="\n    private func")
        self.assertIn('Image(systemName: "exclamationmark.triangle.fill")', helper)
        self.assertIn("HubStatus.warn", helper)
        self.assertIn("fixedSize(horizontal: false, vertical: true)", helper)

    def test_the_fixture_runs_both_answers(self) -> None:
        self.assertIn("setSubtitleMemoryWarning", NATIVE,
                      "the fixture must be able to publish the warning and to publish none")
        self.assertIn("the Subtitle worker's published warning must reach the page's model", NATIVE)
        self.assertIn("a Mac at or above the tier must render nothing", NATIVE)
        self.assertIn("a RAM tier is advice, not a wall", NATIVE)


if __name__ == "__main__":
    unittest.main()
