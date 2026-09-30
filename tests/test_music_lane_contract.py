"""The Music lane's published dispatch contract, and the evidence behind its numbers.

Gateway Studio asked for this by name: *"Publish each lane's execution contract … what a lane needs
stated, per model: the operation name it should be asked for, the accepted parameters with types, the
artifact form, the terminal vocabulary including which state means 'uncertain' and whether the lane
supports discard, and cancel semantics. With that, a new lane is a data change on their side and no
gateway release at all."*

The shape is specified in `docs/lane-execution-contract.md`. These tests assert the properties that
make it worth publishing rather than the membership of today's list:

* every model in the catalog publishes a contract, built from **its own** limits;
* the parameters offered are the ones this model's validator actually accepts — a lane that
  discards a control must not advertise it;
* `uncertain` is published as explicitly not terminal, which is the one thing a scheduler gets
  wrong by default;
* a published runtime traces back to a stored measurement, so the number can be checked rather
  than believed;
* the duration ladder a control can be rendered from is the model's own, so no rung it would
  refuse is ever offered.
"""
from __future__ import annotations

from dataclasses import replace

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "qualification" / "evidence"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.hub.backend.capabilities import OPERATION_BY_MODALITY  # noqa: E402
from components.model_defaults import validate as validate_defaults  # noqa: E402
from components.music.app.backend import catalog, generation  # noqa: E402


class TheContractCoversEveryModelTests(unittest.TestCase):
    def test_the_contract_tables_cover_the_same_runtimes_as_the_validators(self) -> None:
        """A runtime added to one table and not the other must fail here, not in production."""
        self.assertEqual(
            set(generation._CONTRACT_EXTRAS), set(generation._VALIDATORS),
            "`_CONTRACT_EXTRAS` and `_VALIDATORS` in components/music/app/backend/generation.py "
            "disagree about which runtimes exist, so a model would publish a contract that "
            "silently omits half of what its validator accepts")

    def test_every_catalog_model_publishes_a_contract_from_its_own_limits(self) -> None:
        self.assertTrue(catalog.CATALOG, "the music catalog is empty")
        for model in catalog.CATALOG:
            with self.subTest(repo=model.repo):
                contract = generation.lane_contract(model)
                self.assertEqual(contract["schema"], "studiohub.lane-contract.v1")
                self.assertEqual(contract["modality"], "music")
                parameters = {item["name"]: item for item in contract["parameters"]}
                self.assertEqual(
                    parameters["prompt"]["max_characters"],
                    generation.PROMPT_MAX_CHARACTERS)
                # The bounds are this model's, read through the same helpers the validator uses.
                self.assertEqual(parameters["duration_seconds"]["minimum"],
                                 model.limits["min_duration_seconds"])
                self.assertEqual(parameters["duration_seconds"]["maximum"],
                                 model.limits["max_duration_seconds"])
                self.assertEqual(parameters["duration_seconds"]["default"],
                                 model.defaults["duration_seconds"])
                self.assertEqual(parameters["steps"]["minimum"], model.limits["min_steps"])
                self.assertEqual(parameters["steps"]["maximum"], model.limits["max_steps"])
                self.assertEqual(parameters["steps"]["default"], model.defaults["steps"])
                self.assertEqual(parameters["guidance_scale"]["minimum"],
                                 model.limits["min_guidance"])
                self.assertEqual(parameters["guidance_scale"]["maximum"],
                                 model.limits["max_guidance"])
                self.assertEqual(parameters["seed"]["maximum"], model.limits["max_seed"])
                # And the runtime-specific half is the runtime's own table — both directions.
                #
                # This used to intersect the published names with a hard-coded
                # `{"shift", "instrumental", "lyrics", "vocal_language"}` and compare that to the
                # table. The list was the defect: a fourth engine (YuE2) publishes `mode` and
                # `precision` instead, and the assertion failed on the *new* engine's honest
                # parameters while still passing if a declared extra were silently dropped. The
                # invariant is the pair of set comparisons, and it holds for any engine.
                declared = {item["name"] for item in generation._CONTRACT_EXTRAS[model.runtime]}
                published = {item["name"] for item in contract["parameters"]}
                self.assertTrue(
                    declared <= published,
                    f"{model.runtime} declares {sorted(declared - published)} but publishes none "
                    "of them; a parameter the lane reads must be offered")
                shared = {"prompt", "duration_seconds", "steps", "guidance_scale", "seed"}
                self.assertEqual(
                    published - shared, declared,
                    f"{model.runtime} publishes {sorted((published - shared) - declared)} that its "
                    "own table does not declare")

    def test_a_lane_that_discards_a_control_does_not_offer_it(self) -> None:
        """Stable Audio 3 forces instrumental output; offering the controls would be a lie."""
        stable = next(model for model in catalog.CATALOG
                      if model.runtime == catalog.RUNTIME_STABLE_AUDIO)
        offered = {item["name"] for item in generation.lane_contract(stable)["parameters"]}
        for discarded in ("instrumental", "lyrics", "vocal_language", "shift"):
            self.assertNotIn(
                discarded, offered,
                f"{stable.repo} forces {discarded!r} whatever is sent, so a scheduler that sends "
                f"it would have it silently discarded")

    def test_the_operation_is_the_token_the_hub_publishes(self) -> None:
        """The worker's own label differs; the contract carries the canonical token a reader joins on."""
        contract = generation.lane_contract(catalog.CATALOG[0])
        self.assertEqual(contract["operation"], OPERATION_BY_MODALITY["music"])
        self.assertEqual(contract["operation"], "music.generation")

    def test_uncertain_is_published_as_not_terminal(self) -> None:
        contract = generation.lane_contract(catalog.CATALOG[0])
        states = contract["states"]
        self.assertIn(states["uncertain"], states["active"] + ["uncertain"])
        self.assertNotIn(states["uncertain"], states["terminal"])
        self.assertFalse(states["uncertain_is_terminal"])
        self.assertTrue(states["discard_supported"])
        self.assertTrue(contract["cancel"]["semantics"])


class ThePublishedNumbersTraceToEvidenceTests(unittest.TestCase):
    """A runtime a scheduler sets a deadline from must be checkable, not merely asserted."""

    def test_every_published_runtime_is_labelled_measured_with_a_host(self) -> None:
        for model in catalog.CATALOG:
            contract = generation.lane_contract(model)
            with self.subTest(repo=model.repo):
                published = contract.get("runtime_seconds")
                if published is None:
                    self.assertIsNone(
                        model.measured_runtime,
                        f"{model.repo} has measurements but publishes none")
                    continue
                self.assertEqual(published["evidence"], "measured")
                self.assertTrue(published.get("measured_on"))
                for row in published["measured"]:
                    self.assertGreater(row["seconds"], 0)
                    self.assertGreater(row["duration_seconds"], 0)

    def test_stable_audio_runtimes_match_the_stored_proof(self) -> None:
        stable = next(model for model in catalog.CATALOG
                      if model.runtime == catalog.RUNTIME_STABLE_AUDIO)
        published = {row["duration_seconds"]: row["seconds"]
                     for row in generation.lane_contract(stable)["runtime_seconds"]["measured"]}
        proof = json.loads((EVIDENCE / "sa3-2026-09-22" / "proof.json").read_text())
        for run in proof["runs"]:
            with self.subTest(seconds=run["seconds"]):
                self.assertEqual(published[float(run["seconds"])], run["wall_s"])

    def test_an_unmeasured_model_publishes_nothing_rather_than_an_estimate(self) -> None:
        """The rule, stated where it can be broken: absence is the honest answer."""
        for model in catalog.CATALOG:
            contract = generation.lane_contract(model)
            if model.measured_runtime is None:
                self.assertNotIn("runtime_seconds", contract,
                                 f"{model.repo} has no measurement, so it must publish no runtime")


class TheDurationLadderIsTheModelsOwnTests(unittest.TestCase):
    """A ladder is a published control, so every rung has to be one the model accepts.

    The failure this prevents is a fixed list of rungs in a consumer: 300 s offered to a model
    capped at 60 s, or a 600 s rung offered to Stable Audio 3, which stops at 380 s. Each model's
    ladder is therefore read back against **its own** published bounds.
    """

    @staticmethod
    def duration_parameter(model: catalog.ModelEntry) -> dict:
        return {parameter["name"]: parameter
                for parameter in generation.lane_parameters(model)}["duration_seconds"]

    def test_every_rung_is_inside_the_models_own_published_bounds(self) -> None:
        for model in catalog.CATALOG:
            duration = self.duration_parameter(model)
            with self.subTest(repo=model.repo):
                ladder = duration["ladder"]
                self.assertTrue(ladder, "a model with no rung offers nothing to choose")
                for rung in ladder:
                    self.assertGreaterEqual(rung["seconds"], duration["minimum"])
                    self.assertLessEqual(rung["seconds"], duration["maximum"],
                                         "a rung the model would refuse must never be offered")
                    self.assertEqual(rung["label"], f"{rung['seconds']} s")
                    self.assertIsInstance(rung["exceeds_single_pass"], bool)

    def test_the_models_own_anchors_are_rungs(self) -> None:
        """The shortest, the default and the longest this model states are all reachable."""
        for model in catalog.CATALOG:
            duration = self.duration_parameter(model)
            rungs = {rung["seconds"] for rung in duration["ladder"]}
            with self.subTest(repo=model.repo):
                for anchor in (duration["minimum"], duration["default"], duration["maximum"]):
                    self.assertIn(anchor, rungs)

    def test_only_rungs_beyond_the_single_pass_maximum_are_marked_as_such(self) -> None:
        for model in catalog.CATALOG:
            single_pass = model.limits["single_pass_max_seconds"]
            ladder = self.duration_parameter(model)["ladder"]
            with self.subTest(repo=model.repo):
                self.assertIn(single_pass, {rung["seconds"] for rung in ladder},
                              "the single-pass maximum is what a long-form request turns on")
                for rung in ladder:
                    self.assertEqual(rung["exceeds_single_pass"],
                                     rung["seconds"] > single_pass)
                    if rung["exceeds_single_pass"]:
                        self.assertIn("sections", rung["note"])

    def test_the_ladder_follows_the_models_limits_rather_than_a_constant_list(self) -> None:
        """The control: tighten a model's own limits and the ladder moves with them."""
        model = catalog.CATALOG[0]
        tightened = replace(model, limits={
            **model.limits, "min_duration_seconds": 5, "max_duration_seconds": 45,
            "single_pass_max_seconds": 40})
        ladder = {rung["seconds"]: rung
                  for rung in self.duration_parameter(tightened)["ladder"]}
        self.assertEqual(min(ladder), 5)
        self.assertEqual(max(ladder), 45)
        self.assertEqual({seconds for seconds in ladder if ladder[seconds]
                          ["exceeds_single_pass"]}, {45})
        self.assertNotEqual(set(ladder),
                            {rung["seconds"]
                             for rung in self.duration_parameter(model)["ladder"]},
                            "a constant ladder would have published rungs this model refuses")

    def test_the_ladder_is_what_the_contract_publishes(self) -> None:
        for model in catalog.CATALOG:
            contract = {parameter["name"]: parameter
                        for parameter in generation.lane_contract(model)["parameters"]}
            with self.subTest(repo=model.repo):
                self.assertEqual(contract["duration_seconds"]["ladder"],
                                 generation.duration_ladder(model))


class EverySettableParameterPublishesADefaultTests(unittest.TestCase):
    """What the Models tab renders a control from: a value in force for every settable parameter.

    ``model_defaults`` can only resolve a parameter whose ``default`` the lane publishes, so a
    settable parameter with no default would silently have no value in force — and
    ``validate_request`` would fall through to a second source for it. A ``required`` parameter is
    the one exception, and deliberately so: it is per-request content, not a default.
    """

    def test_every_published_parameter_is_either_required_or_defaultable(self) -> None:
        for model in catalog.CATALOG:
            with self.subTest(repo=model.repo):
                published = generation.lane_parameters(model)
                self.assertTrue(published)
                for parameter in published:
                    if parameter.get("required"):
                        self.assertNotIn("default", parameter)
                        continue
                    self.assertIn("default", parameter, parameter["name"])
                    self.assertEqual(parameter["default"],
                                     model.defaults[parameter["name"]])

    def test_the_published_surface_is_exactly_what_may_be_defaulted(self) -> None:
        """One list, two readers: the contract offers it and ``model_defaults`` accepts it.

        The values sent are the parameters' own published defaults, so this also proves no model
        publishes a default its own bound would refuse.
        """
        samples = {"shift": 3.0, "instrumental": True, "lyrics": "la", "vocal_language": "en"}
        for model in catalog.CATALOG:
            parameters = generation.lane_parameters(model)
            settable = {parameter["name"]: parameter["default"]
                        for parameter in parameters if not parameter.get("required")}
            with self.subTest(repo=model.repo):
                self.assertTrue(settable)
                accepted, refusals = validate_defaults(parameters, settable)
                self.assertEqual(refusals, {})
                self.assertEqual(set(accepted), set(settable))
                # Control: a control this runtime does not publish has nothing to default.
                for name in sorted(set(samples) - set(settable)):
                    _accepted, refused = validate_defaults(parameters, {name: samples[name]})
                    self.assertIn(name, refused)
                    self.assertIn("publishes no such settable parameter", refused[name])


if __name__ == "__main__":
    unittest.main()
