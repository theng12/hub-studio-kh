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


def chatWorkspace() -> str:
    return page("chatWorkspace")


def without_comments(swift: str) -> str:
    """Swift source with `//` comments removed, so prose about a layout cannot pass for the layout."""
    lines = []
    for line in swift.splitlines():
        if line.strip().startswith("//"):
            continue
        lines.append(line.split("//", 1)[0])
    return "\n".join(lines)


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
        """Form on the left, result on the right - the Image page's shape, on all four.

        The owner asked for this twice: the Image generator's layout, applied everywhere. The
        numbers are the same on every page so they cannot drift apart one fix at a time. Chat is in
        this list for the same reason it was missing from it: it was the one workspace page whose
        columns were drawn by hand, so it was the one that could drift.
        """
        for name, body in (("audio", audioWorkspace()), ("subtitle", subtitleWorkspace()),
                           ("image", imageWorkspace()), ("chat", chatWorkspace())):
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
        # Searched across the file: one of these lives in the voice picker, the other in the panel
        # beneath it, and both are on the page that was clipped. The captions that used to sit in
        # the loose-clip block went with that block: cloning is library-only now.
        for caption in ("voicePickerDetail",
                        "This voice's clip, its transcript and its timing are already stored "
                        "with it.",
                        "This saved voice has no transcript yet, so a clone cannot align its "
                        "audio."):
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


class WorkspaceFormOrderTests(unittest.TestCase):
    """Every workspace form reads the same way: what you write, the action, a divider, the model.

    The owner's report was that Chat "is so bad" beside the other four, and this is the concrete
    difference: Chat drew its model picker *between* the message box and the Send button, so the
    one control the page exists for was stranded mid-column and the paragraph explaining a blocked
    model floated underneath the button instead of under the model it describes.

    No behaviour test can see that. These pin the order itself, and they are written as properties
    over the page bodies rather than against literal text, so the next page - or the next rename of
    a button - is held to the same order instead of quietly exempting itself.
    """

    # page, member, the box it starts from (None where a page has no text box), its primary
    # action, and its model picker.
    FORMS = (
        ("chat", "chatWorkspace", "TextEditor(", 'Button("Send")', "modelPicker(.chat)"),
        ("image", "imageWorkspace", "TextEditor(", 'Button("Generate Image")', "modelPicker(.image)"),
        ("audio", "audioWorkspace", "TextEditor(", 'Button("Generate Audio")', "modelPicker(.audio)"),
        ("subtitle", "transcriptionWorkspace", None, 'Button("Transcribe")',
         "modelPicker(.transcription)"),
    )

    def test_the_action_row_comes_before_the_model_picker_behind_a_divider(self) -> None:
        """The property worth pinning: input, then action, then a divider, then the model.

        A disabled prominent button that sits below the picker is the shape the owner saw: the
        action and the thing it acts on are separated by settings, and the settings' own warnings
        land under the button.
        """
        for name, member, editor, action, picker in self.FORMS:
            body = without_comments(page(member))
            self.assertIn(action, body, f"{name} no longer has its primary action")
            self.assertIn(picker, body, f"{name} no longer has its model picker")
            action_at, picker_at = body.index(action), body.index(picker)
            self.assertLess(action_at, picker_at,
                            f"{name}'s action sits below its model picker")
            if editor:
                self.assertLess(body.index(editor), action_at,
                                f"{name}'s action comes before the box it acts on")
            self.assertIn("Divider()", body[action_at:picker_at],
                          f"no divider separates {name}'s action row from its model picker")

    def test_every_result_pane_opens_with_a_heading_row(self) -> None:
        """The heading is fixed and the content scrolls under it, on every page.

        On Chat the heading was the first line *inside* the pane's ScrollView, so it scrolled away
        with the conversation and never lined up with the other pages' heading rows. The pane's
        first HStack holds the heading and a Spacer, and it is finished before anything scrolls.
        """
        panes = (
            ("chat", "chatWorkspace", 'Text("Conversation")'),
            ("image", "imageWorkspace", 'Text("Preview")'),
            ("audio", "audioWorkspace", 'Text("Recent audio")'),
            ("subtitle", "transcriptionWorkspace", 'Text("Transcript")'),
        )
        for name, member, heading in panes:
            result = without_comments(page(member)).split("maxWidth: 440)", 1)[1]
            self.assertIn(heading, result, f"{name}'s result pane lost its heading")
            heading_at = result.index(heading)
            spacer_at = result.index("Spacer()")
            scroll_at = result.find("ScrollView")
            self.assertLess(heading_at, spacer_at,
                            f"{name}'s heading row must hold the heading and a spacer")
            self.assertTrue(scroll_at < 0 or spacer_at < scroll_at,
                            f"{name}'s heading row is inside the scrolling content")

    def test_the_empty_result_pane_uses_the_house_empty_state(self) -> None:
        """An empty pane says so the way every other empty pane does, not with a stray sentence."""
        for name, member, title in (("chat", "chatWorkspace", "No messages yet"),
                                    ("image", "imageWorkspace", "Your image will appear here"),
                                    ("audio", "audioWorkspace", "Your audio will appear here"),
                                    ("subtitle", "transcriptionWorkspace", "Your transcript")):
            body = without_comments(page(member))
            self.assertIn(f'empty("{title}"', body,
                          f"{name}'s empty pane no longer uses the shared empty state")


if __name__ == "__main__":
    unittest.main()
