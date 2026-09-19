"""A job cancelled while it is still queued must not leave its cancel Event behind.

`GenerationManager.cancel` sets the job's `threading.Event`; the job's own thread notices it at the
top of `_run`, marks the job cancelled - and used to `return` from there without removing the event
from `cancel_events`. Only two other paths remove it (a failed running-state persist, and the
`finally` of the inference block), and both come later, so every cancellation that arrived while the
job was queued left one Event in the dictionary for the life of the process. The app runs for weeks.
"""
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.image.app.backend.generation import GenerationManager  # noqa: E402


def _manager(root: Path) -> GenerationManager:
    return GenerationManager(data=root, model_library=root / "library", manifest={},
                             runtime_sha256="0" * 64)


def _queued_job(job_id: str) -> dict:
    return {"job_id": job_id, "state": "queued", "progress": 0.0, "created_at": 0.0,
            "finished_at": None, "started_at": None, "error": None, "error_code": None,
            "prompt": "a picture", "model_revision": "r", "acknowledged": False}


class CancelEventReleaseTests(unittest.TestCase):
    def test_a_job_cancelled_while_queued_releases_its_event(self):
        with TemporaryDirectory() as temporary:
            manager = _manager(Path(temporary))
            event = threading.Event()
            event.set()                        # the operator cancelled before the thread ran
            manager.jobs["job-1"] = _queued_job("job-1")
            manager.cancel_events["job-1"] = event

            manager._run("job-1", str(Path(temporary) / "model"))

            self.assertEqual(manager.jobs["job-1"]["state"], "cancelled")
            self.assertNotIn("job-1", manager.cancel_events,
                             "a finished job must not keep its cancel Event for ever")

    def test_many_cancellations_do_not_accumulate(self):
        with TemporaryDirectory() as temporary:
            manager = _manager(Path(temporary))
            for index in range(25):
                job_id = f"job-{index}"
                event = threading.Event()
                event.set()
                manager.jobs[job_id] = _queued_job(job_id)
                manager.cancel_events[job_id] = event
                manager._run(job_id, str(Path(temporary) / "model"))
            self.assertEqual(manager.cancel_events, {},
                             "the dictionary must not grow with every cancellation")

    def test_cancel_still_marks_a_queued_job_cancelled(self):
        with TemporaryDirectory() as temporary:
            manager = _manager(Path(temporary))
            manager.jobs["job-9"] = _queued_job("job-9")
            manager.cancel_events["job-9"] = threading.Event()
            result = manager.cancel("job-9")
            self.assertEqual(result["state"], "cancelled")
            self.assertTrue(manager.cancel_events["job-9"].is_set())


if __name__ == "__main__":
    unittest.main()
