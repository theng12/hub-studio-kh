"""Why a Mac's Studio Hub last stopped travels to its controller and on to the Gateway.

The app records it (`StopRecord`, native/ShellCore.swift) in ``last-stop.json``; the Hub publishes
it as ``last_stop = {reason, at, label, relaunched}`` on its snapshot, and a controller relays each
node's - repeating what a node that stopped answering last said, marked ``stale``. Owner decision
2026-10-03: "Force-quit at 09:36" on the Gateway's Fleet page instead of a bare "offline".
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx

from components.hub.backend import fleet_nodes

STOP = {"reason": "force_quit", "at": 1_800_000_000.0, "label": "Force-quit", "relaunched": False}


class _Response:
    def __init__(self, payload: object):
        self.status_code, self._payload = 200, payload

    @property
    def is_success(self) -> bool:
        return True

    def json(self):
        return self._payload


class _Client:
    def __init__(self, answers: dict[str, object]):
        self.answers = answers

    async def get(self, url: str, timeout=None, headers=None):
        for address, payload in self.answers.items():
            if url.startswith(address):
                return _Response(payload)
        raise httpx.ConnectError("connection refused")

    async def aclose(self):
        return None


class LastStopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-last-stop-")
        old_env, old_data = os.environ.copy(), fleet_nodes.DATA_DIR
        os.environ["STUDIOHUB_DATA_DIR"] = self.temporary.name
        fleet_nodes.DATA_DIR = Path(self.temporary.name)

        def restore() -> None:
            fleet_nodes.DATA_DIR = old_data
            os.environ.clear()
            os.environ.update(old_env)
            self.temporary.cleanup()
        self.addCleanup(restore)

    def test_this_mac_publishes_what_the_app_recorded(self) -> None:
        path = Path(self.temporary.name) / "last-stop.json"
        os.environ["STUDIOHUB_LAST_STOP_FILE"] = str(path)
        self.assertIsNone(fleet_nodes.this_last_stop(), "no file yet: nothing is known")
        path.write_text(json.dumps({**STOP, "pid": 4242}))
        self.assertEqual(fleet_nodes.this_last_stop(), STOP)
        path.write_text("{not json")
        self.assertIsNone(fleet_nodes.this_last_stop())

    def test_only_the_contract_travels(self) -> None:
        self.assertEqual(fleet_nodes.last_stop_of({**STOP, "secret": "x"}), STOP)
        for junk in (None, "crashed", {"reason": "abducted", "at": 1.0},
                     {"reason": "quit", "at": "yesterday"}, {"reason": "quit", "at": True},
                     {"reason": "quit"}):
            self.assertIsNone(fleet_nodes.last_stop_of(junk), junk)
        reasons = {"quit", "crashed", "updated", "force_quit", "low_memory", "mac_restarted",
                   "stopped_answering"}
        self.assertEqual(set(fleet_nodes.LAST_STOP_REASONS), reasons,
                         "the reasons are the ones StopReason in native/ShellCore.swift writes")

    def test_a_controller_relays_a_node_and_remembers_it_when_the_node_goes_quiet(self) -> None:
        fleet_nodes.join({"node_id": "mini-7", "name": "Mini 7", "address": "http://192.168.1.27:8080"})
        answering = _Client({"http://192.168.1.27:8080": {"version": "0.6.136", "last_stop": STOP}})
        row = asyncio.run(fleet_nodes.inventory(answering))["nodes"][0]
        self.assertEqual(row["state"], "reachable")
        self.assertEqual(row["last_stop"], STOP)
        self.assertEqual(fleet_nodes._member(row, role="agent")["last_stop"], STOP,
                         "the overview row carries it too")
        # The Mac is force-quit again and stays down: the controller repeats what it last said,
        # marked stale, rather than dropping it or inventing a reason.
        row = asyncio.run(fleet_nodes.inventory(_Client({})))["nodes"][0]
        self.assertEqual(row["state"], "unreachable")
        self.assertEqual(row["last_stop"], {**STOP, "stale": True})


if __name__ == "__main__":
    unittest.main()
