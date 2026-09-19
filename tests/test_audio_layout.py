"""The Audio page's two-column layout, pinned by the arithmetic that broke it.

The owner reported three times that the Generation column could not be read or used, and each time
the cause was the same: the column was narrower than its own contents, so SwiftUI clipped them at
its edge rather than the column getting room. Worse, the Script editor above it was greedy - an
unbounded `TextEditor` takes every pixel the split will give - so the controls column was squeezed
no matter how wide the window was.

A layout defect is invisible to a behaviour test, which is why the suite was green through all
three reports. Two things guard it now:

* the numbers that have to agree are pinned here, so a revert fails the suite rather than the
  owner's screen;
* `native/render-page.sh` renders the real page to a PNG, because nothing in a headless
  environment can otherwise see this window: `screencapture` needs Screen Recording permission
  (it answers "could not create image from display") and the sidebar cannot be driven reliably
  through Accessibility.
"""
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()
HUB = (ROOT / "native" / "StudioHub.swift").read_text()
API = (ROOT / "native" / "StudioAPI.swift").read_text()

# What the two-column pages need, in points: the sidebar, the page padding, and the two columns at
# their minimums. The window's floor has to be at least this or a column is clipped.
SIDEBAR = 260
PAGE_PADDING = 28 * 2
SCRIPT_COLUMN = 300
CONTROLS_COLUMN = 330


def page(_name: str) -> str:
    """One page's view body, from its declaration to the next one."""
    marker = f"private var {_name}: some View {{"
    body = VIEWS[VIEWS.index(marker) + len(marker):]
    cut = body.find("\n    private ")
    return body if cut < 0 else body[:cut]


def audioWorkspace() -> str:
    return page("audioWorkspace")


def subtitleWorkspace() -> str:
    return page("transcriptionWorkspace")


def imageWorkspace() -> str:
    return page("imageWorkspace")


class AudioLayoutTests(unittest.TestCase):
    def test_the_script_editor_cannot_eat_the_window(self) -> None:
        """An unbounded TextEditor is the original cause: it starved the controls column.

        It is a fixed height now, like the Image page's prompt box. Either shape is fine as long as
        there is a ceiling - the defect was a minimum with no maximum.
        """
        for name, body in (("audio", audioWorkspace()), ("image", imageWorkspace())):
            editor = body[body.index("TextEditor("):]
            editor = editor[:editor.index("accessibilityLabel")]
            fixed = re.findall(r"\.frame\(height: (\d+)\)", editor)
            bounded = re.findall(r"maxHeight: (\d+)", editor)
            ceilings = [int(value) for value in fixed + bounded]
            self.assertTrue(ceilings, f"{name}'s editor has no height ceiling at all")
            self.assertLessEqual(max(ceilings), 260,
                                 f"{name}'s editor may be at most 260 pt tall, found {ceilings}")

    def test_the_workspace_pages_share_one_shape(self) -> None:
        """Form on the left, result on the right - the Image page's shape, on all three.

        The owner asked for this twice: the Image generator's layout, applied everywhere. The
        numbers are the same on every page so they cannot drift apart one fix at a time.
        """
        for name, body in (("audio", audioWorkspace()), ("subtitle", subtitleWorkspace()),
                           ("image", imageWorkspace())):
            self.assertIn("HSplitView", body, f"{name} must use the two-column split")
            self.assertIn("minWidth: 350, idealWidth: 390, maxWidth: 440", body,
                          f"{name}'s form column must use the house width")
            self.assertIn("minWidth: 320, maxWidth: .infinity, maxHeight: .infinity", body,
                          f"{name}'s result column must fill and be top-aligned")
            self.assertIn("alignment: .topLeading", body,
                          f"{name}'s result column must start at the top")
            self.assertNotIn("minWidth: 245", body,
                             f"{name} still carries the 245 pt column that clipped its controls")

    def test_the_result_column_holds_the_result(self) -> None:
        """The history/preview belongs to the result side, and the form to the form side."""
        audio = audioWorkspace()
        form, result = audio.split("maxWidth: 440)", 1)
        self.assertIn("Generate Audio", form, "the primary action belongs with the form")
        self.assertIn("Recent audio", result, "the history belongs on the result side")
        subtitle = subtitleWorkspace()
        form, result = subtitle.split("maxWidth: 440)", 1)
        self.assertIn("Transcribe", form, "the primary action belongs with the form")
        self.assertIn("Recent subtitles", result, "the history belongs on the result side")

    def test_the_window_floor_is_at_least_what_the_columns_need(self) -> None:
        """The floor is arithmetic: below this the split has to clip a column, and it does."""
        match = re.search(r"contentMinSize = NSSize\(width: (\d+), height: (\d+)\)", HUB)
        self.assertIsNotNone(match, "the window's minimum size must be stated, not left to chance")
        width, height = int(match.group(1)), int(match.group(2))
        needed = SIDEBAR + PAGE_PADDING + SCRIPT_COLUMN + CONTROLS_COLUMN
        self.assertGreaterEqual(width, needed,
                                f"a {width} pt window cannot hold the columns ({needed} pt)")
        self.assertGreaterEqual(height, 620, "short windows clipped the Script label in half")

    def test_no_page_pushes_its_notes_to_the_foot_of_the_window(self) -> None:
        """A flexible split pushes anything after it down, which reads as a stray footer.

        On Models the library-wide note and the "start the local services" line lived *below* the
        split, so they appeared at the very bottom of the window, far from the list they describe.
        """
        models = page("modelsWorkspace")
        note = models.index("Start the local services to inspect the model library")
        split = models.index("HSplitView")
        self.assertLess(note, split, "the library note belongs above the list, not below the split")

    def test_the_voice_tabs_are_labelled_short_enough_to_fit(self) -> None:
        """Three long titles could not fit the column, so the tabs themselves were cut off."""
        self.assertIn("mode.shortTitle", VIEWS,
                      "the segmented tabs must use the short titles")
        shorts = API[API.index("var shortTitle: String"):]
        shorts = shorts[:shorts.index("var symbol")]
        for label in ('return "Clone"', 'return "Ready-Made"', 'return "Design"'):
            self.assertIn(label, shorts)
        self.assertIn("accessibilityLabel(mode.title)", VIEWS,
                      "VoiceOver must still hear the full tab name")

    def test_captions_wrap_rather_than_being_clipped(self) -> None:
        # Searched across the file: one of these lives in the panel proper, another in the voice
        # picker beside it, and both are on the page that was clipped.
        for caption in ("voicePickerDetail", "A short, clean recording of the voice you want to reuse.",
                        "Required: this model matches the clip to these words."):
            index = VIEWS.index(caption)
            window = VIEWS[index:index + 400]
            self.assertIn("fixedSize(horizontal: false, vertical: true)", window,
                          f"{caption} must wrap instead of being cut off at the column edge")

    def test_the_page_can_be_looked_at_without_a_screen(self) -> None:
        """The tool that found this, kept so the next layout change is not written blind."""
        script = ROOT / "native" / "render-page.sh"
        harness = ROOT / "tests" / "native" / "render_main.swift"
        self.assertTrue(script.is_file() and os.access(script, os.X_OK),
                        "native/render-page.sh must exist and be executable")
        self.assertTrue(harness.is_file(), "the render harness must exist")
        source = harness.read_text()
        self.assertIn("NSHostingView", source,
                      "the harness must host a real window: ImageRenderer cannot draw AppKit controls")
        self.assertIn("cacheDisplay", source)


if __name__ == "__main__":
    unittest.main()
