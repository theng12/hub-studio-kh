"""The Image page's loading-mode lever is published data, not an app-side opinion.

The owner asked for exactly this control: *"there is a real lever for image if you want one... the
low-RAM mode... That could be exposed as Auto / Low memory / Full on the Image page."* It is a
different question from Settings' release policy - the Image worker's `/api/memory-policy` is a
fixed one-mode answer because mflux is built inside a job and dropped when it returns - and the
app has to keep the two apart.

What makes this page honest is where every string comes from. Whether a control exists at all is
the model's own catalogue row (`loading_modes.supported`); the options, their order and their
sentences are that row's `options` (the sentences carry the model's measured peak and wall time
and the host they were measured on); and the configuration actually in force is the worker's own
`effective`, so an operator can see that `auto` resolved to the bounded configuration on this Mac.
A model with exactly one loading mode publishes `supported: false`, an empty option list and its
own `reason`, and gets a sentence instead of a control.

The source-level half is here; the behaviour half - the real store against fixture catalogue rows
with both answers, the PUT and the refreshed `effective` - is in `tests/native/api_main.swift`,
because the wire structs are private to the app.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = (ROOT / "native" / "StudioAPI.swift").read_text()
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()
NATIVE = (ROOT / "tests" / "native" / "api_main.swift").read_text()


def without_comments(swift: str) -> str:
    return "\n".join(line.split("//", 1)[0] for line in swift.splitlines())


def body(of: str, marker: str, stop: str = "\n    }") -> str:
    """One declaration's body, by its exact signature, with comments removed."""
    text = without_comments(of)
    start = text.index(marker)
    end = text.find(stop, start + len(marker))
    return text[start:end if end >= 0 else len(text)]


class ImageLoadingSourceTests(unittest.TestCase):
    def test_the_control_renders_the_models_own_published_options(self) -> None:
        view = body(VIEWS, "private var imageLoadingModeControl: some View")
        self.assertIn("workspace.imageLoadingControl", view,
                      "the control must read the store's published-derived control")
        self.assertIn("control.offersControl", view,
                      "the control must appear exactly when the model published one")
        # The list is the payload's: `control.options.map` in the producer's order.
        self.assertIn("control.options.map", view,
                      "the picker must iterate the published options, not a local list")
        self.assertIn("tag: $0.id", view, "each option is chosen by the id the producer published")
        # ...and the sentence under it is the selected option's own, with its measured numbers.
        self.assertIn("control.detail", view, "the selected option's own sentence must be shown")
        self.assertIn("control.effectiveSentence", view,
                      "the page must show what this Mac will actually load")

    def test_the_control_has_no_local_mode_list_and_no_local_machine_rule(self) -> None:
        """The negative control for the hardcoding rule, read off the real source.

        A Swift array of `auto`/`low`/`full`, a copy of the model's threshold, or a locally worded
        trade are the three ways this page could lie about what the worker will do. None may
        appear in the control, in the store's derivation, or in the decoded payload.
        """
        control = body(VIEWS, "private var imageLoadingModeControl: some View")
        store = body(API, "var imageLoadingControl: NativeImageLoadingControl? {")
        decoder = body(API, "struct NativeImageLoadingModes: Decodable, Equatable")
        for name, text in (("the view", control), ("the store", store), ("the decoded row", decoder)):
            with self.subTest(where=name):
                for literal in ('"auto"', '"low"', '"full"', '["auto"', '"auto", "low"'):
                    self.assertNotIn(literal, text,
                                     f"{name} carries a local mode list ({literal})")
                for rule in ("16 *", "GiB", "24000000000", "16_000_000_000"):
                    self.assertNotIn(rule, text,
                                     f"{name} carries a copy of the worker's threshold rule ({rule})")
                # This control is not the release policy, and must not be wired to it.
                self.assertNotIn("memory-policy", text,
                                 f"{name} is reading the release policy, which is a different question")

    def test_a_single_mode_model_renders_its_own_reason_instead_of_a_control(self) -> None:
        view = body(VIEWS, "private var imageLoadingModeControl: some View")
        # The unsupported branch is the else of the control: it renders the sentence and offers
        # nothing that would do something.
        reason_branch = view[view.index("else if let reason = control.reason"):]
        self.assertIn("Text(reason)", reason_branch,
                      "a single-mode model must render the model's own reason")
        self.assertNotIn("Picker(", reason_branch,
                         "a single-mode model must not be offered a control")
        self.assertNotIn("Button(", reason_branch,
                         "a single-mode model must offer no action")
        # ...and the reason is the published one, or a sentence saying nothing was published -
        # never a locally worded trade.
        store = body(API, "var imageLoadingControl: NativeImageLoadingControl? {")
        self.assertIn("published?.supported == true", store,
                      "a control must exist only when the model's row says it has more than one mode")
        self.assertIn("options: offered ? options : []", store,
                      "an unsupported model must be offered no options at all")
        self.assertIn("published?.reason", store,
                      "the model's own reason must be the sentence for a single-mode model")
        self.assertIn("publishes no loading mode", store,
                      "a row that published no loading modes at all must say that")

    def test_the_models_loading_modes_are_decoded_from_its_catalogue_row(self) -> None:
        catalog = body(API, "private struct CatalogModel: Decodable")
        self.assertIn("let loadingModes: NativeImageLoadingModes?", catalog,
                      "the catalogue row's `loading_modes` is not decoded")
        image = body(API, "let imageModels = catalog.filter")
        self.assertIn("loadingModes: item.loadingModes", image,
                      "the image row must carry the model's own loading modes to the page")
        row = body(API, "struct NativeImageLoadingModes: Decodable, Equatable")
        for field in ("supported", "selected", "effective", "options", "reason"):
            with self.subTest(field=field):
                self.assertIn(f"let {field}", row, f"`{field}` is not decoded from the payload")
        option = body(API, "struct NativeImageLoadingOption: Decodable, Identifiable, Equatable")
        self.assertIn("let detail: String?", option,
                      "the option's own sentence is not decoded, so the measured numbers are lost")

    def test_choosing_a_mode_puts_it_to_the_image_worker_and_then_refreshes(self) -> None:
        method = body(API, "func setImageLoadingMode(_ mode: String) async {")
        self.assertIn('["studio", "image", "api", "loading-mode"]', method,
                      "the change must go through the Hub's generic studio proxy")
        self.assertIn('method: "PUT"', method, "the worker's route is a PUT")
        self.assertIn("ImageLoadingModeBody(mode: mode)", method,
                      "the chosen id must be the body the worker reads")
        self.assertIn("await refresh()", method,
                      "the catalogue must be refreshed so the page shows the new effective")
        read = body(API, "func refreshImageLoadingMode() async {")
        self.assertIn('["studio", "image", "api", "loading-mode"]', read,
                      "the per-machine status must be read from the worker's own route")

    def test_the_fixture_covers_supported_unsupported_and_unpublished_models(self) -> None:
        """The three answers the page must tell apart, each actually served by the fixture."""
        catalog = NATIVE[NATIVE.index("private static let catalogBaseFixture"):]
        catalog = catalog[:catalog.index('"#.utf8)')]
        self.assertIn('"supported":true', catalog,
                      "the fixture must publish a model with a control")
        self.assertIn('"supported":false', catalog,
                      "the fixture must publish a single-mode model")
        self.assertIn('"options":[]', catalog,
                      "a single-mode model must publish an empty option list")
        self.assertIn('"fixture/flux-unpackaged"', catalog,
                      "the fixture must carry a row with no loading_modes at all")
        # ...and the assertions that run against the real store.
        self.assertIn("must be the published ones in the published order", NATIVE)
        self.assertIn("a single-mode model must not be given a control the lane would discard", NATIVE)
        self.assertIn("the model's own reason must be the sentence shown", NATIVE)
        self.assertIn('FixtureProtocol.imageLoadingPutModes() == ["full"]', NATIVE,
                      "the test must check the PUT carried the chosen id")
        self.assertIn("the page must show the new effective after the change", NATIVE)
        self.assertIn("this model still loads its default configuration", NATIVE,
                      "the low-setting half of the model's reason must be exercised")


if __name__ == "__main__":
    unittest.main()
