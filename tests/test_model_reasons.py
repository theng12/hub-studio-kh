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
        """The owner asked how chunking is applied per model. Answering it needs no terminal.

        The page renders every fact a producer publishes through `model.facts`, so the section
        bounds, the hard cap, the join pause and the speed route are shown without a click per
        model. The sentences are built in the store from the producer's own payload — the
        catalogue's long-form policy and the lane's parameter bounds — so the page cannot restate
        a number the runtime does not enforce.
        """
        models = VIEWS[VIEWS.index("private var modelsWorkspace"):]
        models = models[:models.index("private var ", 10)]
        self.assertIn("modelFactsBlock(model)", models,
                      "the Models page must render the model's own published facts")
        self.assertIn("modelLongFormBlock(model)", models,
                      "the long-form facts must be on the pane, not only in the payload")
        self.assertIn('Text("Long-form delivery")', VIEWS,
                      "the block must name what it is")
        # The list row marks the section size and the speed route, so scanning the library does not
        # need a click per model; the row is its own helper, which is where it is read from.
        row = VIEWS[VIEWS.index("private func modelListRow("):]
        row = row[:row.index("\n    }")]
        self.assertIn("model.longForm", row, "the list must mark sectioning without a click")
        self.assertIn("speedDelivery", row, "the list row must say how speed is delivered")
        long_form = API[API.index("struct NativeLongFormDelivery"):]
        long_form = long_form[:long_form.index("\n}")]
        for phrase in ("Section size", "Adjustable range", "Hard cap", "Between sections",
                       "Speed control"):
            self.assertIn(f'label: "{phrase}"', long_form,
                          f"the published long-form facts never carry {phrase!r}")
        # The step and the engine default are part of the bound, not decoration: a control whose
        # step nobody published is a step this app invented.
        self.assertIn("sectionStep", long_form, "the catalogue's own step is not read")
        self.assertIn("engine default", long_form, "the engine's own default section size is not shown")
        self.assertIn("delivery", long_form, "how speed is delivered must be part of the fact")
        for source in ("section_size_control", "hard_cap_characters", "join_pause_milliseconds",
                       "speed.delivery"):
            self.assertIn(source, long_form, f"the fact must name the key that stated it: {source}")

    def test_the_parameter_facts_are_the_lanes_own_bounds(self) -> None:
        """Every parameter a lane publishes is a fact, with the bound the lane enforces.

        This is what makes "the steps range" or "the duration ladder" a read of the producer's
        numbers: a new parameter, a re-bounded one, or a whole new Studio reaches this screen
        because `NativeModel.facts` walks `lane_contract.parameters` rather than a table here.
        """
        facts = API[API.index("var facts: [NativeModelFact] {"):]
        facts = facts[:facts.index("\n    }")]
        self.assertIn("for parameter in contract.parameters", facts,
                      "the facts must walk the lane's published parameters")
        self.assertIn("Self.parameterSummary(parameter)", facts,
                      "each parameter's bound must be rendered from the parameter itself")
        summary = API[API.index("static func parameterSummary("):]
        summary = summary[:summary.index("\n    }")]
        for bound in ("parameter.minimum", "parameter.maximum", "parameter.options",
                      "parameter.maxCharacters", "parameter.defaultValue"):
            self.assertIn(bound, summary, f"the summary ignores {bound}, so a bound is not read")
        # ...and a limit the producer states no value for renders nothing at all.
        limits = API[API.index("static func limitFacts("):]
        limits = limits[:limits.index("\n    }")]
        self.assertIn("guard let value = limit.value", limits,
                      "a limit with no stated value must render nothing, never a curated number")
        self.assertIn("case .null: return nil", limits, "a null limit must render nothing")

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
