"""Worker job records (`jobs.json`) are kept 90 days, then dropped when the worker restarts."""
from __future__ import annotations

import inspect
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components import job_history  # noqa: E402

DAY = 86400


class JobHistoryTests(unittest.TestCase):
    def test_a_finished_job_expires_after_90_days_by_its_finish_time(self):
        now = time.time()
        self.assertTrue(job_history.expired({"state": "done", "finished_at": now - 91 * DAY}))
        self.assertFalse(job_history.expired({"state": "done", "finished_at": now - 89 * DAY}))
        self.assertTrue(job_history.expired({"state": "error", "created_at": now - 200 * DAY}))

    def test_nothing_unfinished_or_undated_is_ever_dropped(self):
        old = time.time() - 400 * DAY
        for job in ({"state": "running", "created_at": old}, {"state": "queued", "finished_at": old},
                    {"state": "done"}, {"state": "done", "finished_at": "yesterday"},
                    {"state": "cancel_requested", "created_at": old}, "not a job", None):
            self.assertFalse(job_history.expired(job), job)

    def test_every_worker_that_keeps_a_jobs_file_applies_it_when_it_restores(self):
        for relative in ("components/image/app/backend/generation.py", "components/video/app/backend/generation.py",
                         "components/music/app/backend/generation.py", "components/subtitle/app/backend/manager.py"):
            source = (ROOT / relative).read_text()
            restore = source[source.index("def _restore(self)"):]
            restore = restore[:restore.index("self._persist()")]
            self.assertIn("job_history.expired(job)", restore, relative)

    def test_a_restored_music_worker_drops_the_old_record_and_keeps_the_recent_one(self):
        import json
        import tempfile
        from components.music.app.backend import generation
        manager = generation.GenerationManager.__new__(generation.GenerationManager)
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            now = time.time()
            old = {"id": "old", "state": "error", "error_code": "X", "created_at": now - 120 * DAY,
                   "finished_at": now - 120 * DAY}
            recent = {"id": "recent", "state": "error", "error_code": "X", "created_at": now - 5 * DAY,
                      "finished_at": now - 5 * DAY}
            (data / "jobs.json").write_text(json.dumps({"old": old, "recent": recent}))
            manager.state_file, manager.outputs, manager.jobs = data / "jobs.json", data / "outputs", {}
            manager._persist = lambda: None
            manager._restore()
            self.assertEqual(set(manager.jobs), {"recent"})

    def test_a_running_worker_trims_once_a_day_not_only_at_restart(self):
        class Owner:
            pass

        now = time.time()
        owner = Owner()
        jobs = {"old": {"state": "done", "finished_at": now - 100 * DAY},
                "live": {"state": "running", "created_at": now - 100 * DAY},
                "new": {"state": "done", "finished_at": now - DAY}}
        self.assertEqual(job_history.prune_daily(owner, jobs, now=now), 1)
        self.assertEqual(set(jobs), {"live", "new"})
        jobs["old2"] = {"state": "done", "finished_at": now - 100 * DAY}
        self.assertEqual(job_history.prune_daily(owner, jobs, now=now + 3600), 0, "pruned twice in one day")
        self.assertEqual(job_history.prune_daily(owner, jobs, now=now + 86401), 1)
        self.assertNotIn("old2", jobs)

    def test_every_worker_persist_applies_the_daily_prune(self):
        for relative in ("components/image/app/backend/generation.py", "components/video/app/backend/generation.py",
                         "components/music/app/backend/generation.py", "components/subtitle/app/backend/manager.py"):
            source = (ROOT / relative).read_text()
            persist = source[source.index("def _persist(self)"):]
            persist = persist[:persist.index("def ", 10)]
            self.assertIn("job_history.prune_daily(self, self.jobs)", persist, relative)

    def test_the_voice_history_drops_finished_records_older_than_90_days(self):
        import types
        # The module is not imported: its manager loads (and may rewrite) the real voice history at import.
        source = (ROOT / "components/voice/app/backend/generation.py").read_text()
        start = source.index("HISTORY_RETENTION_DAYS = 90")
        namespace = {"time": time}
        end = source.index("\n\n\n", source.index("def _history_expired", start))
        exec(source[start:end], namespace)
        generation = types.SimpleNamespace(_history_expired=namespace["_history_expired"])
        now = time.time()
        job = lambda state, age: types.SimpleNamespace(state=state, finished_at=now - age * DAY, created_at=now - age * DAY)
        self.assertTrue(generation._history_expired(job("done", 91)))
        self.assertTrue(generation._history_expired(job("uncertain", 200)))
        self.assertFalse(generation._history_expired(job("done", 89)))
        self.assertFalse(generation._history_expired(job("running", 400)))
        persist = source[source.index("def _persist(self, *, strict"):source.index("def _load_history")]
        self.assertIn("_history_expired", persist)
        self.assertIn("_history_expired(job)", source[source.index("def _load_history"):])


class ReviewCandidateLocatorRetentionTests(unittest.TestCase):
    """A refused narration section's Hub locator outlives the batch it came from."""

    LOCATOR = {"studio": "voice", "worker_job_id": "c85e8c5d0a1b",
               "client_request_id": "studiohub:old-batch:0", "final_quality_sha256": "a" * 64,
               "receipt_sha256": "b" * 64, "pcm_bytes": 4844, "pcm_sha256": "c" * 64}

    def test_clear_compaction_and_restart_keep_the_protected_locator(self):
        import json
        import tempfile
        from unittest.mock import patch
        from components.hub.backend import control_plane, ledger

        old = time.time() - 120 * DAY
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(ledger, "DB_FILE", Path(folder) / "hub.db"), \
                patch.object(control_plane, "queue_shadow_job", lambda *args, **kwargs: None):
            ledger.save_batch({"id": "old-batch", "created_at": old, "modality": "voice", "items": [
                {"index": 0, "state": "error", "error_code": "TTS_SECTION_SILENT",
                 "_review_candidate": dict(self.LOCATOR)}]})
            self.assertNotIn("_review_candidate", json.dumps(ledger.load_batch("old-batch")))
            ledger.save_batch({"id": "other-old", "created_at": old, "modality": "voice",
                               "items": [{"index": 0, "state": "error"}]})

            self.assertEqual(ledger.delete_batches(["old-batch"]), 1)   # "Clear finished"
            self.assertEqual(ledger.compact_history()["batches"], 1)    # the daily 90-day trim

            self.assertIsNone(ledger.load_batch("old-batch"))
            self.assertIsNone(ledger.load_batch("other-old"))
            kept = ledger.review_locator("old-batch", 0)   # a fresh connection: a restarted Hub
            self.assertEqual({key: kept[key] for key in self.LOCATOR}, self.LOCATOR)
            self.assertEqual(kept["schema_version"], 1)

    def test_an_identical_replay_is_kept_and_a_conflict_or_full_studio_is_refused(self):
        import tempfile
        from unittest.mock import patch
        from components.hub.backend import control_plane, ledger

        def save(batch_id, locator, index=0):
            item = {"index": index, "state": "error", "_review_candidate": dict(locator)}
            ledger.save_batch({"id": batch_id, "created_at": time.time(), "items": [item]})
            return item

        with tempfile.TemporaryDirectory() as folder, \
                patch.object(ledger, "DB_FILE", Path(folder) / "hub.db"), \
                patch.object(control_plane, "queue_shadow_job", lambda *args, **kwargs: None):
            save("b", self.LOCATOR)
            save("b", self.LOCATOR)                                      # replay: unchanged
            item = save("b", dict(self.LOCATOR, receipt_sha256="d" * 64))  # conflict: refused
            self.assertEqual(item["state"], "error")                     # the item still settles
            self.assertEqual(ledger.review_locator("b", 0)["receipt_sha256"], "b" * 64)
            save("b2", self.LOCATOR)                                     # same worker job elsewhere
            self.assertIsNone(ledger.review_locator("b2", 0))
            for n in range(1, ledger.REVIEW_LOCATORS_PER_STUDIO):
                save(f"fill-{n}", dict(self.LOCATOR, worker_job_id=f"{n:012x}"))
            save("one-too-many", dict(self.LOCATOR, worker_job_id="f" * 12))
            self.assertIsNone(ledger.review_locator("one-too-many", 0))
            save("other-studio", dict(self.LOCATOR, studio="voice@node-a", worker_job_id="e" * 12))
            self.assertIsNotNone(ledger.review_locator("other-studio", 0))


if __name__ == "__main__":
    unittest.main()
