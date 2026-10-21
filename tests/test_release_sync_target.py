"""A Mac that joined a controller mirrors that controller's release, whatever its role still says.

The app's join records the adopted credential and the controller address but not the role, so the
default ``standalone`` survives a join; requiring ``role == "agent"`` to mirror can strand a joined
Mac before it can pull the build that makes it visible. These tests pin the contract
``fleet_nodes.membership()`` documents: the credential is the test, not the role, with a controller
as the one exclusion.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# A reserved Tailscale-range address, deliberately not the real controller's.
CONTROLLER = "http://100.64.0.1:47873"
NODE_CREDENTIAL = "node-credential-aaaaaaaaaaaaaaaa"


class ControllerTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="studiohub-release-target-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

        self.control_plane = importlib.import_module("components.hub.backend.control_plane")
        self.peers = importlib.import_module("components.hub.backend.peers")
        self.fleet_nodes = importlib.import_module("components.hub.backend.fleet_nodes")
        self.release_sync = importlib.import_module("components.hub.backend.release_sync")

        for module, attribute, name in (
            (self.control_plane, "SETTINGS_FILE", "controller_settings.json"),
            (self.control_plane, "DATABASE_URL_FILE", ".controller_database_url"),
            (self.peers, "FLEET_TOKEN_FILE", ".fleet_token"),
            (self.peers, "SHARED_STUDIO_TOKEN_FILE", ".kh_studio_token"),
            (self.fleet_nodes, "NODE_CREDENTIAL_FILE", ".node_credential"),
        ):
            original = getattr(module, attribute)
            self.addCleanup(setattr, module, attribute, original)
            setattr(module, attribute, self.root / name)
        original_cache = self.control_plane._settings_cache
        self.addCleanup(setattr, self.control_plane, "_settings_cache", original_cache)
        self.control_plane._settings_cache = None
        cache = self.peers._cache
        self.addCleanup(setattr, self.peers, "_cache", cache)
        self.peers._cache = {}
        # A fleet token or database URL in the environment would answer for a Mac that holds
        # neither, which is the one case the last test distinguishes.
        self._env = {name: os.environ.pop(name)
                     for name in ("STUDIOHUB_FLEET_TOKEN", "STUDIOHUB_DATABASE_URL")
                     if name in os.environ}
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        os.environ.update(self._env)

    def joined(self, *, role: str, origin: str = "adopted",
               controller: str | None = CONTROLLER, credential: str | None = NODE_CREDENTIAL) -> None:
        self.control_plane.save_settings({
            "role": role, "fleet_credential_origin": origin,
            "parent_controller_url": controller,
        })
        self.fleet_nodes.set_node_credential(credential)

    def test_a_joined_mac_whose_role_is_still_standalone_has_a_controller(self) -> None:
        # The regression: a successful join that never set the role was invisible to release_sync,
        # so it could not pull the build that would make it reachable.
        self.joined(role="standalone")
        self.assertEqual(
            self.release_sync.controller_target(),
            (CONTROLLER, {"X-Hub-Token": NODE_CREDENTIAL}))

    def test_a_mac_switched_to_the_node_role_still_has_a_controller(self) -> None:
        self.joined(role="agent")
        self.assertEqual(
            self.release_sync.controller_target(),
            (CONTROLLER, {"X-Hub-Token": NODE_CREDENTIAL}))

    def test_a_controller_never_targets_itself_or_another_controller(self) -> None:
        # Promotion mints this Mac's own credential and clears the adopted origin; this also holds
        # if a stale file still carries both, so a controller can never mirror as a node.
        self.joined(role="controller")
        self.assertIsNone(self.release_sync.controller_target())

    def test_a_credential_this_hub_minted_is_not_a_membership(self) -> None:
        self.joined(role="agent", origin="minted")
        self.assertIsNone(self.release_sync.controller_target())

    def test_a_controller_address_that_does_not_validate_is_not_a_membership(self) -> None:
        for bad in ("http://example.com:47873", "https://100.64.0.1:47873",
                    "http://100.64.0.1", "", None):
            with self.subTest(bad=bad):
                self.joined(role="standalone", controller=bad)
                self.assertIsNone(self.release_sync.controller_target())

    def test_no_credential_yet_means_an_empty_header_not_a_minted_token(self) -> None:
        # Before the first join is answered a member may hold no node credential and no group
        # token; it must not mint one here, and it must still name its controller.
        self.joined(role="standalone", credential=None)
        target = self.release_sync.controller_target()
        self.assertEqual(target, (CONTROLLER, {}))


if __name__ == "__main__":
    unittest.main()
