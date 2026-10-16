"""Nothing the Hub retires is permanently deleted (owner request 2026-10-15).

`output_storage.retire` is the one mover for retired files and folders that are not indexed outputs:
a same-volume rename into `<data>/trash/<UTC date>/<label>-<id>/`, or - when that fails - the item is
left where it is. The transcription spool's automatic cleanup goes through it.
"""
from __future__ import annotations

import errno
import os
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub import output_storage  # noqa: E402
from components.hub.backend import ledger, transcription_jobs  # noqa: E402


def _forbidden(*_a, **_k):
    raise AssertionError("a retirement fell back to a delete")


class Fixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(os.path.realpath(temp.name))
        patcher = mock.patch.object(ledger, "DATA_DIR", self.root / "hub")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.trash = self.root / "hub" / "trash" / time.strftime("%Y-%m-%d", time.gmtime())

    @staticmethod
    def no_deletes(*, cross_volume: bool = False) -> ExitStack:
        """Every delete raises; with `cross_volume`, every rename fails as it does across volumes."""
        import shutil
        stack = ExitStack()
        for name, target in (("rmtree", shutil), ("unlink", os), ("remove", os)):
            stack.enter_context(mock.patch.object(target, name, _forbidden))
        if cross_volume:
            stack.enter_context(mock.patch.object(os, "rename", side_effect=OSError(errno.EXDEV, "Cross-device link")))
        return stack


class RetireTests(Fixture):
    def test_a_folder_moves_into_the_dated_trash_folder_with_its_contents(self):
        folder = self.root / "Updates" / "k1"
        folder.mkdir(parents=True)
        (folder / "a.dmg").write_bytes(b"dmg")
        self.assertTrue(output_storage.retire(folder, "stale-updates"))
        self.assertFalse(folder.exists())
        moved = list(self.trash.glob("stale-updates-*/k1/a.dmg"))
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].read_bytes(), b"dmg")

    def test_the_same_name_twice_never_overwrites(self):
        for content in (b"one", b"two"):
            item = self.root / "same.bin"
            item.write_bytes(content)
            self.assertTrue(output_storage.retire(item, "x"))
        self.assertEqual(sorted(p.read_bytes() for p in self.trash.glob("x-*/same.bin")), [b"one", b"two"])

    def test_a_failed_move_leaves_the_item_and_no_empty_folder_and_never_deletes(self):
        item = self.root / "elsewhere" / "big.bin"
        item.parent.mkdir()
        item.write_bytes(b"keep me")
        with self.no_deletes(cross_volume=True):
            self.assertFalse(output_storage.retire(item, "x"))
        self.assertEqual(item.read_bytes(), b"keep me")
        self.assertEqual(list(self.trash.glob("x-*")), [], "an empty Trash Folder entry was left behind")


    def test_a_taken_leaf_name_is_never_reused(self):
        item = self.root / "same.bin"
        item.write_bytes(b"new")
        taken = self.trash / "x-aaaaaaaa"
        taken.mkdir(parents=True)
        (taken / "same.bin").write_bytes(b"old")
        names = iter([bytes.fromhex("aaaaaaaa"), bytes.fromhex("bbbbbbbb")])
        with mock.patch.object(os, "urandom", lambda _n: next(names)):
            self.assertTrue(output_storage.retire(item, "x"))
        self.assertEqual((taken / "same.bin").read_bytes(), b"old", "a colliding name overwrote an earlier item")
        self.assertEqual((self.trash / "x-bbbbbbbb" / "same.bin").read_bytes(), b"new")


class ReclaimAckedTests(Fixture):
    def test_a_failed_move_keeps_the_output_present(self):
        import hashlib
        out = self.root / "outputs"
        out.mkdir()
        path = out / "a.png"
        path.write_bytes(b"12345")
        self.assertTrue(output_storage.register_finalized_output(
            machine="m", studio="image", job_id="j", managed_output_roots=[str(out)], output_path=str(path),
            generated_at=1, batch_id="b1", item_index=0, bytes=5, sha256=hashlib.sha256(b"12345").hexdigest()))
        output_storage.mark_receipt_acked(batch_id="b1", item_index=0)
        with self.no_deletes(cross_volume=True):
            _freed, files = output_storage.reclaim_acked(machine="m", min_age_seconds=0, done=lambda: False)
        self.assertEqual(files, 0)
        self.assertEqual(path.read_bytes(), b"12345")
        with ledger._conn() as conn:
            self.assertEqual(conn.execute("SELECT present FROM output_files").fetchone()["present"], 1)


class ReleaseGarbageTests(Fixture):
    def test_old_manifests_and_unreferenced_blobs_are_moved_not_deleted(self):
        from components.hub.backend import app_release
        manifests, blobs = self.root / "manifests", self.root / "blobs"
        for name, age in (("m-new1", 0), ("m-new2", 10), ("m-old", 100)):
            (manifests / name).mkdir(parents=True)
            os.utime(manifests / name, (time.time() - age, time.time() - age))
        blobs.mkdir()
        (blobs / "deadbeef").write_bytes(b"blob")
        with mock.patch.object(app_release, "RELEASE_DIR", self.root), \
                mock.patch.object(app_release, "blob_dir", lambda: blobs), \
                mock.patch.object(app_release.fm, "load_dir", side_effect=app_release.fm.ManifestError("x")), \
                self.no_deletes():
            app_release.collect_garbage()
        self.assertFalse((manifests / "m-old").exists())
        self.assertEqual(len(list(self.trash.glob("release-manifest-*/m-old"))), 1)
        self.assertEqual(len(list(self.trash.glob("release-blob-*/deadbeef"))), 1)


class StagedVoiceCopyTests(Fixture):
    def test_an_expired_copy_is_moved_not_deleted(self):
        import json
        from components.hub.backend import execution_assets
        staged = self.root / "assets"
        folder = staged / "expired"
        folder.mkdir(parents=True)
        (folder / "metadata.json").write_text(json.dumps({"asset_id": "expired", "created_at": 1, "expires_at": 2}))
        with mock.patch.object(execution_assets, "ROOT", staged), self.no_deletes():
            self.assertEqual(execution_assets.cleanup_expired(), 1)
        self.assertFalse(folder.exists())
        self.assertEqual(len(list(self.trash.glob("staged-voice-copy-*/expired/metadata.json"))), 1)


class TranscriptionSpoolTests(Fixture):
    def setUp(self):
        super().setUp()
        self.spool = self.root / "hub" / "transcription_jobs"
        self.batch = {"id": "b1", "updated_at": 1, "finished_at": 1,
                      "items": [{"state": "done", "input_path": "x", "artifact_path": "y"}]}
        (self.spool / "b1").mkdir(parents=True)
        (self.spool / "b1" / "chapter.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
        self.saved = []
        for target, name, value in ((transcription_jobs, "ROOT", self.spool),
                                    (transcription_jobs, "_load_rows", lambda *a: [self.batch]),
                                    (transcription_jobs, "_save", self.saved.append),
                                    (transcription_jobs, "settings", lambda: {"retention_days": 3})):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_an_expired_batch_moves_to_the_trash_folder(self):
        with self.no_deletes():
            result = transcription_jobs.cleanup()
        self.assertEqual(result["cleaned"], 1)
        self.assertFalse((self.spool / "b1").exists())
        self.assertEqual(len(list(self.trash.glob("transcription-*/b1/chapter.srt"))), 1)
        self.assertTrue(self.saved and self.saved[0]["storage_cleaned_at"])

    def test_a_batch_that_cannot_move_is_kept_and_not_marked_cleaned(self):
        with self.no_deletes(cross_volume=True):
            result = transcription_jobs.cleanup()
        self.assertEqual(result, {"cleaned": 0, "reclaimed_bytes": 0})
        self.assertTrue((self.spool / "b1" / "chapter.srt").is_file())
        self.assertEqual(self.saved, [], "a batch whose files are still on disk was marked cleaned")


if __name__ == "__main__":
    unittest.main()
