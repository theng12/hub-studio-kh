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
  than believed.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "qualification" / "evidence"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.hub.backend.capabilities import OPERATION_BY_MODALITY  # noqa: E402
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
                # And the runtime-specific half is the runtime's own table.
                extras = {item["name"] for item in generation._CONTRACT_EXTRAS[model.runtime]}
                self.assertEqual(
                    {name for name in parameters if name in
                     {"shift", "instrumental", "lyrics", "vocal_language"}}, extras)

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

    def test_ace_step_runtimes_match_the_stored_probe_results(self) -> None:
        ace = next(model for model in catalog.CATALOG
                   if model.runtime == catalog.RUNTIME_ACE_STEP)
        published = {row["duration_seconds"]: row["seconds"]
                     for row in generation.lane_contract(ace)["runtime_seconds"]["measured"]}
        for name in ("ace-step-turbo", "ace-step-turbo-60s", "ace-step-turbo-6min",
                     "ace-step-turbo-vocal"):
            record = json.loads((EVIDENCE / "r07-2026-09-21" / f"{name}.json").read_text())
            with self.subTest(probe=name):
                seconds = float(record["params"]["duration_seconds"])
                self.assertEqual(
                    published[seconds], record["generate_seconds"],
                    f"the contract's runtime for {name} does not match the stored probe result")

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


if __name__ == "__main__":
    unittest.main()
