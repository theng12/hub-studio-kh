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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.voice.app.backend import catalog, long_form_policy, model_audits  # noqa: E402

GENERATION = (ROOT / "components/voice/app/backend/generation.py").read_text()
FAMILIES = sorted({entry.family for entry in catalog.CATALOG})


def _engine_cliff_family():
    """A throwaway registry entry with an engine cliff, for the cap mechanism.

    The shipped families that had cliffs (orpheus at 170 characters, bark at
    150) were cut on 2026-09-24, so the mechanism is exercised on a declared
    entry rather than a dead `if family == "orpheus"` branch that would hand a
    re-adopted family a policy nobody has measured since.
    """
    return catalog.Family(
        id="engine-cliff-probe",
        label="Engine cliff probe",
        engine=catalog.ENGINE_MLX_AUDIO,
        requirements=("soundfile",),
        summary="probe",
        how_to_use="probe",
        text_guidance=catalog.TextGuidance(
            soft_max_chars=None, chunking="auto-split", note="probe"
        ),
        section_max_characters=170,
        section_hard_cap_characters=170,
        section_origin="engine_input_limit",
        section_note="Stops rather than degrading past its own input limit.",
    )


class SectionBudgetTests(unittest.TestCase):
    def test_every_family_has_a_section_budget(self) -> None:
        """No family may fall back to "one request for the whole script"."""
        for family in FAMILIES:
            policy = long_form_policy.policy_for(family, f"{family}/repo")
            self.assertIsNotNone(policy, f"{family} has no long-form policy")
            self.assertGreater(policy["section_max_characters"], 0, family)

    def test_an_unknown_family_gets_the_conservative_default(self) -> None:
        """A newly adopted family must inherit sectioning rather than skip it."""
        policy = long_form_policy.policy_for("brand-new-family", "org/repo", uniform=False)
        self.assertEqual(policy["section_max_characters"],
                         long_form_policy.CONSERVATIVE_DEFAULT_SECTION_MAX_CHARACTERS)
        self.assertEqual(long_form_policy.policy_for("brand-new-family", "org/repo")
                         ["section_max_characters"], 250)
        self.assertEqual(policy["source"], "conservative_default")
        self.assertEqual(policy["origin"], "conservative_default")

    def test_every_shipped_family_publishes_its_registry_sectioning(self) -> None:
        """One place decides a family's long-form behaviour: its registry entry.

        Stated over the registry, so it covers a new family: the budget, the
        join pause and the provenance the policy publishes are the family's own
        declared facts, and a refinement applies only to the checkpoint whose
        name it marks.
        """
        for entry in catalog.FAMILIES.values():
            policy = long_form_policy.policy_for(entry.id, f"{entry.id}/repo", uniform=False)
            self.assertEqual(policy["section_max_characters"], entry.section_max_characters, entry.id)
            self.assertEqual(policy["join_pause_milliseconds"],
                             round(entry.join_pause_seconds * 1000), entry.id)
            self.assertEqual(policy["origin"], entry.section_origin, entry.id)
            for refinement in entry.section_refinements:
                refined = long_form_policy.policy_for(
                    entry.id, f"mlx-community/{entry.id}-{refinement.marker}-probe", uniform=False
                )
                if refinement.section_max_characters is not None:
                    self.assertEqual(refined["section_max_characters"],
                                     refinement.section_max_characters, entry.id)
                if refinement.join_pause_seconds is not None:
                    self.assertEqual(refined["join_pause_milliseconds"],
                                     round(refinement.join_pause_seconds * 1000), entry.id)
        # The families with their own measurements keep them as the measured policy...
        measured = lambda family: long_form_policy.policy_for(family, "x", uniform=False)  # noqa: E731
        self.assertEqual(measured("omnivoice")["section_max_characters"], 288)
        self.assertEqual(measured("chatterbox-mlx")["section_max_characters"], 500)

    def test_every_voice_model_renders_one_section_size_and_one_join(self) -> None:
        """Owner 2026-10-03: 250-character sections and the uniform sentence join (1.0 s since 2026-10-04) for every model,
        audited ones included (the Qwen 1.7B audit's Auto 280 is capped, not followed)."""
        for entry in catalog.CATALOG:
            policy = catalog._audited_long_form_policy(entry.family, entry.repo)
            self.assertEqual(policy["section_max_characters"], 250, entry.repo)
            self.assertEqual(policy["join_pause_milliseconds"], 1000, entry.repo)
            self.assertEqual(catalog.section_size_control_for(entry.repo)["runtime_default"], 250,
                             entry.repo)

    def test_an_engine_cliff_is_the_section_budget_and_is_published_as_a_cap(self) -> None:
        with patch.dict(catalog.FAMILIES, {"engine-cliff-probe": _engine_cliff_family()}):
            policy = long_form_policy.policy_for(
                "engine-cliff-probe", "mlx-community/engine-cliff-probe"
            )
        self.assertEqual(policy["section_max_characters"], 170)
        self.assertEqual(policy["hard_cap_characters"], 170)

    def test_an_audit_cannot_raise_a_family_past_its_engine_cliff(self) -> None:
        """A model audit may pick a finer section. It may never send an engine past its own limit."""
        with patch.dict(catalog.FAMILIES, {"engine-cliff-probe": _engine_cliff_family()}):
            raised = long_form_policy.policy_for(
                "engine-cliff-probe", "mlx-community/engine-cliff-probe",
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
        """The engine's own declaration decides, and the policy obeys it.

        This used to pin `NATIVE_SPEED_FAMILIES` to exactly {"kittentts",
        "kokoro-mlx"} - two families that no longer ship - so it asserted a fact
        about a list rather than the rule. The rule is: a family whose engine
        scales its own predicted durations is delivered natively, and every
        other family gets exactly one tempo pass. Stated over the registry, it
        covers the next family instead of needing an edit.
        """
        for entry in catalog.FAMILIES.values():
            expected = "native" if entry.native_speed else "tempo"
            self.assertEqual(
                long_form_policy.speed_policy_for(entry.id).delivery, expected, entry.id
            )
        # A family the registry has never heard of takes the conservative path:
        # one tempo pass is audible and correct, while claiming native delivery
        # would silently drop the caller's speed control.
        self.assertEqual(long_form_policy.speed_policy_for("brand-new-family").delivery, "tempo")

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
        repo = "mlx-community/VoxCPM2-4bit"
        resolved = catalog.resolve_section_budget("voxcpm-mlx", repo, None)
        self.assertEqual(resolved["section_max_characters"],
                         long_form_policy.UNIFORM_SECTION_MAX_CHARACTERS)

    def test_a_measured_family_publishes_a_budget_below_its_engine_ceiling(self) -> None:
        """OmniVoice's 288 is a measurement, not the shared default, and is not raised.

        Measured 2026-08-07 by transcribe-back coverage at equal text/seed/voice:
        288 and 350 both held every word at 99.4% coverage, while 400 dropped to
        97.0% and 600 to 83.0%. 288 is kept because it paces better at that equal
        coverage. The evidence lives beside the constant in `long_form_policy`.
        """
        policy = long_form_policy.policy_for("omnivoice", "x", uniform=False)
        self.assertEqual(policy["section_max_characters"], 288)
        self.assertEqual(policy["origin"], "family_policy")
        self.assertNotEqual(policy["section_max_characters"],
                            long_form_policy.CONSERVATIVE_DEFAULT_SECTION_MAX_CHARACTERS)
        self.assertLess(policy["section_max_characters"], 350)

    def test_the_hand_declared_qwen_control_still_wins(self) -> None:
        # The 1.7B Base 8-bit production-v2 audit was cut on 2026-09-24; the kept 4-bit tier
        # carries its own model audit, and it must still override the family default.
        repo = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit"
        control = catalog.section_size_control_for(repo)
        self.assertEqual(control["source"], "model_audit")
        self.assertEqual((control["minimum"], control["maximum"]), (40, 3000))
        # The kept tier's record derives from the measured production audit, so its measured
        # default is the audited Auto 280 - not the family's 288 and not the 400 maximum. The
        # owner's uniform 250 (2026-10-03) caps it at runtime; a caller may still ask up to 3000.
        self.assertEqual(control["runtime_default"], 250)
        self.assertEqual(
            long_form_policy.policy_for("qwen3-tts", repo, uniform=False)["section_max_characters"],
            288,
        )
        pinned = model_audits.qwen_17b_production_v2_limits(repo)
        self.assertEqual(pinned["default_private_section_max_characters"], 280)
        self.assertEqual(
            catalog._audited_long_form_policy("qwen3-tts", repo)["section_max_characters"], 250
        )


if __name__ == "__main__":
    unittest.main()
