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
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub.backend import execution_assets, job_storage, transcription_jobs  # noqa: E402


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

    def test_a_refresh_and_sweep_cannot_delete_the_refreshed_copy(self):
        def stage():
            return execution_assets.stage_local_voice_reference(
                audio_bytes=b"RIFF____WAVE", filename="reference.wav")

        asset = stage()
        folder = self.root / asset["id"]
        metadata = folder / "metadata.json"
        metadata.write_text(json.dumps({**asset, "expires_at": 1}))
        refreshing, release, sweeping = threading.Event(), threading.Event(), threading.Event()
        write = execution_assets._atomic_json

        def paused_write(path, value):
            refreshing.set()
            if not release.wait(2):
                raise TimeoutError("refresh was not released")
            write(path, value)

        def sweep():
            sweeping.set()
            return execution_assets.cleanup_expired()

        with ThreadPoolExecutor(max_workers=2) as workers:
            with mock.patch.object(execution_assets, "_atomic_json", paused_write):
                refresh = workers.submit(stage)
                try:
                    self.assertTrue(refreshing.wait(2))
                    cleanup = workers.submit(sweep)
                    self.assertTrue(sweeping.wait(2))
                    try:
                        cleanup.result(timeout=0.1)
                    except TimeoutError:
                        pass  # Cleanup may correctly wait for the refresh transaction.
                finally:
                    release.set()
                refresh.result(timeout=2)
                self.assertEqual(cleanup.result(timeout=2), 0)
        self.assertTrue(folder.is_dir())
        execution_assets.resolve_voice_reference(asset["id"])

    def test_a_symlinked_store_does_not_purge_its_target(self):
        expired = self._asset("expired", age_seconds=3600)
        linked = self.root / "linked"
        linked.symlink_to(self.root, target_is_directory=True)
        with mock.patch.object(execution_assets, "ROOT", linked):
            self.assertEqual(execution_assets.cleanup_expired(), 0)
        self.assertTrue(expired.exists())

    def test_a_reader_copies_bytes_before_expiry_cleanup(self):
        audio = b"RIFF____WAVE"
        asset = execution_assets.stage_local_voice_reference(audio_bytes=audio, filename="ref.wav")
        reading, release = threading.Event(), threading.Event()
        read_bytes = Path.read_bytes

        def paused_read(path):
            if path.name == 'reference.wav':
                reading.set()
                if not release.wait(2):
                    raise TimeoutError('reader was not released')
            return read_bytes(path)

        with ThreadPoolExecutor(max_workers=2) as workers, mock.patch.object(Path, 'read_bytes', paused_read):
            reader = workers.submit(execution_assets.read_voice_reference, asset['id'])
            try:
                self.assertTrue(reading.wait(2))
                cleanup = workers.submit(execution_assets.cleanup_expired, asset['expires_at'] + 1)
                try:
                    cleanup.result(timeout=0.1)
                except TimeoutError:
                    pass
            finally:
                release.set()
            metadata, payload = reader.result(timeout=2)
            self.assertEqual(payload, audio)
            self.assertEqual(metadata['id'], asset['id'])
            self.assertEqual(cleanup.result(timeout=2), 1)

    def test_an_invalid_deadline_does_not_block_other_expired_copies(self):
        invalid = self._asset("invalid", age_seconds=0)
        (invalid / "metadata.json").write_text(json.dumps({"expires_at": "later"}))
        expired = self._asset("expired", age_seconds=3600)
        self.assertEqual(execution_assets.cleanup_expired(), 1)
        self.assertTrue(invalid.exists())
        self.assertFalse(expired.exists())

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
        """Execute the sweep: a textual import can be in the wrong function."""
        expired = self._asset("expired", age_seconds=3600)
        live = self._asset("live", age_seconds=0)
        with mock.patch.object(job_storage, "_read", return_value={
            "enabled": True, "max_bytes": 1000,
        }), mock.patch.object(transcription_jobs, "ROOT", self.root / "transcription"), \
                mock.patch.object(transcription_jobs, "cleanup", return_value={
                    "cleaned": 0, "reclaimed_bytes": 0,
                }):
            result = job_storage.enforce_budget()
        self.assertEqual(result["cleared"], 1)
        self.assertFalse(expired.exists())
        self.assertTrue(live.exists())


if __name__ == "__main__":
    unittest.main()
