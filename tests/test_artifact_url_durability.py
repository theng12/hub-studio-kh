"""A finished artifact must still be readable after the Hub restarts.

This is a durability contract, not a formatting one. Worker ports are assigned at start, so an
artifact URL that contains the port names a port that belongs to nothing after the next restart.

Measured on 2026-09-18: a Voice job completed at 127.0.0.1:65461, the Hub restarted, its worker
came back on 127.0.0.1:58107, and `GET /api/hub/jobs/<batch>/items/0/artifact` answered

    502 {"detail":"worker artifact reference is invalid"}

for a file that was on disk the whole time. `job_artifact` requires the stored URL to share the
worker's current origin, which a stored absolute URL can never do again. Every Voice and Image
artifact became unreadable after a restart - the operator's own history was reachable but not
openable.

The fix has two halves, and both are tested here: a job item stores the worker-relative path, and a
batch read from the ledger has any absolute URL normalised to its path so batches written before the
fix keep working.
"""
import json
import os
import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The real package, so the module's relative imports resolve the way they do in the Hub.
from components.hub.backend import ledger  # noqa: E402


class RelativeWorkerUrlTests(unittest.TestCase):
    def setUp(self):
        self.ledger = ledger

    def test_a_worker_origin_is_stripped_and_the_path_kept(self):
        value = "http://127.0.0.1:65461/api/generate/jobs/75de860a98bd/audio"
        self.assertEqual(self.ledger._relative_worker_url(value),
                         "/api/generate/jobs/75de860a98bd/audio")

    def test_an_already_relative_path_is_untouched(self):
        value = "/api/generate/jobs/75de860a98bd/audio"
        self.assertEqual(self.ledger._relative_worker_url(value), value)

    def test_nothing_and_nonsense_are_returned_as_they_are(self):
        for value in (None, "", 0, {}, "not a url"):
            self.assertEqual(self.ledger._relative_worker_url(value), value)

    def test_a_url_with_no_path_yields_the_original(self):
        self.assertEqual(self.ledger._relative_worker_url("http://127.0.0.1:9"), "http://127.0.0.1:9")


class StoredBatchTests(unittest.TestCase):
    """A batch written by the previous build must read back with a usable path."""

    def setUp(self):
        # DB_FILE is bound at import time, so the environment alone is not enough - the module
        # attribute is redirected too, the same way the fleet roster tests do it.
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.ledger = ledger
        self._old_db = ledger.DB_FILE
        ledger.DB_FILE = self.root / "hub.db"

    def tearDown(self):
        self.ledger.DB_FILE = self._old_db
        self.temporary.cleanup()

    def _store_old_shape_batch(self, batch_id: str, absolute: str):
        """Write a batch exactly as the pre-fix code stored it."""
        batch = {
            "id": batch_id, "created_at": 1.0, "modality": "voice",
            "model": "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit",
            "items": [{"index": 0, "state": "done", "studio": "voice",
                       "studio_job_id": "75de860a98bd",
                       "worker_artifact_url": absolute, "artifact_url": absolute}],
        }
        self.ledger.save_batch(batch)
        return batch

    def _db(self):
        return self.root / "hub.db"

    def test_a_stale_absolute_url_reads_back_as_a_path(self):
        absolute = "http://127.0.0.1:65461/api/generate/jobs/75de860a98bd/audio"
        self._store_old_shape_batch("old-batch", absolute)
        loaded = self.ledger.load_batch("old-batch")
        item = loaded["items"][0]
        self.assertEqual(item["worker_artifact_url"], "/api/generate/jobs/75de860a98bd/audio")
        self.assertEqual(item["artifact_url"], "/api/generate/jobs/75de860a98bd/audio")

    def test_the_stored_row_is_not_rewritten_by_reading_it(self):
        """Normalisation happens on read, so a rollback would still find the original."""
        absolute = "http://127.0.0.1:65461/api/generate/jobs/75de860a98bd/audio"
        self._store_old_shape_batch("old-batch", absolute)
        self.ledger.load_batch("old-batch")
        with sqlite3.connect(self._db()) as conn:
            payload = conn.execute(
                "SELECT payload FROM batches WHERE id = 'old-batch'").fetchone()[0]
        self.assertEqual(json.loads(payload)["items"][0]["worker_artifact_url"], absolute)

    def test_an_unknown_batch_is_still_none(self):
        self.assertIsNone(self.ledger.load_batch("never-existed"))


class RouteOriginTests(unittest.TestCase):
    """The route must judge a stored path against the worker's origin *now*."""

    SOURCE = (ROOT / "components" / "hub" / "standalone.py").read_text()

    def test_an_absolute_url_is_only_accepted_at_the_current_worker(self):
        body = self.SOURCE[self.SOURCE.index("async def job_artifact"):]
        body = body[:body.index("async def cancel_job")]
        # A path is trusted; an absolute URL must match the worker's current origin AND the path
        # this Hub would have written itself.
        self.assertIn("if not parsed.scheme and not parsed.netloc:", body)
        self.assertIn("parsed.netloc == worker_origin.netloc", body)
        self.assertIn("parsed.path == expected", body)

    def test_the_dispatch_no_longer_stores_a_worker_port(self):
        broker = (ROOT / "components" / "hub" / "backend" / "broker.py").read_text()
        self.assertIn('item["worker_artifact_url"] = job.get("output_url")', broker)
        self.assertIn('item["artifact_url"] = job.get("output_url")', broker)
        # The absolute form still exists for the asset record, which fetches from it.
        self.assertIn("artifact_url=worker_url, batch_id=b[\"id\"]", broker)


if __name__ == "__main__":
    unittest.main()
