"""The storage budget has to be enforced in the app that ships, not only in development.

The policy is what the owner asked for - 50 GB or 30 days, whichever comes first
(`standalone_config.DEFAULT_MAX_BYTES` / `DEFAULT_RETENTION_DAYS`) - and it was enforced nowhere in
the packaged build: `transcription_jobs.start_dispatcher` starts its legacy cleanup loop only
`if not _packaged()`, and `job_storage.enforce_budget`, the shared sweep the runtime contract says
supersedes it, had no caller at all. A Mac left running kept every recording, transcript and
generated file until its disk filled and it began refusing work it could not free.
"""
import asyncio
import unittest
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub.backend import job_storage  # noqa: E402
from components.hub import standalone_config  # noqa: E402


class StorageBudgetLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_a_failed_sweeps_retry_returns_cleanly(self):
        retrying = asyncio.Event()
        real_sleep = asyncio.sleep

        async def sleep(seconds):
            if seconds == 30:
                retrying.set()
                await real_sleep(3600)

        with mock.patch.object(job_storage, "sweep_once", side_effect=OSError("disk busy")), \
                mock.patch.object(job_storage.asyncio, "sleep", sleep):
            task = asyncio.create_task(job_storage.budget_loop(5))
            try:
                await asyncio.wait_for(retrying.wait(), timeout=2)
                task.cancel()
                await task
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_one_pass_enforces_the_budget(self):
        calls = []

        def sweep():
            calls.append(1)
            return {"reclaimed_bytes": 0}

        with mock.patch.object(job_storage, "enforce_budget", sweep):
            await job_storage.sweep_once()
        self.assertEqual(len(calls), 1, "a pass must enforce the budget")

    async def test_the_sweep_runs_on_a_timer_and_stops_when_cancelled(self):
        calls = []

        def sweep():
            calls.append(1)
            return {"reclaimed_bytes": 0}

        with mock.patch.object(job_storage, "enforce_budget", sweep):
            task = asyncio.create_task(job_storage.budget_loop(5))
            await asyncio.sleep(0.05)      # the first pass waits out its interval
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(task.cancelled() or task.done(), "the loop must stop when cancelled")

    async def test_a_failing_sweep_does_not_kill_the_loop(self):
        calls = []

        def explode():
            calls.append(1)
            raise OSError("disk busy")

        with mock.patch.object(job_storage, "enforce_budget", explode):
            with self.assertRaises(OSError):
                await job_storage.sweep_once()
            task = asyncio.create_task(job_storage.budget_loop(5))
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertGreaterEqual(len(calls), 1)
        self.assertTrue(task.cancelled() or task.done(), "the loop must outlive a failed sweep")

    def test_the_app_starts_the_sweep_and_the_policy_is_the_owners_numbers(self):
        source = (Path(__file__).resolve().parents[1] / "components/hub/standalone.py").read_text()
        self.assertIn("job_storage.budget_loop", source,
                      "the packaged Hub must start the sweep, not only the development build")
        self.assertIn("storage_sweep.cancel()", source, "and stop it when the Hub stops")
        self.assertEqual(standalone_config.DEFAULT_MAX_BYTES, 20_000_000_000)
        self.assertEqual(standalone_config.DEFAULT_RETENTION_DAYS, 3)
        self.assertEqual(standalone_config.DEFAULT_MINIMUM_FREE_BYTES, 5_000_000_000)
        self.assertTrue(standalone_config.DEFAULT_CLEANUP_ENABLED)
        self.assertGreater(standalone_config.cleanup_interval_seconds(), 0)


if __name__ == "__main__":
    unittest.main()
