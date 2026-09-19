"""Staged voice-reference copies must actually be deleted when their TTL passes.

`execution_assets.cleanup_expired` has existed since the staging feature did, and nothing ever called
it: expiry only blocked *reuse* (`resolve_voice_reference` refuses an expired asset), so every
duplicate clip the Hub made to hand a worker stayed on disk for the life of the Mac. The owner's rule
is that these duplicates are cleaned up like everything else - the voices themselves are permanent,
and a copy of one can always be made again.
"""
import json
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub.backend import execution_assets  # noqa: E402


class StagedAssetPurgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self._old_root = execution_assets.ROOT
        execution_assets.ROOT = self.root

    def tearDown(self):
        execution_assets.ROOT = self._old_root
        self.temporary.cleanup()

    def _asset(self, name: str, *, age_seconds: float) -> Path:
        folder = self.root / name
        folder.mkdir(parents=True)
        (folder / "reference.wav").write_bytes(b"RIFF____WAVE")
        (folder / "metadata.json").write_text(json.dumps({
            "asset_id": name, "created_at": time.time() - age_seconds,
            "expires_at": time.time() - age_seconds + 60,
        }))
        return folder

    def test_an_expired_copy_is_deleted(self):
        expired = self._asset("expired", age_seconds=3600)
        removed = execution_assets.cleanup_expired()
        self.assertEqual(removed, 1)
        self.assertFalse(expired.exists(), "the duplicate clip must not stay on disk")

    def test_a_live_copy_is_kept(self):
        live = self._asset("live", age_seconds=0)
        self.assertEqual(execution_assets.cleanup_expired(), 0)
        self.assertTrue(live.exists())

    def test_a_copy_with_no_readable_deadline_is_cleared_out(self):
        """Metadata that cannot say when it expires describes an asset nothing can use.

        Staging always writes `expires_at`, so a folder without one is a leftover rather than a
        deliberate permanent copy - and `resolve_voice_reference` would not hand it to a worker. It
        is swept, which is the behaviour the store has always had; this pins it so the sweep cannot
        quietly start keeping junk."""
        folder = self.root / "no-deadline"
        folder.mkdir(parents=True)
        (folder / "metadata.json").write_text(json.dumps({"asset_id": "no-deadline"}))
        self.assertEqual(execution_assets.cleanup_expired(), 1)
        self.assertFalse(folder.exists())

    def test_the_storage_sweep_purges_them(self):
        """The wiring, which is what was missing: a function nothing calls is not a policy."""
        source = (Path(__file__).resolve().parents[1]
                  / "components/hub/backend/job_storage.py").read_text()
        self.assertIn("execution_assets.cleanup_expired()", source,
                      "the budget sweep must purge expired staged copies")
        self.assertIn("from . import execution_assets, transcription_jobs", source,
                      "and it has to import it to call it")


if __name__ == "__main__":
    unittest.main()
