"""An oversized Subtitle upload must be refused as it arrives, not after it is in memory.

`await file.read()` returned the entire body before the size was checked against this worker's 2 GiB
per-file limit, so an oversized upload was buffered in the worker process - on a Mac that is also
holding a model in unified memory, that is an out-of-memory kill rather than a clean 413. The read
now stops at the first byte past the limit, which is the property these checks pin: not just that it
refuses, but that it stops early.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.subtitle.app.backend import main as subtitle_main  # noqa: E402


class _Upload:
    """A body of `chunks` pieces, recording how much of it was ever read."""

    def __init__(self, chunks: int, size: int = 4):
        self.remaining = chunks
        self.size = size
        self.reads = 0

    async def read(self, count: int) -> bytes:
        self.reads += 1
        if self.remaining <= 0:
            return b""
        self.remaining -= 1
        return b"x" * self.size


class UploadLimitTests(unittest.TestCase):
    def setUp(self):
        # One chunk per read, so the byte budget maps onto read calls.
        self._old_chunk = subtitle_main.UPLOAD_CHUNK_BYTES
        subtitle_main.UPLOAD_CHUNK_BYTES = 4

    def tearDown(self):
        subtitle_main.UPLOAD_CHUNK_BYTES = self._old_chunk

    def test_a_body_within_the_limit_is_returned_whole(self):
        upload = _Upload(chunks=3)
        data = asyncio.run(subtitle_main.read_upload_limited(upload, 12))
        self.assertEqual(data, b"x" * 12)

    def test_an_oversized_body_is_refused(self):
        upload = _Upload(chunks=1000)
        with self.assertRaises(Exception) as caught:
            asyncio.run(subtitle_main.read_upload_limited(upload, 12))
        self.assertEqual(getattr(caught.exception, "status_code", None), 413)

    def test_the_refusal_stops_reading_instead_of_buffering_everything(self):
        upload = _Upload(chunks=1000)
        with self.assertRaises(Exception):
            asyncio.run(subtitle_main.read_upload_limited(upload, 12))
        self.assertLessEqual(upload.reads, 4,
                             "the limit must be applied as the body arrives, not to the whole body")
        self.assertGreater(upload.remaining, 900, "the rest of the upload must not be read")

    def test_an_empty_body_is_not_an_error(self):
        self.assertEqual(asyncio.run(subtitle_main.read_upload_limited(_Upload(chunks=0), 12)), b"")

    def test_both_upload_handlers_use_the_limited_reader(self):
        source = (Path(__file__).resolve().parents[1]
                  / "components/subtitle/app/backend/main.py").read_text()
        self.assertNotIn("data = await file.read()", source,
                         "a bare read buffers the whole body before its limit is applied")
        self.assertEqual(source.count("read_upload_limited(file, MAX_INPUT_BYTES)"), 2)


if __name__ == "__main__":
    unittest.main()
