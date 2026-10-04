"""One join-pause rule for every voice model, and the caller's own `paragraph_pause_sec` /
`sentence_pause_sec` (owner 2026-10-03).

Every model joins its sections at 1.0 s after a sentence and 1.5 s at a paragraph break (owner
2026-10-04; was 0.6 / 1.0) (a cut
inside an over-long sentence keeps a 0.15 s soft join). Pinned here:

* the request model accepts 0-3 s and refuses anything else; an absent field is not recorded, so an
  older caller's retried request still compares equal;
* every voice model publishes both fields (bounds, the uniform default, request-only);
* the joiner follows the request for any family, and the uniform rule without it;
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
            for boundary, default in (("paragraph", 1.5), ("sentence", 1.0)):
                parameter = parameters[f"{boundary}_pause_sec"]
                self.assertEqual((parameter["minimum"], parameter["maximum"]), (0.0, 3.0))
                self.assertTrue(parameter["request_only"])
                self.assertAlmostEqual(parameter["default"], default)
            self.assertNotIn("paragraph_pause_sec",
                             [p["name"] for p in model_defaults.editable(parameters.values())])

    def test_one_rule_for_every_model(self):
        self.assertEqual(long_form_policy.default_boundary_pauses(),
                         {"sentence": 1.0, "paragraph": 1.5, "soft": 0.15})
        for model in catalog.CATALOG:
            self.assertEqual(long_form_policy.policy_for(model.family, model.repo)
                             ["join_pause_seconds"], 1.0, model.repo)


class JoinTests(unittest.TestCase):
    def test_no_request_joins_at_the_uniform_rule(self):
        self.assertEqual(generation._request_join_pauses_s(TEXT, CHUNKS, {}), [1.0, 1.5])

    def test_a_cut_inside_a_sentence_keeps_a_short_soft_join(self):
        text = "The river ran on and on past the mill and the bridge. Then it stopped."
        chunks = ["The river ran on and on", "past the mill and the bridge.", "Then it stopped."]
        self.assertEqual(generation._request_join_pauses_s(text, chunks, {}), [0.15, 1.0])

    def test_a_requested_pause_replaces_the_rule(self):
        asked = {"paragraph_pause_sec": 1.5, "sentence_pause_sec": 0.3}
        self.assertEqual(generation._request_join_pauses_s(TEXT, CHUNKS, asked), [0.3, 1.5])

    def test_a_boundary_left_out_keeps_the_uniform_one(self):
        self.assertEqual(generation._request_join_pauses_s(TEXT, CHUNKS, {"paragraph_pause_sec": 2.0}),
                         [1.0, 2.0])

    def test_pauses_are_set_before_the_tempo_pass(self):
        asked = {"paragraph_pause_sec": 1.0, "sentence_pause_sec": 0.3, "speed": 0.8}
        result = generation._request_join_pauses_s(TEXT, CHUNKS, asked)
        self.assertEqual([round(p, 3) for p in result], [0.24, 0.8])


class SectionCheckTests(unittest.TestCase):
    def test_the_silence_allowance_follows_the_uniform_and_requested_pause(self):
        sentence = "One sentence. Another one."
        margin = tts_quality.PAUSE_MARGIN_S
        self.assertEqual(tts_quality.max_pause_s(sentence), round(1.0 + margin, 2))
        self.assertEqual(tts_quality.max_pause_s("One.\n\nTwo."), round(1.5 + margin, 2))
        self.assertEqual(tts_quality.max_pause_s(sentence, requested_pause_s=1.5),
                         round(1.5 + margin, 2))
        self.assertEqual(tts_quality.max_pause_s(sentence, requested_pause_s=0.1),
                         round(1.0 + margin, 2))

    def test_a_section_holding_the_requested_pause_plus_breath_is_accepted(self):
        text = "Mara listened to the river for a long while. " * 3
        metrics = {"duration_s": 14.0, "voiced_s": 10.0, "voiced_ratio": 0.7, "dc_offset": 0.0,
                   "longest_pause_s": 4.9}
        with self.assertRaises(tts_quality.SectionQualityError):
            tts_quality.check_section(text, "breeze-tts", metrics)
        evidence = tts_quality.check_section(text, "breeze-tts", metrics, requested_pause_s=1.5)
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
