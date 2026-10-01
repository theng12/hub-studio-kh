"""Voice ``output_format: m4a``: transport compression after the lossless WAV is final.

Pinned per the 2026-10-01 compression plan:

* the request model accepts ``wav``/``m4a`` only, and an absent field leaves the recorded params
  exactly as an older caller always got them (so a retried ``client_request_id`` still matches);
* the join/pause/level-match stay on WAV inside the worker: the encode starts from the finished WAV
  and the job's decoded facts (duration, rate, channels) are the WAV's;
* the encode runs **after** the generation lock is released;
* the media fields describe the delivered file; the lossless master is removed only after the
  encode is verified; an encode that cannot run delivers the WAV and records why.

The generation loop is driven for real with the model dispatch and Qwen validators stubbed, so the
path under test (hold master -> release lock -> encode -> publish) is the production one.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
from unittest import mock
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components import audio_encode  # noqa: E402
from components.voice.app.backend import generation, lane_contract, main  # noqa: E402

FFMPEG = audio_encode.find_tool("ffmpeg")
RATE = 24000
SECONDS = 3.0


def _write_speechlike_wav(path: Path) -> None:
    frames = int(RATE * SECONDS)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes(b"".join(
            struct.pack("<h", int(9000 * math.sin(i / 9) * math.sin(i / 4000)))
            for i in range(frames)))


class RequestTests(unittest.TestCase):
    def test_the_body_accepts_wav_and_m4a_and_refuses_anything_else(self):
        for value in ("wav", "m4a", None):
            body = main.Txt2SpeechBody(repo="r", text="hi", output_format=value)
            self.assertEqual(body.output_format, value)
        with self.assertRaises(ValueError):
            main.Txt2SpeechBody(repo="r", text="hi", output_format="mp3")

    def test_an_absent_field_is_not_recorded_so_old_callers_compare_equal(self):
        model = next(m for m in generation.catalog.CATALOG if "tts" in (m.capabilities or ()))
        with mock.patch.object(main, "_defaults_in_force", return_value={}):
            plain = main._job_parameters(main.Txt2SpeechBody(repo=model.repo, text="hi"), model)
            asked = main._job_parameters(
                main.Txt2SpeechBody(repo=model.repo, text="hi", output_format="m4a"), model)
        self.assertNotIn("output_format", plain)
        self.assertEqual(asked["output_format"], "m4a")

    def test_every_voice_model_publishes_the_choice_and_it_is_never_defaultable(self):
        for model in generation.catalog.CATALOG:
            parameters = lane_contract.parameters_for(model)
            parameter = next(p for p in parameters if p["name"] == "output_format")
            self.assertEqual(parameter["enum"], ["wav", "m4a"])
            self.assertTrue(parameter["request_only"])
            self.assertNotIn("default", parameter)
            from components import model_defaults
            self.assertNotIn("output_format",
                             [p["name"] for p in model_defaults.editable(parameters)])


class _Run(unittest.TestCase):
    """Drive ``_run_txt2speech`` with the model stubbed out."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / "output"
        self.out.mkdir()
        for target, value in (("OUTPUT_DIR", self.out), ("HISTORY_FILE", self.out / ".history.json")):
            patcher = mock.patch.object(generation, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(generation, "TTS_AVAILABLE", True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.manager = generation.GenerationManager.__new__(generation.GenerationManager)
        self.manager._lock = threading.RLock()
        self.manager._jobs = {}
        self.manager._last_model_activity_at = 0.0
        self.manager._consecutive_memory_failures = 0
        self.manager._restart_scheduled = False
        self.manager._persist = lambda strict=False: True
        self.manager._evict_loaded_models = mock.Mock()
        self.manager.has_loaded_model = lambda: False
        self.seen = {}

    def run_job(self, **params):
        job = generation.GenerationJob(
            job_id="abc123def456", mode="txt2speech",
            params={"repo": "x/y", "text": "hello", **params})
        manager = self.manager

        def dispatch(_job, output_path):
            _write_speechlike_wav(Path(output_path))

        for name, replacement in (
                ("_validate_qwen_reference_locked", lambda j: None),
                ("_dispatch_txt2speech", dispatch),
                ("_apply_parent_output_speed", lambda j, p: None),
                ("_validate_qwen_output_locked", lambda j, p: None),
                ("_record_qwen_quality_attempt", lambda j, **k: None),
                ("_record_local_revision_evidence", lambda j: None)):
            patcher = mock.patch.object(manager, name, replacement, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        with mock.patch.object(generation, "_enforce_output_duration_limit", lambda p, q: None):
            manager._run_txt2speech(job)
        return job

    def visible(self):
        return sorted(p.name for p in self.out.iterdir() if p.name != ".history.json")


class DefaultDeliveryTests(_Run):
    def test_no_output_format_is_the_wav_exactly_as_before(self):
        job = self.run_job()
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(self.visible(), ["abc123def456.wav"])
        self.assertEqual((job.media_type, job.format), ("audio/wav", "wav"))
        data = (self.out / "abc123def456.wav").read_bytes()
        self.assertEqual((job.bytes, job.sha256), (len(data), hashlib.sha256(data).hexdigest()))
        self.assertIsNone(job.delivery)
        self.assertNotIn("delivery", job.serialize())

    def test_an_explicit_wav_is_the_same_as_absent(self):
        job = self.run_job(output_format="wav")
        self.assertEqual(self.visible(), ["abc123def456.wav"])
        self.assertEqual(job.media_type, "audio/wav")


@unittest.skipUnless(FFMPEG, "no ffmpeg available")
class M4aDeliveryTests(_Run):
    def test_the_job_delivers_aac_with_the_delivered_files_receipt(self):
        job = self.run_job(output_format="m4a")
        self.assertEqual(job.state, "done", job.error)
        # Only the delivered file remains: the hidden lossless master and temp are gone.
        self.assertEqual(self.visible(), ["abc123def456.m4a"])
        data = (self.out / "abc123def456.m4a").read_bytes()
        self.assertEqual(data[4:8], b"ftyp")
        self.assertEqual(job.output_path, str((self.out / "abc123def456.m4a").resolve()))
        self.assertEqual((job.media_type, job.format), ("audio/mp4", "m4a"))
        self.assertEqual((job.bytes, job.sha256), (len(data), hashlib.sha256(data).hexdigest()))
        # Decoded facts are the WAV master's.
        self.assertEqual((job.sample_rate_hz, job.channels), (RATE, 1))
        self.assertAlmostEqual(job.audio_duration_s, SECONDS, places=3)
        self.assertEqual(job.audio_duration_ms, round(SECONDS * 1000))
        self.assertAlmostEqual(audio_encode.probe_duration(self.out / "abc123def456.m4a"),
                               SECONDS, delta=0.1)
        self.assertEqual(job.delivery["applied"], "m4a")
        self.assertEqual(job.delivery["master_media_type"], "audio/wav")
        self.assertGreater(job.delivery["master_bytes"], job.bytes)
        self.assertEqual(job.progress, 1.0)
        served = job.serialize()
        self.assertEqual((served["media_type"], served["bytes"]), ("audio/mp4", len(data)))
        self.assertEqual(served["output_url"], "/api/generate/jobs/abc123def456/audio")

    def test_the_job_survives_a_history_round_trip(self):
        job = self.run_job(output_format="m4a")
        restored = generation.GenerationManager._from_disk(
            json.loads(json.dumps(generation.GenerationManager._to_disk(job))))
        self.assertEqual((restored.media_type, restored.format, restored.sha256),
                         (job.media_type, job.format, job.sha256))
        self.assertEqual(restored.delivery, job.delivery)
        self.assertEqual(restored.output_path, job.output_path)

    def test_the_encode_runs_after_the_generation_lock_is_released(self):
        real = audio_encode.encode_m4a

        def spy(*args, **kwargs):
            self.seen["locked"] = generation._GEN_LOCK.locked()
            return real(*args, **kwargs)

        with mock.patch.object(audio_encode, "encode_m4a", spy):
            job = self.run_job(output_format="m4a")
        self.assertEqual(job.format, "m4a")
        self.assertIs(self.seen["locked"], False)

    def test_the_master_is_held_hidden_and_never_a_visible_output_while_encoding(self):
        real = audio_encode.encode_m4a

        def spy(master, *args, **kwargs):
            self.seen["during"] = self.visible()
            return real(master, *args, **kwargs)

        with mock.patch.object(audio_encode, "encode_m4a", spy):
            self.run_job(output_format="m4a")
        self.assertEqual(self.seen["during"], [".abc123def456.master.wav"])

    def test_a_cancel_during_the_encode_discards_everything(self):
        job_holder = {}
        real = audio_encode.encode_m4a

        def cancelling(*args, **kwargs):
            job_holder["job"].cancel_event.set()
            return real(*args, **kwargs)

        original = generation.GenerationJob

        def make(*args, **kwargs):
            job = original(*args, **kwargs)
            job_holder["job"] = job
            return job

        with mock.patch.object(audio_encode, "encode_m4a", cancelling), \
                mock.patch.object(generation, "GenerationJob", make):
            job = self.run_job(output_format="m4a")
        self.assertEqual(job.state, "cancelled")
        self.assertEqual(self.visible(), [])


class FallbackTests(_Run):
    def test_a_missing_encoder_delivers_the_wav_and_records_why(self):
        with mock.patch.object(audio_encode, "find_tool", lambda name="ffmpeg": None):
            job = self.run_job(output_format="m4a")
        self.assertEqual(job.state, "done", job.error)
        self.assertEqual(self.visible(), ["abc123def456.wav"])
        self.assertEqual((job.media_type, job.format), ("audio/wav", "wav"))
        data = (self.out / "abc123def456.wav").read_bytes()
        self.assertEqual((job.bytes, job.sha256), (len(data), hashlib.sha256(data).hexdigest()))
        self.assertIn("ffmpeg", job.delivery_fallback_reason)
        self.assertEqual(job.delivery["applied"], "wav")
        self.assertEqual(job.serialize()["delivery_fallback_reason"], job.delivery_fallback_reason)

    def test_an_encoder_that_raises_delivers_the_wav(self):
        with mock.patch.object(audio_encode, "encode_m4a",
                               side_effect=audio_encode.EncodeError("AAC encode failed: boom")):
            job = self.run_job(output_format="m4a")
        self.assertEqual(job.state, "done")
        self.assertEqual(self.visible(), ["abc123def456.wav"])
        self.assertIn("boom", job.delivery_fallback_reason)


if __name__ == "__main__":
    unittest.main()
