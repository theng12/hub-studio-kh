"""The Jobs page's tabs: every Studio has one, and a tab with nothing to list says so.

The owner asked for two things the tab bar could not do. Video: *"maybe add one for video as a
placeholder now so we don't forget about it"* — a tab that reads Video and says what video will be,
rather than no tab at all. Chat: *"with the addition of llm, we'd need an llm job's tab too. don't
miss that."* — which needed the Hub to start recording a Chat-page completion as a job, and the app
to place that job, carry its answer and draw it in a row of its own.

A tab is not a claim that work exists, so the two questions are separate and both are pinned here:
whether a Studio appears in the tab bar (`hasHistoryTab`), and whether the Hub files batches for it
(`hasBatchHistory`). Video answers yes to the first and no to the second, and its tab has to say
exactly that - no rows, no controls, no date.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()
SHELL = (ROOT / "native" / "ShellCore.swift").read_text()
API = (ROOT / "native" / "StudioAPI.swift").read_text()


def without_comments(swift: str) -> str:
    lines = []
    for line in swift.splitlines():
        if line.strip().startswith("//"):
            continue
        lines.append(line.split("//", 1)[0])
    return "\n".join(lines)


def body(of: str, marker: str, stop: str = "\n    private func") -> str:
    """One function's body, by its declaration, with comments removed."""
    text = without_comments(of)
    start = text.index(marker)
    end = text.find(stop, start + len(marker))
    return text[start:end if end >= 0 else len(text)]


def studio_switches(property_name: str, _seen: tuple[str, ...] = ()) -> dict[str, bool]:
    """The answers one `StudioFunction` property gives, read from its own switch.

    A case may answer with another property (`case .audio: return hasBatchHistory`) rather than a
    literal, so that answer is followed to the property it names - otherwise the reader would take
    "not the word true" for false and report a Studio that does have a history as if it had none.
    """
    text = without_comments(SHELL)
    start = text.index(f"var {property_name}: Bool {{")
    block = text[start:text.index("\n    }", start)]
    answers: dict[str, bool] = {}
    for match in re.finditer(r"case ([^:]+): return ([\w.]+)", block):
        value = match.group(2)
        if value in ("true", "false"):
            answer = value == "true"
        elif value in _seen:
            raise AssertionError(f"{property_name} and {value} answer with each other")
        else:
            answer = None
        for name in match.group(1).split(","):
            answers[name.strip().lstrip(".")] = answer if answer is not None else value
    for studio, answer in answers.items():
        if isinstance(answer, str):
            answers[studio] = studio_switches(answer, _seen + (property_name,))[studio]
    return answers


class JobsTabTests(unittest.TestCase):
    def test_the_tabs_are_derived_from_the_studio_enum(self) -> None:
        """A hand-written tab list is how a Studio ends up with no tab and nothing saying so."""
        tabs = VIEWS[VIEWS.index("enum HistoryTab"):]
        tabs = tabs[:tabs.index("\n}")]
        self.assertIn("StudioFunction.allCases", tabs, "the tab list is hand-written again")
        self.assertNotIn('"Video"', tabs, "a Studio is named by hand in the tab list")

    def test_every_studio_has_a_tab_and_only_some_have_rows(self) -> None:
        tabbed = studio_switches("hasHistoryTab")
        rows = studio_switches("hasBatchHistory")
        for studio in studio_switches("hasEngine"):
            self.assertIn(studio, tabbed, f"{studio} was never asked whether it has a Jobs tab")
            self.assertTrue(tabbed[studio], f"{studio} must have a Jobs tab")
            self.assertIn(studio, rows, f"{studio} was never asked whether the Hub files its jobs")
        # The whole point of the split: Video is listed and has nothing filed, so its tab can only
        # ever be the placeholder - while every Studio with a real history keeps its rows.
        self.assertTrue(tabbed["video"] and not rows["video"],
                        "Video must have a tab without claiming a batch history")
        for studio in ("audio", "transcription", "image", "music", "chat"):
            self.assertTrue(rows[studio], f"{studio} files jobs, so it must have a batch history")

    def test_every_function_with_a_history_lands_on_a_real_row(self) -> None:
        """The dispatcher is exhaustive, so a sixth Studio stops the build rather than the page."""
        dispatcher = body(VIEWS, "private func jobRow(_ job: NativeJob, for function")
        cases = list(re.finditer(r"case ([^:]+):", dispatcher))
        self.assertTrue(cases, "the dispatcher has no cases at all")
        for index, case in enumerate(cases):
            end = cases[index + 1].start() if index + 1 < len(cases) else len(dispatcher)
            branch = dispatcher[case.end():end]
            for studio in (name.strip().lstrip(".") for name in case.group(1).split(",")):
                with self.subTest(studio=studio):
                    if studio_switches("hasBatchHistory")[studio]:
                        self.assertRegex(branch, r"\bjobRow\(|\b\w+JobRow\(",
                                         f"{studio} has a batch history but no row style")
                    else:
                        self.assertIn("EmptyView()", branch,
                                      f"{studio} has no rows, so its branch must say so")

    def test_the_video_tab_is_a_placeholder_with_nothing_in_it(self) -> None:
        jobs = VIEWS[VIEWS.index("private var jobsWorkspace"):]
        jobs = jobs[:jobs.index("\n    private func")]
        placeholder = jobs[jobs.index("!studio.hasBatchHistory"):]
        placeholder = placeholder[:placeholder.index("\n                }")]
        # Which Studio it is comes from the tab, never from a name written into the page.
        self.assertNotIn(".video", placeholder, "the placeholder must be driven by the tab's studio")
        self.assertIn("studio.hasBatchHistory", placeholder,
                      "the placeholder is for a Studio the Hub files nothing for")
        self.assertIn("is not in this build yet", placeholder,
                      "the tab must say the Studio is not here, not that it has no jobs")
        # ...and it lists nothing: no rows, and no control that would do something.
        self.assertNotIn("jobRow(", placeholder, "the Video tab must not draw a job row")
        self.assertNotIn("Button(", placeholder, "the Video tab must offer no controls")
        self.assertNotIn("ProgressView", placeholder)
        # No promise of a date, and no claim that work exists.
        for promise in ("coming soon", "Coming soon", "soon", "2026", "2027"):
            self.assertNotIn(promise, placeholder,
                             f"the placeholder must not promise {promise!r}")

    def test_an_empty_chat_tab_says_no_chat_jobs_yet(self) -> None:
        """The honest empty state, for a build running before the Hub records any."""
        jobs = VIEWS[VIEWS.index("private var jobsWorkspace"):]
        jobs = jobs[:jobs.index("\n    private func")]
        self.assertIn('Text("No \\(history.function.rawValue.lowercased()) jobs yet.")', jobs,
                      "an empty section must still say so per Studio")

    def test_the_chat_row_is_a_text_row(self) -> None:
        """Prompt, model, state, time and the answer - and no artifact affordance it has none."""
        row = body(VIEWS, "private func chatJobRow(_ job: NativeJob) -> some View")
        self.assertIn("job.text", row, "the row must show the prompt that was sent")
        self.assertIn("job.answer", row, "the row must show the answer the Hub recorded")
        self.assertIn("stateBadge(job.state)", row, "the row must carry the state badge")
        self.assertIn("modelName(job.model)", row,
                      "the row must name the model by its published label, quantization included")
        self.assertIn("job.generationSuffix", row, "the row must show how long it took")
        self.assertIn("textSelection(.enabled)", row, "an answer is text the operator may copy")
        for offered in ('Button("Copy Answer")', 'Button("Save…")'):
            self.assertIn(offered, row, f"an answer must offer {offered}")
        for wrong in ('Button("Play")', 'Button("Preview")', "imageJobRow", "subtitleJobRow",
                      "Open Transcript", "artifact"):
            self.assertNotIn(wrong, row,
                             f"a Chat answer is not an artifact: the row must not offer {wrong}")

    def test_a_chat_answer_is_the_item_the_hub_recorded(self) -> None:
        """The Hub streams a completion and keeps no file, so the item's text is the answer."""
        item = API[API.index("private struct HubJobItem"):]
        item = item[:item.index("\n}")]
        self.assertIn("let text: String?", item,
                      "the item's answer field is not decoded, so the row has nothing to show")
        mapping = API[API.index("private static func mapHubJob"):]
        mapping = mapping[:mapping.index("\n    }")]
        self.assertIn("answer: item?.text", mapping,
                      "the answer must reach the row from the item the Hub recorded")


if __name__ == "__main__":
    unittest.main()
