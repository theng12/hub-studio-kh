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


if __name__ == "__main__":
    unittest.main()
