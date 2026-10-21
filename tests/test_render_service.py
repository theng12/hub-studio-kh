#!/usr/bin/env python3
"""Authenticated final-render service boundary; no network, tools, or media."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.hub.backend.render_service import (  # noqa: E402
    RenderService,
    register_relay_routes,
    register_routes,
)
from components.hub.backend.render_assets import AssetNotFoundError  # noqa: E402
from components.hub.backend.final_render_compiler import (  # noqa: E402
    compile_final_render_packet,
)
from components.hub.backend.render_jobs import RenderJobUnavailable  # noqa: E402
from components.hub.backend import auth, fleet_nodes  # noqa: E402


PACKET = json.loads(
    (ROOT / "tests/fixtures/final-render-compiler-v1.json").read_text()
)["f0_base_audio"]["packet"]


def closed_request(key: str = "dispatch-1", *, base_handle: str = "a" * 32) -> bytes:
    return json.dumps({
        "format": "studio.render.hub-request.v1",
        "dispatch": {
            "story_uuid": "story", "intent_id": "intent", "attempt_no": 1,
            "gateway_attempt_key": "gateway-1", "destination_site": "site-a",
            "destination_controller": "controller-a", "destination_node": "node-a",
            "dispatch_key": key, "dispatch_fence": "fence-1",
        },
        "packet": PACKET,
        "assets": {"audio": "b" * 32, "base": base_handle},
    }, sort_keys=True, separators=(",", ":")).encode()


class FakeAssets:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.descriptors = {
            "a" * 32: {"sha256": PACKET["manifest"][1]["sha256"], "size_bytes": 21},
            "b" * 32: {"sha256": PACKET["manifest"][0]["sha256"], "size_bytes": 22},
        }
        self.status_calls = []
        self.released = []
        self.sweeps = []

    def upload_by_digest(self, owner_scope, sha256, size_bytes):
        self.status_calls.append((owner_scope, sha256, size_bytes))
        if sha256 == "0" * 64:
            raise AssetNotFoundError("missing")
        return {"sha256": sha256, "size_bytes": size_bytes,
                "upload_id": "c" * 32, "committed_offset": size_bytes,
                "state": "published", "asset_handle": "a" * 32}

    def describe(self, owner_scope, handle):
        self.events.append(f"describe:{handle[0]}")
        return dict(self.descriptors[handle])

    def acquire_reference(self, owner_scope, handle, reference):
        self.events.append(f"reference:{handle[0]}")

    def release_reference(self, owner_scope, handle, reference):
        self.released.append((owner_scope, handle, reference))

    def release_reference_everywhere(self, owner_scope, reference):
        self.released.append((owner_scope, "*", reference))
        return 1

    def retire_stale(self, *, now=None, **_kwargs):
        self.sweeps.append(now)
        return {"retired": 0, "published": 0, "retained": 0}


class FakeRuntime:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.rows = {}

    def by_key(self, owner_scope, key):
        return self.rows.get((owner_scope, key))

    def accept(self, owner_scope, key, raw, *, qualification_permit=None):
        self.events.append("accept")
        row = {
            "id": "job-1", "owner_scope": owner_scope, "dispatch_key": key,
            "accepted_request_sha256": hashlib.sha256(raw).hexdigest(),
            "state": "accepted", "replayed": False, "purpose": (
                "qualification" if qualification_permit is not None else "production"),
        }
        self.rows[(owner_scope, key)] = row
        return dict(row)

    def get(self, owner_scope, job_id):
        return next((dict(row) for (scope, _), row in self.rows.items()
                     if scope == owner_scope and row["id"] == job_id), None)

    def acquire_file_lease(self, owner_scope, job_id, kind, lease_id):
        self.events.append(("lease", owner_scope, job_id, kind, lease_id))

    def release_file_lease(self, owner_scope, job_id, kind, lease_id):
        self.events.append(("release", owner_scope, job_id, kind, lease_id))

    def execution_context(self, owner_scope, job_id, attempt_id, launch_token):
        row = self.get(owner_scope, job_id)
        if row is None or attempt_id != row.get("attempt_id") or launch_token is not None:
            raise ValueError("wrong attempt")
        return type("Context", (), {"assets": {"base": "a" * 32}})()

    def request_cancel(self, owner_scope, job_id):
        row = self.get(owner_scope, job_id)
        if row:
            row["state"] = "cancel_requested"
        return row

    def request_cancel_by_key(self, owner_scope, key, expected_request_sha256):
        self.events.append(("cancel-key", owner_scope, key, expected_request_sha256))
        row = self.by_key(owner_scope, key)
        if row is not None:
            row = dict(row)
            row["state"] = "cancelled"
            return row
        return {
            "state": "cancelled_before_acceptance", "owner_scope": owner_scope,
            "dispatch_key": key, "expected_request_sha256": expected_request_sha256,
            "fence_id": "f" * 32, "cancelled_at": 1.0,
        }


class FakeAuthority:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def qualification_permit(self, vector_id, owner_scope, raw, descriptors):
        self.events.append(f"permit:{vector_id}")
        return object() if vector_id == "sealed-m4" else None


class FakeExecutor:
    def __init__(self, runtime, events):
        self.runtime = runtime
        self.events = events

    async def request_cancel(self, owner_scope, job_id):
        self.events.append(("executor-cancel", owner_scope, job_id))
        return self.runtime.get(owner_scope, job_id)


class SnapshotAuthority(FakeAuthority):
    """The installer lookup the encode preflight reads for a production request."""

    def __init__(self, events: list[str], authority) -> None:
        super().__init__(events)
        self.authority = authority

    def snapshot(self):
        self.events.append("snapshot")
        return self.authority


class CandidateAuthority(FakeAuthority):
    """The installer lookup the encode preflight reads for a qualification request."""

    def __init__(self, events: list[str], authority, intent_id="sealed-m4") -> None:
        super().__init__(events)
        self.authority = authority
        self.intent_id = intent_id

    def qualification_candidates(self):
        self.events.append("candidates")
        return ([({"intent_id": self.intent_id}, self.authority)]
                if self.authority is not None else [])


def _compiled_tier(packet: dict):
    plan = compile_final_render_packet(
        json.dumps(packet, sort_keys=True, separators=(",", ":")).encode(),
        {"audio": "/dev/fd/0", "base": "/dev/fd/1"}, "final.mp4")
    tier = plan.tiers[0]
    encoder = next(arg for arg in tier.argv
                   if arg in ("libx264", "libx265", "h264_videotoolbox", "libvpx-vp9"))
    return tier, encoder


class RenderServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "_trash/render-service-testroots" / uuid4().hex
        self.root.mkdir(parents=True)
        self.events = []
        self.runtime = FakeRuntime(self.events)
        self.runtime.root = self.root
        self.service = RenderService(
            self.runtime, FakeAssets(self.events), executor=None,
            authority_loader=FakeAuthority(self.events),
        )

    def test_assets_are_exactly_verified_and_referenced_before_fresh_acceptance(self) -> None:
        raw = closed_request()

        accepted = self.service.accept(
            "site-a:node-a:fleet", "dispatch-1", raw,
            qualification_vector="sealed-m4")

        self.assertEqual(accepted["purpose"], "qualification")
        self.assertEqual(
            self.events,
            ["permit:sealed-m4", "describe:b", "describe:a",
             "reference:b", "reference:a", "accept"],
        )

    def test_encode_preflight_refuses_before_any_reference_or_accept(self) -> None:
        owner = "site-a:node-a:fleet"
        tier, encoder = _compiled_tier(PACKET)
        loader = SnapshotAuthority(
            self.events, SimpleNamespace(selected_tier=tier.label, exact_encoder=encoder))
        service = RenderService(
            self.runtime, FakeAssets(self.events), executor=None,
            authority_loader=loader)
        service.accept(owner, "dispatch-1", closed_request("dispatch-1"))
        self.assertIn("accept", self.events)

        events: list[str] = []
        runtime = FakeRuntime(events)
        runtime.root = self.root
        wrong = SnapshotAuthority(
            events, SimpleNamespace(selected_tier="not a compiled tier",
                                    exact_encoder=encoder))
        refused = RenderService(
            runtime, FakeAssets(events), executor=None, authority_loader=wrong)
        with self.assertRaises(RenderJobUnavailable):
            refused.accept(owner, "dispatch-2", closed_request("dispatch-2"))
        self.assertEqual(events, ["snapshot"],
                         "the encode refusal precedes every asset and permit step")
        self.assertNotIn("accept", events)

    def test_missing_installed_authority_refuses_before_any_reference(self) -> None:
        owner = "site-a:node-a:fleet"
        events: list[str] = []
        runtime = FakeRuntime(events)
        runtime.root = self.root
        loader = SnapshotAuthority(events, None)
        service = RenderService(
            runtime, FakeAssets(events), executor=None, authority_loader=loader)
        with self.assertRaises(RenderJobUnavailable):
            service.accept(owner, "dispatch-1", closed_request())
        self.assertEqual(events, ["snapshot"])

    def test_qualification_preflight_reads_its_candidate_before_the_permit(self) -> None:
        owner = "site-a:node-a:fleet"
        tier, encoder = _compiled_tier(PACKET)

        events: list[str] = []
        runtime = FakeRuntime(events)
        runtime.root = self.root
        wrong = CandidateAuthority(
            events, SimpleNamespace(selected_tier="not a compiled tier",
                                    exact_encoder=encoder))
        refused = RenderService(
            runtime, FakeAssets(events), executor=None, authority_loader=wrong)
        with self.assertRaises(RenderJobUnavailable):
            refused.accept(owner, "dispatch-1", closed_request("dispatch-1"),
                           qualification_vector="sealed-m4")
        self.assertEqual(events, ["candidates"],
                         "the refusal precedes the permit and every asset step")

        events.clear()
        runtime = FakeRuntime(events)
        runtime.root = self.root
        matching = CandidateAuthority(
            events, SimpleNamespace(selected_tier=tier.label, exact_encoder=encoder))
        service = RenderService(
            runtime, FakeAssets(events), executor=None, authority_loader=matching)
        accepted = service.accept(owner, "dispatch-1", closed_request("dispatch-1"),
                                  qualification_vector="sealed-m4")
        self.assertEqual(accepted["purpose"], "qualification")
        self.assertEqual(
            events,
            ["candidates", "permit:sealed-m4", "describe:b", "describe:a",
             "reference:b", "reference:a", "accept"],
        )

    def test_qualification_preflight_refuses_a_closed_or_foreign_intent(self) -> None:
        owner = "site-a:node-a:fleet"
        tier, encoder = _compiled_tier(PACKET)
        events: list[str] = []
        runtime = FakeRuntime(events)
        runtime.root = self.root
        # The live candidate belongs to a different intent: the request names one this
        # loader cannot resolve, so admission refuses before any permit or reference.
        loader = CandidateAuthority(
            events, SimpleNamespace(selected_tier=tier.label, exact_encoder=encoder),
            intent_id="some-other-intent")
        service = RenderService(
            runtime, FakeAssets(events), executor=None, authority_loader=loader)
        with self.assertRaises(RenderJobUnavailable):
            service.accept(owner, "dispatch-1", closed_request(),
                           qualification_vector="sealed-m4")
        self.assertEqual(events, ["candidates"])
        self.assertNotIn("accept", events)

    def test_exact_replay_precedes_mutable_asset_and_qualification_checks(self) -> None:
        raw = closed_request()
        accepted = self.service.accept(
            "site-a:node-a:fleet", "dispatch-1", raw,
            qualification_vector="sealed-m4")
        self.events.clear()
        self.service.assets.descriptors.clear()

        replay = self.service.accept(
            "site-a:node-a:fleet", "dispatch-1", raw,
            qualification_vector="missing")

        self.assertEqual(replay["id"], accepted["id"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.events, [])
        with self.assertRaisesRegex(ValueError, "different exact body"):
            self.service.accept(
                "site-a:node-a:fleet", "dispatch-1",
                closed_request(base_handle="c" * 32),
                qualification_vector=None)

    def test_content_mismatch_never_accepts_but_keeps_prior_reference_evidence(self) -> None:
        self.service.assets.descriptors["b" * 32]["size_bytes"] = 99

        with self.assertRaisesRegex(ValueError, "size"):
            self.service.accept(
                "site-a:node-a:fleet", "dispatch-1", closed_request(),
                qualification_vector="sealed-m4")

        self.assertNotIn("accept", self.events)

    def test_failed_qualification_is_refused_before_any_reference(self) -> None:
        owner = "site-a:node-a:fleet"

        with self.assertRaises(Exception):
            self.service.accept(
                owner, "dispatch-1", closed_request(),
                qualification_vector="missing")

        self.assertNotIn("accept", self.events)
        self.assertNotIn("reference:b", self.events)
        self.assertNotIn("describe:b", self.events)
        self.assertEqual(self.service.assets.released, [])

    def test_accept_failure_releases_only_when_no_job_won_the_key(self) -> None:
        owner = "site-a:node-a:fleet"
        raw = closed_request()
        self.runtime.accept = mock.Mock(side_effect=RuntimeError("store down"))
        with self.assertRaises(RuntimeError):
            self.service.accept(owner, "dispatch-1", raw)
        self.assertEqual(len(self.service.assets.released), 2)

        self.service.assets.released.clear()
        winner = {
            "id": "job-9", "owner_scope": owner, "dispatch_key": "dispatch-1",
            "accepted_request_sha256": hashlib.sha256(raw).hexdigest(),
            "state": "accepted",
        }
        self.runtime.by_key = mock.Mock(side_effect=[None, winner])
        self.runtime.accept = mock.Mock(side_effect=RuntimeError("race lost"))
        with self.assertRaises(RuntimeError):
            self.service.accept(owner, "dispatch-1", raw)
        self.assertEqual(self.service.assets.released, [])

    def test_terminal_status_releases_but_uncertain_retains(self) -> None:
        owner = "site-a:node-a:fleet"
        row = self.runtime.accept(owner, "dispatch-1", closed_request())
        stored = self.runtime.rows[(owner, "dispatch-1")]
        stored["state"] = "uncertain"
        self.service.get(owner, row["id"])
        self.assertEqual(self.service.assets.released, [])

        stored["state"] = "failed"
        self.service.get(owner, row["id"])
        self.assertEqual(len(self.service.assets.released), 1)

    def test_cancel_releases_only_a_settled_cancellation(self) -> None:
        owner = "site-a:node-a:fleet"
        row = self.runtime.accept(owner, "dispatch-1", closed_request())
        self.service.executor = FakeExecutor(self.runtime, self.events)
        stored = self.runtime.rows[(owner, "dispatch-1")]

        stored["state"] = "cancel_requested"
        asyncio.run(self.service.cancel(owner, row["id"]))
        self.assertEqual(self.service.assets.released, [])

        stored["state"] = "cancelled"
        asyncio.run(self.service.cancel(owner, row["id"]))
        self.assertEqual(len(self.service.assets.released), 1)

    def test_sweep_is_throttled_and_can_be_forced(self) -> None:
        self.service.sweep(force=True)
        self.assertEqual(len(self.service.assets.sweeps), 1)
        self.service.sweep()
        self.assertEqual(len(self.service.assets.sweeps), 1)
        self.service.sweep(force=True)
        self.assertEqual(len(self.service.assets.sweeps), 2)

    def test_acked_attempt_moves_into_runtime_trash_only_without_a_reader(self) -> None:
        owner = "site-a:node-a:fleet"
        attempt = "d" * 32
        attempt_dir = self.root / "jobs" / "job-1" / attempt
        attempt_dir.mkdir(parents=True)
        (attempt_dir / "final.mp4").write_bytes(b"0123456789")
        (attempt_dir / "scratch.bin").write_bytes(b"work")
        row = {
            "id": "job-1", "owner_scope": owner, "dispatch_key": "dispatch-1",
            "state": "output_ready", "attempt_id": attempt,
            "leases": {"reader": 1, "ack": 0, "transfer": 0},
        }
        self.runtime.rows[(owner, "dispatch-1")] = row

        self.assertFalse(self.service._retire_output(owner, "job-1"))
        self.assertTrue((attempt_dir / "final.mp4").is_file())

        row["leases"] = {"reader": 0, "ack": 1, "transfer": 0}
        self.assertFalse(self.service._retire_output(owner, "job-1"))
        self.assertTrue((attempt_dir / "final.mp4").is_file())

        row["leases"] = {"reader": 0, "ack": 0, "transfer": 0}
        self.assertTrue(self.service._retire_output(owner, "job-1"))
        self.assertFalse(attempt_dir.exists())
        moved = list((self.root / "_trash").rglob("final.mp4"))
        self.assertEqual(len(moved), 1)
        self.assertTrue(moved[0].is_file())
        self.assertTrue(list((self.root / "_trash").rglob("scratch.bin")))
        self.assertFalse(self.service._retire_output(owner, "job-1"))

    def test_acked_attempt_moves_into_the_supplied_hub_trash_folder(self) -> None:
        owner = "site-a:node-a:fleet"
        attempt = "e" * 32
        trash = self.root / "hub-trash"
        trash.mkdir()
        service = RenderService(
            self.runtime, FakeAssets(self.events), executor=None,
            authority_loader=FakeAuthority(self.events), trash_root=trash)
        attempt_dir = self.root / "jobs" / "job-1" / attempt
        attempt_dir.mkdir(parents=True)
        (attempt_dir / "final.mp4").write_bytes(b"0123456789")
        row = {
            "id": "job-1", "owner_scope": owner, "dispatch_key": "dispatch-1",
            "state": "output_ready", "attempt_id": attempt,
            "leases": {"reader": 0, "ack": 0, "transfer": 0},
        }
        self.runtime.rows[(owner, "dispatch-1")] = row

        self.assertTrue(service._retire_output(owner, "job-1"))
        self.assertFalse(attempt_dir.exists())
        moved = list(trash.rglob("final.mp4"))
        self.assertEqual(len(moved), 1)
        self.assertRegex(
            moved[0].parent.name, rf"job-1-{attempt}-[0-9a-f]{{8}}\Z")
        self.assertFalse((self.root / "_trash").exists())

    def test_terminal_attempt_retires_after_grace_and_only_without_a_lease(self) -> None:
        owner = "site-a:node-a:fleet"
        attempt = "f" * 32
        attempt_dir = self.root / "jobs" / "job-1" / attempt
        attempt_dir.mkdir(parents=True)
        output = attempt_dir / "final.mp4"
        output.write_bytes(b"partial output")
        row = {
            "id": "job-1", "owner_scope": owner, "dispatch_key": "dispatch-1",
            "state": "failed", "attempt_id": attempt,
            "leases": {"compute": False, "reader": 0, "transfer": 0, "ack": 0},
        }
        self.runtime.rows[(owner, "dispatch-1")] = row

        self.service.release_job(owner, "job-1")
        self.assertTrue(attempt_dir.is_dir())

        old = time.time() - 2 * 24 * 3600
        os.utime(output, (old, old))
        os.utime(attempt_dir, (old, old))
        row["leases"] = {"compute": False, "reader": 1, "transfer": 0, "ack": 0}
        self.service.release_job(owner, "job-1")
        self.assertTrue(attempt_dir.is_dir())

        row["leases"] = {"compute": False, "reader": 0, "transfer": 0, "ack": 0}
        self.service.release_job(owner, "job-1")
        self.assertFalse(attempt_dir.exists())
        moved = list((self.root / "_trash").rglob("final.mp4"))
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].read_bytes(), b"partial output")

    def test_uncertain_and_rendering_attempts_are_never_retired(self) -> None:
        owner = "site-a:node-a:fleet"
        attempt = "a" * 32
        attempt_dir = self.root / "jobs" / "job-1" / attempt
        attempt_dir.mkdir(parents=True)
        output = attempt_dir / "final.mp4"
        output.write_bytes(b"still owned")
        old = time.time() - 2 * 24 * 3600
        os.utime(output, (old, old))
        os.utime(attempt_dir, (old, old))
        for state in ("uncertain", "rendering", "cancel_requested", "stopped"):
            self.runtime.rows[(owner, "dispatch-1")] = {
                "id": "job-1", "owner_scope": owner, "dispatch_key": "dispatch-1",
                "state": state, "attempt_id": attempt,
                "leases": {"compute": True, "reader": 0, "transfer": 0, "ack": 0},
            }
            self.service.release_job(owner, "job-1")
            self.service._settle(owner, self.runtime.get(owner, "job-1"))
            self.assertTrue(attempt_dir.is_dir())

    def test_routes_require_exact_header_even_on_loopback_and_derive_stable_scope(self) -> None:
        app = FastAPI()
        register_routes(
            app, self.service, hub_token="hub-secret",
            fleet_token=lambda: "fleet-secret",
            settings=lambda: {
                "site_id": "site-a", "controller_id": "node-a", "role": "agent",
            },
        )
        with TestClient(app) as client:
            self.assertEqual(client.get(
                "/api/hub/final-renders/by-idempotency-key/missing").status_code, 401)
            self.assertEqual(client.get(
                "/api/hub/final-renders/by-idempotency-key/missing",
                headers={"Authorization": "Bearer hub-secret"}).status_code, 403)
            submitted = client.post(
                "/api/hub/final-renders", content=closed_request(), headers={
                    "X-Hub-Token": "fleet-secret", "Idempotency-Key": "dispatch-1",
                    "X-Studio-Render-Qualification-Vector": "sealed-m4",
                })
            self.assertEqual(submitted.status_code, 200, submitted.text)
            self.assertEqual(submitted.json()["owner_scope"], "site-a:node-a:fleet")
            status = client.get(
                "/api/hub/final-renders/by-idempotency-key/dispatch-1",
                headers={"X-Hub-Token": "fleet-secret"})
            self.assertEqual(status.status_code, 200, status.text)
            self.assertEqual(status.json()["id"], submitted.json()["id"])
            cross_actor = client.get(
                f"/api/hub/final-renders/{submitted.json()['id']}",
                headers={"X-Hub-Token": "hub-secret"})
            self.assertEqual(cross_actor.status_code, 404)

        paths = {(route.path, method) for route in app.routes for method in route.methods}
        for path, method in {
            ("/api/hub/final-renders/blobs/{sha256}", "PUT"),
            ("/api/hub/final-renders/blobs/{sha256}/status", "GET"),
            ("/api/hub/final-renders", "POST"),
            ("/api/hub/final-renders/by-idempotency-key/{key}", "GET"),
            ("/api/hub/final-renders/by-idempotency-key/{key}/cancel", "POST"),
            ("/api/hub/final-renders/{job_id}", "GET"),
            ("/api/hub/final-renders/{job_id}/cancel", "POST"),
            ("/api/hub/final-renders/{job_id}/output", "GET"),
            ("/api/hub/final-renders/{job_id}/ack", "POST"),
        }:
            self.assertIn((path, method), paths)

        from components.hub.backend import auth
        self.assertTrue(auth.fleet_path_allowed("POST", "/api/hub/final-renders"))
        self.assertTrue(auth.fleet_path_allowed(
            "GET", "/api/hub/final-renders/job-1/output"))
        self.assertFalse(auth.node_path_allowed("POST", "/api/hub/final-renders"))

    def test_original_key_cancel_route_has_exact_body_and_scope(self) -> None:
        app = FastAPI()
        register_routes(
            app, self.service, hub_token="hub-secret", fleet_token=lambda: "fleet-secret",
            settings=lambda: {"site_id": "site-a", "controller_id": "node-a"},
        )
        expected = "1" * 64
        with TestClient(app) as client:
            malformed = client.post(
                "/api/hub/final-renders/by-idempotency-key/dispatch-1/cancel",
                content=json.dumps({"expected_request_sha256": expected, "job_id": "guess"}),
                headers={"X-Hub-Token": "fleet-secret"})
            cancelled = client.post(
                "/api/hub/final-renders/by-idempotency-key/dispatch-1/cancel",
                content=json.dumps({"expected_request_sha256": expected}),
                headers={"X-Hub-Token": "fleet-secret"})
            crossed = client.post(
                "/api/hub/final-renders/by-idempotency-key/dispatch-1/cancel",
                content=json.dumps({"expected_request_sha256": expected}),
                headers={"X-Hub-Token": "hub-secret"})

        self.assertEqual(malformed.status_code, 400, malformed.text)
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["state"], "cancelled_before_acceptance")
        self.assertEqual(crossed.status_code, 200, crossed.text)
        scopes = [event[1] for event in self.events if isinstance(event, tuple)
                  and event[0] == "cancel-key"]
        self.assertEqual(scopes, ["site-a:node-a:fleet", "site-a:node-a:hub"])

    def test_original_key_cancel_invokes_exact_active_executor_owner(self) -> None:
        owner = "site-a:node-a:fleet"
        raw = closed_request()
        row = self.runtime.accept(owner, "dispatch-1", raw)
        self.service.executor = FakeExecutor(self.runtime, self.events)
        app = FastAPI()
        register_routes(
            app, self.service, hub_token="hub-secret", fleet_token=lambda: "fleet-secret",
            settings=lambda: {"site_id": "site-a", "controller_id": "node-a"},
        )

        with TestClient(app) as client:
            response = client.post(
                "/api/hub/final-renders/by-idempotency-key/dispatch-1/cancel",
                content=json.dumps({
                    "expected_request_sha256": hashlib.sha256(raw).hexdigest(),
                }),
                headers={"X-Hub-Token": "fleet-secret"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(("executor-cancel", owner, row["id"]), self.events)

    def test_controller_mirror_requires_exact_machine_actor_before_relay(self) -> None:
        calls = []

        async def relay(node_id, operation, **kwargs):
            calls.append((node_id, operation, kwargs))
            return SimpleNamespace(status_code=200, headers={}, body=b"{}")

        app = FastAPI()
        app.middleware("http")(auth.make_middleware("hub-secret"))
        register_relay_routes(
            app, relay, hub_token="hub-secret", fleet_token=lambda: "fleet-secret",
            settings=lambda: {"site_id": "site-a", "controller_id": "controller-a"},
        )
        path = (
            "/api/hub/fleet/nodes/node-a/final-renders/"
            "12345678-1234-1234-1234-123456789abc"
        )

        with mock.patch(
                "components.hub.backend.peers.fleet_token",
                return_value="fleet-secret"):
            with TestClient(app) as client:
                client.cookies.clear()
                tokenless = client.get(path)
                client.cookies.clear()
                browser = client.get(
                    path, cookies={auth.SESSION_COOKIE_NAME: "browser-session"})
                client.cookies.clear()
                forwarded = client.get(
                    path,
                    headers={
                        "X-Hub-Token": "hub-secret",
                        "X-Forwarded-For": "127.0.0.1",
                    },
                )
                client.cookies.clear()
                accepted = client.get(path, headers={"X-Hub-Token": "fleet-secret"})

        self.assertIn(tokenless.status_code, {401, 403})
        self.assertIn(browser.status_code, {401, 403})
        self.assertEqual(forwarded.status_code, 403)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(len(calls), 1)

    def test_blob_status_is_read_only_and_missing_digest_is_404(self) -> None:
        app = FastAPI()
        self.service.assets.begin_upload = mock.Mock(
            side_effect=AssertionError("GET must not create an upload"))
        register_routes(
            app, self.service, hub_token="hub-secret", fleet_token=lambda: "fleet-secret",
            settings=lambda: {"site_id": "site-a", "controller_id": "node-a"},
        )
        with TestClient(app) as client:
            found = client.get(
                "/api/hub/final-renders/blobs/" + "1" * 64 + "/status?size_bytes=22",
                headers={"X-Hub-Token": "fleet-secret"})
            missing = client.get(
                "/api/hub/final-renders/blobs/" + "0" * 64 + "/status?size_bytes=22",
                headers={"X-Hub-Token": "fleet-secret"})

        self.assertEqual(found.status_code, 200, found.text)
        self.assertEqual(missing.status_code, 404, missing.text)
        self.service.assets.begin_upload.assert_not_called()

    def test_output_range_and_ack_are_scope_and_receipt_bound(self) -> None:
        owner = "site-a:node-a:fleet"
        output = self.root / "jobs" / "job-1" / ("d" * 32) / "final.mp4"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"0123456789")
        row = {
            "id": "job-1", "owner_scope": owner, "dispatch_key": "dispatch-1",
            "state": "output_ready", "attempt_id": "d" * 32,
            "accepted_request_sha256": "e" * 64,
            "output_receipt": {
                "receipt_digest": "f" * 64, "output_sha256": hashlib.sha256(
                    output.read_bytes()).hexdigest(), "output_size_bytes": 10,
            },
        }
        self.runtime.root = self.root
        self.runtime.rows[(owner, "dispatch-1")] = row
        app = FastAPI()
        register_routes(
            app, self.service, hub_token="hub-secret", fleet_token=lambda: "fleet-secret",
            settings=lambda: {"site_id": "site-a", "controller_id": "node-a"},
        )
        etag = f'"{row["output_receipt"]["output_sha256"]}"'
        with TestClient(app) as client:
            missing_match = client.get(
                "/api/hub/final-renders/job-1/output",
                headers={"X-Hub-Token": "fleet-secret", "Range": "bytes=0-3"})
            weak_match = client.get(
                "/api/hub/final-renders/job-1/output",
                headers={"X-Hub-Token": "fleet-secret", "Range": "bytes=0-3",
                         "If-Match": f"W/{etag}"})
            wrong_match = client.get(
                "/api/hub/final-renders/job-1/output",
                headers={"X-Hub-Token": "fleet-secret", "Range": "bytes=0-3",
                         "If-Match": f'"{"0" * 64}"'})
            missing_range = client.get(
                "/api/hub/final-renders/job-1/output",
                headers={"X-Hub-Token": "fleet-secret", "If-Match": etag})
            malformed_range = client.get(
                "/api/hub/final-renders/job-1/output",
                headers={"X-Hub-Token": "fleet-secret", "If-Match": etag,
                         "Range": "bytes=0-"})
            oversized_range = client.get(
                "/api/hub/final-renders/job-1/output",
                headers={"X-Hub-Token": "fleet-secret", "If-Match": etag,
                         "Range": f"bytes=0-{8 * 1024 * 1024}"})
            rejected_events = list(self.events)
            crossed = client.get(
                "/api/hub/final-renders/job-1/output",
                headers={"X-Hub-Token": "hub-secret", "Range": "bytes=0-3",
                         "If-Match": etag})
            ranged = client.get(
                "/api/hub/final-renders/job-1/output",
                headers={"X-Hub-Token": "fleet-secret", "Range": "bytes=2-5",
                         "If-Match": etag})
            changed = client.post(
                "/api/hub/final-renders/job-1/ack",
                content=json.dumps({"attempt_id": "d" * 32,
                                    "receipt_digest": "0" * 64}),
                headers={"X-Hub-Token": "fleet-secret"})
            acked = client.post(
                "/api/hub/final-renders/job-1/ack",
                content=json.dumps({"attempt_id": "d" * 32,
                                    "receipt_digest": "f" * 64}),
                headers={"X-Hub-Token": "fleet-secret"})

        self.assertEqual(missing_match.status_code, 428)
        self.assertEqual(weak_match.status_code, 412)
        self.assertEqual(wrong_match.status_code, 412)
        self.assertEqual(missing_range.status_code, 416)
        self.assertEqual(malformed_range.status_code, 416)
        self.assertEqual(oversized_range.status_code, 416)
        self.assertFalse(any(
            isinstance(event, tuple) and event[0] == "lease"
            for event in rejected_events))
        self.assertEqual(crossed.status_code, 404)
        self.assertEqual(ranged.status_code, 206, ranged.text)
        self.assertEqual(ranged.content, b"2345")
        self.assertEqual(ranged.headers["content-range"], "bytes 2-5/10")
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(acked.status_code, 200, acked.text)
        self.assertEqual(len(self.service.assets.released), 1)


class RelayTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_relay_uses_only_fixed_node_path_headers_and_bounded_output_range(self) -> None:
        calls = []
        etag = f'"{"a" * 64}"'

        class Response:
            status_code = 206
            headers = {"content-range": "bytes 0-4/5", "content-length": "5",
                       "server": "private"}

            async def aiter_bytes(self, chunk_size):
                self.assert_chunk = chunk_size
                yield b"fixed"

        class Stream:
            async def __aenter__(self):
                return Response()

            async def __aexit__(self, *_args):
                return False

        class Client:
            def stream(self, method, url, **kwargs):
                calls.append((method, url, kwargs))
                return Stream()

            async def request(self, *_args, **_kwargs):
                raise AssertionError("relay must never buffer response.content")

        with mock.patch.object(fleet_nodes, "_read", return_value={
                "node-a": {"address": "https://node.invalid"}}):
            with self.assertRaisesRegex(ValueError, "identity"):
                await fleet_nodes.relay_final_render(
                    "node-a", "output", identity="job/../escape", client=Client(),
                    headers={"Range": "bytes=0-4", "If-Match": etag})
            with self.assertRaisesRegex(ValueError, "bounded Range"):
                await fleet_nodes.relay_final_render(
                    "node-a", "output",
                    identity="12345678-1234-1234-1234-123456789abc", client=Client(),
                    headers={"Range": "bytes=0-", "If-Match": etag})
            with self.assertRaisesRegex(ValueError, "If-Match"):
                await fleet_nodes.relay_final_render(
                    "node-a", "output",
                    identity="12345678-1234-1234-1234-123456789abc", client=Client(),
                    headers={"Range": "bytes=0-4", "If-Match": f"W/{etag}"})
            self.assertEqual(calls, [])
            result = await fleet_nodes.relay_final_render(
                "node-a", "output",
                identity="12345678-1234-1234-1234-123456789abc", client=Client(),
                headers={"Range": "bytes=0-4", "If-Match": etag,
                         "X-Caller-URL": "https://attacker.invalid"},
            )
            with self.assertRaisesRegex(ValueError, "unsupported"):
                await fleet_nodes.relay_final_render(
                    "node-a", "caller_path", identity="/admin", client=Client())

        self.assertEqual(result.body, b"fixed")
        self.assertEqual(result.headers, {
            "content-range": "bytes 0-4/5", "content-length": "5"})
        method, url, kwargs = calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(
            url,
            "https://node.invalid/api/hub/final-renders/"
            "12345678-1234-1234-1234-123456789abc/output",
        )
        self.assertEqual(kwargs["headers"], {"Range": "bytes=0-4", "If-Match": etag})

    async def test_relay_stream_aborts_oversized_node_body_without_buffering(self) -> None:
        yielded = 0
        limit = 8 * 1024 * 1024

        class Response:
            status_code = 502
            headers = {}

            async def aiter_bytes(self, chunk_size):
                nonlocal yielded
                self.chunk_size = chunk_size
                for payload in (b"x" * limit, b"y"):
                    yielded += len(payload)
                    yield payload

        class Stream:
            async def __aenter__(self):
                return Response()

            async def __aexit__(self, *_args):
                return False

        class Client:
            def stream(self, *_args, **_kwargs):
                return Stream()

            async def request(self, *_args, **_kwargs):
                raise AssertionError("relay must never call buffered request")

        with mock.patch.object(fleet_nodes, "_read", return_value={
                "node-a": {"address": "https://node.invalid"}}):
            with self.assertRaisesRegex(fleet_nodes.StartupRelayError, "oversized"):
                await fleet_nodes.relay_final_render(
                    "node-a", "status",
                    identity="12345678-1234-1234-1234-123456789abc", client=Client())

        self.assertLessEqual(yielded, limit + 1)


if __name__ == "__main__":
    unittest.main()
