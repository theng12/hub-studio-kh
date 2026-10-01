"""`/api/hub/resources` and the 5 s proxy check must not shell out or walk every process each time.

Measured on this Mac: the route took ~0.6 s (six `lsof` subprocesses plus a Caddy search over all
processes) and the monitor repeated the process walk every poll for a Caddy the standalone app does
not run. Both answers are remembered briefly; they are still refreshed.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub.backend import resources  # noqa: E402


class ResourcePollCostTests(unittest.TestCase):
    def setUp(self):
        resources._listener_cache.clear()
        resources._proxy_stats_cache = None
        self.addCleanup(lambda: (resources._listener_cache.clear(),
                                 setattr(resources, "_proxy_stats_cache", None)))

    def test_the_listener_of_a_port_is_looked_up_once_while_it_is_alive(self):
        clock = [1000.0]
        with mock.patch.object(resources.time, "monotonic", lambda: clock[0]), \
                mock.patch.object(resources, "_lsof_listening_pids", return_value=[4242]) as lsof, \
                mock.patch.object(resources.psutil, "pid_exists", return_value=True):
            for _ in range(6):
                self.assertEqual(resources._listening_pids(47873), [4242])
            self.assertEqual(lsof.call_count, 1, "lsof ran for every call")
            clock[0] += resources.LISTENER_TTL_S + 1
            resources._listening_pids(47873)
            self.assertEqual(lsof.call_count, 2, "the listener was never looked up again")

    def test_a_listener_that_died_is_looked_up_again_at_once(self):
        with mock.patch.object(resources, "_lsof_listening_pids", side_effect=[[1], [2]]) as lsof, \
                mock.patch.object(resources.psutil, "pid_exists", side_effect=lambda pid: pid != 1):
            self.assertEqual(resources._listening_pids(9), [1])
            self.assertEqual(resources._listening_pids(9), [2])
        self.assertEqual(lsof.call_count, 2)

    def test_an_empty_answer_is_not_trusted(self):
        with mock.patch.object(resources, "_lsof_listening_pids", return_value=[]) as lsof:
            resources._listening_pids(9)
            resources._listening_pids(9)
        self.assertEqual(lsof.call_count, 2, "a worker that was not up yet stayed 'not listening'")

    def test_the_process_walk_for_caddy_runs_once_a_minute(self):
        clock = [50.0]
        walked = []

        def walk():
            walked.append(1)
            return {"status": "not_running"}

        with mock.patch.object(resources.time, "monotonic", lambda: clock[0]), \
                mock.patch.object(resources, "_proxy_stats", walk):
            for _ in range(12):             # one minute of 5 s polls
                resources.check_proxy_health()
                clock[0] += 5.0
            self.assertEqual(len(walked), 1)
            clock[0] += resources.PROXY_STATS_TTL_S
            resources.check_proxy_health()
            self.assertEqual(len(walked), 2)


if __name__ == "__main__":
    unittest.main()
