"""Caller-chosen join pauses: `paragraph_pause_sec` / `sentence_pause_sec` (owner 2026-10-03).

Sleep narration wants 1.0 s at a paragraph break and 0.3 s after a sentence. Pinned here:

* the request model accepts 0-3 s and refuses anything else; an absent field is not recorded, so an
  older caller's retried request still compares equal;
* every voice model publishes both fields (bounds, the model's own default, request-only);
* the joiner follows the request for any family (Breeze too), and keeps today's pauses without it;
* the section check never reads the requested pause as an unnatural silence;
* the broker sends the fields only to a worker whose lane contract publishes them.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components import model_defaults  # noqa: E402
from components.hub.backend import broker  # noqa: E402
from components.voice.app.backend import (  # noqa: E402
    catalog, generation, lane_contract, long_form_policy, main, tts_quality,
)

TEXT = "The river was quiet. The mill stood still.\n\nMara listened."
CHUNKS = ["The river was quiet.", "The mill stood still.", "Mara listened."]
BREEZE = ("breeze-tts", "breeze/breeze-tts-2-mlx-4bit")
FISH = ("fish-audio-mlx", "majentik/fishaudio-s2-pro-MLX-4bit")


class RequestTests(unittest.TestCase):
    def test_the_body_takes_zero_to_three_seconds(self):
        for value in (0, 0.3, 1.0, 3):
            body = main.Txt2SpeechBody(repo="r", text="hi", paragraph_pause_sec=value,
                                       sentence_pause_sec=value)
            self.assertEqual((body.paragraph_pause_sec, body.sentence_pause_sec), (value, value))
        for bad in (-0.1, 3.5):
            with self.assertRaises(ValueError):
                main.Txt2SpeechBody(repo="r", text="hi", paragraph_pause_sec=bad)
            with self.assertRaises(ValueError):
                main.Txt2SpeechBody(repo="r", text="hi", sentence_pause_sec=bad)

    def test_an_absent_pause_is_not_recorded(self):
        model = next(m for m in catalog.CATALOG if "tts" in (m.capabilities or ()))
        # The lane's own defaults are in force (as with no operator choice): they must not fill in.
        in_force = model_defaults.stated(lane_contract.parameters_for(model))
        with mock.patch.object(main, "_defaults_in_force", return_value=in_force):
            plain = main._job_parameters(main.Txt2SpeechBody(repo=model.repo, text="hi"), model)
            asked = main._job_parameters(
                main.Txt2SpeechBody(repo=model.repo, text="hi", paragraph_pause_sec=1.0), model)
        self.assertNotIn("paragraph_pause_sec", plain)
        self.assertNotIn("sentence_pause_sec", plain)
        self.assertEqual(asked["paragraph_pause_sec"], 1.0)
        self.assertNotIn("sentence_pause_sec", asked)


class ContractTests(unittest.TestCase):
    def test_every_voice_model_publishes_both_pauses(self):
        for model in catalog.CATALOG:
            parameters = {p["name"]: p for p in lane_contract.parameters_for(model)}
            defaults = long_form_policy.default_boundary_pauses(model.family, model.repo)
            for boundary in ("paragraph", "sentence"):
                parameter = parameters[f"{boundary}_pause_sec"]
                self.assertEqual((parameter["minimum"], parameter["maximum"]), (0.0, 3.0))
                self.assertTrue(parameter["request_only"])
                self.assertAlmostEqual(parameter["default"], defaults[boundary])
            self.assertNotIn("paragraph_pause_sec",
                             [p["name"] for p in model_defaults.editable(parameters.values())])

    def test_defaults_are_the_pauses_in_force_today(self):
        self.assertEqual(long_form_policy.default_boundary_pauses(*FISH),
                         {"sentence": 0.3, "paragraph": 0.6, "soft": 0.18})
        self.assertEqual(long_form_policy.default_boundary_pauses(*BREEZE),
                         {"sentence": 0.6, "paragraph": 0.6, "soft": 0.6})


class JoinTests(unittest.TestCase):
    def test_no_request_keeps_todays_joins(self):
        self.assertIsNone(generation._request_join_pauses_s(*BREEZE, TEXT, CHUNKS, {}))
        self.assertEqual(generation._request_join_pauses_s(*FISH, TEXT, CHUNKS, {}), [0.3, 0.6])

    def test_sleep_pauses_follow_the_text_for_breeze_and_fish(self):
        sleep = {"paragraph_pause_sec": 1.0, "sentence_pause_sec": 0.3}
        for family in (BREEZE, FISH):
            self.assertEqual(generation._request_join_pauses_s(*family, TEXT, CHUNKS, sleep),
                             [0.3, 1.0])

    def test_a_boundary_left_out_keeps_the_models_own(self):
        only_paragraph = {"paragraph_pause_sec": 1.0}
        self.assertEqual(generation._request_join_pauses_s(*BREEZE, TEXT, CHUNKS, only_paragraph),
                         [0.6, 1.0])
        self.assertEqual(generation._request_join_pauses_s(*FISH, TEXT, CHUNKS, only_paragraph),
                         [0.3, 1.0])

    def test_pauses_are_set_before_the_tempo_pass(self):
        asked = {"paragraph_pause_sec": 1.0, "sentence_pause_sec": 0.3, "speed": 0.8}
        result = generation._request_join_pauses_s(*FISH, TEXT, CHUNKS, asked)
        self.assertEqual([round(p, 3) for p in result], [0.24, 0.8])


class SectionCheckTests(unittest.TestCase):
    def test_a_requested_pause_widens_the_silence_allowance(self):
        sentence = "One sentence. Another one."
        self.assertEqual(tts_quality.max_pause_s(sentence), 3.8)
        self.assertEqual(tts_quality.max_pause_s(sentence, requested_pause_s=1.0), 4.5)
        self.assertEqual(tts_quality.max_pause_s(sentence, requested_pause_s=0.1), 3.8)

    def test_a_section_holding_the_requested_pause_plus_breath_is_accepted(self):
        text = "Mara listened to the river for a long while. " * 3
        metrics = {"duration_s": 14.0, "voiced_s": 10.0, "voiced_ratio": 0.7, "dc_offset": 0.0,
                   "longest_pause_s": 4.2}
        with self.assertRaises(tts_quality.SectionQualityError):
            tts_quality.check_section(text, "breeze-tts", metrics)
        evidence = tts_quality.check_section(text, "breeze-tts", metrics, requested_pause_s=1.0)
        self.assertTrue(evidence["accepted"])


class BrokerTests(unittest.TestCase):
    def test_the_pause_reaches_only_a_worker_that_publishes_it(self):
        new = {"lane_contract": {"parameters": [{"name": "paragraph_pause_sec", "maximum": 3.0}]}}
        for entry, expected in ((new, True), ({"lane_contract": {"parameters": []}}, False),
                                (None, False)):
            with mock.patch.object(broker, "_catalog_entry", mock.AsyncMock(return_value=entry)):
                found = asyncio.run(broker._lane_parameter({}, "m", "paragraph_pause_sec"))
            self.assertEqual(found is not None, expected)
        self.assertIn("paragraph_pause_sec", broker._OPTIONAL_LANE_FIELDS)
        self.assertIn("sentence_pause_sec", broker._OPTIONAL_LANE_FIELDS)


if __name__ == "__main__":
    unittest.main()
