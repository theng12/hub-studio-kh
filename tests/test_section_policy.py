"""Every local voice model must say how it is sectioned, and how it gets its speed.

The gateway asked for a 15,000-character ceiling and reported back the ceiling *it* could see:
orpheus stopped at 170 characters, because that is the engine's own input limit and Voice Studio
was still handing it whole scripts. The cause was structural rather than a wrong number - seven
families (orpheus, bark, voxtral-tts, marvis, kittentts, spark-tts-mlx, f5-tts) had no long-form
policy at all, and `_runtime_default` returning None meant "send the whole script in one call".

So these tests are about the two properties that make the failure impossible to repeat:
  1. every family has a section budget, and a family with an engine cliff can never exceed it;
  2. every family has a stated way to honour the speed control, and the dispatcher obeys it.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.voice.app.backend import catalog, long_form_policy  # noqa: E402

GENERATION = (ROOT / "components/voice/app/backend/generation.py").read_text()
FAMILIES = sorted({entry.family for entry in catalog.CATALOG})
# Families whose engine stops working past a fixed input length. These are not quality budgets.
ENGINE_CLIFFS = {"orpheus": 170, "bark": 150}


class SectionBudgetTests(unittest.TestCase):
    def test_every_family_has_a_section_budget(self) -> None:
        """No family may fall back to "one request for the whole script"."""
        for family in FAMILIES:
            policy = long_form_policy.policy_for(family, f"{family}/repo")
            self.assertIsNotNone(policy, f"{family} has no long-form policy")
            self.assertGreater(policy["section_max_characters"], 0, family)

    def test_an_unknown_family_gets_the_conservative_default(self) -> None:
        """A newly adopted family must inherit sectioning rather than skip it."""
        policy = long_form_policy.policy_for("brand-new-family", "org/repo")
        self.assertEqual(policy["section_max_characters"],
                         long_form_policy.CONSERVATIVE_DEFAULT_SECTION_MAX_CHARACTERS)
        self.assertEqual(policy["source"], "conservative_default")
        self.assertEqual(policy["origin"], "conservative_default")

    def test_a_family_with_its_own_evidence_keeps_its_own_number(self) -> None:
        self.assertEqual(long_form_policy.policy_for("omnivoice", "x")["section_max_characters"], 288)
        self.assertEqual(long_form_policy.policy_for("chatterbox-mlx", "x/turbo")["section_max_characters"], 400)

    def test_engine_cliffs_are_the_section_budget_and_are_published_as_a_cap(self) -> None:
        for family, limit in ENGINE_CLIFFS.items():
            policy = long_form_policy.policy_for(family, f"{family}/repo")
            self.assertEqual(policy["section_max_characters"], limit, family)
            self.assertEqual(policy["hard_cap_characters"], limit, family)

    def test_an_audit_cannot_raise_a_family_past_its_engine_cliff(self) -> None:
        """A model audit may pick a finer section. It may never send an engine past its own limit."""
        raised = long_form_policy.policy_for(
            "orpheus", "mlx-community/orpheus-3b-0.1-ft-4bit",
            audited_section_max_characters=900)
        self.assertEqual(raised["section_max_characters"], 170)
        finer = long_form_policy.policy_for(
            "orpheus", "mlx-community/orpheus-3b-0.1-ft-4bit",
            audited_section_max_characters=120)
        self.assertEqual(finer["section_max_characters"], 120)
        self.assertEqual(finer["source"], "model_audit")

    def test_the_runtime_actually_uses_the_published_budget(self) -> None:
        """The number the catalogue shows is the number the sectioner is given."""
        from components.voice.app.backend import generation
        text = "One sentence here. " * 60
        for family, repo in (("orpheus", "mlx-community/orpheus-3b-0.1-ft-4bit"),
                             ("bark", "mlx-community/bark"),
                             ("voxtral-tts", "mlx-community/Voxtral-4B-TTS-2603-mlx-4bit")):
            budget = long_form_policy.policy_for(family, repo)["section_max_characters"]
            sections = generation._internal_mlx_text_chunks(family, repo, text)
            self.assertGreater(len(sections), 1, f"{family} was sent as one call")
            self.assertTrue(all(len(section) <= budget for section in sections),
                            f"{family} produced a section past {budget}")


class SpeedDeliveryTests(unittest.TestCase):
    def test_every_family_states_how_speed_is_delivered(self) -> None:
        for family in FAMILIES:
            delivery = long_form_policy.speed_policy_for(family).delivery
            self.assertIn(delivery, {"native", "tempo"}, family)

    def test_only_the_engines_that_apply_a_multiplier_get_it_natively(self) -> None:
        """Audited against the installed mlx-audio source: these predict their own durations."""
        self.assertEqual(sorted(long_form_policy.NATIVE_SPEED_FAMILIES), ["kittentts", "kokoro-mlx"])
        self.assertEqual(long_form_policy.speed_policy_for("kokoro-mlx").delivery, "native")
        self.assertEqual(long_form_policy.speed_policy_for("kittentts").delivery, "native")

    def test_the_families_that_ignore_or_misread_speed_get_the_tempo_pass(self) -> None:
        # Chatterbox documents speed as "Ignored"; LongCat as "Unused"; Bark still has the original
        # "# TODO: adjust speed"; Spark indexes SPEED_MAP[float] and raises on anything but its five
        # levels; Orpheus, Marvis, Voxtral, OmniVoice and Fish never apply the multiplier themselves.
        for family in ("chatterbox-mlx", "longcat-audiodit", "bark", "spark-tts-mlx", "orpheus",
                       "marvis", "voxtral-tts", "omnivoice", "fish-audio-mlx", "voxcpm-mlx"):
            self.assertEqual(long_form_policy.speed_policy_for(family).delivery, "tempo", family)

    def test_a_tempo_family_is_never_handed_a_speed_argument(self) -> None:
        """The dispatcher decides from the policy, so a new family cannot be forgotten."""
        self.assertIn('if _speed_delivery(family) == "native":\n            gen_kwargs["speed"] = speed',
                      GENERATION)
        self.assertEqual(GENERATION.count('gen_kwargs["speed"] = speed'), 1)

    def test_the_tempo_pass_runs_for_every_family_that_needs_it(self) -> None:
        """Both places that can apply the pass ask the policy, not a hand-kept list."""
        self.assertIn('if model is not None and _speed_delivery(model.family) == "tempo":', GENERATION)
        self.assertIn('and _speed_delivery(family) == "tempo"):', GENERATION)
        self.assertIn("long_form_policy.SPEED_MINIMUM", GENERATION)
        self.assertNotIn("not configured for", GENERATION,
                         "a family missing from a label table must not fail the job")

    def test_a_tempo_pass_says_so_in_the_log(self) -> None:
        """A working speed control that logs nothing is indistinguishable from a broken one."""
        parent = GENERATION[GENERATION.index("def _apply_parent_output_speed("):]
        parent = parent[:parent.index("def _dispatch_txt2speech_direct(")]
        self.assertIn("applied pitch-preserving", parent)

    def test_the_policy_is_published_with_the_section_control(self) -> None:
        row = catalog.serialize_model(catalog.get_model("mlx-community/chatterbox-4bit"))
        published = row["long_form_delivery"]
        self.assertEqual(published["speed"]["delivery"], "tempo")
        self.assertEqual(published["speed"]["minimum"], long_form_policy.SPEED_MINIMUM)
        self.assertEqual(published["speed"]["maximum"], long_form_policy.SPEED_MAXIMUM)
        self.assertEqual(published["section_size_control"]["runtime_default"],
                         published["section_max_characters"])
        self.assertEqual(published["section_size_control"]["maximum"], 3000)


class SectionSizeControlTests(unittest.TestCase):
    def test_every_sectioned_model_offers_a_viewable_and_adjustable_section_size(self) -> None:
        """Only Qwen 1.7B Base had a control, so every other family's chunking was invisible."""
        for entry in catalog.CATALOG:
            if long_form_policy.policy_for(entry.family, entry.repo) is None:
                continue
            control = catalog.section_size_control_for(entry.repo)
            self.assertIsNotNone(control, f"{entry.repo} publishes no section-size control")
            # Compared against what the catalogue publishes, audit included: a repo whose exact
            # audit tightens the family budget must show the tightened number in both places.
            published = catalog.serialize_model(entry)["long_form_delivery"]
            self.assertEqual(control["runtime_default"], published["section_max_characters"],
                             f"{entry.repo} shows one section size and renders another")

    def test_a_caller_can_tighten_a_section_but_not_leave_the_published_range(self) -> None:
        # The engine-cliff families (orpheus, bark) were cut on 2026-09-24; the kept families
        # bound the control by the published minimum/maximum instead.
        repo = "mlx-community/chatterbox-4bit"
        self.assertEqual(catalog.resolve_section_budget("chatterbox-mlx", repo, 120)["section_max_characters"], 120)
        with self.assertRaises(catalog.SectionSizeControlError) as caught:
            catalog.resolve_section_budget("chatterbox-mlx", repo, 10)
        self.assertEqual(caught.exception.code, "SECTION_MAX_CHARACTERS_OUT_OF_RANGE")

    def test_an_undeclared_section_size_uses_the_family_budget(self) -> None:
        repo = "mlx-community/Voxtral-4B-TTS-2603-mlx-4bit"
        resolved = catalog.resolve_section_budget("voxtral-tts", repo, None)
        self.assertEqual(resolved["section_max_characters"],
                         long_form_policy.VOXTRAL_SECTION_MAX_CHARACTERS)

    def test_the_measured_family_keeps_its_measurement(self) -> None:
        """Voxtral's 800 comes from a measurement, not from the shared default."""
        policy = long_form_policy.policy_for("voxtral-tts", "x")
        self.assertEqual(policy["section_max_characters"], 800)
        self.assertEqual(policy["origin"], "measured")
        self.assertEqual(policy["source"], "measured_fleet")
        self.assertNotEqual(policy["section_max_characters"],
                            long_form_policy.CONSERVATIVE_DEFAULT_SECTION_MAX_CHARACTERS)
        # The evidence: a single call still held every word at 1162 characters, and began losing
        # words by 1685. The budget must stay under the point that was verified perfect.
        self.assertLess(policy["section_max_characters"], 1162)

    def test_the_hand_declared_qwen_control_still_wins(self) -> None:
        # The 1.7B Base 8-bit production-v2 audit was cut on 2026-09-24; the kept 4-bit tier
        # carries its own model audit, and it must still override the family default.
        repo = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit"
        control = catalog.section_size_control_for(repo)
        self.assertEqual(control["source"], "model_audit")
        self.assertEqual((control["minimum"], control["maximum"]), (40, 3000))
        self.assertEqual(control["runtime_default"], 400)
        self.assertEqual(
            long_form_policy.policy_for("qwen3-tts", repo)["section_max_characters"], 288
        )


if __name__ == "__main__":
    unittest.main()
