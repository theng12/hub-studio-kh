"""Why a model will not run here, said out loud.

The owner reported that the app "just shows unavailable with no proper reason" and guessed at RAM
pressure. He was guessing because the app told him nothing: the Hub checks five independent gates
before it will accept a job - the model's files, the studio runtime, the macOS floor, the chip, and
whether the studio process is running - and `/api/hub/model-packages` has always sent all of them.
`HubModelPackage` decoded four fields and dropped the gates, so the app could only say that
something was unavailable.

Each gate has a different fix, so each gets its own sentence, and the reason is shown where the
operator is: the generator's model picker, the Generate button's own explanation, and the Models
page.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = (ROOT / "native" / "StudioAPI.swift").read_text()
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()


class ModelReasonTests(unittest.TestCase):
    def test_the_package_row_decodes_every_gate_the_hub_sends(self) -> None:
        """The bug, pinned at its source: four fields decoded where the Hub sends eleven."""
        package = API[API.index("private struct HubModelPackage: Decodable"):]
        package = package[:package.index("private struct HubModelDownload")]
        for field in ("filesState", "runtimeState", "osState", "chipState", "workerState",
                      "minimumMacos", "macosVersion", "reason"):
            self.assertIn(f"let {field}", package, f"{field} must be decoded, not discarded")

    def test_every_gate_has_its_own_sentence(self) -> None:
        builder = API[API.index("private static func blockReason("):]
        builder = builder[:builder.index("private static func mapHubJob")]
        for phrase in ("not verified", "runtime for this model", "macOS", "Apple Silicon",
                       "Studio is not running", "cannot run this model right now",
                       "not have enough memory"):
            self.assertIn(phrase, builder, f"no sentence for the {phrase!r} gate")
        # A published RAM tier is advice, not a gate. The worker still enforces the live
        # free-memory guard, and the owner runs FLUX.2 Klein on 8 GB Macs; returning the
        # tier as a block reason made an 8 GB Mac read "needs 16 GB" and refuse to run.
        self.assertNotIn("if let warning = memoryWarning", builder,
                         "the RAM tier must be shown, not turned into a block")
        self.assertIn("memoryEligible == false", builder,
                      "the worker's own hard memory gate must still block")

    def test_an_unusable_model_is_marked_and_explained_where_it_is_chosen(self) -> None:
        picker = VIEWS[VIEWS.index("private func modelPicker("):]
        picker = picker[:picker.index("\n    private func", 10)]
        self.assertIn("model.pickerSuffix", picker, "the picker must mark a model it cannot run")
        self.assertIn("model.blockReason", picker, "and say why underneath")

    def test_generate_says_the_reason_rather_than_staying_disabled(self) -> None:
        blocked = VIEWS[VIEWS.index("private var audioBlockedReason"):]
        blocked = blocked[:blocked.index("private var voiceLibraryLoadKey")]
        self.assertIn("if let reason = model.blockReason { return reason }", blocked)

    def test_the_models_page_names_the_gate_too(self) -> None:
        """The page an operator opens to ask exactly this question."""
        models = VIEWS[VIEWS.index("private var modelsWorkspace"):]
        models = models[:models.index("private var ", 10)]
        self.assertIn("model.blockReason", models)

    def test_the_models_page_shows_how_a_model_is_sectioned_and_speeded(self) -> None:
        """The owner asked how chunking is applied per model. Answering it needs no terminal."""
        models = VIEWS[VIEWS.index("private var modelsWorkspace"):]
        models = models[:models.index("private var ", 10)]
        for phrase in ("Section size", "Adjustable range", "Speed control", "Long-form delivery"):
            self.assertIn(phrase, models, f"the Models page never shows {phrase!r}")
        self.assertIn("model.longForm", models, "the list must mark sectioning without a click")

    def test_the_app_decodes_the_published_long_form_policy(self) -> None:
        """The worker publishes the policy per model; the app decoded none of it before."""
        wire = API[API.index("struct HubLongFormDelivery: Decodable"):]
        wire = wire[:wire.index("struct NativeLongFormDelivery")]
        for field in ("sectionMaxCharacters", "hardCapCharacters", "speed", "sectionSizeControl"):
            self.assertIn(f"let {field}", wire, f"{field} must be decoded, not discarded")
        resolved = API[API.index("struct NativeLongFormDelivery"):]
        resolved = resolved[:resolved.index("private struct TranscriptionAvailability")]
        self.assertIn("hardCapCharacters", resolved)
        self.assertIn("speedDelivery", resolved)

    def test_readiness_is_no_longer_computed_from_a_subset(self) -> None:
        """`ready` used to ignore the gates entirely for audio and subtitle."""
        self.assertIn("blockReason(memoryWarning", API)
        self.assertNotIn("item.memoryEligible != false && package?.dispatchState == \"eligible\"", API,
                         "one of the three mappers still uses a hand-rolled readiness rule")

    def test_voice_modes_are_read_from_the_producer_not_derived(self) -> None:
        """The tab list is the engine's own `modes`, not a rule this app keeps.

        The app derived one form per model, from `reference_audio`/`capabilities` plus a Qwen tier
        name check. That is a second source of truth free to disagree with the engine that answers
        the request, and it showed a single tab for a model that publishes two (VoxCPM2 4-bit /
        8-bit / bf16 clone *and* design). The row field is the only answer read now.
        """
        self.assertIn("Self.voiceModes(item.modes)", API,
                      "the audio mapper must read the producer's own modes")
        self.assertIn("let modes: [String]?", API, "the catalogue row must decode what it sends")
        self.assertNotIn("voiceMode(repo:", API,
                         "a derived form must not come back beside the published one")


if __name__ == "__main__":
    unittest.main()
