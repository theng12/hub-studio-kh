"""Both voice field names must work for every family that takes a preset voice.

The worker has two names for the same idea: the Qwen families read `preset_speaker`, and the
mlx-audio families read `voice`. Every client - the native app, the gateway, the drive kit's smoke
script - sends `preset_speaker`, because that is the field the app's audio request has always
carried. So a request for one of those families looked like a model that could not run at all: it
failed with "needs a preset voice" while holding a perfectly good one under the other name.

Retargeted 2026-09-24 (ST-13.A cleanup): the family these tests were written against (Kokoro, and
the Orpheus/VibeVoice pickers) left the catalog in ST-13.B, and `_mlx_kwargs_voice_picker` went with
it. Pocket TTS is the surviving family whose resolver reads both field names, so the invariant is
tested there instead of being deleted with its first subject.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.voice.app.backend import generation as generation_module  # noqa: E402
from components.voice.app.backend.generation import GenerationManager  # noqa: E402

MANAGER = GenerationManager.__new__(GenerationManager)
POCKET = "mlx-community/pocket-tts-4bit"


class VoiceFieldAliasTests(unittest.TestCase):
    """`voice` and `preset_speaker` are the same field, and the reference clip beats both."""

    def test_preset_speaker_is_accepted_where_the_family_reads_voice(self):
        gen_kwargs = {}
        MANAGER._mlx_kwargs_voice_or_clone({"preset_speaker": "alba"}, gen_kwargs, None)
        self.assertEqual(gen_kwargs["voice"], "alba")

    def test_the_family_native_field_still_wins(self):
        gen_kwargs = {}
        MANAGER._mlx_kwargs_voice_or_clone(
            {"voice": "marius", "preset_speaker": "alba"}, gen_kwargs, None)
        self.assertEqual(gen_kwargs["voice"], "marius")

    def test_a_reference_clip_beats_a_preset_name(self):
        """Pocket TTS does both; when both are supplied the clip is what the engine clones."""
        gen_kwargs = {}
        params = {"voice": "alba", "voice_library_id": "some-voice"}
        with mock.patch.object(GenerationManager, "_inject_voice_clone") as inject:
            label = MANAGER._mlx_kwargs_voice_or_clone(params, gen_kwargs, None)
        inject.assert_called_once()
        self.assertIn("clone", label)
        self.assertNotIn("voice", gen_kwargs)

    def test_no_voice_and_no_reference_is_the_engines_own_preset(self):
        """Unlike a preset-only picker, this family has a default voice of its own."""
        gen_kwargs = {}
        label = MANAGER._mlx_kwargs_voice_or_clone({}, gen_kwargs, None)
        self.assertEqual(label, "zero-shot")
        self.assertNotIn("voice", gen_kwargs)


class RevisionEvidenceTests(unittest.TestCase):
    """The evidence fence is a second reader of the same fields, and it failed closed.

    The fence stamps immutable voice evidence for the finished artifact. It reads the reference
    hash for a cloned voice; for a preset or zero-shot job it stamps the model revision and leaves
    the voice revision empty rather than inventing one.
    """

    def _job(self, **params):
        return SimpleNamespace(params={"repo": POCKET, **params},
                               model_revision=None, voice_revision=None)

    def test_a_private_reference_is_the_voice_evidence(self):
        job = self._job(_reference_audio_sha256="deadbeef")
        with mock.patch.object(generation_module.cache, "snapshot_revision", return_value="abc123"):
            GenerationManager._record_local_revision_evidence(job)
        self.assertEqual(job.model_revision, "abc123")
        self.assertEqual(job.voice_revision, "deadbeef")

    def test_a_preset_job_records_the_model_and_no_invented_voice(self):
        job = self._job(voice="alba")
        with mock.patch.object(generation_module.cache, "snapshot_revision", return_value="abc123"):
            GenerationManager._record_local_revision_evidence(job)
        self.assertEqual(job.model_revision, "abc123")
        self.assertIsNone(job.voice_revision)


if __name__ == "__main__":
    unittest.main()
