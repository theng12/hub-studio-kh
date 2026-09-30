"""A finished job's detail is fetched once. That is only safe while nothing can re-open one.

The app asked the Hub for the detail of every batch it had ever recorded - one request per historical
job, every two seconds, for the life of the app - to re-read records that could not have changed. It
now remembers a job whose state is final. The whole safety of that rests on one assumption: no final
job can become live again. This file states the assumption and fails if it stops being true, so the
cache cannot quietly start hiding a job you just retried.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = (ROOT / "native" / "StudioAPI.swift").read_text()
HUB_ROUTES = (ROOT / "components" / "hub" / "standalone.py").read_text()


class JobDetailCacheTests(unittest.TestCase):
    def test_only_final_states_are_remembered(self):
        self.assertIn('if ["done", "cancelled"].contains(job.state) || (job.state == "error" && batch.settled) {', API,
                      "only a state that cannot come back may be cached; a failure comes back through Retry, "
                      "so it is cached only on the Hub's word that nothing in the batch is waiting or running")
        settled = API[API.index("var settled: Bool {"):][:260]
        for count in ("queued", "retrying", "running", "cancelRequested", "uncertain"):
            self.assertIn(count, settled, f"a batch with {count} work is not settled")

    def test_the_cache_is_dropped_when_the_connection_changes(self):
        disconnect = API[API.index("func disconnect()"):]
        disconnect = disconnect[:disconnect.index("\n    }")]
        self.assertIn("finishedJobCache.removeAll()", disconnect,
                      "a reconnect must not serve the previous Hub's jobs")

    def test_cached_jobs_are_shown_without_a_request(self):
        self.assertIn('if let known = finishedJobCache[batch.id], known.state != "error" || batch.settled {', API)

    def test_a_retry_drops_the_batch_from_the_cache(self):
        retry = API[API.index("func retryJob("):][:700]
        self.assertIn("finishedJobCache.removeValue(forKey: job.id)", retry)

    def test_the_only_routes_that_re_open_a_finished_job_are_the_retry_routes(self):
        """The Hub may re-open exactly one failed item, and only through the retry routes.

        `broker.retry_batch`, `broker.remove_batch` and `broker.clear_terminal` still have no
        route. The two retry routes the GatewayKH peer uses can turn a finished batch live
        again, so the native `finishedJobCache` has to drop the affected batch when it retries
        one. That native invalidation is deliberately outside this Hub-only change; this test
        pins the Hub side so the assumption cannot drift silently.
        """
        routes = re.findall(r'@app\.(get|post|put|delete)\("([^"]+)"', HUB_ROUTES)
        offenders = sorted(f"{method.upper()} {path}" for method, path in routes
                           if re.search(r"retry|reopen|requeue|clear[-_]?terminal|remove[-_]?batch",
                                        path))
        self.assertEqual(offenders, [
            "POST /api/hub/jobs/{batch_id}/items/{item_index}/retry",
            "POST /api/hub/transcription/jobs/{batch_id}/items/{item_index}/retry",
        ], "only the peer's retry routes may re-open a failed item; "
           "anything else needs finishedJobCache invalidation in native/StudioAPI.swift")

    def test_the_hub_functions_really_have_no_caller(self):
        for name in ("retry_batch", "clear_terminal"):
            callers = [path.name for path in (ROOT / "components").rglob("*.py")
                       if re.search(rf"(?<!def )\b{name}\(", path.read_text())]
            self.assertEqual(callers, [], f"{name} now has a caller: {callers}")


if __name__ == "__main__":
    unittest.main()
