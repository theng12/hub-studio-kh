"""The Settings memory row says what the Studio says, `auto` included.

Every worker now publishes a fifth mode, `auto`, and it is the default. Only the worker knows what
`auto` resolved to on this Mac - "Follows this Mac: releases after 10 idle minutes here" on a 32 GB
machine, "…as soon as each job finishes here" on an 8 GB one - so its own `detail` sentence is the
one the row must render. The app's derived sentence ("Releases after 10 idle minutes.") cannot tell
the two machines apart, and is kept only as the fallback for a worker that published no detail at
all.

The mode list itself already renders from the worker's published `options`, so `Auto` appears
without an app-side edit and must keep doing so. Which mode is in force likewise comes from the
payload: `resolved_mode` (with `machine_mode` as the fallback for the one mode whose answer is the
machine's) says what `auto` became, and the app never re-derives the threshold rule.

`tests/native/api_main.swift` drives the real store against both payloads - one with the worker's
sentences, one without - and is where the rendered sentences are asserted.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = (ROOT / "native" / "StudioAPI.swift").read_text()
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()
NATIVE = (ROOT / "tests" / "native" / "api_main.swift").read_text()
SHARED = (ROOT / "components" / "memory_modes.py").read_text()


def without_comments(swift: str) -> str:
    return "\n".join(line.split("//", 1)[0] for line in swift.splitlines())


def body(of: str, marker: str, stop: str = "\n    }") -> str:
    text = without_comments(of)
    start = text.index(marker)
    end = text.find(stop, start + len(marker))
    return text[start:end if end >= 0 else len(text)]


class MemoryAutoSentenceSourceTests(unittest.TestCase):
    def test_the_published_detail_is_the_sentence_and_the_derivation_is_only_a_fallback(self) -> None:
        mode = body(API, "struct NativeMemoryMode: Decodable, Identifiable, Equatable")
        self.assertIn("let detail: String?", mode,
                      "the mode's own sentence is not decoded, so `auto` has no machine answer")
        summary = mode[mode.index("var summary: String {"):]
        published = summary.index("detail")
        derived = summary.index("guard let idleSeconds")
        self.assertLess(published, derived,
                        "the derived sentence is tried before the worker's own sentence")
        self.assertIn("return detail", summary,
                      "the published sentence must be returned as-is, not reworded")
        # ...and the fallback is still there for a payload that stated nothing.
        self.assertIn("Releases after \\(idleSeconds / 60) idle minutes.", summary,
                      "a payload with no detail must still render a readable sentence")

    def test_the_mode_list_is_the_workers_options_not_a_local_array(self) -> None:
        row = body(VIEWS, "private func memoryRow(_ row: NativeMemoryStudio, options: [NativeMemoryMode])")
        self.assertIn("items: options.map", row,
                      "the picker must iterate the published options, not a local list")
        self.assertIn("option.mode", row, "each mode is chosen by the id the worker published")
        self.assertIn("options.first { $0.mode == policy.mode }?.summary", row,
                      "the sentence must be the selected mode's published one")
        # No hand-written list of modes anywhere in the memory section.
        section = VIEWS[VIEWS.index("private var memoryControls: some View"):]
        section = section[:section.index("private func memoryStateLabel")]
        for literal in ('"auto"', '"balanced"', '"memory_saver"', '"immediate"', '"performance"'):
            with self.subTest(literal=literal):
                self.assertNotIn(literal, section,
                                 f"the Settings memory section hardcodes the mode {literal}")

    def test_which_mode_is_in_force_comes_from_the_published_resolution(self) -> None:
        policy = body(API, "struct NativeMemoryPolicy: Decodable, Equatable")
        self.assertIn("let resolvedMode: String?", policy,
                      "the payload's own resolved mode is not decoded")
        self.assertIn("let machineMode: String?", policy,
                      "the machine's own answer is not decoded")
        resolution = policy[policy.index("var resolution: String? {"):]
        self.assertIn("resolvedMode", resolution,
                      "the resolution must read the published resolved_mode")
        self.assertIn("label(for:", resolution,
                      "both mode names must be the worker's own published labels")
        self.assertIn("resolved != mode", resolution,
                      "a mode that resolves to itself must not print a resolution line")
        # The machine rule itself never appears in the app.
        for rule in ("16 * 1024", "AUTO_BALANCED_MIN_BYTES", "16 GiB", "17179869184"):
            with self.subTest(rule=rule):
                self.assertNotIn(rule, policy, f"the machine rule {rule} is being re-derived in Swift")
        row = body(VIEWS, "private func memoryRow(_ row: NativeMemoryStudio, options: [NativeMemoryMode])")
        self.assertIn("policy.resolution", row,
                      "the row must show the selected mode and what it resolved to")

    def test_the_worker_publishes_exactly_what_the_app_decodes(self) -> None:
        """The shape the app decodes is the shape `memory_modes.options()` publishes."""
        for key in ('"mode"', '"label"', '"idle_seconds"', '"detail"'):
            with self.subTest(key=key):
                self.assertIn(key, SHARED, f"the worker no longer publishes {key}")
        mode = body(API, "struct NativeMemoryMode: Decodable, Identifiable, Equatable")
        for field in ("let mode: String", "let label: String", "let idleSeconds: Int?",
                      "let detail: String?"):
            with self.subTest(field=field):
                self.assertIn(field, mode, f"the app does not decode {field}")

    def test_the_fixture_and_its_negative_control_both_run(self) -> None:
        self.assertIn("static let memoryAutoFixture", NATIVE,
                      "the fixture must publish a mode with the worker's own sentence")
        self.assertIn("Follows this Mac: releases after 10 idle minutes here", NATIVE)
        self.assertIn("static let memoryNoDetailFixture", NATIVE,
                      "the fixture must also cover a payload with no detail")
        self.assertIn("the worker's own sentence must be rendered, not a locally derived one", NATIVE)
        self.assertIn("must fall back to the derived sentence", NATIVE)
        self.assertIn("the row must name the selected mode and what it resolved to", NATIVE)


if __name__ == "__main__":
    unittest.main()
