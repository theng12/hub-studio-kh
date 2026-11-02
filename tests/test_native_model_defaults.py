"""The Models tab: one section per Studio, per-generator facts, and per-model defaults.

The owner's ask, verbatim: *"for the model tab, it needs to be split between each generators because
they all have different properties such as LLM has context windows. tts has word chunks and i can't
see/edit it. i want to have that control. image have default steps and resolution control and
ratio… music, i'm not sure but you can suggest."* He made the deciding call himself: **defaults are
per model, not per generator** — *"each model is weight differently. we can't have a blanket default
meant for one model but applied to all."*

The behaviour half is in `tests/native/api_main.swift`, driven against the real store and the
fixture routes. This module pins the shape that makes it true, because three of these were the kind
of defect that stays green while the screen lies:

* the sections are the app's own Studio list, not a second list that a new Studio misses;
* the editor's controls are the lane's published parameters — its order, its types, its bounds —
  with nothing about any parameter written down in Swift;
* a refused value's reason belongs to its own field, a stored value the lane no longer accepts is
  shown, and the page it seeds cannot quietly prefer the model's default over the operator's.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = (ROOT / "native" / "StudioAPI.swift").read_text()
VIEWS = (ROOT / "native" / "StudioViews.swift").read_text()
NATIVE = (ROOT / "tests" / "native" / "api_main.swift").read_text()
CONTRACT = (ROOT / "components" / "model_defaults.py").read_text()


def without_comments(swift: str) -> str:
    return "\n".join(line.split("//", 1)[0] for line in swift.splitlines())


def body(of: str, marker: str, stop: str = "\n    }") -> str:
    """One declaration's body, by its exact signature, with comments removed."""
    text = without_comments(of)
    start = text.index(marker)
    end = text.find(stop, start + len(marker))
    return text[start:end if end >= 0 else len(text)]


class SectionDerivationTests(unittest.TestCase):
    def test_the_sections_are_the_studio_enum_in_its_own_order(self) -> None:
        sections = body(API, "func modelSections(search: String, installedOnly: Bool)")
        self.assertIn("StudioFunction.allCases", sections,
                      "the sections must be the app's own Studio list, not a list kept here")
        self.assertIn("NativeModelSection(function: function", sections,
                      "each section is built from the Studio it came from")
        # No hand-written Studio list anywhere in the derivation: a `[.image, .audio]` literal is
        # exactly the list that goes stale when a Studio is added.
        for hand_list in (".image,", ".audio,", ".transcription,", ".music,", ".chat,"):
            with self.subTest(literal=hand_list):
                self.assertNotIn(hand_list, sections,
                                 f"the section derivation hand-lists a Studio ({hand_list})")
        self.assertNotIn("rawValue ==", sections,
                         "a Studio must not be picked out of the list by its name here")

    def test_the_view_renders_the_derived_sections_and_collapses_them(self) -> None:
        listing = body(VIEWS, "private var modelsWorkspace: some View")
        self.assertIn("modelSectionsToShow", listing, "the view must render the derived sections")
        derived = body(VIEWS, "private var modelSections: [NativeModelSection]")
        self.assertIn("workspace.modelSections(search: modelSearch, installedOnly: installedOnly)",
                      derived, "the view's sections must come from the store's derivation")
        self.assertIn("ForEach(modelSectionsToShow)", listing, "one section per Studio")
        self.assertIn("modelSectionHeader(section)", listing, "each section carries its own header")
        self.assertIn("section.header", VIEWS,
                      "the header must be the Studio's own name and count")
        # A folded section still holds its models; the search opens it, so a result can never stay
        # hidden inside one the operator had collapsed.
        expanded = body(VIEWS, "private func isModelSectionExpanded(")
        self.assertIn("modelSearch", expanded,
                      "a search must open every section it matched")
        self.assertIn("collapsedModelSections", expanded, "a section is collapsible")
        self.assertIn("collapsedModelSections", VIEWS, "the fold state must exist")

    def test_the_function_tabs_replace_the_jump_picker(self) -> None:
        """The one long stack of sections is tabs now.

        The owner's report, over the 0.6.186 Models page: *"it has no tab or proper spacing. it's
        squished and also ugly."* The "Jump to" picker did not hide anything - it only scrolled -
        so every Studio's models were in one column with a heading between them. The tabs are the
        function filter, and what each one shows is the store's own derivation, never a list here.
        """
        listing = body(VIEWS, "private var modelsWorkspace: some View")
        self.assertIn("modelTabs", listing, "the Models page draws the tab bar")
        tabs = body(VIEWS, "private var modelTabs: some View")
        self.assertIn("selection: $modelFilter", tabs,
                      "the tab bar is the app's segmented picker, as the Jobs page's history is")
        self.assertIn("HubSegmentedControl(", tabs, "the tabs are a segmented control")
        self.assertIn('HubSegmentItem(tag: "All functions", label: "All \\(total)")', tabs,
                      "the first tab is All and it carries a count like every other tab")
        # A function's tab filters: that is the whole point of a tab, and it is the opposite of the
        # picker it replaced, which was pinned as "must not filter" because it sat above one list.
        shown = body(VIEWS, "private var modelSectionsToShow: [NativeModelSection]")
        self.assertIn("modelFilter ==", shown, "a function's tab shows that Studio's section only")
        self.assertIn("!section.models.isEmpty", shown,
                      "under All, a Studio with nothing in it is not a heading either")
        # Which tabs exist is read from the library, in the app's own Studio order.
        functions = body(VIEWS, "private var modelTabFunctions: [StudioFunction]")
        self.assertIn("StudioFunction.allCases", functions,
                      "the tabs are the app's own Studio list")
        self.assertIn("modelLibraryHolds", functions,
                      "which tabs exist is read from the library, not written down here")
        self.assertIn("StudioPage.createOrder", functions,
                      "the tabs keep the app's own Studio order")
        self.assertIn("StudioFunction(rawValue: modelFilter)", functions,
                      "a Studio reached from its own page keeps its tab even with nothing in it")
        holds = body(VIEWS, "private func modelLibraryHolds(")
        self.assertIn('workspace.modelSections(search: "", installedOnly: false)', holds,
                      "a tab's existence is the library's answer, not the search's")
        self.assertNotIn("jumpToModelSection", VIEWS, "the jump picker is gone, not left behind")
        # Two different nothings, said differently: a Studio whose catalogue lists no model at all
        # is a fact about the Hub, and "clear the search" would point at a control that changes
        # nothing.
        listing = body(VIEWS, "private var modelsWorkspace: some View")
        self.assertIn("has no model in this library yet", listing,
                      "a Studio with nothing in its catalogue says so, by its own name")
        self.assertIn("No models match this view", listing,
                      "a search that matched nothing still says to clear the search")

    def test_the_details_pane_is_a_two_way_split_that_remembers_its_width(self) -> None:
        """The owner: *"it should work both ways because more often than not, I want to see details
        more than the model name."*

        An `HSplitView` capped the details at 380 pt and floored the list at 380 pt, so the divider
        would only move one way. The list now has a floor of its own and no ceiling but the window's,
        the width is dragged either way, and it is remembered across launches.
        """
        listing = body(VIEWS, "private var modelsWorkspace: some View")
        self.assertNotIn("HSplitView", listing,
                         "the capped split is gone: it could not be dragged both ways")
        self.assertIn("modelSplitDivider", listing, "the divider is the view's own, so it drags")
        self.assertIn('@AppStorage("studioHub.modelsListWidth")', VIEWS,
                      "the chosen width must be remembered across launches")
        divider = body(VIEWS, "private var modelSplitDivider: some View")
        self.assertIn("DragGesture", divider, "the divider is dragged")
        self.assertIn("modelsSplitDragStart", divider, "a drag starts from where the divider is now")
        self.assertIn("modelsListMinWidth", divider, "the list has a floor of its own")
        self.assertIn("modelsListMaxWidth", divider, "and the details side keeps the rest")
        self.assertIn("static let modelsListMinWidth: Double = 360", VIEWS,
                      "the list's floor is the model name plus its status badge")
        # Nothing selected, nothing to inspect: the list takes the whole width instead of leaving a
        # panel that says only "select a model" across a third of the page.
        self.assertIn('if let model = workspace.models.first(where: { $0.repo == selectedModel })',
                      listing, "the details pane is drawn only for a selected model")
        self.assertNotIn('"Select a model to inspect its status and available actions."', listing,
                         "the empty details panel is gone, not just narrowed")

    def test_the_manage_buttons_still_name_their_own_studio(self) -> None:
        """Every generator page hands the Models tab a Studio to jump to; Video joined them."""
        manage = re.compile(r'Button\("Manage [A-Za-z]+ Models"\) \{ modelFilter = "([A-Za-z]+)"; '
                            r'studio\.select\(\.models\) \}')
        found = manage.findall(VIEWS)
        self.assertEqual(len(found), 6,
                         "every generator page still hands over its own Studio")
        self.assertEqual(found.count("Video"), 1,
                         "the Video page must jump to the Video section by the enum's own name")


class EditorDerivationTests(unittest.TestCase):
    """The editor is the lane's surface, rendered — not a form this app designed."""

    PARAMETER_NAMES = ("steps", "guidance", "seed", "aspect_ratio", "resolution",
                       "duration_seconds", "shift", "speed", "section_max_characters",
                       "top_p", "temperature", "max_tokens")

    def test_a_control_exists_for_every_settable_parameter_the_lane_publishes(self) -> None:
        controls = body(API, "static func modelDefaultControls(_ row: NativeModelDefaults)")
        self.assertIn("row.parameters", controls,
                      "the controls must come from the Studio's published parameter list")
        self.assertIn(".filter { $0.required != true }", controls,
                      "a required parameter is per-request content and must render no control")
        self.assertIn("row.defaults[parameter.name]", controls,
                      "the value in force is read by the lane's own parameter name")
        self.assertIn("row.stated[parameter.name]", controls,
                      "what the lane states is kept so Restore can be seen to return to it")
        self.assertIn("row.chosen[parameter.name]", controls,
                      "a value the operator chose is marked as chosen")

    def test_the_control_kind_is_the_published_type_and_nothing_else(self) -> None:
        controls = body(API, "static func modelDefaultControls(_ row: NativeModelDefaults)")
        for published in ('case "integer"', 'case "number"', 'case "boolean"', 'case "string"'):
            with self.subTest(kind=published):
                self.assertIn(published, controls,
                              f"the editor must map the published type {published}")
        self.assertIn("parameter.options", controls,
                      "an enum is a picker of exactly the lane's options")
        self.assertIn("default: kind = .unrenderable", controls,
                      "a shape with no rule must be shown as unrenderable, not guessed at")

    def test_no_parameter_name_and_no_bound_is_written_down_in_the_editor(self) -> None:
        editor = "\n".join([
            body(API, "static func modelDefaultControls(_ row: NativeModelDefaults)"),
            body(API, "func modelDefaultControls(for model: NativeModel)"),
            body(API, "var boundText: String {"),
        ])
        for name in self.PARAMETER_NAMES:
            with self.subTest(parameter=name):
                self.assertNotIn(f'"{name}"', editor,
                                 f"the editor hardcodes the parameter {name!r}")
        for bound in ("4294967295", "2147483648", "0.5", "2.0", "4000", "5000", "1.0"):
            with self.subTest(bound=bound):
                self.assertNotIn(bound, editor,
                                 f"the editor carries a copy of a bound ({bound})")
        # The bounds are rendered from the parameter the lane published, in the lane's own numbers.
        bounds = body(API, "static func rangeText(")
        self.assertIn("minimum", bounds)
        self.assertIn("maximum", bounds)
        self.assertIn("NativeJSONValue.trimmed", bounds,
                      "a bound must be rendered from the lane's own number")

    def test_the_editor_view_has_no_per_modality_switch_and_no_local_control_list(self) -> None:
        view = body(VIEWS, "private func modelDefaultsEditor(")
        self.assertIn("workspace.modelDefaultControls(for: model)", view,
                      "the editor must render the store's derived controls")
        self.assertIn("ForEach(controls)", view, "one row per published parameter")
        for name in self.PARAMETER_NAMES:
            with self.subTest(parameter=name):
                self.assertNotIn(f'"{name}"', view,
                                 f"the editor view names the parameter {name!r}")
        for studio in (".image", ".music", ".audio", ".chat", ".transcription"):
            with self.subTest(studio=studio):
                self.assertNotIn(f"== {studio}", view,
                                 f"the editor decides something from the Studio ({studio})")

    def test_a_parameter_the_lane_ignores_is_shown_as_ignored_and_never_sent(self) -> None:
        parameter = API[API.index("struct NativeLaneParameter"):]
        parameter = parameter[:parameter.index("\n}")]
        self.assertIn("isDeclaredIgnored", parameter,
                      "the lane's own answer that it discards a parameter must be read")
        # The machine-readable field first, the lane's own sentence as the fallback for a worker
        # that has not published the field yet.
        self.assertIn("if let ignored { return ignored }", parameter,
                      "the structured marker must outrank a prose note")
        self.assertIn('hasPrefix("accepted and ignored")', parameter,
                      "the note is still the answer where no field is published")
        self.assertIn("let ignored: Bool?", parameter,
                      "the structured marker must be decoded")
        control = API[API.index("struct NativeModelDefaultControl"):]
        control = control[:control.index("\n}")]
        self.assertIn("if ignored { return false }", control,
                      "a parameter the lane ignores must not be sent back to it")
        view = body(VIEWS, "private func modelDefaultControlRow(")
        self.assertIn("control.ignored", view,
                      "the row must say a parameter is ignored rather than look editable")
        self.assertIn("case .unrenderable", body(VIEWS, "private func modelDefaultControl("),
                      "a shape with no rule is shown, not faked as editable")

    def test_the_route_is_addressed_with_the_modality_the_lane_states(self) -> None:
        key = body(API, "func modelDefaultsKey(for model: NativeModel)")
        self.assertIn("model.laneContract?.modality", key,
                      "the modality must be the lane's own, so a new Studio needs no table here")
        for request in ("func refreshModelDefaults(", "func saveModelDefaults(",
                        "func restoreModelDefaults("):
            with self.subTest(method=request):
                text = body(API, request)
                self.assertIn('["studio", modality, "api", "model-defaults"]', text,
                              f"{request} must use the Studio proxy path")
        modalities = body(API, "var modelDefaultModalities: [String]")
        self.assertIn("for model in models", modalities,
                      "the Studios to read are derived from the models themselves")
        self.assertIn("model.laneContract?.modality", modalities,
                      "each Studio comes from the lane contract the model published")

    def test_the_lane_contract_is_decoded_where_the_producers_publish_it(self) -> None:
        catalog = body(API, "private struct CatalogModel: Decodable")
        self.assertIn("let laneContract: NativeLaneContract?", catalog,
                      "the catalogue row's lane_contract is not decoded")
        transcription = body(API, "private struct TranscriptionModel: Decodable")
        self.assertIn("let laneContract: NativeLaneContract?", transcription,
                      "the availability row's lane_contract is not decoded")
        model = body(API, "struct NativeModel: Identifiable, Equatable")
        self.assertIn("var laneContract: NativeLaneContract?", model,
                      "the model must carry the contract to the page")
        # A limit is only ever rendered from the row's own `model_limits` object, which is where
        # the producer puts the source. The bare `context_window_tokens` beside it carries a number
        # with no provenance, and the worker's fallback must never be shown as the model's fact.
        self.assertIn("let modelLimits: [String: NativeModelLimit]?", catalog,
                      "the catalogue row's model_limits is not decoded")
        self.assertIn("var modelLimits: [String: NativeModelLimit]?", model,
                      "the model must carry the row's limits to the page")
        facts = API[API.index("var facts: [NativeModelFact] {"):]
        facts = facts[:facts.index("\n    }")]
        self.assertIn("Self.limitFacts(modelLimits ?? contract.modelLimits ?? [:])", facts,
                      "the limits must come from the row's model_limits, with the contract's own "
                      "object only as the fallback")
        self.assertIn('label: "Label", value: label, source: "label"', facts,
                      "the published label is shown verbatim, under its own honest heading")
        self.assertNotIn('label: "Quantization"', API,
                         "nothing may present a label as a quantization the row never stated")
        self.assertNotIn("quantization(in:", API,
                         "nothing may parse a quantization out of a repo or label string")
        self.assertIn("laneContract: item.laneContract", API,
                      "every builder must thread the row's own contract onto the model")
        # The contract's own fields, read from the spec's shape rather than invented here.
        contract = body(API, "struct NativeLaneContract: Decodable, Equatable")
        for field in ("modality", "operation", "parameters", "modelLimits", "artifact"):
            with self.subTest(field=field):
                self.assertIn(f"let {field}", contract, f"the contract's {field} is not decoded")

    def test_the_parameter_shape_matches_what_the_shared_contract_publishes(self) -> None:
        """The bounds the app renders are the bounds `components/model_defaults.py` validates."""
        for published in ('"minimum"', '"maximum"', '"enum"', '"max_characters"', '"required"',
                          '"default"'):
            with self.subTest(key=published):
                self.assertIn(published, CONTRACT,
                              f"the shared module no longer reads {published}")
        parameter = body(API, "struct NativeLaneParameter: Decodable, Equatable, Identifiable")
        for field in ("minimum", "maximum", "maxCharacters", "maxBytes", "options",
                      "defaultValue", "required", "boundSource"):
            with self.subTest(field=field):
                self.assertIn(f"let {field}", parameter, f"the parameter's {field} is not decoded")
        # `minimum_exclusive` is real: the Chat lane refuses the bound itself for `top_p`.
        self.assertIn("minimumExclusive", parameter,
                      "an exclusive bound must be decoded or the editor offers a refused value")


class RefusalAndRestoreTests(unittest.TestCase):
    def test_a_422_carries_each_reason_against_its_own_parameter(self) -> None:
        self.assertIn("case refusals([String: String])", API,
                      "a per-parameter refusal must survive as one, not as a sentence")
        parsing = body(API, "private static func refusalDetail(")
        self.assertIn('object["detail"] as? [String: Any]', parsing,
                      "the reasons are keyed by parameter in the payload's own detail object")
        self.assertIn("throw NativeAPIError.refusals(reasons)", API,
                      "a 422 with parameter reasons must be thrown as such")
        save = body(API, "func saveModelDefaults(")
        self.assertIn("catch NativeAPIError.refusals(let reasons)", save,
                      "the save must catch the per-parameter refusal")
        self.assertIn("modelDefaultRefusals[key] = reasons", save,
                      "each reason must be kept against the model it belongs to")
        self.assertNotIn("self.error =", save.split("catch NativeAPIError.refusals")[1].split("} catch")[0],
                         "a per-parameter refusal must not become one generic banner")
        view = body(VIEWS, "private func modelDefaultControlRow(")
        self.assertIn("refusals[control.name]", view,
                      "each reason must be rendered under the field it belongs to")

    def test_a_stored_value_the_lane_refuses_is_shown_with_its_reason(self) -> None:
        not_used = body(API, "func modelDefaultsNotUsed(for model: NativeModel)")
        self.assertIn("row.notUsed", not_used, "the Studio's not_used map must be read")
        self.assertIn("sorted", not_used, "the lines must render in a stable order")
        view = body(VIEWS, "private func modelDefaultsEditor(")
        self.assertIn("notUsed", view, "the editor must render the values the lane refuses")
        self.assertIn("no longer accepts", view, "the block must say what it is")
        for phrase in ("They are kept in the file and ignored until you change them",
                       "nothing sends them"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, view,
                              "a refused stored value must not be silently dropped")

    def test_restore_issues_the_delete_and_returns_to_what_the_lane_states(self) -> None:
        restore = body(API, "func restoreModelDefaults(")
        self.assertIn('method: "DELETE"', restore, "Restore must use the DELETE the route defines")
        self.assertIn("ModelDefaultsRepoBody(repo: model.repo)", restore,
                      "the DELETE body names the one model it forgets")
        self.assertIn("mergeModelDefaults(payload", restore,
                      "the Studio's own answer must replace the editor's state")
        self.assertIn("modelDefaultRefusals[key] = nil", restore,
                      "Restore must clear the refusals it replaced")
        view = body(VIEWS, "private func modelDefaultsEditor(")
        self.assertIn('Button("Restore This Model\'s Defaults")', view,
                      "the per-model restore must exist on the pane")


class SeedFromCatalogueTests(unittest.TestCase):
    """The generator page's starting value is the catalogue's own resolved answer.

    The Image and Music workers publish `generation_defaults` **resolved**: the operator's saved
    default over the model's own, which is the value a job that omits the parameter is given. So
    the page seeds from the catalogue — the one read path a job's parameters can come from — and
    `/api/model-defaults` stays the editor's question (what is settable, stated, chosen, refused).
    A second seeding path from the defaults route would be a value the worker might not apply.
    """

    def test_the_image_page_seeds_from_the_catalogues_resolved_configuration(self) -> None:
        model = API[API.index("@Published var imageModel"):][:900]
        self.assertIn("imageConfigurations[imageModel]?.defaults", model,
                      "the Image page must seed from the catalogue's own configuration")
        self.assertNotIn("savedDefault(", model,
                         "the defaults route is the editor's read, not the page's seed")
        restore = body(API, "func restoreImageDefaults()")
        self.assertIn("imageConfiguration?.defaults", restore,
                      "Restore means what the worker will actually run with")
        self.assertIn("musicConfiguration?.defaults", body(API, "func restoreMusicDefaults()"))
        for gone in ("func seededImageSettings(", "func seededMusicSettings(",
                     "func seededAudioSpeed(", "func reseedGeneratorControls()"):
            with self.subTest(helper=gone):
                self.assertNotIn(gone, API,
                                 f"{gone} is a second seeding path from the defaults route")

    def test_a_changed_published_default_is_adopted_on_the_next_refresh(self) -> None:
        adopt = body(API, "private func adoptResolvedDefaults()")
        restore = API[API.index("func refresh(reportErrors: Bool) async {"):]
        restore = restore[:restore.index("func refreshImageLoadingMode")]
        self.assertIn("adoptResolvedDefaults()", restore,
                      "a catalogue refresh must adopt a resolved default that changed")
        # ...and only when the page is still holding the value it was seeded with: a poll must not
        # overwrite what the operator typed.
        self.assertIn("imageSettings == nil || imageSettings == previous", adopt,
                      "the page must adopt the new value only while it still holds the old one")
        self.assertIn("musicSettings == nil || musicSettings == previous", adopt)
        self.assertIn("previous != published", adopt,
                      "only a *change* is adopted, not every read of the same value")

    def test_saving_a_default_refreshes_so_the_page_can_pick_it_up(self) -> None:
        for method in ("func saveModelDefaults(", "func restoreModelDefaults("):
            with self.subTest(method=method):
                self.assertIn("await refresh()", body(API, method),
                              f"{method} must refresh the catalogue, which is what the page seeds from")

    def test_every_generator_page_asks_for_its_models_defaults(self) -> None:
        for page in ("chatWorkspace", "imageWorkspace", "musicWorkspace", "audioWorkspace",
                     "transcriptionWorkspace"):
            with self.subTest(page=page):
                text = body(VIEWS, f"private var {page}: some View")
                self.assertIn("loadModelDefaultsIfNeeded(for:", text,
                              f"{page} must read its Studio's published defaults")

    def test_the_editing_page_reads_every_studio_once_per_connection(self) -> None:
        load = body(API, "func loadModelDefaultsIfNeeded() async")
        self.assertIn("modelDefaultModalities", load)
        self.assertIn("readModelDefaults(modality:", load)
        read = body(API, "private func readModelDefaults(modality: String) async")
        self.assertIn("modelDefaultsReadModalities.contains(modality)", read,
                      "a page asking twice must not fetch twice")
        listing = body(VIEWS, "private var modelsWorkspace: some View")
        self.assertIn(".task(id: modelDefaultsLoadKey)", listing,
                      "the Models tab must read the defaults when it appears")
        self.assertIn("workspace.loadModelDefaultsIfNeeded()", listing)

    def test_a_reconnect_forgets_the_last_hubs_defaults(self) -> None:
        disconnect = body(API, "func disconnect()")
        for cleared in ("modelDefaults = [:]", "modelDefaultRefusals = [:]",
                        "modelDefaultsReadModalities = []", "publishedImageDefaults = [:]",
                        "publishedMusicDefaults = [:]"):
            with self.subTest(cleared=cleared):
                self.assertIn(cleared, disconnect,
                              "a default belongs to one worker on one Mac; a reconnect must forget it")


class LadderTests(unittest.TestCase):
    """Music publishes suggested durations; they are suggestions, and the sectioned rung is shown."""

    def test_the_ladder_is_decoded_off_the_parameter(self) -> None:
        parameter = API[API.index("struct NativeLaneParameter"):]
        parameter = parameter[:parameter.index("\n}")]
        self.assertIn("let ladder: [NativeLadderRung]?", parameter,
                      "the ladder the lane publishes under a parameter is not decoded")
        rung = body(API, "struct NativeLadderRung: Decodable, Equatable, Identifiable")
        for field in ("seconds", "label", "exceedsSinglePass"):
            with self.subTest(field=field):
                self.assertIn(f"let {field}", rung, f"a ladder rung's {field} is not decoded")

    def test_the_control_carries_the_ladder_and_keeps_the_published_bound(self) -> None:
        control = API[API.index("struct NativeModelDefaultControl"):]
        control = control[:control.index("\n}")]
        self.assertIn("let ladder: [NativeLadderRung]?", control,
                      "the editor control must carry the lane's suggestions")
        self.assertIn("ladder: parameter.ladder", API,
                      "the suggestions must come from the parameter the lane published")
        # The rungs never become the accepted set: the bound is still the model's own range.
        self.assertIn("parameter.minimum", API)
        self.assertIn("parameter.maximum", API)

    def test_the_facts_render_the_ladder_with_the_sectioned_rung_marked(self) -> None:
        facts = API[API.index("var facts: [NativeModelFact] {"):]
        facts = facts[:facts.index("\n    }")]
        self.assertIn("parameter.ladder", facts, "the ladder must be published as a fact")
        self.assertIn("(renders in sections)", facts,
                      "a rung beyond one pass must say so rather than be hidden")
        self.assertIn("rung.label", facts,
                      "the rung's own label must be used, not one this app words")

    def test_the_view_offers_the_rungs_as_suggestions_not_as_a_closed_set(self) -> None:
        row = body(VIEWS, "private func modelLadderRow(")
        self.assertIn("ForEach(ladder)", row, "one control per published rung")
        self.assertIn("rung.label", row, "the rung's own label")
        self.assertIn("rung.exceedsSinglePass", row, "the flag must reach the screen")
        self.assertIn("setDefaultNumber", row, "a rung fills the field rather than replacing it")
        self.assertIn("anything inside this range is accepted", row,
                      "the field must say the rungs are not the only accepted lengths")
        self.assertIn("modelLadderRow(control, ladder: ladder)",
                      body(VIEWS, "private func modelDefaultControlRow("),
                      "the suggestions belong on the control's own row")
        self.assertNotIn("Picker(", row,
                         "a ladder is not a picker: any value inside the bound is accepted")


class FixtureCoverageTests(unittest.TestCase):
    def test_the_fixture_publishes_a_lane_contract_and_a_narrow_bound(self) -> None:
        self.assertIn('"lane_contract"', NATIVE, "the fixture must publish lane contracts")
        self.assertIn('"fixture/narrow-image"', NATIVE,
                      "the fixture must carry a model with a narrow bound and an enum")
        self.assertIn('"minimum":3,"maximum":5', NATIVE,
                      "the narrow bound must be narrow enough to catch a copied default")
        self.assertIn('"enum":["4:3","3:2"]', NATIVE, "the fixture must publish an enum")

    def test_the_fixture_serves_the_defaults_route_like_the_studios_do(self) -> None:
        for route in ('"/api/model-defaults"', 'modelDefaultsPayload(modality:',
                      '"settings_file": "model_defaults.json"'):
            with self.subTest(route=route):
                self.assertIn(route, NATIVE, f"the fixture does not serve {route}")
        self.assertIn('"detail": refusals', NATIVE,
                      "the fixture's PUT must answer 422 with a reason per parameter")
        self.assertIn('"not_used": notUsed', NATIVE,
                      "the fixture must publish values the lane no longer accepts")

    def test_the_behaviour_checks_cover_each_claim(self) -> None:
        for claim in (
            "one section per Studio, in the Studio list's own order",
            "the editor's controls must be the Studio's settable parameters in its own order",
            "a required parameter is per-request content and must render no control",
            "the refused steps value must carry the lane's own reason",
            "a stored value the lane refuses must be shown with its reason",
            "a parameter with no control must still be in the values in force",
            "the Image page must pick up the resolved default on the refresh after a save",
            "a refresh must not overwrite a value the operator typed",
            "the published ladder must be shown in the lane's own labels",
            "Restore must issue the DELETE for this model's Studio",
            "Restore must return the control to what the lane states",
        ):
            with self.subTest(claim=claim):
                self.assertIn(claim, NATIVE, f"no behaviour check for: {claim}")


if __name__ == "__main__":
    unittest.main()
