"""A healthy, idle fleet reads healthy: no phantom studios, no stale "needs attention".

Live 2026-10-02 (Hub 0.6.132, nothing generating): every node that joined before Video stopped
shipping still had a `video` studio on its controller's roster, so the controller polled a worker
that did not exist — the Gateway showed "10 workers are offline", every idle Mac "needs attention"
and its activity "partially unavailable". And a routing refusal from the night before held three
Macs amber all day, because any error as the newest job meant "needs attention" until the next
success.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub.backend import activity, fleet_nodes

SIX = [{"id": s, "modality": m} for s, m in (
    ("voice", "voice"), ("subtitle", "transcription"), ("chat", "chat"),
    ("music", "music"), ("image", "image"), ("video", "video"))]


def snapshot(*studios: tuple[str, str], reachable: bool = True, auth: bool = True) -> dict:
    """What `/api/hub/resources?local_only=true` gives a controller: `studios` keyed by modality."""
    return {"reachable": reachable, "auth": auth, "status": "connected",
            "studios": {modality: {"id": worker, "status": "up"} for worker, modality in studios}}


FIVE = [("voice", "voice"), ("subtitle", "transcription"), ("chat", "chat"),
        ("music", "music"), ("image", "image")]


class RosterFollowsTheNode(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-fleet-health-")
        self.old_data = fleet_nodes.DATA_DIR
        self.old_env = os.environ.copy()
        os.environ["STUDIOHUB_DATA_DIR"] = self.temporary.name
        fleet_nodes.DATA_DIR = Path(self.temporary.name)
        self.addCleanup(self.restore)
        fleet_nodes.join({"node_id": "mac-1106", "name": "1106", "address": "http://100.64.0.6:47873",
                          "studios": SIX})

    def restore(self) -> None:
        fleet_nodes.DATA_DIR = self.old_data
        os.environ.clear()
        os.environ.update(self.old_env)
        self.temporary.cleanup()

    def studios(self) -> list[str]:
        return sorted(s["id"] for s in fleet_nodes._read()["mac-1106"]["studios"])

    def test_a_studio_the_node_no_longer_runs_leaves_the_roster(self) -> None:
        with mock.patch.object(fleet_nodes, "fresh_snapshot", return_value=snapshot(*FIVE)):
            self.assertTrue(fleet_nodes.sync_studios_from_snapshots())
            self.assertFalse(fleet_nodes.sync_studios_from_snapshots(), "a second pass changes nothing")
        self.assertEqual(self.studios(), ["chat", "image", "music", "subtitle", "voice"])
        # What the monitor polls (after `reload_registry`) no longer has a worker that does not exist.
        self.assertNotIn("mac-1106:video", [s["id"] for s in fleet_nodes.remote_studios()])

    def test_a_studio_that_comes_back_is_offered_again(self) -> None:
        with mock.patch.object(fleet_nodes, "fresh_snapshot", return_value=snapshot(*FIVE)):
            fleet_nodes.sync_studios_from_snapshots()
        with mock.patch.object(fleet_nodes, "fresh_snapshot",
                               return_value=snapshot(*FIVE, ("video", "video"))):
            self.assertTrue(fleet_nodes.sync_studios_from_snapshots())
        self.assertIn("video", self.studios())

    def test_silence_never_empties_a_roster_row(self) -> None:
        for said in (None, snapshot(*FIVE, reachable=False), snapshot(*FIVE, auth=False), snapshot(),
                     {"reachable": True, "auth": True, "studios": "nonsense"},
                     snapshot(("Bad Id!", "voice"), ("x", "not-a-modality"))):
            with mock.patch.object(fleet_nodes, "fresh_snapshot", return_value=said):
                self.assertFalse(fleet_nodes.sync_studios_from_snapshots(), said)
        self.assertEqual(len(self.studios()), 6)


def up(*studios: str) -> dict:
    return {s: {"status": "up", "activity_support": "available"} for s in studios}


class AttentionMeansNow(unittest.TestCase):
    NOW = 1_790_953_000.0
    STUDIOS = [{"id": "voice", "modality": "voice", "machine": "m"},
               {"id": "image", "modality": "image", "machine": "m"}]

    def state(self, events: list[dict], statuses: dict | None = None) -> str:
        return activity._state_at("m", statuses or up("voice", "image"), self.STUDIOS, [], events, self.NOW)[0]

    def finished(self, state: str, ago: float) -> dict:
        return {"machine": "m", "studio": "voice", "state": state, "finished_at": self.NOW - ago,
                "observed_at": self.NOW - ago}

    def test_an_error_from_last_night_does_not_hold_an_idle_mac_amber(self) -> None:
        self.assertEqual(self.state([self.finished("error", 16 * 3600)]), "long_idle")
        self.assertEqual(self.state([self.finished("error", 40 * 60)]), "ready")

    def test_a_fresh_error_still_needs_attention(self) -> None:
        self.assertEqual(self.state([self.finished("error", 60)]), "needs_attention")

    def test_a_studio_that_should_run_and_is_down_still_needs_attention(self) -> None:
        statuses = {**up("voice"), "image": {"status": "down", "activity_support": None}}
        self.assertEqual(self.state([self.finished("done", 16 * 3600)], statuses), "needs_attention")

    def test_an_idle_mac_with_every_studio_up_is_ready_or_idle(self) -> None:
        self.assertEqual(self.state([]), "ready")
        self.assertEqual(self.state([self.finished("done", 3 * 3600)]), "long_idle")


if __name__ == "__main__":
    unittest.main()
