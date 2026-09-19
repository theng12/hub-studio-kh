"""An alert that cannot be sent must not stop the health poll.

The poll is what tells the rest of the Hub whether a Studio is live - `_submission_readiness` reads
`monitor.status` to decide whether a job can run at all (0.6.3) - so an unreachable webhook or an
unwritable alert log must not be able to abort the pass. When it did, every Studio kept reporting its
previous state while the app showed that as current, and work was refused or queued on stale truth.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub.backend import alerts, monitor  # noqa: E402


def _bare_monitor() -> monitor.StudioMonitor:
    """A monitor without __init__: these helpers only need the one attribute they use."""
    instance = monitor.StudioMonitor.__new__(monitor.StudioMonitor)
    instance._restart_alerts = {}
    return instance


STUDIO = {"id": "voice-1", "title": "Voice", "machine": "local"}


class AlertIsolationTests(unittest.TestCase):
    def test_a_down_transition_survives_a_broken_alert(self):
        instance = _bare_monitor()
        with mock.patch.object(alerts, "emit", side_effect=RuntimeError("webhook unreachable")):
            instance._note_transition(STUDIO, "up", "down")      # must not raise

    def test_a_recovery_transition_survives_a_broken_alert(self):
        instance = _bare_monitor()
        with mock.patch.object(alerts, "emit", side_effect=OSError("alert log is read-only")):
            instance._note_transition(STUDIO, "down", "up")

    def test_the_alert_is_still_sent_when_it_can_be(self):
        instance = _bare_monitor()
        sent = []
        with mock.patch.object(alerts, "emit", lambda kind, message, detail: sent.append(kind)):
            instance._note_transition(STUDIO, "up", "down")
        self.assertEqual(sent, ["studio_down"], "the guard must not silence working alerts")

    def test_no_alert_for_the_first_poll_or_an_unchanged_state(self):
        instance = _bare_monitor()
        sent = []
        with mock.patch.object(alerts, "emit", lambda kind, message, detail: sent.append(kind)):
            instance._note_transition(STUDIO, "unknown", "up")
            instance._note_transition(STUDIO, "up", "up")
        self.assertEqual(sent, [], "startup and steady state are not events")

    def test_the_restart_alert_is_guarded_too(self):
        instance = _bare_monitor()
        health = {"restart_health": {"restarting": True, "restarts": 3, "window_seconds": 60}}
        with mock.patch.object(alerts, "emit", side_effect=RuntimeError("webhook unreachable")):
            instance._note_restart_health(STUDIO, health)        # must not raise

    def test_every_alert_call_goes_through_the_guarded_helper(self):
        source = (Path(__file__).resolve().parents[1]
                  / "components/hub/backend/monitor.py").read_text()
        self.assertEqual(source.count("alerts.emit("), 1,
                         "only the guarded helper may emit directly")
        helper = source[source.index("    def _alert("):]
        helper = helper[:helper.index("\n    def ", 10)]
        self.assertIn("alerts.emit(", helper)
        self.assertIn("except Exception", helper, "and it must swallow the failure")
        # The poll-path helpers must not call the alerting module themselves any more.
        for name in ("_note_transition", "_note_restart_health"):
            body = source[source.index(f"    def {name}("):]
            body = body[:body.index("\n    def ", 10)]
            self.assertNotIn("alerts.emit(", body, f"{name} must use self._alert")


if __name__ == "__main__":
    unittest.main()
