"""Both voice field names must work, whichever family a request is for.

The worker has two names for the same idea: the Qwen families read `preset_speaker`, and the
mlx-audio picker families (Kokoro, orpheus, VibeVoice, Voxtral) read `voice`. Every client - the
native app, the gateway, the drive kit's smoke script - sends `preset_speaker`, because that is the
field the app's audio request has always carried. So a request for one of those families looked like
a model that could not run at all: it failed with "needs a preset voice" while holding a perfectly
good one under the other name.
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
SOURCE = (Path(__file__).resolve().parents[1]
          / "components/voice/app/backend/generation.py").read_text()


class VoiceFieldAliasTests(unittest.TestCase):
    def test_preset_speaker_is_accepted_for_a_picker_family(self):
        picked = MANAGER._mlx_kwargs_voice_picker("orpheus", {"preset_speaker": "tara"}, {})
        self.assertIn("tara", str(picked))

    def test_the_family_native_field_still_wins(self):
        picked = MANAGER._mlx_kwargs_voice_picker(
            "orpheus", {"voice": "leah", "preset_speaker": "tara"}, {})
        self.assertIn("leah", str(picked))

    def test_no_voice_at_all_still_refuses_with_the_actionable_message(self):
        with self.assertRaises(ValueError) as caught:
            MANAGER._mlx_kwargs_voice_picker("orpheus", {}, {})
        self.assertIn("needs a preset voice", str(caught.exception))

    def test_an_unknown_vibevoice_voice_is_still_refused(self):
        with self.assertRaises(ValueError):
            MANAGER._mlx_kwargs_voice_picker("vibevoice", {"preset_speaker": "not-a-voice"}, {})


class RevisionEvidenceTests(unittest.TestCase):
    """The evidence fence is a second reader of the same field, and it failed closed.

    Generation proved the picker resolves both names, but the fence that stamps immutable voice
    evidence ran first and only read `voice`. A client-submitted Kokoro job therefore ended as a
    terminal "preset voice evidence is unavailable" three times in five seconds, which paused the
    model in the Hub before the engine had loaded once.
    """

    REPO = "mlx-community/Kokoro-82M-bf16"

    def _job(self, **params):
        return SimpleNamespace(params={"repo": self.REPO, **params},
                               model_revision=None, voice_revision=None)

    def test_kokoro_evidence_accepts_the_client_voice_field(self):
        job = self._job(preset_speaker="af_heart")
        with mock.patch.object(generation_module.cache, "snapshot_revision", return_value="abc123"):
            GenerationManager._record_local_revision_evidence(job)
        self.assertEqual(job.voice_revision, "abc123:preset:af_heart")

    def test_the_family_native_field_still_wins_for_evidence(self):
        job = self._job(voice="af_bella", preset_speaker="af_heart")
        with mock.patch.object(generation_module.cache, "snapshot_revision", return_value="abc123"):
            GenerationManager._record_local_revision_evidence(job)
        self.assertEqual(job.voice_revision, "abc123:preset:af_bella")

    def test_evidence_still_refuses_when_no_voice_is_supplied(self):
        job = self._job()
        with mock.patch.object(generation_module.cache, "snapshot_revision", return_value="abc123"):
            with self.assertRaises(RuntimeError) as caught:
                GenerationManager._record_local_revision_evidence(job)
        self.assertIn("Kokoro preset voice evidence", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
