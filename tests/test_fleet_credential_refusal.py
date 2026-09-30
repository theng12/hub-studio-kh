"""A node that refuses this controller must not be reported as merely unreachable.

The probe treated any non-success answer as an error, and the Fleet page's plainest reading of a node
that does not answer is "unreachable". A 401/403 means the opposite: the Mac is up and answering, and
it refused *this controller* - which happens when a node was re-joined with its own token, or the
controller's fleet token was replaced. Reporting it as unreachable sends the operator to the network
instead of the credential.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub.backend import fleet_nodes  # noqa: E402

NODE = {"id": "node-1", "name": "Studio Mini 01", "address": "http://10.0.0.5:47873",
        "role": "node", "studios": [], "joined_at": 1.0}


class _Response:
    def __init__(self, status):
        self.status_code = status
        self.is_success = 200 <= status < 300
        self.text = ""

    def json(self):
        return {"version": "0.6.15"}


class _Client:
    def __init__(self, status):
        self.status = status
        self.calls = 0

    async def get(self, url, timeout=None):
        self.calls += 1
        return _Response(self.status)

    async def aclose(self):
        pass


class CredentialRefusalTests(unittest.TestCase):
    def _probe(self, status):
        return asyncio.run(fleet_nodes.probe(dict(NODE), _Client(status)))

    def test_a_rejected_credential_is_its_own_state_with_an_actionable_detail(self):
        row = self._probe(401)
        self.assertEqual(row["state"], "unauthorized")
        self.assertIn("Fleet Token", row["detail"])
        self.assertIn("401", row["detail"])
        self.assertEqual(row["name"], NODE["name"], "the node's identity is still reported")

    def test_forbidden_reads_the_same_way(self):
        self.assertEqual(self._probe(403)["state"], "unauthorized")

    def test_other_failures_stay_errors(self):
        row = self._probe(500)
        self.assertEqual(row["state"], "error")
        self.assertIn("500", row["detail"])

    def test_a_healthy_node_is_still_reachable(self):
        row = self._probe(200)
        self.assertEqual(row["state"], "reachable")
        self.assertEqual(row["version"], "0.6.15")

    def test_the_summary_counts_it_separately(self):
        async def probe(node, client=None):
            return {**node, "state": "unauthorized", "detail": "rejected"}

        async def run():
            with mock.patch.object(fleet_nodes, "_read", lambda: {"node-1": dict(NODE)}), \
                 mock.patch.object(fleet_nodes, "probe", probe):
                return await fleet_nodes.inventory()

        result = asyncio.run(run())
        self.assertEqual(result["summary"]["unauthorized"], 1)
        self.assertEqual(result["summary"]["unreachable"], 0,
                         "a refused credential is not a silent Mac")


class _RaisingClient:
    """A client whose GET fails the way the network fails."""

    def __init__(self, exc):
        self.exc = exc

    async def get(self, url, timeout=None):
        raise self.exc

    async def aclose(self):
        pass


class UnreachableReasonTests(unittest.TestCase):
    """**Why** a node did not answer, as a field rather than a sentence.

    The reason decides what the operator is told to do, and the two answers are opposite:
    a refused TCP connection means the Mac is up and its Studio Hub is not running, while
    dropped packets mean the Mac is asleep, off, or firewalled — nothing about which is the
    Hub's fault. That distinction was only ever in prose, spelled two different ways by the
    two paths below, so a reader could not act on it without parsing a sentence. The gateway
    consequently told the owner to *"fix Studio Hub on that Mac"* for machines that were
    simply switched off.
    """

    def test_a_refused_connection_is_no_hub(self):
        # TCP refused: the Mac answered and nothing is listening on :47873.
        row = asyncio.run(fleet_nodes.probe(
            dict(NODE), _RaisingClient(httpx.ConnectError("Connection refused"))))
        self.assertEqual(row["state"], "unreachable")
        self.assertEqual(row["reason"], "no_hub")

    def test_a_dropped_packet_is_unreachable(self):
        # Packets dropped: the Mac is asleep/off, or a firewall is blocking the port.
        row = asyncio.run(fleet_nodes.probe(
            dict(NODE), _RaisingClient(httpx.ConnectTimeout("timed out"))))
        self.assertEqual(row["state"], "unreachable")
        self.assertEqual(row["reason"], "unreachable")

    def test_a_poll_snapshot_carries_its_own_status_as_the_reason(self):
        from_snapshot = fleet_nodes._from_snapshot
        no_hub = from_snapshot(dict(NODE), {"reachable": False, "auth": True, "status": "no_hub"})
        self.assertEqual(no_hub["reason"], "no_hub")
        dropped = from_snapshot(dict(NODE), {"reachable": False, "auth": True})
        self.assertEqual(dropped["reason"], "unreachable",
                         "a snapshot that states no status is not a Hub that refused")

    def test_both_paths_name_the_same_vocabulary(self):
        # The point of the field: whichever path produced the row, the reason reads the
        # same way, so a consumer never has to know which one it was.
        rows = [
            asyncio.run(fleet_nodes.probe(
                dict(NODE), _RaisingClient(httpx.ConnectError("refused")))),
            fleet_nodes._from_snapshot(
                dict(NODE), {"reachable": False, "auth": True, "status": "no_hub"}),
        ]
        self.assertEqual({r["reason"] for r in rows}, {"no_hub"})


if __name__ == "__main__":
    unittest.main()
