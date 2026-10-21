"""A node that can reach its controller is no longer invisible — and is not mistaken for reachable.

`GET …/status` is the call a node makes every 60 s on builds predating check-in; a node credential
may ask it only for the Mac that holds it. The call is recorded as presence and nothing else. These
tests pin what makes the stamp safe: it clears `never_connected`, does not make the Mac reachable or
pullable, carries nothing a dial would have returned, never overwrites transport evidence, and only
a node's own credential records it.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub.backend import fleet_nodes  # noqa: E402

NODE = "office-imac-blue-m1-8-256-hub"
# A reserved Tailscale-range address, deliberately not the real controller's.
ADDRESS = "http://100.64.0.1:47873"
STUDIOS = [{"id": "image", "modality": "image"}]


# The route lives inside `create_app`, so its credential scope is checked against a real Hub app in
# a child process with throwaway private roots and mock auth tokens; no request leaves the process.
PROOF = r'''
import asyncio
import json
import os
from pathlib import Path
import httpx

# `standalone` builds its app at import time (`app = create_app()` at module scope), so the child's
# private roots must exist, be owner-only, and carry a valid worker manifest before that import.
state = Path(os.environ['STUDIOHUB_DATA_DIR'])
token_file = state / '.hub_token'
token_file.write_text('hub-operator-token\n')
os.chmod(token_file, 0o600)
workers_file = Path(os.environ['STUDIOHUB_WORKERS_FILE'])
workers_file.write_text(json.dumps([{
    'id': 'image', 'modality': 'image', 'host': '127.0.0.1', 'port': 51101,
    'machine': 'local', 'local': True, 'runtime_identity': {'id': 'image-runtime'},
    'hub_token': 'hub-operator-token'}]))
os.chmod(workers_file, 0o600)

from components.hub import standalone
from components.hub.backend import auth, fleet_nodes, peers

A = 'office-imac-blue-m1-8-256-hub'
B = 'grace-2fs-imac-hub'
ADDRESS = 'http://100.64.0.1:47873'
STUDIOS = [{'id': 'image', 'modality': 'image'}]


def join(node_id, name):
    return fleet_nodes.join({'node_id': node_id, 'name': name,
                             'address': ADDRESS, 'studios': STUDIOS})


a = join(A, 'Office iMac')
b = join(B, 'Grace iMac')
group = peers.fleet_token()


def row(node_id):
    return fleet_nodes._read()[node_id]


async def proof():
    app = standalone.create_app(start_background=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url='http://fixture-hub') as client:
        # The node's own credential: presence is recorded.
        answer = await client.get(f'/api/hub/fleet/nodes/{A}/status',
                                  headers={'X-Hub-Token': a['credential']})
        assert answer.status_code == 200, answer.text
        assert answer.json()['state'] == 'member', answer.json()
        assert row(A)['last_status_at'] is not None, row(A)
        stamped = row(A)['last_status_at']

        # The shared Fleet token identifies nobody: it may read, and records no presence.
        answer = await client.get(f'/api/hub/fleet/nodes/{A}/status',
                                  headers={'X-Hub-Token': group})
        assert answer.status_code == 200, answer.text
        assert row(A)['last_status_at'] == stamped, row(A)

        # Another Mac's credential is refused, and the Mac it names is not stamped.
        answer = await client.get(f'/api/hub/fleet/nodes/{B}/status',
                                  headers={'X-Hub-Token': a['credential']})
        assert answer.status_code == 403, answer.text
        # `join` seeds no `last_status_at`; only `note_status_seen` writes it, so a never-stamped
        # Mac has no key at all (the roster projection reads it with `.get`). Absent == None here.
        assert row(B).get('last_status_at') is None, row(B)


asyncio.run(proof())
print('status presence: own credential stamps; group and cross-node do not')
'''


class DeadDial:
    """A controller that cannot reach the Mac at all — the one-way path, exactly."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get(self, url: str, timeout=None, **kwargs):
        self.calls.append(url)
        raise httpx.ConnectTimeout("timed out")

    async def aclose(self) -> None:
        return None


class StatusPresenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-status-presence-")
        self.addCleanup(self.temporary.cleanup)
        patcher = mock.patch.object(fleet_nodes, "DATA_DIR", Path(self.temporary.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        fleet_nodes.join({"node_id": NODE, "name": "Office iMac", "address": ADDRESS,
                          "studios": STUDIOS})
        # Joined 51 hours ago and never once dialled successfully.
        nodes = fleet_nodes._read()
        nodes[NODE]["joined_at"] = time.time() - 51 * 3600
        nodes[NODE]["last_seen_at"] = nodes[NODE]["joined_at"]
        fleet_nodes._write(nodes)

    def row(self) -> dict:
        return fleet_nodes._read()[NODE]

    def probe(self) -> dict:
        return asyncio.run(fleet_nodes.probe(fleet_nodes._read()[NODE], DeadDial()))

    def test_a_status_call_stamps_presence_and_clears_never_connected(self) -> None:
        before = self.probe()
        self.assertTrue(before["never_connected"])
        self.assertEqual("unreachable", before["state"])

        self.assertTrue(fleet_nodes.note_status_seen(NODE))

        after = self.probe()
        self.assertFalse(after["never_connected"], "the controller has now heard from this Mac")
        self.assertAlmostEqual(time.time(), self.row()["last_seen_at"], delta=5)
        self.assertAlmostEqual(time.time(), self.row()["last_status_at"], delta=5)
        self.assertEqual("status", self.row()["last_seen_via"])

    def test_presence_never_downgrades_transport_evidence(self) -> None:
        """A build polls status *and* checks in; the status ask must not erase the check-in.

        `dial_is_failing` and the pull transport read `last_seen_via`; overwriting `check-in` between
        check-ins would stop the controller recognising a Mac it knows it cannot dial.
        """
        fleet_nodes.record_checkin(NODE, {"address": ADDRESS, "name": "Office iMac",
                                          "version": "0.6.193", "studios": STUDIOS})
        checkin_at = self.row()["last_seen_at"]
        self.assertEqual("check-in", self.row()["last_seen_via"])
        self.assertTrue(fleet_nodes.dial_is_failing(self.row()))

        fleet_nodes.note_status_seen(NODE, at=checkin_at + 1)

        self.assertEqual("check-in", self.row()["last_seen_via"], "transport evidence was lost")
        self.assertTrue(fleet_nodes.dial_is_failing(self.row()))
        self.assertGreaterEqual(self.row()["last_seen_at"], checkin_at,
                                "a presence call must never move last seen backwards")

    def test_presence_in_the_first_minute_still_clears_never_connected(self) -> None:
        # `last_seen_at` is seeded at join, so a status call inside the first minute leaves
        # `last_seen_at - joined_at < 60`; the separate status field is what makes the flag honest.
        joined_at = time.time() - 25 * 3600
        nodes = fleet_nodes._read()
        nodes[NODE]["joined_at"] = joined_at
        nodes[NODE]["last_seen_at"] = joined_at
        fleet_nodes._write(nodes)

        fleet_nodes.note_status_seen(NODE, at=joined_at + 10)

        row = self.probe()
        self.assertFalse(row["never_connected"])
        self.assertLess(row["last_seen_at"] - joined_at, 60)

    def test_presence_does_not_make_a_mac_reachable_checkin_alive_or_pullable(self) -> None:
        fleet_nodes.note_status_seen(NODE)
        row = self.probe()
        # Heard from, and still not a Mac this controller can dial or hand work to.
        self.assertEqual("unreachable", row["state"])
        self.assertIsNone(row.get("via"))
        self.assertFalse(fleet_nodes.checkin_alive(self.row()))
        self.assertFalse(fleet_nodes.dial_is_failing(self.row()))

    def test_presence_carries_nothing_a_dial_would_have_returned(self) -> None:
        fleet_nodes.note_status_seen(NODE)
        row = self.row()
        for key in fleet_nodes._CHECKIN_KEYS:
            self.assertIsNone(row.get(key), f"a status call must not set {key}")
        self.assertIsNone(self.probe().get("version"))

    def test_unknown_and_removed_ids_are_not_stamped(self) -> None:
        self.assertFalse(fleet_nodes.note_status_seen("never-joined-hub"))
        fleet_nodes.remove(NODE)
        self.assertFalse(fleet_nodes.note_status_seen(NODE),
                         "a removed Mac must not be able to look alive")

    def test_the_status_read_itself_still_stamps_nothing(self) -> None:
        stamped = self.row()["last_seen_at"]
        fleet_nodes.status(NODE)
        self.assertEqual(stamped, self.row()["last_seen_at"])

    def test_only_a_nodes_own_credential_records_presence(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="hub-status-presence-route-")
        self.addCleanup(temporary.cleanup)
        state = temporary.name
        env = {key: value for key, value in os.environ.items() if not key.startswith("STUDIOHUB_")}
        env.update(STUDIOHUB_DATA_DIR=state, STUDIOHUB_MODELS_DIR=state + "/models",
                   STUDIOHUB_WORKERS_FILE=state + "/workers.json",
                   STUDIOHUB_PACKAGED="1", STUDIOHUB_REQUIRE_AUTH="1",
                   PYTHONPATH=str(PROJECT_ROOT))
        result = subprocess.run(
            [os.environ.get("STUDIOHUB_TEST_RUNTIME", sys.executable), "-c", PROOF],
            env=env, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
