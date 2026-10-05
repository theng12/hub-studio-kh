"""A voice job's per-section quality evidence reaches the Hub job item, and only its numbers do.

Voice Studio records each TTS section's checks (`components/voice/app/backend/tts_quality.py`) in
the worker job's `section_quality`; the broker copies it onto the batch item so the Gateway and
Story can show which section failed and why. Worker payloads cross a service boundary, so the copy
is a whitelist: numbers, booleans and known codes, never text or paths.
"""
from __future__ import annotations

import unittest

from components.hub.backend import broker


class SectionQualityPassthroughTests(unittest.TestCase):
    def test_numbers_and_known_codes_pass_and_anything_else_is_dropped(self) -> None:
        item: dict = {}
        broker._record_worker_section_quality(item, {"section_quality": {
            "revision": "voicestudio.tts-section-quality.v1",
            "sections": [
                {"section": 3, "attempt": 1, "accepted": False, "duration_s": 49.0,
                 "voiced_s": 2.8, "word_overlap": 0.19, "transcribe_back": "whisper",
                 "rejection_code": "TTS_SECTION_SILENT", "text": "the customer's script",
                 "path": "/Users/someone/out.wav"},
                {"section": 3, "attempt": 2, "accepted": True, "transcribe_back": "/etc/passwd",
                 "word_overlap": float("nan")},
            ],
        }})
        first, second = item["section_quality"]["sections"]
        self.assertEqual(first, {
            "section": 3, "attempt": 1, "accepted": False, "duration_s": 49.0, "voiced_s": 2.8,
            "word_overlap": 0.19, "transcribe_back": "whisper",
            "rejection_code": "TTS_SECTION_SILENT",
        })
        self.assertEqual(second, {"section": 3, "attempt": 2, "accepted": True})
        self.assertEqual(item["section_quality"]["revision"], "voicestudio.tts-section-quality.v1")

    def test_every_code_and_number_the_voice_gate_writes_passes(self) -> None:
        from components.voice.app.backend import tts_quality
        item: dict = {}
        record = {"section": 1, "attempt": 1, "accepted": False, "longest_pause_s": 34.98,
                  "max_pause_s": 3.8, "longest_missing_run": 7, "text_check": "advisory",
                  "rejection_code": "TTS_SECTION_INTERNAL_SILENCE"}
        broker._record_worker_section_quality(item, {"section_quality": {
            "revision": tts_quality.REVISION, "sections": [record]}})
        self.assertEqual(item["section_quality"]["sections"], [record])

    def test_a_job_without_section_evidence_leaves_the_item_alone(self) -> None:
        item: dict = {}
        broker._record_worker_section_quality(item, {"section_quality": None})
        self.assertNotIn("section_quality", item)

    def test_the_section_timeline_passes_as_numbers_only(self) -> None:
        item: dict = {}
        row = {"section": 1, "start_s": 0.08, "end_s": 14.2, "text_start": 0, "text_end": 231}
        broker._record_worker_section_quality(item, {"section_quality": {
            "revision": "r", "sections": [],
            "timeline": [{**row, "text": "the customer's script"}, {**row, "section": 2}]}})
        self.assertEqual(item["section_quality"]["timeline"], [row, {**row, "section": 2}])

    def test_a_timeline_with_a_missing_number_is_dropped_whole(self) -> None:
        item: dict = {}
        broker._record_worker_section_quality(item, {"section_quality": {
            "revision": "r", "sections": [],
            "timeline": [{"section": 1, "start_s": 0.0, "end_s": float("nan"), "text_start": 0,
                          "text_end": 5}]}})
        self.assertNotIn("timeline", item["section_quality"])

    def test_a_retried_attempt_does_not_keep_the_previous_workers_evidence(self) -> None:
        self.assertIn("section_quality", broker._ATTEMPT_EVIDENCE_FIELDS)


if __name__ == "__main__":
    unittest.main()
