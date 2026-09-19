"""A video file must have its audio extracted, not be handed to a decoder that cannot open it.

Subtitle accepts .mp4 and .mov (upload validation and the Hub both allow them), and the Whisper
decoder it uses reads audio through miniaudio, which does not open those containers - so a screen
recording was accepted, dispatched, and always failed, with nothing on screen to say the file was
the reason. The owner's decision: extract the audio track with the bundled ffmpeg. These checks pin
the extraction, the pass-through for ordinary audio, and the error when ffmpeg is genuinely absent.
"""
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.subtitle.app.backend import engine  # noqa: E402

STUB = """#!/bin/sh
# A stand-in for the bundled ffmpeg: records its arguments and writes a WAV at the output path.
echo "$@" > "$STUB_LOG"
for last in "$@"; do :; done
printf 'RIFF____WAVEfmt ' > "$last"
"""


class VideoAudioExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.tools = self.root / "bin"
        self.tools.mkdir()
        self.log = self.root / "ffmpeg-args.txt"
        self.ffmpeg = self.tools / "ffmpeg"
        self.ffmpeg.write_text(STUB)
        self.ffmpeg.chmod(0o755)
        self._old_tools = os.environ.get("STUDIOHUB_TOOLS_DIR")
        os.environ["STUDIOHUB_TOOLS_DIR"] = str(self.tools)
        os.environ["STUB_LOG"] = str(self.log)

    def tearDown(self):
        if self._old_tools is None:
            os.environ.pop("STUDIOHUB_TOOLS_DIR", None)
        else:
            os.environ["STUDIOHUB_TOOLS_DIR"] = self._old_tools
        self.temporary.cleanup()

    def test_an_audio_file_is_handed_straight_to_the_decoder(self):
        clip = self.root / "voice.m4a"
        clip.write_bytes(b"audio")
        with engine.decodable_audio(clip) as media:
            self.assertEqual(media, clip, "audio must not be re-encoded on the way in")
        self.assertFalse(self.log.exists(), "no extraction for a file the decoder can read")

    def test_a_video_file_yields_the_extracted_audio_track(self):
        video = self.root / "screen recording.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        with engine.decodable_audio(video) as media:
            self.assertNotEqual(media, video)
            self.assertEqual(media.suffix, ".wav")
            self.assertTrue(media.is_file(), "the decoder must be given a file that exists")
            extracted = media
        arguments = self.log.read_text()
        self.assertIn("-vn", arguments, "the video stream must be dropped, not copied")
        self.assertIn(str(video), arguments)
        self.assertFalse(extracted.exists(), "the temporary audio must not be left behind")

    def test_mov_is_extracted_too(self):
        video = self.root / "clip.mov"
        video.write_bytes(b"\x00\x00\x00\x14ftypqt")
        with engine.decodable_audio(video) as media:
            self.assertEqual(media.suffix, ".wav")

    def test_a_missing_ffmpeg_names_the_reason(self):
        # No bundled tools *and* nothing on PATH: the real absence, not the case where this Mac
        # happens to have its own ffmpeg (which is used, and is the reason to fall back to PATH).
        os.environ["STUDIOHUB_TOOLS_DIR"] = str(self.root / "nothing-here")
        path = os.environ.get("PATH")
        os.environ["PATH"] = str(self.root / "empty-path")
        try:
            video = self.root / "clip.mp4"
            video.write_bytes(b"\x00")
            with self.assertRaises(RuntimeError) as caught:
                with engine.decodable_audio(video):
                    pass
        finally:
            if path is not None:
                os.environ["PATH"] = path
        self.assertIn("ffmpeg", str(caught.exception))
        self.assertIn("clip.mp4", str(caught.exception))

    def test_the_formats_the_app_offers_are_the_ones_this_handles(self):
        """The upload list and the extraction list must not drift apart again."""
        from components.subtitle.app.backend import manager
        videos = {".mp4", ".mov"}
        self.assertTrue(videos <= manager.ALLOWED_EXTENSIONS)
        self.assertTrue(videos <= engine.VIDEO_CONTAINER_EXTENSIONS)


if __name__ == "__main__":
    unittest.main()
