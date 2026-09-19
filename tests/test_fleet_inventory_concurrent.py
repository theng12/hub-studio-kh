"""The fleet inventory must probe every node at once, not one after another.

`inventory` awaited `probe` in a loop. Each probe waits out its own timeout when a node is silent, so
the cost of the page was one timeout per switched-off Mac: a controller with ten of them took ten
timeouts to answer, which the app reports as the Hub not answering at all. The probe itself is
unchanged - only the waiting is shared.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from components.hub.backend import fleet_nodes  # noqa: E402

NODES = {f"node-{index}": {"id": f"node-{index}", "name": f"Node {index}",
                           "address": f"http://10.0.0.{index}:47873"}
         for index in range(4)}


class FleetInventoryConcurrencyTests(unittest.TestCase):
    def test_the_whole_inventory_costs_one_probe_wait_not_one_per_node(self):
        """Four nodes that each take 0.15 s must finish in about 0.15 s, not about 0.6 s."""
        async def probe(node, client=None):
            await asyncio.sleep(0.15)
            return {**node, "state": "unreachable"}

        async def run():
            with mock.patch.object(fleet_nodes, "_read", lambda: dict(NODES)), \
                 mock.patch.object(fleet_nodes, "probe", probe):
                loop = asyncio.get_running_loop()
                started = loop.time()
                result = await fleet_nodes.inventory()
                return result, loop.time() - started

        result, elapsed = asyncio.run(run())
        self.assertEqual(len(result["nodes"]), len(NODES))
        sequential = 0.15 * len(NODES)
        self.assertLess(elapsed, sequential / 2,
                        f"probes ran one after another: {elapsed:.3f}s for {len(NODES)} nodes")

    def test_probes_start_together(self):
        """Measured from inside: every probe begins before the first one ends."""
        starts = []

        async def probe(node, client=None):
            starts.append(asyncio.get_running_loop().time())
            await asyncio.sleep(0.15)
            return {**node, "state": "unreachable"}

        async def run():
            with mock.patch.object(fleet_nodes, "_read", lambda: dict(NODES)), \
                 mock.patch.object(fleet_nodes, "probe", probe):
                await fleet_nodes.inventory()

        asyncio.run(run())
        self.assertEqual(len(starts), len(NODES))
        self.assertLess(max(starts) - min(starts), 0.1,
                        f"probes did not start together: spread {max(starts) - min(starts):.3f}s")

    def test_the_probe_still_reports_state_and_detail(self):
        async def probe(node, client=None):
            return {**node, "state": "unreachable", "detail": "could not reach the node"}

        async def run():
            with mock.patch.object(fleet_nodes, "_read", lambda: dict(NODES)), \
                 mock.patch.object(fleet_nodes, "probe", probe):
                return await fleet_nodes.inventory()

        result = asyncio.run(run())
        self.assertEqual(len(result["nodes"]), len(NODES))
        self.assertTrue(all(row["state"] == "unreachable" for row in result["nodes"]))


if __name__ == "__main__":
    unittest.main()
