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

from components.hub.backend.auth import (
    FLEET_ALLOWED,
    fleet_path_allowed,
    node_origin_allowed,
    node_path_allowed,
)


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
            ("GET", "/studio/image/api/health"),
            ("GET", "/studio/music/api/health"),
            ("GET", "/studio/music/api/catalog"),
            ("GET", "/studio/voice/api/fleet/activity"),
            ("GET", "/studio/transcription/api/transcribe/availability"),
            ("GET", "/studio/image/api/memory-policy"),
            ("GET", "/studio/voice/api/generate/jobs/job-1"),
            ("GET", "/studio/voice/api/generate/jobs/job-1/audio"),
            ("GET", "/studio/image/api/generate/jobs/job-2/image"),
            ("GET", "/studio/music/api/generate/jobs/job-4/audio"),
            ("GET", "/studio/subtitle/api/generate/transcribe/jobs"),
            ("GET", "/studio/subtitle/api/generate/transcribe/jobs/job-3"),
            ("GET", "/studio/subtitle/api/generate/transcribe/jobs/job-3/artifact"),
            ("GET", "/studio/voice/api/voices/seed-catalog"),
            ("GET", "/studio/voice/api/voices/voice-1/audio"),
            # Submit, watch, collect, acknowledge, reconcile, cancel.
            ("POST", "/api/hub/jobs"),
            ("POST", "/studio/music/api/generate/txt2music"),
            ("POST", "/studio/music/api/memory/release"),
            ("DELETE", "/studio/music/api/generate/job-4"),
            ("GET", "/api/hub/jobs"),
            ("GET", "/api/hub/jobs/abc123"),
            ("DELETE", "/api/hub/jobs/abc123"),
            ("GET", "/api/hub/jobs/abc123/items/0/artifact"),
            ("POST", "/api/hub/jobs/abc123/items/0/ack"),
            ("POST", "/api/hub/jobs/abc123/items/0/reconcile"),
            # The controller may release a node by discarding an uncertain attempt.
            ("POST", "/api/hub/jobs/abc123/items/0/discard"),
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
            # Whether it is still listed: how a removal on the controller reaches the Mac.
            ("GET", "/api/hub/fleet/nodes/node-1/status"),
            # The fleet view, for an operator running the app on a Mac that is not the
            # controller. It reads health, disk and measured speed; it changes nothing.
            ("GET", "/api/hub/fleet/overview"),
            # One Mac's own final renders, which a controller lists on its Render tab
            # (2026-10-26). A read of what that Mac already recorded, like its Trash Folder.
            ("GET", "/api/hub/fleet/nodes/node-1/renders"),
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
            # Anything nobody has classified yet.
            ("POST", "/api/hub/fleet/nodes/node-1/status"),
            ("POST", "/api/hub/there-is-no-such-route"),
            ("GET", "/api/hub/there-is-no-such-route"),
            ("PUT", "/api/hub/stats"),
            # The Render tab's own page is this Mac's view, not a fleet read: a controller
            # aggregates by asking each Mac's `/fleet/nodes/<id>/renders`, never this one.
            ("GET", "/api/hub/renders"),
            ("POST", "/api/hub/jobs/abc123"),
            ("GET", "/api/hub/jobs/abc123/items/0"),
            # Worker reads and metadata edits are individually classified.
            ("GET", "/studio/voice/api/settings"),
            ("GET", "/studio/voice/api/downloads"),
            ("GET", "/studio/voice/api/model-storage"),
            ("GET", "/studio/voice/api/not-yet-classified"),
            ("GET", "/studio/voice/api/generate/jobs"),
            ("GET", "/studio/voice/api/generate/jobs/job-1/secrets"),
            ("PATCH", "/studio/voice/api/not-yet-classified"),
            ("PATCH", "/studio/voice/api/settings"),
            ("PATCH", "/studio/voice/api/voices/voice-1/audio"),
            ("PATCH", "/studio/image/api/voices/voice-1"),
            # **Removing a node left this list deliberately, in 0.6.92 (A2).** The
            # allowlist refused every fleet credential outright, which made "Leave this
            # group" a 100% failure: the app reported success while the controller still
            # listed the Mac. The route is now reachable and the *self*-check lives in
            # the handler (`auth.self_removal_allowed`), because an allowlist has no
            # idea who is asking. **Removing a different machine is still refused** —
            # `test_standalone_connections.LeavingAndRemovalAreRealTest` pins both
            # halves against a running Hub.
        ):
            with self.subTest(method=method, path=path):
                self.assertFalse(fleet_path_allowed(method, path),
                                 f"a fleet credential must not reach {method} {path}")

    def test_editing_a_saved_voice_is_allowed_but_creating_one_is_not(self) -> None:
        """A voice's metadata is the node owner's, but a transcript typo has to be fixable
        from the controller, so PATCH is narrower than POST/DELETE on the same path."""
        self.assertTrue(fleet_path_allowed("GET", "/studio/voice/api/voices"))
        self.assertTrue(fleet_path_allowed("PATCH", "/studio/voice/api/voices/voice-1"))
        self.assertFalse(fleet_path_allowed("POST", "/studio/voice/api/voices"))
        self.assertFalse(fleet_path_allowed("DELETE", "/studio/voice/api/voices/voice-1"))

    def test_the_method_is_part_of_the_permission(self) -> None:
        # Reading the memory policy is useful for routing; changing it is not the controller's.
        self.assertTrue(fleet_path_allowed("GET", "/api/hub/memory/policy"))
        self.assertFalse(fleet_path_allowed("PUT", "/api/hub/memory/policy"))
        # A node may list the fleet, and may *reach* the removal route — allowed here because
        # the id it names is checked against the caller's own identity in the route, which this
        # function cannot see. Pressing it about another Mac is still refused, one layer in.
        self.assertTrue(fleet_path_allowed("GET", "/api/hub/fleet/nodes"))
        self.assertTrue(fleet_path_allowed("DELETE", "/api/hub/fleet/nodes/other"))
        self.assertFalse(fleet_path_allowed("DELETE", "/api/hub/fleet/nodes/other/readmit"),
                         "re-admitting a removed Mac is the owner's decision and stays out")

    def test_a_controller_can_release_only_known_studio_workers(self) -> None:
        for modality in ("voice", "subtitle", "image", "transcription", "music", "chat",
                         "embeddings", "vision"):
            path = f"/studio/{modality}/api/memory/release"
            with self.subTest(modality=modality):
                self.assertTrue(fleet_path_allowed("POST", path))
                self.assertFalse(node_path_allowed("POST", path))
                self.assertFalse(node_origin_allowed("POST", path))

        for method, path in (
            ("PUT", "/studio/music/api/memory/release"),
            ("POST", "/studio/video/api/memory/release"),
            ("POST", "/studio/unknown/api/memory/release"),
            ("POST", "/studio/music/api/memory/release/extra"),
            ("POST", "/api/hub/memory/release"),
            ("PUT", "/studio/music/api/memory-policy"),
            ("POST", "/studio/music/api/settings"),
        ):
            with self.subTest(method=method, path=path):
                self.assertFalse(fleet_path_allowed(method, path))

    def test_a_path_that_merely_starts_the_same_is_not_granted(self) -> None:
        # Prefix walking is the classic way an allowlist is defeated.
        for path in ("/api/hub/jobs-extra", "/api/hub/jobs/abc/items/0/artifact/../../etc",
                     "/api/hub/jobs/abc/items/0/ack/extra", "/api/hub/catalog/../settings",
                     "/api/version/../../api/hub/settings"):
            with self.subTest(path=path):
                self.assertFalse(fleet_path_allowed("GET", path))
                self.assertFalse(fleet_path_allowed("POST", path))

    def test_a_rejected_section_review_candidate_is_four_exact_reads(self) -> None:
        handle = "eyJqIjoiYWJjIn0.c2lnbmF0dXJl-_"
        allowed = (
            "/api/hub/jobs/batch-1/items/0/review-candidate",
            "/api/hub/jobs/batch-1/items/0/review-candidate/audio",
            "/studio/voice/api/fleet/jobs/c85e8c5d0a1b/review-candidate",
            f"/studio/voice/api/fleet/jobs/c85e8c5d0a1b/review-candidate/media/{handle}",
        )
        for path in allowed:
            with self.subTest(path=path):
                self.assertTrue(fleet_path_allowed("GET", path))
                for method in ("POST", "PUT", "PATCH", "DELETE"):
                    self.assertFalse(fleet_path_allowed(method, path))
                self.assertFalse(fleet_path_allowed("GET", path + "/extra"))
                self.assertFalse(node_path_allowed("GET", path))
        for path in (
            "/api/hub/jobs/batch-1/items/0/review-candidate/audio/../../artifact",
            "/api/hub/jobs/batch-1/items/x/review-candidate",
            "/studio/voice/api/fleet/jobs/C85E8C5D0A1B/review-candidate",
            "/studio/voice/api/fleet/jobs/c85e8c5d0a1b/details",
            f"/studio/voice/api/fleet/jobs/c85e8c5d0a1b/media/{handle}",
            "/studio/voice/api/fleet/jobs/c85e8c5d0a1b/review-candidate/media/no-dot",
            "/studio/voice/api/fleet/jobs/c85e8c5d0a1b/review-candidate/media/a.b/c",
            "/studio/image/api/fleet/jobs/c85e8c5d0a1b/review-candidate",
            "/studio/voice/api/fleet/jobs",
            "/studio/voice/api/generate/jobs",
        ):
            with self.subTest(path=path):
                self.assertFalse(fleet_path_allowed("GET", path))

    def test_every_entry_is_a_method_and_an_anchored_pattern(self) -> None:
        # A pattern that is not anchored would match anywhere in the path.
        for method, pattern in FLEET_ALLOWED:
            with self.subTest(pattern=pattern):
                self.assertIn(method, {"GET", "POST", "DELETE", "PUT", "PATCH"})
                self.assertTrue(pattern.startswith("^"), pattern)
                self.assertTrue(pattern.endswith("$"), pattern)


if __name__ == "__main__":
    unittest.main()
