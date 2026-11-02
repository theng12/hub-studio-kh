"""The Hub's child watcher must outlive every studio it watches.

`packaging/hub_worker.py` owns every private studio from one thread. That thread dying is the
worst failure available to it: the Hub process keeps answering while no studio ever comes back,
which is the shape of the outages at sites 1400 and 1300 (2026-10-31). Two invariants are
pinned here, both driven with fakes so they fail for the reason they name:

* any exception raised while one studio is watched - `ProcessLookupError` from `getpgid`, an
  `OSError` from `spawn`, a `TimeoutExpired` from `stop_child` - is logged and backed off, and
  the watcher carries on with every other studio;
* a studio whose process is still alive is restarted only on the Hub's own figures: the monitor
  reports it `down` (which it never does while this Hub holds a job lease on it) *and* it has
  answered no health for as long as the app itself waits before ending silent services. A long
  model job is never killed here for being slow.
"""
from __future__ import annotations

from contextlib import redirect_stderr
import io
import sys
import time
import types
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
import hub_worker  # noqa: E402


def setUpModule():
    # The "no recovery policy was given" note is a once-per-process line, and most tests here ask
    # the question with no policy on purpose. `RecoveryPolicyTests` clears the flag for itself, so
    # the suite prints it exactly where it is being tested instead of once at random.
    hub_worker._recovery_policy_said = True


class FakeProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode

    def poll(self):
        return self.returncode


class FakeStop:
    """Stands in for `child_watch_stop`; `wait` records the backoff it was asked for."""

    def __init__(self, set_: bool = False):
        self._set = set_
        self.waits: list[float] = []

    def is_set(self):
        return self._set

    def wait(self, seconds):
        self.waits.append(seconds)
        return self._set


def child(process=None, **extra):
    value = {"id": "voice", "process": process or FakeProcess(None), "started": time.monotonic()}
    value.update(extra)
    return value


class WatcherSurvivalTests(unittest.TestCase):
    def test_a_restart_that_raises_does_not_end_the_watch(self):
        """`getpgid` on a child that just died, or a `spawn` that ran out of descriptors, must
        not end the thread that restarts every other studio."""
        stop = FakeStop()
        seen = []

        def exploding_restart(studio, **kwargs):
            seen.append(studio["id"])
            raise ProcessLookupError(3, "No such process")

        with redirect_stderr(io.StringIO()) as log:
            hub_worker.watch_studio(child(FakeProcess(1)), exploding_restart, stop)

        self.assertIn("keeps watching every studio", log.getvalue())
        self.assertEqual(seen, ["voice"], "the studio was never offered to the watcher pass")
        self.assertEqual(stop.waits, [hub_worker.WATCH_ERROR_BACKOFF_SECONDS],
                         "a failure the watcher did not expect must back off, not spin")

    def test_every_kind_of_failure_is_survived(self):
        """The three that were reachable and uncaught: `OSError` from `spawn`, `TimeoutExpired`
        from `stop_child`'s last wait, and a plain `Exception` from anything else."""
        def raiser(error):
            def fail(studio, **kwargs):
                raise error
            return fail

        for error in (OSError(24, "Too many open files"),
                      __import__("subprocess").TimeoutExpired(cmd="wait", timeout=5),
                      RuntimeError("Private Voice worker process group is not isolated")):
            with self.subTest(error=type(error).__name__):
                with redirect_stderr(io.StringIO()) as log:
                    hub_worker.watch_studio(child(FakeProcess(1)), raiser(error), FakeStop())
                self.assertIn("Voice", log.getvalue())

    def test_an_exited_studio_is_still_restarted(self):
        calls = []
        hub_worker.watch_studio(child(FakeProcess(9)),
                                lambda studio, **kw: calls.append(kw), FakeStop())
        self.assertEqual(calls, [{}], "the crash path must keep restarting without a reason")

    def test_nothing_is_restarted_while_the_hub_is_shutting_down(self):
        """An owner stop stays stopped: the watcher only ever runs while the Hub runs."""
        calls = []
        hub_worker.watch_studio(child(FakeProcess(9)), lambda studio, **kw: calls.append(kw),
                                FakeStop(set_=True))
        self.assertEqual(calls, [])

    def test_the_watch_pass_never_raises(self):
        for process in (FakeProcess(1), FakeProcess(None), FakeProcess(0)):
            with self.subTest(returncode=process.returncode):
                with redirect_stderr(io.StringIO()):
                    hub_worker.watch_studio(child(process), lambda studio, **kw: None, FakeStop())


class SilentStudioTests(unittest.TestCase):
    def test_a_live_studio_the_monitor_reports_up_is_left_alone(self):
        self.assertIsNone(hub_worker.hung_reason(child(), status={"status": "up"}))

    def test_silence_inside_the_bound_is_left_alone(self):
        """The bound is the app's own `StartupRecovery.silentServicesRestartAfter` (120 s)."""
        status = {"status": "down", "last_seen": time.time() - 30}
        self.assertIsNone(hub_worker.hung_reason(child(), status=status))

    def test_silence_past_the_bound_is_a_reason(self):
        status = {"status": "down", "last_seen": time.time() - 200}
        reason = hub_worker.hung_reason(child(), status=status)
        self.assertIsNotNone(reason)
        self.assertIn("stopped answering health", reason)
        self.assertIn("monitor reports it down", reason)
        self.assertIn("200", reason)

    def test_a_studio_that_never_answered_is_left_alone_until_the_bound(self):
        studio = child(started=time.monotonic() - 5)
        self.assertIsNone(hub_worker.hung_reason(studio, status={"status": "down", "last_seen": None}))
        studio = child(started=time.monotonic() - 200)
        reason = hub_worker.hung_reason(studio, status={"status": "down", "last_seen": None})
        self.assertIsNotNone(reason)
        self.assertIn("never answered", reason)

    def test_a_silent_studio_with_a_monitor_row_of_its_own_is_restarted_by_the_pass(self):
        calls = []
        stop = FakeStop()
        studio = child()
        with redirect_stderr(io.StringIO()), \
                mock.patch.object(hub_worker, "hung_reason",
                                  return_value="stopped answering health 200 s ago"):
            hub_worker.watch_studio(studio, lambda s, **kw: calls.append(kw), stop)
        self.assertEqual(len(calls), 1)
        self.assertIn("reason", calls[0])
        self.assertIn("stopped answering health", calls[0]["reason"])

    def test_a_monitor_row_that_cannot_be_read_leaves_the_studio_alone_and_says_so_once(self):
        """If this process cannot see the Hub's monitor it cannot tell silence from a long job,
        so it leaves the studio alone and says why - once, not on every pass."""
        studio = child()
        with redirect_stderr(io.StringIO()) as log:
            first = hub_worker.hung_reason(studio, status={"_unavailable": "ImportError: no app"})
            second = hub_worker.hung_reason(studio, status={"_unavailable": "ImportError: no app"})
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(log.getvalue().count("cannot be read"), 1)
        self.assertIn("left alone", log.getvalue())

    def test_the_monitor_row_is_read_from_the_hub_app_when_no_status_is_supplied(self):
        """The live row is the Hub app's own monitor - never a second one this process would
        build, which would poll nothing and report every studio as unknown forever."""
        studio = child()
        monitor = types.SimpleNamespace(status={"voice": {"status": "down", "last_seen": 0}})
        app = types.SimpleNamespace(state=types.SimpleNamespace(monitor=monitor))
        module = types.ModuleType("components.hub.standalone")
        module.app = app
        saved = {name: sys.modules.get(name) for name in
                 ("components", "components.hub", "components.hub.standalone")}
        sys.modules.update({"components": types.ModuleType("components"),
                            "components.hub": types.ModuleType("components.hub"),
                            "components.hub.standalone": module})
        try:
            reason = hub_worker.hung_reason(studio)
        finally:
            for name, previous in saved.items():
                if previous is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = previous
        self.assertIsNotNone(reason)
        self.assertIn("monitor reports it down", reason)


class RecoveryPolicyTests(unittest.TestCase):
    """The app's recovery switch reaches this watcher and governs the silence restart only.

    `--recovery-attempts` is the app's own `setRecoveryPolicy(maxAttempts:)`, passed on the
    command line the app starts the worker with. Zero means this Mac's owner asked for no
    automatic restarts, so a studio that is still running but has stopped answering is left
    alone here too - the app already honours the same switch for its own hung services. A studio
    whose process has exited is a different question and keeps being restarted.
    """

    def setUp(self):
        self._saved = (hub_worker.RECOVERY_ATTEMPTS, hub_worker._recovery_policy_said)
        hub_worker._recovery_policy_said = False
        self.addCleanup(self._restore)

    def _restore(self):
        hub_worker.RECOVERY_ATTEMPTS, hub_worker._recovery_policy_said = self._saved

    @staticmethod
    def _silent():
        return {"status": "down", "last_seen": time.time() - 200}

    def test_zero_leaves_a_down_and_silent_studio_alone_and_says_why_once(self):
        hub_worker.RECOVERY_ATTEMPTS = 0
        studio = child()
        with redirect_stderr(io.StringIO()) as log:
            first = hub_worker.hung_reason(studio, status=self._silent())
            second = hub_worker.hung_reason(studio, status=self._silent())
        self.assertIsNone(first, "policy 0 must not produce a restart reason")
        self.assertIsNone(second)
        self.assertEqual(log.getvalue().count("left alone"), 1,
                         "the reason must be said once, not on every pass")
        self.assertIn("never restart services by itself", log.getvalue())

    def test_zero_stops_the_watch_pass_from_offering_a_restart(self):
        hub_worker.RECOVERY_ATTEMPTS = 0
        calls = []
        with redirect_stderr(io.StringIO()), \
                mock.patch.object(hub_worker, "_studio_status", return_value=self._silent()):
            hub_worker.watch_studio(child(), lambda studio, **kw: calls.append(kw), FakeStop())
        self.assertEqual(calls, [], "a silent studio must not be restarted under policy 0")

    def test_zero_still_restarts_a_studio_whose_process_exited(self):
        hub_worker.RECOVERY_ATTEMPTS = 0
        calls = []
        hub_worker.watch_studio(child(FakeProcess(9)),
                                lambda studio, **kw: calls.append(kw), FakeStop())
        self.assertEqual(calls, [{}],
                         "an exited studio is restarted whatever the recovery policy says")

    def test_a_value_that_was_not_given_keeps_the_restart_and_is_said_once(self):
        """Not told and allowed are the same behaviour and must not be the same log line."""
        hub_worker.RECOVERY_ATTEMPTS = None
        with redirect_stderr(io.StringIO()) as log:
            first = hub_worker.hung_reason(child(), status=self._silent())
            second = hub_worker.hung_reason(child(), status=self._silent())
        self.assertIsNotNone(first, "with no policy given the restart is today's default")
        self.assertIsNotNone(second)
        self.assertEqual(log.getvalue().count("not given a readable recovery policy"), 1)

    def test_an_enabled_policy_keeps_the_restart_without_a_note(self):
        hub_worker.RECOVERY_ATTEMPTS = 5
        with redirect_stderr(io.StringIO()) as log:
            reason = hub_worker.hung_reason(child(), status=self._silent())
        self.assertIsNotNone(reason)
        self.assertEqual(log.getvalue(), "")

    def test_the_flag_is_read_as_the_policy_it_names(self):
        for given, expected in (("0", 0), ("5", 5), (None, None), ("later", None), ("", None)):
            with self.subTest(given=given):
                hub_worker.set_recovery_attempts(given)
                self.assertEqual(hub_worker.RECOVERY_ATTEMPTS, expected)


class ReasonPublicationTests(unittest.TestCase):
    def test_the_reason_is_published_on_the_existing_alert_channel(self):
        emitted = []

        class FakeAlerts:
            @staticmethod
            def emit(kind, message, data=None):
                emitted.append((kind, message, data or {}))

        backend = types.ModuleType("components.hub.backend")
        backend.alerts = FakeAlerts
        components = types.ModuleType("components")
        hub = types.ModuleType("components.hub")
        saved = {name: sys.modules.get(name) for name in
                 ("components", "components.hub", "components.hub.backend", "components.hub.backend.alerts")}
        sys.modules.update({"components": components, "components.hub": hub,
                            "components.hub.backend": backend, "components.hub.backend.alerts": FakeAlerts})
        try:
            hub_worker.announce_restart("voice", "stopped answering health 200 s ago")
        finally:
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
        self.assertEqual(len(emitted), 1)
        kind, message, data = emitted[0]
        self.assertEqual(kind, "studio_restarted")
        self.assertIn("stopped answering health", message)
        self.assertEqual(data["studio"], "voice")

    def test_a_missing_alert_module_never_stops_a_restart(self):
        backend = types.ModuleType("components.hub.backend")
        components = types.ModuleType("components")
        hub = types.ModuleType("components.hub")
        saved = {name: sys.modules.get(name) for name in
                 ("components", "components.hub", "components.hub.backend")}
        sys.modules.update({"components": components, "components.hub": hub,
                            "components.hub.backend": backend})
        try:
            hub_worker.announce_restart("voice", "a reason")  # must not raise
        finally:
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module


if __name__ == "__main__":
    unittest.main()
