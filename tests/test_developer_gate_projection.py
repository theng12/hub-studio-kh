"""The optional developer-gate inventory is projected only when it is exactly this node's."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.hub import developer_gate_contract as contract  # noqa: E402
from components.hub.backend import fleet_nodes  # noqa: E402

NOW = "2026-11-08T00:00:00Z"


def inventory(node: str = "node-1", controller: str = "ctrl-1") -> dict:
    return {
        "format": contract.INVENTORY_FORMAT,
        "node_id": node,
        "controller_id": controller,
        "observed_at": NOW,
        "internal_physical_drive": {
            "device_id": "disk0", "internal": True, "physical": True,
            "size_bytes": 500_000_000_000, "source": "unknown",
            "workspace_backing_device_id": "disk0", "backing_source": "unknown",
            "observed_at": NOW, "reason": None,
        },
        "free_bytes": 0,
        "toolchains": [],
        "developer_gate_space": {
            "observed_at": NOW, "workspace_bytes": 0, "result_spool_bytes": 0,
            "shared_cache_bytes": 0, "workspace_limit_bytes": 1,
            "result_spool_limit_bytes": 1, "shared_cache_limit_bytes": 0,
            "cleanup_state": "none", "reason": None,
        },
    }


class DeveloperGateProjectionTests(unittest.TestCase):
    def test_resources_keep_only_this_node_and_controllers_inventory(self) -> None:
        kept = fleet_nodes._resources_of(
            {"developer_gate_inventory": inventory()}, "node-1", "ctrl-1")
        self.assertEqual(kept["developer_gate_inventory"]["node_id"], "node-1")

        for value in (
                {"developer_gate_inventory": inventory(node="node-2")},
                {"developer_gate_inventory": inventory(controller="ctrl-2")},
                {"developer_gate_inventory": {"format": contract.INVENTORY_FORMAT}},
                {"developer_gate_inventory": "not-a-dict"}):
            self.assertNotIn(
                "developer_gate_inventory",
                fleet_nodes._resources_of(value, "node-1", "ctrl-1"), value)

    def test_snapshot_projection_omits_missing_or_foreign_inventory(self) -> None:
        row = {"id": "node-1"}
        with mock.patch("components.hub.backend.control_plane.load_settings",
                        return_value={"controller_id": "ctrl-1"}):
            kept = fleet_nodes._from_snapshot(
                row, {"developer_gate_inventory": inventory()})
            self.assertEqual(kept["developer_gate_inventory"]["controller_id"], "ctrl-1")

            for value in (
                    {},
                    {"developer_gate_inventory": inventory(node="node-2")},
                    {"developer_gate_inventory": inventory(controller="ctrl-2")},
                    {"developer_gate_inventory": {}}):
                self.assertNotIn(
                    "developer_gate_inventory",
                    fleet_nodes._from_snapshot(row, value), value)


if __name__ == "__main__":
    unittest.main()
