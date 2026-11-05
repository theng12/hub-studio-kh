"""The controller's own roster identity, recorded with the credential that came from it.

No hostname, address or label is ever promoted into an identity.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub import developer_tests as dt
from components.hub.backend import control_plane, fleet_nodes


class ControllerIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-controller-id-")
        self.addCleanup(self.temporary.cleanup)
        self.old_data = fleet_nodes.DATA_DIR
        fleet_nodes.DATA_DIR = Path(self.temporary.name)
        self.addCleanup(setattr, fleet_nodes, "DATA_DIR", self.old_data)

    def test_join_returns_the_controllers_own_roster_identity(self) -> None:
        with patch.object(fleet_nodes, "this_machine",
                          return_value={"id": "controller-a", "name": "Controller A"}):
            answer = fleet_nodes.join({
                "node_id": "node-a", "name": "Node A",
                "address": "http://192.168.1.21:47873", "studios": [],
            })
        self.assertEqual(answer["controller"],
                         {"id": "controller-a", "name": "Controller A"})

    def test_settings_persist_and_clear_the_actual_parent_controller_id(self) -> None:
        settings_file = Path(self.temporary.name) / "controller_settings.json"
        with patch.object(control_plane, "SETTINGS_FILE", settings_file), \
                patch.object(control_plane, "_settings_cache", None):
            control_plane.save_settings({
                "role": "agent",
                "fleet_credential_origin": "adopted",
                "parent_controller_url": "http://100.64.0.1:47873",
                "parent_controller_id": "controller-a",
            })
            saved = json.loads(settings_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["parent_controller_id"], "controller-a")

            control_plane.save_settings({"fleet_credential_origin": "minted"})
            saved = json.loads(settings_file.read_text(encoding="utf-8"))
            self.assertIsNone(saved["parent_controller_id"])

            with self.assertRaises(ValueError):
                control_plane.save_settings({
                    "fleet_credential_origin": "adopted",
                    "parent_controller_url": "http://100.64.0.1:47873",
                    "parent_controller_id": "bad/id",
                })

    def test_repoint_without_a_new_identity_clears_the_old_one(self) -> None:
        settings_file = Path(self.temporary.name) / "controller_settings.json"
        with patch.object(control_plane, "SETTINGS_FILE", settings_file), \
                patch.object(control_plane, "_settings_cache", None):
            control_plane.save_settings({
                "role": "agent", "fleet_credential_origin": "adopted",
                "parent_controller_url": "http://100.64.0.1:47873",
                "parent_controller_id": "controller-a"})
            control_plane.save_settings({"parent_controller_url": "http://100.64.0.2:47873"})
            self.assertIsNone(control_plane.load_settings()["parent_controller_id"])

    def test_identities_use_the_persisted_id_and_refuse_an_address(self) -> None:
        with patch.object(fleet_nodes, "this_machine", return_value={"id": "node-a"}), \
                patch.object(control_plane, "load_settings", return_value={
                    "parent_controller_url": "http://100.64.0.1:47873",
                    "parent_controller_id": "controller-a"}):
            self.assertEqual(dt._identities(), ("node-a", "controller-a"))
        with patch.object(fleet_nodes, "this_machine", return_value={"id": "node-a"}), \
                patch.object(control_plane, "load_settings", return_value={
                    "parent_controller_url": "http://100.64.0.1:47873"}):
            self.assertEqual(dt._identities(), ("node-a", None))


if __name__ == "__main__":
    unittest.main()
