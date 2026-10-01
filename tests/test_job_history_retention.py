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


if __name__ == "__main__":
    unittest.main()
