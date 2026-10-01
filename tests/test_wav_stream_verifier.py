"""Voice artifacts are verified in one streaming pass: same answers as `wav_metadata`, no whole-file buffer."""
import asyncio
import hashlib
import io
from pathlib import Path
import sys
import unittest
import wave

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub.backend import artifact_metadata as am, broker  # noqa: E402


def wav(frames=800, channels=1, rate=8000, width=2) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(width)
        audio.setframerate(rate)
        audio.writeframes(b"\x01" * frames * channels * width)
    return out.getvalue()


def streamed(data: bytes, size: int) -> dict:
    verifier = am.WavStreamVerifier()
    for start in range(0, len(data), size):
        verifier.feed(data[start:start + size])
    return verifier.finish()


class SameAnswersAsWholeFile(unittest.TestCase):
    def test_every_chunking_gives_wav_metadata_exactly(self):
        for data in (wav(), wav(channels=2, rate=24000), wav(width=3, frames=5)):
            expected = am.wav_metadata(data)
            for size in (1, 7, 44, 45, 1000, len(data)):
                self.assertEqual(streamed(data, size), expected, size)

    def test_metadata_chunks_before_the_audio_are_skipped(self):
        data = wav()
        listing = b"LIST" + (5).to_bytes(4, "little") + b"abcde" + b"\0"   # odd size, padded
        padded = data[:36] + listing + data[36:]
        padded = padded[:4] + (len(padded) - 8).to_bytes(4, "little") + padded[8:]
        self.assertEqual(streamed(padded, 9)["audio_duration_s"], am.wav_metadata(data)["audio_duration_s"])

    def test_the_refusals_match(self):
        good = wav()
        cases = {
            "not RIFF": b"x" * 100,
            "truncated": good[:-10],
            "truncated header": good[:30],
            "three channels": wav(channels=3),
            "no frames": wav(frames=0),
        }
        for name, data in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    am.wav_metadata(data)
                with self.assertRaises(ValueError):
                    streamed(data, 13)


class RealStreamingDownload(unittest.TestCase):
    STUDIO = {"id": "voice", "modality": "voice", "host": "127.0.0.1", "port": 1, "machine": "local",
              "local": True, "hub_token": "t", "studio_token": "t", "base_url": "http://127.0.0.1:1"}

    def run_case(self, data: bytes, size=None, sha=None):
        seen = {"yielded": 0}

        async def body():
            for start in range(0, len(data), 4096):
                seen["yielded"] += 1
                yield data[start:start + 4096]

        def handler(request):
            return httpx.Response(200, content=body())

        item: dict = {}

        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                await broker._cache_voice_artifact_metadata(
                    client, item, self.STUDIO, "http://127.0.0.1:1/api/generate/jobs/j/audio", size, sha)
        asyncio.run(go())
        return item, seen

    def test_one_streamed_download_verifies_and_records(self):
        data = wav(frames=20000)
        item, seen = self.run_case(data, len(data), hashlib.sha256(data).hexdigest())
        self.assertGreater(seen["yielded"], 5, "the body was not streamed in pieces")
        self.assertNotIn("artifact_metadata_error", item)
        self.assertEqual(item["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(item["bytes"], len(data))

    def test_a_hash_or_size_mismatch_fails_verification(self):
        data = wav()
        item, _ = self.run_case(data, len(data), "0" * 64)
        self.assertIn("checksum", item["artifact_metadata_error"])
        item, _ = self.run_case(data, len(data) + 1, None)
        self.assertIn("byte count", item["artifact_metadata_error"])
        item, _ = self.run_case(data[:-10], None, None)
        self.assertIn("truncated", item["artifact_metadata_error"])


if __name__ == "__main__":
    unittest.main()
