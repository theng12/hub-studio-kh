"""Story uploads 16 kHz mono FLAC for transcription: both the controller and the worker accept it.

The 2026-10-01 compression plan has Story send lossless-but-small FLAC instead of 192 kbps MP3, so
the Hub's intake (controller spool) and the Subtitle worker's own input check must both list it, and
the worker must treat it as plain audio (it is decoded directly, never run through the video path).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub.backend import transcription_jobs  # noqa: E402
from components.subtitle.app.backend import engine, manager  # noqa: E402


class FlacUploadTests(unittest.TestCase):
    def test_the_controller_intake_lists_flac(self):
        self.assertIn(".flac", transcription_jobs.ALLOWED_EXTENSIONS)

    def test_the_subtitle_worker_accepts_a_flac_filename(self):
        self.assertEqual(manager.safe_input_name("chapter-01.flac"), "chapter-01.flac")
        self.assertIn(".flac", manager.ALLOWED_EXTENSIONS)

    def test_flac_is_decoded_as_audio_not_extracted_as_a_video(self):
        self.assertNotIn(".flac", engine.VIDEO_CONTAINER_EXTENSIONS)
        path = Path("chapter-01.flac")
        with engine.decodable_audio(path) as readable:
            self.assertEqual(readable, path)

    def test_the_two_allow_lists_agree(self):
        """A type the controller accepts but the worker refuses would fail only after transfer."""
        self.assertEqual(transcription_jobs.ALLOWED_EXTENSIONS, manager.ALLOWED_EXTENSIONS)


if __name__ == "__main__":
    unittest.main()
