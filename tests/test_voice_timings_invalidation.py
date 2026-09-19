"""Correcting a voice's words must drop the timings that were measured against the old ones.

A voice's aligned timings (`transcript.timed.json`) are what a cloning model uses to pick the window
of a long clip that the transcript describes. They only mean anything next to the words they were
measured from. Editing the transcript through the Voices page rewrote `transcript.txt` and left the
old timings in place, so a clone aligned the new words to timings belonging to different words -
a wrong-sounding result on a long reference, with nothing on screen to explain it.

Correcting the words now invalidates them; "Transcribe Clip" measures them again. Renaming a voice,
or changing anything else, must leave them alone.
"""
import importlib
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WAV = b"RIFF____WAVEfmt " + b"x" * 400
SEGMENTS = [{"start_seconds": 0.0, "end_seconds": 1.4, "text": "the old words"}]


class TranscriptEditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.voices = importlib.import_module("components.voice.app.backend.voices")
        self._old_dir = self.voices.VOICES_DIR
        self.voices.VOICES_DIR = self.root / "voices"
        self.voices.VOICES_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.voices.VOICES_DIR = self._old_dir
        self.temporary.cleanup()

    def _voice_with_timings(self) -> str:
        """A library voice as a shared-voice push leaves it: a clip, words, and their timings."""
        import hashlib
        voice, _ = self.voices.library.sync_from_hub(
            "0fb9c76a924f", audio_bytes=WAV, original_filename="reference.wav",
            audio_sha256=hashlib.sha256(WAV).hexdigest(), name="Todd", language="en",
            gender="m", license="self-owned", transcript="the old words",
            transcript_segments=SEGMENTS, permission_acknowledged=True)
        self.assertTrue(self.voices.library.segments(voice.id), "the timings must be stored first")
        return voice.id

    def _timed(self, voice_id: str) -> Path:
        return self.voices.VOICES_DIR / voice_id / "transcript.timed.json"

    def test_correcting_the_words_drops_the_timings_measured_against_the_old_ones(self):
        voice_id = self._voice_with_timings()
        self.assertTrue(self._timed(voice_id).is_file())

        self.voices.library.update(voice_id, transcript="the corrected words")

        self.assertFalse(self._timed(voice_id).exists(),
                         "timings measured from different words must not survive the edit")
        self.assertEqual(self.voices.library.transcript(voice_id).strip(), "the corrected words")
        self.assertEqual(self.voices.library.segments(voice_id), [],
                         "and the timings must read as absent rather than as the old ones")

    def test_clearing_the_words_drops_the_timings_too(self):
        voice_id = self._voice_with_timings()
        self.voices.library.update(voice_id, transcript="")
        self.assertFalse(self._timed(voice_id).exists())
        self.assertEqual(self.voices.library.segments(voice_id), [])

    def test_editing_anything_else_keeps_them(self):
        voice_id = self._voice_with_timings()
        self.voices.library.update(voice_id, name="Todd Renamed", notes="a note", license="permission")
        self.assertTrue(self._timed(voice_id).is_file(),
                        "a rename says nothing about the words, so the timings stand")
        self.assertEqual(self.voices.library.segments(voice_id), SEGMENTS)

    def test_re_saving_the_same_words_keeps_them(self):
        """A no-op edit (the operator pressed save without changing anything) must not cost the
        timings, or re-saving a corrected transcript would silently undo its alignment."""
        voice_id = self._voice_with_timings()
        self.voices.library.update(voice_id, transcript="the old words")
        self.assertTrue(self._timed(voice_id).is_file())
        self.assertEqual(self.voices.library.segments(voice_id), SEGMENTS)


if __name__ == "__main__":
    unittest.main()
