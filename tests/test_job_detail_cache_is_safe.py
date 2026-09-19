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
        self.assertIn('if ["done", "cancelled"].contains(job.state) { finished[batch.id] = job }', API,
                      "only a state that cannot come back may be cached")

    def test_the_cache_is_dropped_when_the_connection_changes(self):
        disconnect = API[API.index("func disconnect()"):]
        disconnect = disconnect[:disconnect.index("\n    }")]
        self.assertIn("finishedJobCache.removeAll()", disconnect,
                      "a reconnect must not serve the previous Hub's jobs")

    def test_cached_jobs_are_shown_without_a_request(self):
        self.assertIn("if let known = finishedJobCache[batch.id] {", API)

    def test_the_assumption_holds_no_route_can_re_open_a_finished_job(self):
        """The Hub's retry/remove/clear functions exist; none of them is reachable over HTTP.

        `broker.retry_batch`, `broker.remove_batch` and `broker.clear_terminal` skip or refuse an
        unfinished batch, and they are only called from tests. If a route for any of them is ever
        registered, a finished job *can* become queued again, and this cache must be invalidated
        there - so this test fails and points at it.
        """
        routes = re.findall(r'@app\.(get|post|put|delete)\("([^"]+)"', HUB_ROUTES)
        offenders = [f"{method.upper()} {path}" for method, path in routes
                     if re.search(r"retry|reopen|requeue|clear[-_]?terminal|remove[-_]?batch", path)]
        self.assertEqual(offenders, [],
                         "a route can re-open a finished job now; invalidate finishedJobCache in it "
                         "(native/StudioAPI.swift) and update this test")

    def test_the_hub_functions_really_have_no_caller(self):
        for name in ("retry_batch", "clear_terminal"):
            callers = [path.name for path in (ROOT / "components").rglob("*.py")
                       if re.search(rf"(?<!def )\b{name}\(", path.read_text())]
            self.assertEqual(callers, [], f"{name} now has a caller: {callers}")


if __name__ == "__main__":
    unittest.main()
