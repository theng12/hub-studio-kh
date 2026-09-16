"""What a fleet credential may do on a Mac it does not own.

A controller used to need the node's Hub token — the same credential that can delete that
Mac's generated files and change its settings. The fleet token exists so a controller can send
work and watch it without owning the machine.

The rule is an allowlist. A route added later is refused to a fleet caller until someone
decides it belongs, rather than being granted by default — which is the failure that matters
here, because a denylist silently widens every time a route is added.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from components.hub.backend.auth import FLEET_ALLOWED, fleet_path_allowed


class FleetPermissionTests(unittest.TestCase):
    def test_a_controller_can_do_the_work_it_exists_for(self) -> None:
        for method, path in (
            ("GET", "/api/health"),
            ("GET", "/api/version"),
            ("GET", "/api/hub/capabilities"),
            ("GET", "/api/hub/catalog"),
            ("GET", "/api/hub/model-packages"),
            ("GET", "/api/hub/stats"),
            ("GET", "/api/hub/workers"),
            ("GET", "/studio/voice/api/generate/availability"),
            ("GET", "/studio/subtitle/api/catalog"),
            # Submit, watch, collect, acknowledge, reconcile, cancel.
            ("POST", "/api/hub/jobs"),
            ("GET", "/api/hub/jobs"),
            ("GET", "/api/hub/jobs/abc123"),
            ("DELETE", "/api/hub/jobs/abc123"),
            ("GET", "/api/hub/jobs/abc123/items/0/artifact"),
            ("POST", "/api/hub/jobs/abc123/items/0/ack"),
            ("POST", "/api/hub/jobs/abc123/items/0/reconcile"),
            ("POST", "/api/hub/transcription/jobs"),
            ("GET", "/api/hub/transcription/jobs"),
            ("GET", "/api/hub/transcription/jobs/batch-1"),
            ("DELETE", "/api/hub/transcription/jobs/batch-1"),
            ("GET", "/api/hub/transcription/jobs/batch-1/items/0/artifact"),
            ("POST", "/api/hub/transcription/jobs/batch-1/items/0/ack"),
            ("POST", "/api/hub/voice-references"),
            ("GET", "/api/hub/voice-references/asset-1"),
            # Introducing itself, and seeing its siblings.
            ("POST", "/api/hub/fleet/nodes"),
            ("GET", "/api/hub/fleet/nodes"),
            # The fleet view, for an operator running the app on a Mac that is not the
            # controller. It reads health, disk and measured speed; it changes nothing.
            ("GET", "/api/hub/fleet/overview"),
        ):
            with self.subTest(method=method, path=path):
                self.assertTrue(fleet_path_allowed(method, path),
                                f"a controller needs {method} {path}")

    def test_a_controller_cannot_change_this_mac_or_delete_its_things(self) -> None:
        for method, path in (
            # Settings, storage policy and this Mac's role are the owner's.
            ("GET", "/api/hub/settings"),
            ("PUT", "/api/hub/settings"),
            ("POST", "/api/hub/settings/preview"),
            ("POST", "/api/hub/settings/restore-defaults"),
            ("GET", "/api/hub/controller"),
            ("PUT", "/api/hub/controller"),
            # Model management: installing, verifying, importing and removing.
            ("POST", "/api/hub/model-packages/whisper-turbo/import"),
            ("POST", "/api/hub/model-packages/whisper-turbo/verify"),
            ("POST", "/api/hub/model-packages/whisper-turbo/download"),
            ("GET", "/api/hub/model-downloads/run-1"),
            ("DELETE", "/api/hub/model-downloads/run-1"),
            # Memory control is a decision about this Mac's own resources.
            ("PUT", "/api/hub/memory/policy"),
            ("POST", "/api/hub/memory/release"),
            # Membership: a fleet member may introduce itself, not evict others.
            ("DELETE", "/api/hub/fleet/nodes/studio-2"),
            # Anything nobody has classified yet.
            ("POST", "/api/hub/there-is-no-such-route"),
            ("GET", "/api/hub/there-is-no-such-route"),
            ("PUT", "/api/hub/stats"),
            ("POST", "/api/hub/jobs/abc123"),
            ("GET", "/api/hub/jobs/abc123/items/0"),
        ):
            with self.subTest(method=method, path=path):
                self.assertFalse(fleet_path_allowed(method, path),
                                 f"a fleet credential must not reach {method} {path}")

    def test_the_method_is_part_of_the_permission(self) -> None:
        # Reading the memory policy is useful for routing; changing it is not the controller's.
        self.assertTrue(fleet_path_allowed("GET", "/api/hub/memory/policy"))
        self.assertFalse(fleet_path_allowed("PUT", "/api/hub/memory/policy"))
        # A node may list the fleet but not remove members.
        self.assertTrue(fleet_path_allowed("GET", "/api/hub/fleet/nodes"))
        self.assertFalse(fleet_path_allowed("DELETE", "/api/hub/fleet/nodes/other"))

    def test_a_path_that_merely_starts_the_same_is_not_granted(self) -> None:
        # Prefix walking is the classic way an allowlist is defeated.
        for path in ("/api/hub/jobs-extra", "/api/hub/jobs/abc/items/0/artifact/../../etc",
                     "/api/hub/jobs/abc/items/0/ack/extra", "/api/hub/catalog/../settings",
                     "/api/version/../../api/hub/settings"):
            with self.subTest(path=path):
                self.assertFalse(fleet_path_allowed("GET", path))
                self.assertFalse(fleet_path_allowed("POST", path))

    def test_every_entry_is_a_method_and_an_anchored_pattern(self) -> None:
        # A pattern that is not anchored would match anywhere in the path.
        for method, pattern in FLEET_ALLOWED:
            with self.subTest(pattern=pattern):
                self.assertIn(method, {"GET", "POST", "DELETE", "PUT"})
                self.assertTrue(pattern.startswith("^"), pattern)
                self.assertTrue(pattern.endswith("$"), pattern)


if __name__ == "__main__":
    unittest.main()
