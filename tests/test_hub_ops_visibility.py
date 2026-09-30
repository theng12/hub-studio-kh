"""What an operator could not see, and the Hub now says (0.6.114).

Three facts the Hub already had and the app had no way to read: how far a model download is and why
it failed, what the next storage cleanup would delete, and what a queued job is actually waiting for.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.hub import model_downloads  # noqa: E402


class DownloadProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hub-download-progress-"))
        patcher = mock.patch.object(model_downloads, "data_dir", lambda: self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, **fields):
        run_dir = self.tmp / "model-download-runs" / "r1"
        run_dir.mkdir(parents=True)
        staging = self.tmp / "staging"
        staging.mkdir()
        (staging / "model.bin.part").write_bytes(b"x" * 7)
        (staging / "sub").mkdir()
        (staging / "sub" / "a.bin").write_bytes(b"y" * 5)
        return {"id": "r1", "package_id": "p", "state": "running", "total_bytes": 100,
                "staging": str(staging), **fields}

    def test_a_running_download_reports_the_bytes_on_disk_and_hides_the_path(self):
        public = model_downloads._public(self._run())
        self.assertEqual(public["bytes_done"], 12, "progress is what the staging folder holds, .part included")
        self.assertEqual(public["total_bytes"], 100)
        self.assertNotIn("staging", public, "a library path is not part of the API")

    def test_a_failed_download_carries_the_end_of_its_output(self):
        run = self._run(state="error")
        (self.tmp / "model-download-runs" / "r1" / "output.log").write_text("noise\n" * 200 + "PackageError: bad hash\n")
        public = model_downloads._public(run)
        tail = public["error_tail"]
        self.assertIsNotNone(tail, "a failed run must say why")
        self.assertTrue(tail.endswith("PackageError: bad hash"), tail)
        self.assertLessEqual(len(tail), 300)
        self.assertNotIn("bytes_done", public, "a finished run has no progress to report")

    def test_a_failed_download_with_no_log_says_nothing_rather_than_inventing(self):
        self.assertIsNone(model_downloads._public(self._run(state="interrupted"))["error_tail"])

    def test_start_records_the_manifest_total_and_where_the_download_stages(self):
        library = self.tmp / "library"
        library.mkdir()
        digest = "0" * 64
        manifest = {"schema_version": 1, "package_id": "fixture", "model_id": "f/m", "revision": "a" * 40,
                    "engine": "voice-mlx", "minimum_macos": "0.0",
                    "files": [{"path": "a", "size": 40, "sha256": digest, "urls": ["https://example.invalid/a"]},
                              {"path": "b", "size": 60, "sha256": digest, "urls": ["https://example.invalid/b"]}]}
        with mock.patch.object(model_downloads.subprocess, "Popen", return_value=mock.Mock(pid=1)):
            run = model_downloads.start(package_id="fixture", manifest=manifest,
                                        catalog={"fixture": manifest}, library=library, sources=None)
        self.assertEqual(run["total_bytes"], 100)
        self.assertEqual(run["bytes_done"], 0)
        self.assertNotIn("staging", run)
        stored = model_downloads._read()["runs"][run["id"]]
        self.assertIn(".studiohub/staging/fixture-", stored["staging"])
        model_downloads._processes.pop(run["id"], None)


PROOF = r'''
import hashlib, os, tempfile
from pathlib import Path
root = Path(tempfile.mkdtemp(prefix="storage-would-delete-")).resolve()
os.environ["STUDIOHUB_DATA_DIR"] = str(root / "hub")
(root / "hub").mkdir()
folder = root / "out"; folder.mkdir()
from components.hub import output_storage
def add(name, size, generated, job, protected=False):
    data = b"x" * size
    path = folder / name; path.write_bytes(data)
    assert output_storage.register_finalized_output(
        machine="m", studio="image", job_id=job, managed_output_roots=[str(folder)], output_path=str(path),
        generated_at=generated, batch_id=job, item_index=0, bytes=size, sha256=hashlib.sha256(data).hexdigest())
    if not protected:
        output_storage.mark_receipt_acked(batch_id=job, item_index=0)
day = 86400
now = 100 * day
add("old.png", 10, 10 * day, "old")           # past retention
add("mid.png", 20, 90 * day, "mid")           # inside retention
add("new.png", 30, 99 * day, "new")           # inside retention
add("held.png", 40, 1 * day, "held", protected=True)   # never acknowledged: never deleted

# Retention only: the old file is the only one past 30 days that may go.
snap = output_storage.preview(machine="m", max_bytes=10_000, retention_days=30, now=now)
assert snap["would_delete_bytes"] == 10, snap
# Over the size limit as well: oldest eligible first until the total fits (100 -> 60 needs 40, so old+mid = 30... then new).
snap = output_storage.preview(machine="m", max_bytes=60, retention_days=30, now=now)
assert snap["would_delete_bytes"] == 60, snap      # old 10 + mid 20 + new 30 brings 100 to 40
# Cleanup off: nothing is eligible.
snap = output_storage.preview(machine="m", max_bytes=1, retention_days=1, enabled=False, now=now)
assert snap["would_delete_bytes"] == 0, snap
# The dry run is the sweep's own arithmetic: what preview said is what enforce freed.
predicted = output_storage.preview(machine="m", max_bytes=60, retention_days=30, now=now)["would_delete_bytes"]
freed = output_storage.enforce(machine="m", max_bytes=60, retention_days=30, now=now)["freed_bytes"]
assert predicted == freed == 60, (predicted, freed)
assert (folder / "held.png").exists(), "an unacknowledged output must survive"
'''


class StoragePreviewTests(unittest.TestCase):
    def test_preview_predicts_what_cleanup_would_delete(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        result = subprocess.run([sys.executable, "-B", "-c", PROOF], env=env, text=True,
                                capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class QueuedReasonTests(unittest.TestCase):
    def test_the_uncached_model_note_names_an_action_that_exists(self):
        source = (ROOT / "components/hub/backend/broker.py").read_text()
        self.assertNotIn("broadcast the download", source,
                         "no such action exists; the note must point at something the operator can do")
        self.assertIn("download it on that Mac, or pin another", source)


if __name__ == "__main__":
    unittest.main()
