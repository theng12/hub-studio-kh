"""A model import joins a queue; it never fails because another one is running.

Owner, 2026-11-24: *"the app when copying or installing models doesn't queue up the work but fails
instead … because it's not queuing properly."* The library lock stays (one writer per library), and a
second request now waits in line with a position instead of being refused — and the refusal sentence
can never reach a row the person reads, in the running list or under "Finished today".

The child process is stubbed, so this file is about the queue's own bookkeeping: admission, position,
self-starting, pause on a lost drive, cancel, and the requeue on the lock.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

GB = 1_000_000_000


class ModelDownloadQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.data, self.library = root / "data", root / "models"
        self.data.mkdir()
        self.library.mkdir()
        patcher = mock.patch.dict(os.environ, {"STUDIOHUB_DATA_DIR": str(self.data)})
        patcher.start()
        self.addCleanup(patcher.stop)
        from components.hub import model_downloads
        self.module = model_downloads
        patcher = mock.patch.object(model_downloads, "_path",
                                    lambda: self.data / "model_downloads.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        # Stub the child: a launch marks the run running and is remembered, nothing is spawned.
        self.launched: list[str] = []

        class FakeProcess:
            pid = 4242

            def poll(self):
                return None

        def launch(run):
            self.launched.append(run["id"])
            run.update(state="running", started_at=time.time(), pid=4242)
            model_downloads._processes[run["id"]] = FakeProcess()

        patcher = mock.patch.object(model_downloads, "_launch", launch)
        patcher.start()
        self.addCleanup(patcher.stop)
        model_downloads._processes.clear()
        self.addCleanup(model_downloads._processes.clear)

    def manifest(self, package_id: str) -> dict:
        return {"schema_version": 1, "package_id": package_id, "model_id": f"org/{package_id}",
                "revision": "a" * 40, "engine": "voice-mlx", "minimum_macos": "14.0",
                "requires_packages": [],
                "files": [{"path": "w.bin", "size": GB // 1000, "sha256": "0" * 64,
                           "urls": ["https://example.invalid/w"]}]}

    def start(self, package_id: str, **extra):
        manifest = self.manifest(package_id)
        return self.module.start(package_id=package_id, manifest=manifest,
                                 catalog={package_id: manifest}, library=self.library,
                                 sources={}, **extra)

    def test_a_second_request_queues_with_a_position_instead_of_failing(self):
        first = self.start("one")
        self.assertEqual(first["state"], "running")
        second = self.start("two")
        third = self.start("three")
        self.assertEqual(second["state"], "queued")
        self.assertEqual(second["position"], 1)
        self.assertEqual(third["state"], "queued")
        self.assertEqual(third["position"], 2)
        # Only the first one is running: the second was not started and did not error.
        self.assertEqual(self.launched, [first["id"]])
        self.assertNotIn("error", second)

    def test_the_next_one_starts_by_itself_when_the_first_finishes(self):
        first = self.start("one")
        second = self.start("two")
        third = self.start("three")
        store = self.module._read()
        store["runs"][first["id"]].update(state="done", finished_at=time.time())
        self.module._write(store)
        self.module.reconcile()
        self.assertEqual(self.launched, [first["id"], second["id"]])
        self.assertEqual(self.module.status(second["id"])["state"], "running")
        self.assertEqual(self.module.status(third["id"])["position"], 1)

    def test_a_missing_drive_pauses_and_resume_continues(self):
        missing = self.data / "KH-STUDIO-GONE" / "model-library"
        paused = self.start("one", drive=missing)
        self.assertEqual(paused["state"], "paused")
        self.assertIn("not mounted", paused["reason"])
        self.assertEqual(self.launched, [])
        missing.mkdir(parents=True)
        resumed = self.module.resume(paused["id"])
        self.assertEqual(resumed["state"], "running")
        self.assertEqual(self.launched, [paused["id"]])

    def test_cancelling_a_waiting_run_starts_the_one_behind_it(self):
        first = self.start("one")
        second = self.start("two")
        third = self.start("three")
        cancelled = self.module.cancel(second["id"])
        self.assertEqual(cancelled["state"], "cancelled")
        # The cancelled one does not start, and nothing else starts while the first still runs.
        self.assertEqual(self.launched, [first["id"]])
        store = self.module._read()
        store["runs"][first["id"]].update(state="done", finished_at=time.time())
        self.module._write(store)
        self.module._processes.pop(first["id"], None)
        self.module.reconcile()
        self.assertEqual(self.launched, [first["id"], third["id"]])

    def test_the_refusal_sentence_is_a_reason_to_wait_and_never_reaches_a_row(self):
        first = self.start("one")
        second = self.start("two")
        # The child exited non-zero because the library lock was held elsewhere.
        run_root = Path(self.module._read()["runs"][first["id"]]["request"]["root"])
        (run_root / "output.log").write_text(
            "error: Another model import is active for this library\n", encoding="utf-8")
        store = self.module._read()
        store["runs"][first["id"]]["state"] = "running"
        self.module._write(store)
        self.module._mark_exited(store, first["id"], SimpleNamespace(poll=lambda: 2))
        self.module._write(store)
        row = self.module.status(first["id"])
        # It kept its place at the head of the line, was not failed, and the sentence is nowhere
        # in what a row can carry; the queue then restarted it by itself.
        self.assertEqual(row["waited"], 1)
        self.assertNotIn("Another model import is active", str(row))
        self.assertIn(row["state"], {"queued", "running"})
        self.assertEqual(self.module.status(second["id"])["position"], 1)

    def test_a_waited_three_times_run_fails_with_its_own_reason(self):
        run = self.start("one")
        run_root = Path(self.module._read()["runs"][run["id"]]["request"]["root"])
        (run_root / "output.log").write_text(
            "Another model import is active for this library\n", encoding="utf-8")
        store = self.module._read()
        store["runs"][run["id"]].update(state="running", waited=3)
        self.module._write(store)
        self.module._mark_exited(store, run["id"], SimpleNamespace(poll=lambda: 2))
        self.module._write(store)
        row = self.module.status(run["id"])
        self.assertEqual(row["state"], "error")
        self.assertEqual(row["exit_code"], 2)


    def test_a_folder_import_joins_the_queue_instead_of_taking_the_refusal(self):
        """The other door onto the same library lock: a second import while one runs waits."""
        self.start("download")
        manifest = self.manifest("folder")
        queued = self.module.enqueue_import(package_id="folder", manifest=manifest,
                                            library=self.library,
                                            source_folder=self.data / "not-yet",
                                            catalog={"folder": manifest})
        self.assertEqual(queued["state"], "queued")
        self.assertEqual(queued["position"], 1)
        self.assertEqual(self.module._read()["runs"][queued["id"]]["kind"], "import")
        self.assertNotIn("Another model import is active", str(queued))

    def test_a_paused_run_can_be_cancelled_where_it_waits(self):
        missing = self.data / "KH-STUDIO-GONE" / "model-library"
        paused = self.start("one", drive=missing)
        cancelled = self.module.cancel(paused["id"])
        self.assertEqual(cancelled["state"], "cancelled")


if __name__ == "__main__":
    unittest.main()
