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


if __name__ == "__main__":
    unittest.main()
