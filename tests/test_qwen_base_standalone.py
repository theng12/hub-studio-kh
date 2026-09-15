"""ST-04 — Qwen3-TTS Base models are first-class cloning voices.

These are the standalone, packaged-backend contracts for the two Base tiers
(0.6B and 1.7B).  They deliberately run without MLX or a real model file: the
real-model receipts live in ``qualification/st04.md`` and this module owns the
contract those runs must satisfy.

Covered here:
  * capability/readiness reporting is per-model truthful for both Base tiers;
  * the reference-audio contract is audit-bound for every Base tier;
  * invalid references fail with one actionable, machine-readable code each;
  * the internal Whisper quality check stays inside the parent Voice job lease
    (one lock, no second queued Subtitle job, no self-call over HTTP);
  * voice reference files stay outside generated-output cleanup.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
VOICE_APP = REPO_ROOT / "components" / "voice" / "app"
# The adopted Voice backend is imported the same way the worker and the
# existing Voice suite import it: with the app package root on sys.path, so
# ``backend.*`` resolves to this exact tree and no worker-internal path is used.
for _entry in (str(VOICE_APP), str(REPO_ROOT)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from backend import (  # noqa: E402
    catalog, model_audits, paths, qwen_quality, reference_audio,
)

QWEN06 = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit"
QWEN17 = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"
QWEN06_PRESET = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"
QWEN17_PRESET = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"
QWEN17_DESIGN = "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit"


def _wav_bytes(seconds: float = 1.0, sample_rate: int = 24000, amplitude: int = 8000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        frames = int(seconds * sample_rate)
        stream.writeframes(
            b"".join(
                int(amplitude * (1 if (index // 120) % 2 else -1)).to_bytes(
                    2, "little", signed=True
                )
                for index in range(frames)
            )
        )
    return buffer.getvalue()


class QwenBaseCatalogTests(unittest.TestCase):
    """Both Base tiers must be exposed as audited cloning models."""

    def test_both_base_tiers_are_cataloged_as_voice_cloning(self) -> None:
        for repo, minimum_gb in ((QWEN06, 8), (QWEN17, 16)):
            entry = catalog.get_model(repo)
            self.assertIsNotNone(entry, repo)
            self.assertIn("voice-cloning", entry.capabilities)
            self.assertIn("tts", entry.capabilities)
            self.assertEqual(entry.family, "qwen3-tts")
            self.assertEqual(entry.min_unified_memory_gb, minimum_gb)

    def test_base_reference_contract_is_audit_bound_and_reports_its_source(self) -> None:
        for repo in (QWEN06, QWEN17):
            payload = catalog.serialize_model(catalog.get_model(repo))
            reference = payload["reference_audio"]
            self.assertTrue(reference["declared"], repo)
            self.assertTrue(reference["supported"], repo)
            self.assertTrue(reference["profile_audited"], repo)
            self.assertEqual(reference["profile_source"], "audit", repo)
            # The model needs the exact reference transcript; that is a
            # backend-enforced requirement, not a UI hint.
            self.assertEqual(reference["transcript"], "required", repo)
            self.assertNotIn("transcript_requirement", reference, repo)
            self.assertTrue(reference["minimum_duration_seconds"] >= 3, repo)
            self.assertTrue(reference["maximum_duration_seconds"] <= 30, repo)
            self.assertEqual(reference["sample_rate_hz"], 24000, repo)

    def test_preset_and_design_models_are_not_marked_as_cloning(self) -> None:
        for repo in (QWEN06_PRESET, QWEN17_PRESET, QWEN17_DESIGN):
            entry = catalog.get_model(repo)
            self.assertNotIn("voice-cloning", entry.capabilities, repo)
            payload = catalog.serialize_model(entry)
            self.assertFalse(payload["reference_audio"]["declared"], repo)
            self.assertFalse(payload["reference_audio"]["supported"], repo)

    def test_quality_guardrails_are_attached_only_to_cloning_models(self) -> None:
        for repo in (QWEN06, QWEN17):
            payload = catalog.serialize_model(catalog.get_model(repo))
            guardrails = payload["execution_contract"]["quality_guardrails"]
            self.assertIsNotNone(guardrails, repo)
            self.assertEqual(guardrails["required_local_models"], [qwen_quality.WHISPER_REPO])
            self.assertEqual(guardrails["validator_revision"], qwen_quality.VALIDATOR_REVISION)
            self.assertEqual(guardrails["max_local_quality_retries"], 1)
        for repo in (QWEN06_PRESET, QWEN17_PRESET):
            payload = catalog.serialize_model(catalog.get_model(repo))
            self.assertIsNone(payload["execution_contract"]["quality_guardrails"], repo)

    def test_only_base_tiers_are_qwen_clone_jobs(self) -> None:
        reference = {"_reference_audio_path": "/private/reference.wav"}
        self.assertTrue(qwen_quality.is_qwen_base_clone(QWEN06, reference))
        self.assertTrue(qwen_quality.is_qwen_base_clone(QWEN17, reference))
        self.assertTrue(
            qwen_quality.is_qwen_base_clone(QWEN06, {"voice_library_id": "abc123"})
        )
        # A Base model with no reference at all is not a clone job yet.
        self.assertFalse(qwen_quality.is_qwen_base_clone(QWEN06, {}))
        self.assertFalse(qwen_quality.is_qwen_base_clone(QWEN06_PRESET, reference))
        self.assertFalse(qwen_quality.is_qwen_base_clone(QWEN17_DESIGN, reference))

    def test_both_base_tiers_require_an_audited_reference_profile(self) -> None:
        for repo in (QWEN06, QWEN17):
            self.assertTrue(reference_audio._requires_audited_profile(repo), repo)
            self.assertTrue(model_audits.input_limits(repo).get("reference_audio"), repo)
        for repo in (QWEN06_PRESET, QWEN17_PRESET, QWEN17_DESIGN):
            self.assertFalse(reference_audio._requires_audited_profile(repo), repo)

    def test_no_preset_model_is_forced_through_the_clone_contract(self) -> None:
        """The 0.6B Base audit must not leak onto unrelated Qwen entries."""
        self.assertNotEqual(
            model_audits.input_limits(QWEN06)["reference_audio"],
            model_audits.input_limits("mlx-community/Kokoro-82M-bf16").get("reference_audio"),
        )


class ReferenceContractTests(unittest.TestCase):
    """The audit-bound profile is really enforced by ``prepare``."""

    def test_base_preparation_refuses_generic_defaults_without_an_audit(self) -> None:
        with patch.object(reference_audio, "_profile", return_value={}):
            with self.assertRaises(reference_audio.ReferenceAudioError) as error:
                reference_audio.prepare(
                    audio_bytes=_wav_bytes(), filename="voice.wav", model_id=QWEN06,
                    transcript="Exact words.",
                )
        self.assertEqual(error.exception.code, "REFERENCE_AUDIO_CONTRACT_UNAVAILABLE")
        self.assertIn(QWEN06, error.exception.detail)

    def test_base_preparation_requires_a_transcript_when_the_audit_demands_it(self) -> None:
        with self.assertRaises(reference_audio.ReferenceAudioError) as error:
            reference_audio.prepare(
                audio_bytes=_wav_bytes(seconds=6.0), filename="voice.wav",
                model_id=QWEN17, transcript="",
            )
        self.assertEqual(error.exception.code, "REFERENCE_TRANSCRIPT_REQUIRED")

    def test_preset_model_still_accepts_its_generic_fallback(self) -> None:
        """The stricter Base rule must not tighten unrelated families."""
        with patch.object(reference_audio, "_profile", return_value={}):
            prepared = reference_audio.prepare(
                audio_bytes=_wav_bytes(), filename="voice.wav",
                model_id=QWEN06_PRESET, transcript="",
            )
        self.assertTrue(Path(prepared["path"]).is_file())

    def test_prepared_reference_is_deterministic_and_reused(self) -> None:
        audio = _wav_bytes(seconds=6.0)
        first = reference_audio.prepare(
            audio_bytes=audio, filename="voice.wav", model_id=QWEN06,
            transcript="Exact words.",
        )
        second = reference_audio.prepare(
            audio_bytes=audio, filename="voice.wav", model_id=QWEN06,
            transcript="Exact words.",
        )
        self.assertEqual(first["cache_key"], second["cache_key"])
        self.assertEqual(first["derived_sha256"], second["derived_sha256"])
        # A different transcript must not collide onto the same execution copy:
        # Base conditions on the transcript, so it is part of the identity.
        third = reference_audio.prepare(
            audio_bytes=audio, filename="voice.wav", model_id=QWEN06,
            transcript="Different words.",
        )
        self.assertNotEqual(first["cache_key"], third["cache_key"])


class ReferenceFailureCodeTests(unittest.TestCase):
    """Missing/unreadable/empty references each get one actionable code."""

    def setUp(self) -> None:
        from backend import generation
        self.generation = generation
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _require(self, path, source="private_reference"):
        return self.generation.GenerationManager._require_readable_qwen_reference(
            path, source=source,
        )

    def test_not_selected_is_distinct_from_absent(self) -> None:
        with self.assertRaises(qwen_quality.QwenQualityError) as error:
            self._require(None)
        self.assertEqual(error.exception.code, "QWEN_REFERENCE_AUDIO_MISSING")
        self.assertEqual(error.exception.evidence["reason"], "not_selected")

        with self.assertRaises(qwen_quality.QwenQualityError) as error:
            self._require(self.root / "gone.wav")
        self.assertEqual(error.exception.code, "QWEN_REFERENCE_AUDIO_MISSING")
        self.assertEqual(error.exception.evidence["reason"], "absent")

    def test_directory_is_not_treated_as_audio(self) -> None:
        with self.assertRaises(qwen_quality.QwenQualityError) as error:
            self._require(self.root)
        self.assertEqual(error.exception.code, "QWEN_REFERENCE_AUDIO_UNAVAILABLE")
        self.assertEqual(error.exception.evidence["reason"], "not_a_file")

    def test_empty_file_is_reported_as_empty(self) -> None:
        empty = self.root / "empty.wav"
        empty.write_bytes(b"")
        with self.assertRaises(qwen_quality.QwenQualityError) as error:
            self._require(empty)
        self.assertEqual(error.exception.code, "QWEN_REFERENCE_AUDIO_EMPTY")
        self.assertEqual(error.exception.evidence["bytes"], 0)

    def test_unreadable_file_is_reported_as_unreadable(self) -> None:
        locked = self.root / "locked.wav"
        locked.write_bytes(_wav_bytes())
        os.chmod(locked, 0o000)
        try:
            with self.assertRaises(qwen_quality.QwenQualityError) as error:
                self._require(locked)
            self.assertEqual(error.exception.code, "QWEN_REFERENCE_AUDIO_UNREADABLE")
            self.assertEqual(error.exception.evidence["reason"], "not_readable")
        finally:
            os.chmod(locked, 0o600)

    def test_evidence_never_retains_a_path_or_transcript(self) -> None:
        with self.assertRaises(qwen_quality.QwenQualityError) as error:
            self._require(self.root / "gone.wav")
        rendered = json.dumps(error.exception.evidence)
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn("transcript", rendered)

    def test_missing_transcript_demands_the_exact_transcript(self) -> None:
        reference = self.root / "reference.wav"
        reference.write_bytes(_wav_bytes())
        job = _clone_job({"_reference_audio_path": str(reference)})
        manager = self.generation.GenerationManager()
        with patch.object(qwen_quality, "cached_reference_evidence", lambda _key: None):
            with self.assertRaises(qwen_quality.QwenQualityError) as error:
                manager._validate_qwen_reference_locked(job)
        self.assertEqual(error.exception.code, "QWEN_REFERENCE_TRANSCRIPT_REQUIRED")
        self.assertEqual(error.exception.evidence["reference_source"], "private_reference")


def _clone_job(params: dict, repo: str = QWEN06):
    """Minimal job double: the validators only read ``params``/``output_path``.

    ``repo`` is always present in a real job; the validators key off it, so a
    double without one would silently skip the behaviour under test.
    """
    return type("Job", (), {
        "params": {"repo": repo, **params}, "output_path": None,
    })()


class QualityCheckLeaseTests(unittest.TestCase):
    """ST-04.3 — the Whisper check borrows the parent job lease only."""

    def setUp(self) -> None:
        from backend import generation, transcription
        self.generation = generation
        self.transcription = transcription

    def test_reference_check_runs_in_process_under_the_parent_lock(self) -> None:
        calls: list[dict] = []
        release_calls: list[str] = []
        expected = {"text": "Exact words.", "language": "en", "duration": 1.0,
                    "segments": [{"start": 0.0, "end": 1.0, "text": "Exact words.",
                                  "words": [{"word": "Exact", "start": 0.0, "end": 0.4},
                                            {"word": "words", "start": 0.4, "end": 1.0}]}]}

        def fake_transcribe_locked(audio_path, **kwargs):
            # The parent Voice job must already own the single global lock; a
            # second acquisition here is exactly the deadlock ST-04.3 forbids.
            calls.append({"path": audio_path, "lock_held": self.generation._GEN_LOCK.locked(), **kwargs})
            return expected

        with tempfile.TemporaryDirectory() as temporary:
            reference = Path(temporary) / "reference.wav"
            reference.write_bytes(_wav_bytes())
            job = _clone_job({
                "repo": QWEN06,
                "_reference_audio_path": str(reference),
                "ref_transcript": "Exact words.",
                "language": "en",
            })
            manager = self.generation.GenerationManager()
            with patch.object(self.transcription.manager, "transcribe_locked", fake_transcribe_locked), \
                 patch.object(self.transcription.manager, "release_memory_locked",
                              lambda reason="manual": release_calls.append(reason)), \
                 patch.object(qwen_quality, "cached_reference_evidence", lambda _key: None), \
                 patch.object(qwen_quality, "save_reference_evidence", lambda _key, _value: None), \
                 patch.object(manager, "_evict_loaded_models", lambda reason="": {}), \
                 patch.object(qwen_quality, "reference_cache_key", lambda _path, _text: "cache-key"):
                with self.generation._GEN_LOCK:
                    manager._validate_qwen_reference_locked(job)

        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["lock_held"], "quality check must run inside the parent job lease")
        self.assertEqual(calls[0]["model_repo"], qwen_quality.WHISPER_REPO)
        self.assertTrue(calls[0]["word_timestamps"])
        self.assertEqual(release_calls, ["qwen-reference-validation-complete"])
        self.assertTrue(job.params["_qwen_reference_validation"]["accepted"])

    def test_rejected_transcript_is_terminal_without_a_second_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            reference = Path(temporary) / "reference.wav"
            reference.write_bytes(_wav_bytes())
            job = _clone_job({
                "repo": QWEN17, "_reference_audio_path": str(reference),
                "ref_transcript": "Completely different narration.",
            })
            manager = self.generation.GenerationManager()
            with patch.object(self.transcription.manager, "transcribe_locked",
                              lambda *_a, **_k: {"text": "nothing alike here", "duration": 1.0,
                                                "segments": [{"words": [{"word": "nothing",
                                                                         "start": 0.0, "end": 1.0}]}]}), \
                 patch.object(self.transcription.manager, "release_memory_locked", lambda reason="": None), \
                 patch.object(qwen_quality, "cached_reference_evidence", lambda _key: None), \
                 patch.object(manager, "_evict_loaded_models", lambda reason="": {}):
                with self.assertRaises(qwen_quality.QwenQualityError) as error:
                    with self.generation._GEN_LOCK:
                        manager._validate_qwen_reference_locked(job)
            self.assertEqual(error.exception.code, "QWEN_REFERENCE_TRANSCRIPT_MISMATCH")
            self.assertNotIn("_qwen_reference_validation", job.params)

    def test_cached_reference_evidence_skips_whisper_entirely(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            reference = Path(temporary) / "reference.wav"
            reference.write_bytes(_wav_bytes())
            job = _clone_job({
                "repo": QWEN06, "_reference_audio_path": str(reference),
                "ref_transcript": "Exact words.",
            })
            manager = self.generation.GenerationManager()
            with patch.object(self.transcription.manager, "transcribe_locked",
                              side_effect=AssertionError("Whisper must not run on a cache hit")), \
                 patch.object(qwen_quality, "cached_reference_evidence",
                              lambda _key: {"validator_revision": qwen_quality.VALIDATOR_REVISION,
                                            "accepted": True}):
                with self.generation._GEN_LOCK:
                    manager._validate_qwen_reference_locked(job)
            self.assertTrue(job.params["_qwen_reference_validation"]["accepted"])

    def test_quality_check_never_posts_a_second_subtitle_job(self) -> None:
        """No HTTP self-call, so no second queued job and no second memory lease."""
        import httpx
        from backend import main as voice_main

        posted: list[str] = []
        original = httpx.AsyncClient.post

        async def spy(self, url, *args, **kwargs):  # pragma: no cover - guard only
            posted.append(str(url))
            return await original(self, url, *args, **kwargs)

        with patch.object(httpx.AsyncClient, "post", spy), \
             patch.object(voice_main, "stt_manager", self.transcription.manager):
            with tempfile.TemporaryDirectory() as temporary:
                reference = Path(temporary) / "reference.wav"
                reference.write_bytes(_wav_bytes())
                job = _clone_job({
                    "repo": QWEN06, "_reference_audio_path": str(reference),
                    "ref_transcript": "Exact words.", "language": "en",
                })
                manager = self.generation.GenerationManager()
                with patch.object(self.transcription.manager, "transcribe_locked",
                                  lambda *_a, **_k: {"text": "Exact words.", "duration": 1.0,
                                                     "segments": [{"words": [
                                                         {"word": "Exact", "start": 0.0, "end": 0.5},
                                                         {"word": "words", "start": 0.5, "end": 1.0}]}]}), \
                     patch.object(self.transcription.manager, "release_memory_locked", lambda reason="": None), \
                     patch.object(qwen_quality, "cached_reference_evidence", lambda _key: None), \
                     patch.object(qwen_quality, "save_reference_evidence", lambda _key, _value: None), \
                     patch.object(manager, "_evict_loaded_models", lambda reason="": {}):
                    with self.generation._GEN_LOCK:
                        manager._validate_qwen_reference_locked(job)
        self.assertEqual(posted, [])


class ReferenceCleanupTests(unittest.TestCase):
    """Voice reference files are not generated outputs and must survive cleanup."""

    def test_reference_cache_is_outside_the_managed_output_root(self) -> None:
        reference_root = Path(reference_audio.ROOT).resolve()
        output_root = Path(paths.data_path("output")).resolve()
        self.assertNotEqual(reference_root, output_root)
        self.assertNotIn(output_root, reference_root.parents)
        self.assertNotIn(reference_root, output_root.parents)

    def test_expired_reference_cleanup_is_separate_and_expiry_driven(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fresh = root / "fresh"
            stale = root / "stale"
            for directory, expires in ((fresh, time.time() + 600), (stale, time.time() - 1)):
                directory.mkdir()
                (directory / "metadata.json").write_text(json.dumps({"expires_at": expires}))
                (directory / "reference.wav").write_bytes(_wav_bytes())
            with patch.object(reference_audio, "ROOT", root):
                removed = reference_audio.cleanup_expired()
            self.assertEqual(removed, 1)
            self.assertTrue(fresh.is_dir())
            self.assertFalse(stale.exists())

    def test_cleanup_never_deletes_an_unexpired_or_unreadable_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            unreadable = root / "unreadable"
            unreadable.mkdir()
            (unreadable / "metadata.json").write_text("not json")
            (unreadable / "reference.wav").write_bytes(b"audio")
            with patch.object(reference_audio, "ROOT", root):
                removed = reference_audio.cleanup_expired()
            self.assertEqual(removed, 0)
            self.assertTrue(unreadable.is_dir())

    def test_a_jobs_private_reference_is_registered_media_not_an_output(self) -> None:
        """Job details expose the reference for playback; they never register it."""
        from backend import job_details
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.wav"
            reference.write_bytes(_wav_bytes())
            job = _clone_job({"_reference_audio_path": str(reference)})
            with patch.object(job_details.reference_audio, "ROOT", root):
                paths = job_details._reference_paths(job)
                output_paths = job_details._output_paths(job)
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0][0], reference)
        self.assertEqual(output_paths, [])


if __name__ == "__main__":
    unittest.main()
