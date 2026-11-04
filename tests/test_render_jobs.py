#!/usr/bin/env python3
"""Durable internal lifecycle contract for private whole-video renders.

The retained roots under ``_trash`` contain only fake records and byte fixtures.
These tests never start a process, run a compiler, inspect media, call a route, or
touch a fleet node.
"""
from __future__ import annotations

import json
import hashlib
import sqlite3
import sys
import threading
import unittest
from dataclasses import replace
from dataclasses import fields
from pathlib import Path
from unittest import mock
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.hub.backend.render_jobs import (  # noqa: E402
    MAX_REQUEST_BYTES,
    ProcessIdentity,
    QualificationPermit,
    RenderJobConflict,
    RenderExecutionContext,
    RenderJobNotFound,
    RenderJobRuntime,
    RenderJobUnavailable,
    RuntimeQualification,
    StoppedProcessEvidence,
    ValidatedOutputReceipt,
)

FIXTURE = json.loads((ROOT / "tests/fixtures/final-render-compiler-v1.json").read_text())
PACKET = FIXTURE["f0_base_audio"]["packet"]
PROFILE_DIGEST = PACKET["recipe"]["profile_digest"]
TOOLS_DIGEST = "2" * 64
COMPILER_DIGEST = "3" * 64
QUALIFICATION_DIGEST = "4" * 64


def request(*, base_handle: str = "a" * 32, dispatch_key: str = "dispatch-7",
            extra: dict | None = None) -> bytes:
    value = {
        "format": "studio.render.hub-request.v1",
        "dispatch": {
            "story_uuid": "story-2026-10-15-001",
            "intent_id": "render-final-cut",
            "attempt_no": 7,
            "gateway_attempt_key": "gateway-attempt-007",
            "destination_site": "site-phnom-penh",
            "destination_controller": "controller-a",
            "destination_node": "node-m4-01",
            "dispatch_key": dispatch_key,
            "dispatch_fence": "fence-011",
        },
        "packet": PACKET,
        "assets": {"audio": "b" * 32, "base": base_handle},
    }
    value.update(extra or {})
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def qualification() -> RuntimeQualification:
    return RuntimeQualification(
        profile_digest=PROFILE_DIGEST,
        tools_digest=TOOLS_DIGEST,
        compiler_digest=COMPILER_DIGEST,
        qualification_digest=QUALIFICATION_DIGEST,
        hardware_class="m4-16gb",
        production_eligible=True,
        min_free_bytes=2_000_000_000,
        scratch_reserve_bytes=1_000_000_000,
        output_limit_bytes=500_000_000,
    )


class QualificationAuthority:
    """Test authority standing in for the future server-owned qualified registry."""

    def __init__(self) -> None:
        self.current = qualification()

    def resolve(self, _packet: dict) -> RuntimeQualification | None:
        return self.current


class RenderJobRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "_trash/render-jobs-testroots" / uuid4().hex
        self.root.mkdir(parents=True)
        self.authority = QualificationAuthority()
        self.free_bytes = 20_000_000_000
        self.runtime = RenderJobRuntime(
            self.root,
            database_path=self.root / "hub.db",
            qualification_resolver=self.authority.resolve,
            probed_qualification_resolver=self.authority.resolve,
            free_bytes=lambda _path: self.free_bytes,
        )

    def test_claim_refuses_changed_or_removed_probe_binding_at_same_qualification(self) -> None:
        binding = {"format": "studio.render.encode-binding.v1",
                   "authority_digest": "a" * 64,
                   "selected_tier": "test-tier", "exact_encoder": "libx264"}
        accepted_authority = replace(qualification(), document={"encode_authority": binding})
        for index, changed in enumerate((None, dict(binding, authority_digest="b" * 64))):
            with self.subTest(binding=changed):
                self.authority.current = accepted_authority
                key = f"probe-binding-{index}"
                raw = request(dispatch_key=key, extra={
                    "format": "studio.render.hub-request.v2", "encode_authority": binding})
                accepted = self.runtime.accept("owner-a", key, raw)
                self.assertEqual(accepted["encode_authority"], binding)
                self.authority.current = replace(accepted_authority, document=(
                    {"encode_authority": changed} if changed else None))
                with self.assertRaisesRegex(RenderJobUnavailable, "encode evidence"):
                    self.runtime.claim_launch("owner-a", accepted["id"], machine="worker-m4",
                                              dispatch_generation=1)
                self.assertEqual(self.runtime.get("owner-a", accepted["id"])["state"], "accepted")

    def test_v2_requires_exact_binding_and_cannot_fall_back_to_v1(self) -> None:
        binding = {"format": "studio.render.encode-binding.v1",
                   "authority_digest": "a" * 64,
                   "selected_tier": "test-tier", "exact_encoder": "libx264"}
        for index, extra in enumerate((
                {"format": "studio.render.hub-request.v2"},
                {"encode_authority": binding},
                {"format": "studio.render.hub-request.v2", "encode_authority": None},
                {"format": "studio.render.hub-request.v2", "encode_authority": dict(binding, extra=True)})):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                key = f"invalid-v2-{index}"
                self.runtime.accept("owner-a", key, request(dispatch_key=key, extra=extra))
        raw = request(extra={"format": "studio.render.hub-request.v2", "encode_authority": binding})
        with self.assertRaisesRegex(RenderJobUnavailable, "encode evidence"):
            self.runtime.accept("owner-a", "dispatch-7", raw)
        self.authority.current = replace(qualification(), document={"encode_authority": binding})
        accepted = self.runtime.accept("owner-a", "dispatch-7", raw)
        self.assertEqual(accepted["encode_authority"], binding)
        self.assertTrue(self.runtime.accept("owner-a", "dispatch-7", raw)["replayed"])
        changed = request(extra={"format": "studio.render.hub-request.v2",
                                 "encode_authority": dict(binding, authority_digest="b" * 64)})
        with self.assertRaises(RenderJobConflict):
            self.runtime.accept("owner-a", "dispatch-7", changed)

    def test_public_projection_keeps_gateway_keys_and_dispatch_rows_keep_fifo_age(self) -> None:
        public_keys = {
            "id", "owner_scope", "dispatch_key", "accepted_request_sha256", "purpose",
            "state", "machine", "dispatch_generation", "attempt_id",
            "cancel_requested_at", "profile_digest", "tools_digest", "compiler_digest",
            "qualification_digest", "stopped_evidence_digest", "output_receipt", "leases",
            "replayed",
        }
        with mock.patch("components.hub.backend.render_jobs.time.time", return_value=200.0):
            newer = self.runtime.accept(
                "owner-a", "dispatch-newer", request(dispatch_key="dispatch-newer"))
        with mock.patch("components.hub.backend.render_jobs.time.time", return_value=100.0):
            older = self.runtime.accept(
                "owner-a", "dispatch-older", request(dispatch_key="dispatch-older"))

        public_rows = (
            newer,
            older,
            self.runtime.get("owner-a", newer["id"]),
            self.runtime.by_key("owner-a", "dispatch-older"),
        )
        self.assertTrue(all(set(row) == public_keys for row in public_rows))

        dispatchable = self.runtime.dispatchable_jobs()
        self.assertEqual(
            [(row["id"], row["created_at"]) for row in dispatchable],
            [(older["id"], 100.0), (newer["id"], 200.0)],
        )
        self.assertTrue(all(set(row) == public_keys | {"created_at"} for row in dispatchable))

    def test_same_scope_key_and_body_replay_before_mutable_readiness(self) -> None:
        accepted = self.runtime.accept("owner-a", "dispatch-7", request())

        self.authority.current = None
        replay = self.runtime.accept("owner-a", "dispatch-7", request())

        self.assertEqual(replay["id"], accepted["id"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.runtime.by_key("owner-a", "dispatch-7")["id"], accepted["id"])

        with self.assertRaises(RenderJobConflict):
            self.runtime.accept("owner-a", "dispatch-7", request(base_handle="c" * 32))
        with self.assertRaises(RenderJobUnavailable):
            self.runtime.accept(
                "owner-a", "fresh-dispatch", request(dispatch_key="fresh-dispatch"))

    def test_scope_is_part_of_idempotency_identity(self) -> None:
        first = self.runtime.accept("owner-a", "same-key", request(dispatch_key="same-key"))
        second = self.runtime.accept("owner-b", "same-key", request(dispatch_key="same-key"))

        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.runtime.by_key("owner-a", "same-key")["id"], first["id"])
        self.assertEqual(self.runtime.by_key("owner-b", "same-key")["id"], second["id"])
        self.assertIsNone(self.runtime.get("owner-b", first["id"]))
        with self.assertRaises(RenderJobNotFound):
            self.runtime.request_cancel("owner-b", first["id"])
        with self.assertRaises(RenderJobNotFound):
            self.runtime.acquire_file_lease(
                "owner-b", first["id"], "reader", "cross-scope-reader")

    def test_original_key_cancel_fence_replays_across_restart_and_blocks_late_accept(self) -> None:
        raw = request(dispatch_key="cancel-before-send")
        expected = hashlib.sha256(raw).hexdigest()

        first = self.runtime.request_cancel_by_key(
            "owner-a", "cancel-before-send", expected)
        restarted = RenderJobRuntime(
            self.root, database_path=self.root / "hub.db",
            qualification_resolver=self.authority.resolve,
            probed_qualification_resolver=self.authority.resolve,
            free_bytes=lambda _path: self.free_bytes,
        )
        replay = restarted.request_cancel_by_key(
            "owner-a", "cancel-before-send", expected)

        self.assertEqual(type(first).__name__, "CancelledBeforeAcceptanceReceipt")
        self.assertEqual(replay, first)
        self.assertEqual(first.owner_scope, "owner-a")
        self.assertEqual(first.dispatch_key, "cancel-before-send")
        self.assertEqual(first.expected_request_sha256, expected)
        with self.assertRaises(RenderJobConflict):
            restarted.request_cancel_by_key(
                "owner-a", "cancel-before-send", "0" * 64)
        other = restarted.request_cancel_by_key(
            "owner-b", "cancel-before-send", expected)
        self.assertNotEqual(other.fence_id, first.fence_id)
        with self.assertRaises(RenderJobUnavailable):
            restarted.accept("owner-a", "cancel-before-send", raw)
        with self.assertRaises(RenderJobConflict):
            restarted.accept(
                "owner-a", "cancel-before-send",
                request(dispatch_key="cancel-before-send", base_handle="c" * 32),
            )

    def test_concurrent_accept_and_original_key_cancel_have_one_serialized_winner(self) -> None:
        raw = request(dispatch_key="cancel-race")
        expected = hashlib.sha256(raw).hexdigest()
        barrier = threading.Barrier(2)
        outcomes = []

        def accept():
            barrier.wait()
            try:
                outcomes.append(("accept", self.runtime.accept(
                    "owner-a", "cancel-race", raw)))
            except BaseException as exc:
                outcomes.append(("accept-error", exc))

        def cancel():
            barrier.wait()
            try:
                outcomes.append(("cancel", self.runtime.request_cancel_by_key(
                    "owner-a", "cancel-race", expected)))
            except BaseException as exc:
                outcomes.append(("cancel-error", exc))

        threads = [threading.Thread(target=accept), threading.Thread(target=cancel)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual({kind.split("-", 1)[0] for kind, _ in outcomes}, {"accept", "cancel"})
        row = self.runtime.by_key("owner-a", "cancel-race")
        if row is None:
            self.assertTrue(any(
                kind == "accept-error" and isinstance(value, RenderJobUnavailable)
                for kind, value in outcomes))
            self.assertEqual(
                type(self.runtime.request_cancel_by_key(
                    "owner-a", "cancel-race", expected)).__name__,
                "CancelledBeforeAcceptanceReceipt",
            )
        else:
            self.assertEqual(row["state"], "cancelled")
            self.assertIsNotNone(row["cancel_requested_at"])
            self.assertTrue(any(kind == "cancel" for kind, _ in outcomes))

    def test_optional_display_labels_are_stored_but_decide_nothing(self) -> None:
        """The Render tab's channel and episode: display only, optional, and validated exactly.

        They are stored so a render can be named instead of printed as a job id; no dispatch,
        routing, hashing or acceptance decision reads them.
        """
        named = self.runtime.accept("owner-a", "dispatch-7", request(extra={
            "display": {"channel": "Khmer History", "episode": "EP012 ការធ្លាក់ចុះ"}}))
        with sqlite3.connect(self.root / "hub.db") as connection:
            stored = connection.execute(
                "SELECT display_json FROM render_jobs WHERE id=?", (named["id"],)).fetchone()[0]
        self.assertEqual(json.loads(stored),
                         {"channel": "Khmer History", "episode": "EP012 ការធ្លាក់ចុះ"})
        self.assertEqual(named["dispatch_key"], "dispatch-7")
        # One label alone is allowed, and so is neither — an older Story Studio sends no display.
        one = self.runtime.accept("owner-a", "dispatch-8", request(
            dispatch_key="dispatch-8", extra={"display": {"episode": "EP013"}}))
        with sqlite3.connect(self.root / "hub.db") as connection:
            self.assertEqual(json.loads(connection.execute(
                "SELECT display_json FROM render_jobs WHERE id=?", (one["id"],)).fetchone()[0]),
                {"episode": "EP013"})
        plain = self.runtime.accept("owner-a", "dispatch-9", request(dispatch_key="dispatch-9"))
        with sqlite3.connect(self.root / "hub.db") as connection:
            self.assertIsNone(connection.execute(
                "SELECT display_json FROM render_jobs WHERE id=?", (plain["id"],)).fetchone()[0])
        for index, bad in enumerate((
            {}, {"channel": ""}, {"channel": "a\x00b"}, {"channel": "x" * 257},
            {"channel": 7}, {"channel": "EP014", "host": "node-m4-01"}, ["channel"],
        )):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "display"):
                self.runtime.accept("owner-a", f"dispatch-bad-{index}",
                                    request(dispatch_key=f"dispatch-bad-{index}",
                                            extra={"display": bad}))

    def test_request_is_closed_bounded_and_asset_complete(self) -> None:
        self.assertEqual(MAX_REQUEST_BYTES, 1024 * 1024)
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            self.runtime.accept(
                "owner-a", "dispatch-7", request(extra={"argv": ["/tmp/ffmpeg"]}))
        value = json.loads(request())
        value["assets"].pop("audio")
        with self.assertRaisesRegex(ValueError, "asset mapping"):
            self.runtime.accept(
                "owner-a", "dispatch-7", json.dumps(value, separators=(",", ":")).encode())
        with self.assertRaisesRegex(ValueError, "request is too large"):
            self.runtime.accept("owner-a", "dispatch-7", b" " * (MAX_REQUEST_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "dispatch_key"):
            self.runtime.accept("owner-a", "different-key", request())

    def test_default_runtime_refuses_fresh_execution_without_trusted_qualification(self) -> None:
        runtime = RenderJobRuntime(
            self.root / "unqualified", database_path=self.root / "unqualified.db",
            free_bytes=lambda _path: self.free_bytes,
        )
        with self.assertRaises(RenderJobUnavailable):
            runtime.accept("owner-a", "dispatch-7", request())

    def test_sealed_qualification_permit_binds_exact_scope_body_dispatch_and_candidate(self) -> None:
        raw = request(dispatch_key="qualify-m4")
        candidate = replace(qualification(), production_eligible=False)
        self.authority.current = candidate
        dispatch = json.loads(raw)["dispatch"]
        permit = QualificationPermit(
            vector_id="m4-hardware-v1",
            owner_scope="owner-a",
            exact_request_sha256=hashlib.sha256(raw).hexdigest(),
            qualification_digest=candidate.qualification_digest,
            hardware_class=candidate.hardware_class,
            story_uuid=dispatch["story_uuid"],
            intent_id=dispatch["intent_id"],
            attempt_no=dispatch["attempt_no"],
            gateway_attempt_key=dispatch["gateway_attempt_key"],
            destination_site=dispatch["destination_site"],
            destination_controller=dispatch["destination_controller"],
            destination_node=dispatch["destination_node"],
            dispatch_key=dispatch["dispatch_key"],
            dispatch_fence=dispatch["dispatch_fence"],
        )

        accepted = self.runtime.accept(
            "owner-a", "qualify-m4", raw, qualification_permit=permit)
        self.assertEqual(accepted["purpose"], "qualification")
        claim = self.runtime.claim_launch(
            "owner-a", accepted["id"], machine="worker-m4", dispatch_generation=40)
        context = self.runtime.execution_context(
            "owner-a", accepted["id"], claim["attempt_id"], claim["launch_token"])
        self.assertIs(type(context), RenderExecutionContext)
        self.assertEqual(context.raw_request, raw)
        self.assertEqual(context.accepted_request_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(context.purpose, "qualification")
        self.assertEqual(context.qualification_permit, permit)

        replay = self.runtime.accept(
            "owner-a", "qualify-m4", raw, qualification_permit=None)
        self.assertEqual(replay["id"], accepted["id"])
        self.assertTrue(replay["replayed"])
        with self.assertRaises((RenderJobConflict, RenderJobUnavailable, ValueError)):
            self.runtime.accept(
                "owner-b", "qualify-m4", raw, qualification_permit=permit)

    def test_qualification_permit_cannot_launch_after_candidate_authority_changes(self) -> None:
        raw = request(dispatch_key="qualify-change")
        candidate = replace(qualification(), production_eligible=False)
        self.authority.current = candidate
        dispatch = json.loads(raw)["dispatch"]
        permit = QualificationPermit(
            vector_id="m4-hardware-v1", owner_scope="owner-a",
            exact_request_sha256=hashlib.sha256(raw).hexdigest(),
            qualification_digest=candidate.qualification_digest,
            hardware_class=candidate.hardware_class,
            story_uuid=dispatch["story_uuid"], intent_id=dispatch["intent_id"],
            attempt_no=dispatch["attempt_no"],
            gateway_attempt_key=dispatch["gateway_attempt_key"],
            destination_site=dispatch["destination_site"],
            destination_controller=dispatch["destination_controller"],
            destination_node=dispatch["destination_node"],
            dispatch_key=dispatch["dispatch_key"], dispatch_fence=dispatch["dispatch_fence"],
        )
        accepted = self.runtime.accept(
            "owner-a", "qualify-change", raw, qualification_permit=permit)
        self.authority.current = replace(candidate, qualification_digest="8" * 64)

        with self.assertRaises(RenderJobUnavailable):
            self.runtime.claim_launch(
                "owner-a", accepted["id"], machine="worker-m4", dispatch_generation=41)
        self.assertEqual(self.runtime.get("owner-a", accepted["id"])["state"], "accepted")

    def test_qualification_vector_is_consumed_once_across_scope_key_race_and_reopen(self) -> None:
        candidate = replace(qualification(), production_eligible=False)
        self.authority.current = candidate

        def bound(raw: bytes, owner: str) -> QualificationPermit:
            dispatch = json.loads(raw)["dispatch"]
            return QualificationPermit(
                vector_id="m4-one-use-vector", owner_scope=owner,
                exact_request_sha256=hashlib.sha256(raw).hexdigest(),
                qualification_digest=candidate.qualification_digest,
                hardware_class=candidate.hardware_class,
                story_uuid=dispatch["story_uuid"], intent_id=dispatch["intent_id"],
                attempt_no=dispatch["attempt_no"],
                gateway_attempt_key=dispatch["gateway_attempt_key"],
                destination_site=dispatch["destination_site"],
                destination_controller=dispatch["destination_controller"],
                destination_node=dispatch["destination_node"],
                dispatch_key=dispatch["dispatch_key"],
                dispatch_fence=dispatch["dispatch_fence"],
            )

        attempts = [
            ("owner-a", "qualify-race-a", request(dispatch_key="qualify-race-a")),
            ("owner-b", "qualify-race-b", request(dispatch_key="qualify-race-b")),
        ]
        barrier = threading.Barrier(2)
        outcomes = []

        def accept(owner, key, raw):
            barrier.wait()
            try:
                outcomes.append((owner, raw, self.runtime.accept(
                    owner, key, raw, qualification_permit=bound(raw, owner))))
            except Exception as exc:  # exact competing transaction outcome is asserted below
                outcomes.append((owner, raw, exc))

        threads = [threading.Thread(target=accept, args=value) for value in attempts]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        accepted = [row for row in outcomes if isinstance(row[2], dict)]
        refused = [row for row in outcomes if isinstance(
            row[2], (RenderJobConflict, RenderJobUnavailable))]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(refused), 1)
        owner, raw, first = accepted[0]
        reopened = RenderJobRuntime(
            self.root, database_path=self.root / "hub.db",
            qualification_resolver=self.authority.resolve,
            probed_qualification_resolver=self.authority.resolve,
            free_bytes=lambda _path: self.free_bytes,
        )
        replay = reopened.accept(
            owner, json.loads(raw)["dispatch"]["dispatch_key"], raw,
            qualification_permit=None,
        )
        self.assertEqual(replay["id"], first["id"])
        self.assertTrue(replay["replayed"])

    def test_authority_document_rides_on_the_row_and_intent_consumption_is_visible(self) -> None:
        document = {"format": "studio.render.qualification-receipt.v1", "measured": {"a": 1}}
        candidate = replace(qualification(), production_eligible=False, document=document)
        self.authority.current = candidate
        raw = request(dispatch_key="qualify-doc")
        dispatch = json.loads(raw)["dispatch"]
        permit = QualificationPermit(
            vector_id="0b6c8d1e-3f4a-4b5c-8d7e-9f0a1b2c3d4e", owner_scope="owner-a",
            exact_request_sha256=hashlib.sha256(raw).hexdigest(),
            qualification_digest=candidate.qualification_digest,
            hardware_class=candidate.hardware_class,
            story_uuid=dispatch["story_uuid"], intent_id=dispatch["intent_id"],
            attempt_no=dispatch["attempt_no"],
            gateway_attempt_key=dispatch["gateway_attempt_key"],
            destination_site=dispatch["destination_site"],
            destination_controller=dispatch["destination_controller"],
            destination_node=dispatch["destination_node"],
            dispatch_key=dispatch["dispatch_key"], dispatch_fence=dispatch["dispatch_fence"],
        )
        self.assertFalse(self.runtime.qualification_consumed(permit.vector_id))
        accepted = self.runtime.accept(
            "owner-a", "qualify-doc", raw, qualification_permit=permit)
        self.assertTrue(self.runtime.qualification_consumed(permit.vector_id))
        claim = self.runtime.claim_launch(
            "owner-a", accepted["id"], machine="worker-m4", dispatch_generation=42)
        context = self.runtime.execution_context(
            "owner-a", accepted["id"], claim["attempt_id"], claim["launch_token"])
        self.assertEqual(context.qualification, candidate)
        self.assertEqual(context.qualification.document, document)
        with self.assertRaises(ValueError):
            replace(candidate, document=["not", "an", "object"])

    def test_claim_is_durable_before_launch_and_uses_only_server_owned_paths(self) -> None:
        job = self.runtime.accept("owner-a", "dispatch-8", request(dispatch_key="dispatch-8"))

        claim = self.runtime.claim_launch(
            "owner-a", job["id"], machine="worker-m4", dispatch_generation=19)
        restarted = RenderJobRuntime(
            self.root,
            database_path=self.root / "hub.db",
            qualification_resolver=self.authority.resolve,
            probed_qualification_resolver=self.authority.resolve,
            free_bytes=lambda _path: self.free_bytes,
        )
        durable = restarted.get("owner-a", job["id"])

        self.assertEqual(durable["state"], "launch_pending")
        self.assertTrue(durable["leases"]["compute"])
        self.assertEqual(durable["attempt_id"], claim["attempt_id"])
        self.assertEqual(durable["machine"], "worker-m4")
        self.assertEqual(Path(claim["workspace"]).parent.parent, self.root / "jobs")
        self.assertEqual(Path(claim["output_path"]).parent, Path(claim["workspace"]))
        self.assertNotIn("argv", claim)
        self.assertNotIn("url", claim)
        with self.assertRaises(RenderJobConflict):
            restarted.claim_launch(
                "owner-a", job["id"], machine="worker-m4", dispatch_generation=19)

    def test_only_one_render_compute_claim_can_hold_a_machine_across_scopes(self) -> None:
        first = self.runtime.accept(
            "owner-a", "dispatch-machine-a", request(dispatch_key="dispatch-machine-a"))
        second = self.runtime.accept(
            "owner-b", "dispatch-machine-b", request(dispatch_key="dispatch-machine-b"))
        self.runtime.claim_launch(
            "owner-a", first["id"], machine="worker-m4", dispatch_generation=30)

        with self.assertRaises(RenderJobUnavailable):
            self.runtime.claim_launch(
                "owner-b", second["id"], machine="worker-m4", dispatch_generation=30)
        self.assertEqual(self.runtime.get("owner-b", second["id"])["state"], "accepted")
        self.assertEqual(
            {row["id"] for row in self.runtime.activity_jobs()}, {first["id"], second["id"]})

    def test_terminal_jobs_lists_only_failed_or_cancelled_attempt_workspaces(self) -> None:
        failed = self.runtime.accept(
            "owner-a", "dispatch-terminal-failed",
            request(dispatch_key="dispatch-terminal-failed"))
        failed_claim = self.runtime.claim_launch(
            "owner-a", failed["id"], machine="worker-m4-terminal-failed",
            dispatch_generation=35)
        self.runtime.record_execution_failed(
            "owner-a", failed["id"], failed_claim["attempt_id"],
            failed_claim["launch_token"], "prelaunch_failed")

        accepted = self.runtime.accept(
            "owner-a", "dispatch-terminal-accepted",
            request(dispatch_key="dispatch-terminal-accepted"))
        active = self.runtime.accept(
            "owner-a", "dispatch-terminal-active",
            request(dispatch_key="dispatch-terminal-active"))
        self.runtime.claim_launch(
            "owner-a", active["id"], machine="worker-m4-terminal-active",
            dispatch_generation=35)
        cancelled_unclaimed = self.runtime.accept(
            "owner-a", "dispatch-terminal-cancelled",
            request(dispatch_key="dispatch-terminal-cancelled"))
        self.runtime.request_cancel("owner-a", cancelled_unclaimed["id"])

        rows = self.runtime.terminal_jobs()

        self.assertEqual([row["id"] for row in rows], [failed["id"]])
        self.assertEqual(rows[0]["state"], "failed")
        self.assertFalse(rows[0]["leases"]["compute"])
        self.assertNotIn("workspace", rows[0])
        self.assertEqual(
            {row["id"] for row in self.runtime.activity_jobs()},
            {accepted["id"], active["id"]})
        self.assertNotIn(accepted["id"], {row["id"] for row in rows})
        self.assertNotIn(active["id"], {row["id"] for row in rows})
        self.assertNotIn(cancelled_unclaimed["id"], {row["id"] for row in rows})

    def test_ambiguous_launch_or_running_restart_never_requeues_or_launches_again(self) -> None:
        for started in (False, True):
            key = f"dispatch-{str(started).lower()}"
            job = self.runtime.accept("owner-a", key, request(dispatch_key=key))
            claim = self.runtime.claim_launch(
                "owner-a", job["id"], machine=f"worker-m4-{started}", dispatch_generation=20)
            if started:
                self.runtime.record_process_started(
                    "owner-a", job["id"], claim["attempt_id"], claim["launch_token"],
                    ProcessIdentity(pid=701, process_group_id=701, start_token="boot-a:701:99"),
                )
        stopped_job = self.runtime.accept(
            "owner-a", "dispatch-stopped-validation",
            request(dispatch_key="dispatch-stopped-validation"))
        stopped_claim = self.runtime.claim_launch(
            "owner-a", stopped_job["id"], machine="worker-m4-stopped",
            dispatch_generation=20)
        stopped_process = ProcessIdentity(
            pid=709, process_group_id=709, start_token="boot-a:709:99")
        self.runtime.record_process_started(
            "owner-a", stopped_job["id"], stopped_claim["attempt_id"],
            stopped_claim["launch_token"], stopped_process)
        stopped = self.runtime.record_process_stopped(
            "owner-a", stopped_job["id"], stopped_claim["attempt_id"],
            StoppedProcessEvidence(
                process=stopped_process, observed_at=99.0,
                exit_code=0, reason="completed"))
        self.assertEqual(stopped["state"], "stopped")
        self.assertTrue(stopped["leases"]["compute"])

        restarted = RenderJobRuntime(
            self.root,
            database_path=self.root / "hub.db",
            qualification_resolver=self.authority.resolve,
            probed_qualification_resolver=self.authority.resolve,
            free_bytes=lambda _path: self.free_bytes,
        )
        recovered = restarted.recover_incomplete()

        self.assertEqual({row["state"] for row in recovered}, {"uncertain"})
        self.assertEqual(len(recovered), 2)
        self.assertTrue(all(row["leases"]["compute"] for row in recovered))
        # A durable stop record already proves the process was stopped; recovery settles
        # it rather than holding compute forever.
        settled_stopped = restarted.get("owner-a", stopped_job["id"])
        self.assertEqual(settled_stopped["state"], "failed")
        self.assertFalse(settled_stopped["leases"]["compute"])
        self.assertEqual(restarted.dispatchable_jobs(), [])
        for row in recovered:
            with self.assertRaises(RenderJobConflict):
                restarted.claim_launch(
                    row["owner_scope"], row["id"], machine="worker-m4",
                    dispatch_generation=21)

    def test_recovery_settles_only_proved_attempts_and_keeps_honest_uncertainty(self) -> None:
        started = self.runtime.accept(
            "owner-a", "dispatch-recover-proved",
            request(dispatch_key="dispatch-recover-proved"))
        started_claim = self.runtime.claim_launch(
            "owner-a", started["id"], machine="worker-m4-proved", dispatch_generation=23)
        started_process = ProcessIdentity(
            pid=811, process_group_id=811, start_token="boot-a:811:10")
        self.runtime.record_process_started(
            "owner-a", started["id"], started_claim["attempt_id"],
            started_claim["launch_token"], started_process)

        pending = self.runtime.accept(
            "owner-a", "dispatch-recover-pending",
            request(dispatch_key="dispatch-recover-pending"))
        self.runtime.claim_launch(
            "owner-a", pending["id"], machine="worker-m4-pending", dispatch_generation=23)

        validated = self.runtime.accept(
            "owner-a", "dispatch-recover-stopped",
            request(dispatch_key="dispatch-recover-stopped"))
        validated_claim = self.runtime.claim_launch(
            "owner-a", validated["id"], machine="worker-m4-stopped",
            dispatch_generation=23)
        validated_process = ProcessIdentity(
            pid=812, process_group_id=812, start_token="boot-a:812:10")
        self.runtime.record_process_started(
            "owner-a", validated["id"], validated_claim["attempt_id"],
            validated_claim["launch_token"], validated_process)
        self.runtime.record_process_stopped(
            "owner-a", validated["id"], validated_claim["attempt_id"],
            StoppedProcessEvidence(
                process=validated_process, observed_at=102.0,
                exit_code=0, reason="completed"))

        restarted = RenderJobRuntime(
            self.root, database_path=self.root / "hub.db",
            qualification_resolver=self.authority.resolve,
            probed_qualification_resolver=self.authority.resolve,
            free_bytes=lambda _path: self.free_bytes,
        )
        recovered = restarted.recover_incomplete_with_proof(
            lambda identity, _created: identity is not None and identity.pid == 811)

        self.assertEqual([row["id"] for row in recovered], [pending["id"]])
        self.assertEqual(recovered[0]["state"], "uncertain")
        self.assertTrue(recovered[0]["leases"]["compute"])
        for job_id in (started["id"], validated["id"]):
            row = restarted.get("owner-a", job_id)
            self.assertEqual(row["state"], "failed")
            self.assertFalse(row["leases"]["compute"])
        self.assertEqual(restarted.dispatchable_jobs(), [])

        cancelling = restarted.request_cancel_unsettled("owner-a", pending["id"])
        self.assertEqual(cancelling["state"], "uncertain")
        self.assertIsNotNone(cancelling["cancel_requested_at"])
        self.assertTrue(cancelling["leases"]["compute"])
        settled = restarted.settle_recovered(
            "owner-a", pending["id"], proof="test: exact recorded process proved gone")
        self.assertEqual(settled["state"], "cancelled")
        self.assertFalse(settled["leases"]["compute"])

    def test_recovery_proof_exception_never_guesses_a_stop(self) -> None:
        job = self.runtime.accept(
            "owner-a", "dispatch-recover-raising",
            request(dispatch_key="dispatch-recover-raising"))
        claim = self.runtime.claim_launch(
            "owner-a", job["id"], machine="worker-m4-raising", dispatch_generation=24)
        self.runtime.record_process_started(
            "owner-a", job["id"], claim["attempt_id"], claim["launch_token"],
            ProcessIdentity(pid=813, process_group_id=813, start_token="boot-a:813:10"))

        restarted = RenderJobRuntime(
            self.root, database_path=self.root / "hub.db",
            qualification_resolver=self.authority.resolve,
            probed_qualification_resolver=self.authority.resolve,
            free_bytes=lambda _path: self.free_bytes,
        )

        def unproved(_identity, _created):
            raise RuntimeError("process inspection unavailable")

        recovered = restarted.recover_incomplete_with_proof(unproved)

        self.assertEqual([row["id"] for row in recovered], [job["id"]])
        self.assertEqual(recovered[0]["state"], "uncertain")
        self.assertTrue(recovered[0]["leases"]["compute"])

    def test_cancel_retains_compute_until_exact_process_stop_is_durable(self) -> None:
        job = self.runtime.accept(
            "owner-a", "dispatch-cancel", request(dispatch_key="dispatch-cancel"))
        claim = self.runtime.claim_launch(
            "owner-a", job["id"], machine="worker-m4", dispatch_generation=22)
        process = ProcessIdentity(pid=702, process_group_id=702, start_token="boot-a:702:10")
        self.runtime.record_process_started(
            "owner-a", job["id"], claim["attempt_id"], claim["launch_token"], process)

        cancelling = self.runtime.request_cancel("owner-a", job["id"])
        self.assertEqual(cancelling["state"], "cancel_requested")
        self.assertTrue(cancelling["leases"]["compute"])
        with self.assertRaises(RenderJobConflict):
            self.runtime.record_process_stopped(
                "owner-a", job["id"], claim["attempt_id"],
                StoppedProcessEvidence(
                    process=ProcessIdentity(
                        pid=702, process_group_id=702, start_token="different-process"),
                    observed_at=100.0, exit_code=-15, reason="cancelled",
                ),
            )

        stopped = self.runtime.record_process_stopped(
            "owner-a", job["id"], claim["attempt_id"],
            StoppedProcessEvidence(
                process=process, observed_at=101.0, exit_code=-15, reason="cancelled"),
        )
        self.assertEqual(stopped["state"], "cancelled")
        self.assertFalse(stopped["leases"]["compute"])
        self.assertIsNotNone(stopped["stopped_evidence_digest"])

    def test_only_known_prelaunch_or_poststop_failure_releases_compute(self) -> None:
        pre = self.runtime.accept(
            "owner-a", "dispatch-prelaunch-fail",
            request(dispatch_key="dispatch-prelaunch-fail"))
        pre_claim = self.runtime.claim_launch(
            "owner-a", pre["id"], machine="worker-m4-pre", dispatch_generation=41)
        pre_failed = self.runtime.record_execution_failed(
            "owner-a", pre["id"], pre_claim["attempt_id"], pre_claim["launch_token"],
            "prelaunch_failed")
        self.assertEqual(pre_failed["state"], "failed")
        self.assertFalse(pre_failed["leases"]["compute"])

        post = self.runtime.accept(
            "owner-a", "dispatch-validation-fail",
            request(dispatch_key="dispatch-validation-fail"))
        post_claim = self.runtime.claim_launch(
            "owner-a", post["id"], machine="worker-m4-post", dispatch_generation=42)
        process = ProcessIdentity(pid=713, process_group_id=713, start_token="boot-a:713:1")
        self.runtime.record_process_started(
            "owner-a", post["id"], post_claim["attempt_id"],
            post_claim["launch_token"], process)
        with self.assertRaises(RenderJobConflict):
            self.runtime.record_execution_failed(
                "owner-a", post["id"], post_claim["attempt_id"],
                post_claim["launch_token"], "prelaunch_failed")
        self.assertTrue(self.runtime.get("owner-a", post["id"])["leases"]["compute"])
        self.runtime.record_process_stopped(
            "owner-a", post["id"], post_claim["attempt_id"],
            StoppedProcessEvidence(
                process=process, observed_at=110.0, exit_code=0, reason="completed"),
            validation_pending=True)
        post_failed = self.runtime.record_execution_failed(
            "owner-a", post["id"], post_claim["attempt_id"],
            post_claim["launch_token"], "output_validation_failed")
        self.assertEqual(post_failed["state"], "failed")
        self.assertFalse(post_failed["leases"]["compute"])

    def test_cancel_after_successful_stop_settles_and_preserves_evidence_and_file_leases(self) -> None:
        raw = request(dispatch_key="dispatch-stop-then-cancel")
        job = self.runtime.accept("owner-a", "dispatch-stop-then-cancel", raw)
        claim = self.runtime.claim_launch(
            "owner-a", job["id"], machine="worker-m4", dispatch_generation=31)
        process = ProcessIdentity(pid=704, process_group_id=704, start_token="boot-a:704:1")
        self.runtime.record_process_started(
            "owner-a", job["id"], claim["attempt_id"], claim["launch_token"], process)
        evidence = StoppedProcessEvidence(
            process=process, observed_at=103.0, exit_code=0, reason="completed")
        stopped = self.runtime.record_process_stopped(
            "owner-a", job["id"], claim["attempt_id"], evidence)
        self.runtime.acquire_file_lease("owner-a", job["id"], "reader", "reader-kept")
        self.runtime.acquire_file_lease("owner-a", job["id"], "ack", "ack-kept")

        cancelled = self.runtime.request_cancel("owner-a", job["id"])

        self.assertEqual(cancelled["state"], "cancelled")
        self.assertFalse(cancelled["leases"]["compute"])
        self.assertEqual(cancelled["stopped_evidence_digest"], stopped["stopped_evidence_digest"])
        self.assertEqual(cancelled["leases"]["reader"], 1)
        self.assertEqual(cancelled["leases"]["ack"], 1)
        replay = self.runtime.record_process_stopped(
            "owner-a", job["id"], claim["attempt_id"], evidence)
        self.assertEqual(replay["state"], "cancelled")
        with self.assertRaises(RenderJobConflict):
            self.runtime.publish_output(
                "owner-a", job["id"],
                ValidatedOutputReceipt(
                    owner_scope="owner-a", hub_job_id=job["id"],
                    attempt_id=claim["attempt_id"],
                    accepted_request_sha256=hashlib.sha256(raw).hexdigest(),
                    receipt_digest="9" * 64, output_sha256="6" * 64,
                    output_size_bytes=1234, profile_digest=PROFILE_DIGEST,
                    tools_digest=TOOLS_DIGEST, compiler_digest=COMPILER_DIGEST,
                    qualification_digest=QUALIFICATION_DIGEST,
                    generated_command_digest="7" * 64,
                    selected_tier="software-h264", feature_losses=(),
                ),
            )
        next_job = self.runtime.accept(
            "owner-b", "dispatch-after-cancel",
            request(dispatch_key="dispatch-after-cancel"))
        next_claim = self.runtime.claim_launch(
            "owner-b", next_job["id"], machine="worker-m4", dispatch_generation=32)
        self.assertEqual(next_claim["machine"], "worker-m4")

    def test_active_validator_cancel_retains_compute_until_exact_adapter_settlement(self) -> None:
        raw = request(dispatch_key="dispatch-validator-cancel")
        job = self.runtime.accept("owner-a", "dispatch-validator-cancel", raw)
        claim = self.runtime.claim_launch(
            "owner-a", job["id"], machine="worker-m4", dispatch_generation=32)
        process = ProcessIdentity(pid=705, process_group_id=705, start_token="boot-a:705:1")
        self.runtime.record_process_started(
            "owner-a", job["id"], claim["attempt_id"], claim["launch_token"], process)
        stopped = self.runtime.record_process_stopped(
            "owner-a", job["id"], claim["attempt_id"],
            StoppedProcessEvidence(
                process=process, observed_at=104.0, exit_code=0, reason="completed"),
            validation_pending=True,
        )

        pending = self.runtime.request_cancel_pending("owner-a", job["id"])

        self.assertEqual(pending["state"], "cancel_requested")
        self.assertTrue(pending["leases"]["compute"])
        self.assertEqual(pending["stopped_evidence_digest"], stopped["stopped_evidence_digest"])
        cancelled = self.runtime.record_execution_failed(
            "owner-a", job["id"], claim["attempt_id"], claim["launch_token"],
            "output_validation_failed",
        )
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertFalse(cancelled["leases"]["compute"])

    def test_cancel_by_key_during_validation_settles_on_recovery_with_stop_evidence(self) -> None:
        raw = request(dispatch_key="dispatch-key-validator-cancel")
        job = self.runtime.accept("owner-a", "dispatch-key-validator-cancel", raw)
        claim = self.runtime.claim_launch(
            "owner-a", job["id"], machine="worker-m4", dispatch_generation=33)
        process = ProcessIdentity(pid=706, process_group_id=706, start_token="boot-a:706:1")
        self.runtime.record_process_started(
            "owner-a", job["id"], claim["attempt_id"], claim["launch_token"], process)
        self.runtime.record_process_stopped(
            "owner-a", job["id"], claim["attempt_id"],
            StoppedProcessEvidence(
                process=process, observed_at=105.0, exit_code=0, reason="completed"),
            validation_pending=True,
        )

        pending = self.runtime.request_cancel_by_key(
            "owner-a", "dispatch-key-validator-cancel", hashlib.sha256(raw).hexdigest())

        self.assertEqual(pending["state"], "cancel_requested")
        self.assertTrue(pending["leases"]["compute"])
        restarted = RenderJobRuntime(
            self.root,
            database_path=self.root / "hub.db",
            qualification_resolver=self.authority.resolve,
            probed_qualification_resolver=self.authority.resolve,
            free_bytes=lambda _path: self.free_bytes,
        )
        recovered = restarted.recover_incomplete()
        self.assertEqual(recovered, [])
        settled = restarted.get("owner-a", job["id"])
        self.assertEqual(settled["state"], "cancelled")
        self.assertFalse(settled["leases"]["compute"])
        self.assertIsNotNone(settled["stopped_evidence_digest"])
        with self.assertRaises(RenderJobConflict):
            self.runtime.publish_output(
                "owner-a", job["id"],
                ValidatedOutputReceipt(
                    owner_scope="owner-a", hub_job_id=job["id"],
                    attempt_id=claim["attempt_id"],
                    accepted_request_sha256=hashlib.sha256(raw).hexdigest(),
                    receipt_digest="9" * 64, output_sha256="6" * 64,
                    output_size_bytes=1234, profile_digest=PROFILE_DIGEST,
                    tools_digest=TOOLS_DIGEST, compiler_digest=COMPILER_DIGEST,
                    qualification_digest=QUALIFICATION_DIGEST,
                    generated_command_digest="7" * 64,
                    selected_tier="software-h264", feature_losses=(),
                ),
            )

    def test_cancel_timestamp_survives_recovery_and_fences_completed_output(self) -> None:
        raw = request(dispatch_key="dispatch-cancel-recover")
        job = self.runtime.accept("owner-a", "dispatch-cancel-recover", raw)
        claim = self.runtime.claim_launch(
            "owner-a", job["id"], machine="worker-m4-cancel", dispatch_generation=33)
        process = ProcessIdentity(pid=705, process_group_id=705, start_token="boot-a:705:1")
        self.runtime.record_process_started(
            "owner-a", job["id"], claim["attempt_id"], claim["launch_token"], process)
        self.runtime.request_cancel("owner-a", job["id"])
        with sqlite3.connect(self.root / "hub.db") as connection:
            first_cancelled_at = connection.execute(
                "SELECT cancel_requested_at FROM render_jobs WHERE id=?", (job["id"],)
            ).fetchone()[0]
        self.runtime.request_cancel("owner-a", job["id"])
        with sqlite3.connect(self.root / "hub.db") as connection:
            repeated_cancelled_at = connection.execute(
                "SELECT cancel_requested_at FROM render_jobs WHERE id=?", (job["id"],)
            ).fetchone()[0]
        self.assertEqual(repeated_cancelled_at, first_cancelled_at)

        control = self.runtime.accept(
            "owner-a", "dispatch-recover-control",
            request(dispatch_key="dispatch-recover-control"))
        control_claim = self.runtime.claim_launch(
            "owner-a", control["id"], machine="worker-m4-control", dispatch_generation=34)
        control_process = ProcessIdentity(
            pid=706, process_group_id=706, start_token="boot-a:706:1")
        self.runtime.record_process_started(
            "owner-a", control["id"], control_claim["attempt_id"],
            control_claim["launch_token"], control_process)

        restarted = RenderJobRuntime(
            self.root, database_path=self.root / "hub.db",
            qualification_resolver=self.authority.resolve,
            probed_qualification_resolver=self.authority.resolve,
            free_bytes=lambda _path: self.free_bytes,
        )
        restarted.recover_incomplete()
        cancelled = restarted.record_process_stopped(
            "owner-a", job["id"], claim["attempt_id"],
            StoppedProcessEvidence(
                process=process, observed_at=104.0, exit_code=0, reason="completed"),
        )
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertFalse(cancelled["leases"]["compute"])
        with self.assertRaises(RenderJobConflict):
            restarted.publish_output(
                "owner-a", job["id"],
                ValidatedOutputReceipt(
                    owner_scope="owner-a", hub_job_id=job["id"],
                    attempt_id=claim["attempt_id"],
                    accepted_request_sha256=hashlib.sha256(raw).hexdigest(),
                    receipt_digest="a" * 64, output_sha256="b" * 64,
                    output_size_bytes=1234, profile_digest=PROFILE_DIGEST,
                    tools_digest=TOOLS_DIGEST, compiler_digest=COMPILER_DIGEST,
                    qualification_digest=QUALIFICATION_DIGEST,
                    generated_command_digest="c" * 64,
                    selected_tier="software-h264", feature_losses=(),
                ),
            )
        control_stopped = restarted.record_process_stopped(
            "owner-a", control["id"], control_claim["attempt_id"],
            StoppedProcessEvidence(
                process=control_process, observed_at=105.0,
                exit_code=0, reason="completed"),
        )
        self.assertEqual(control_stopped["state"], "stopped")
        self.assertTrue(control_stopped["leases"]["compute"])

    def test_original_claim_can_bind_once_while_cancellation_is_pending(self) -> None:
        job = self.runtime.accept(
            "owner-a", "dispatch-cancel-before-bind",
            request(dispatch_key="dispatch-cancel-before-bind"))
        claim = self.runtime.claim_launch(
            "owner-a", job["id"], machine="worker-m4", dispatch_generation=35)
        cancelling = self.runtime.request_cancel("owner-a", job["id"])
        self.assertEqual(cancelling["state"], "cancel_requested")
        process = ProcessIdentity(pid=707, process_group_id=707, start_token="boot-a:707:1")
        try:
            bound = self.runtime.record_process_started(
                "owner-a", job["id"], claim["attempt_id"], claim["launch_token"], process)
        except RenderJobConflict as exc:
            self.fail(f"the original claimed process must remain bindable: {exc}")
        self.assertEqual(bound["state"], "cancel_requested")
        self.assertTrue(bound["leases"]["compute"])
        self.assertEqual(
            self.runtime.record_process_started(
                "owner-a", job["id"], claim["attempt_id"], claim["launch_token"], process
            )["state"],
            "cancel_requested",
        )
        with self.assertRaises(RenderJobConflict):
            self.runtime.record_process_started(
                "owner-a", job["id"], claim["attempt_id"], claim["launch_token"],
                ProcessIdentity(pid=708, process_group_id=708, start_token="boot-a:708:1"),
            )
        with self.assertRaises(RenderJobConflict):
            self.runtime.record_process_started(
                "owner-a", job["id"], claim["attempt_id"], "wrong-token", process)
        stopped = self.runtime.record_process_stopped(
            "owner-a", job["id"], claim["attempt_id"],
            StoppedProcessEvidence(
                process=process, observed_at=106.0, exit_code=-15, reason="cancelled"),
        )
        self.assertEqual(stopped["state"], "cancelled")
        self.assertFalse(stopped["leases"]["compute"])

    def test_output_receipt_binds_scope_job_attempt_and_exact_request(self) -> None:
        required = {"owner_scope", "hub_job_id", "attempt_id", "accepted_request_sha256"}
        self.assertTrue(required <= {field.name for field in fields(ValidatedOutputReceipt)})
        jobs = []
        for suffix, owner in (("a", "owner-a"), ("b", "owner-b")):
            raw = request(
                dispatch_key=f"dispatch-receipt-{suffix}",
                base_handle=suffix * 32,
            )
            job = self.runtime.accept(owner, f"dispatch-receipt-{suffix}", raw)
            claim = self.runtime.claim_launch(
                owner, job["id"], machine=f"worker-m4-{suffix}", dispatch_generation=36)
            process = ProcessIdentity(
                pid=710 + len(jobs), process_group_id=710 + len(jobs),
                start_token=f"boot-a:{710 + len(jobs)}:1",
            )
            self.runtime.record_process_started(
                owner, job["id"], claim["attempt_id"], claim["launch_token"], process)
            self.runtime.record_process_stopped(
                owner, job["id"], claim["attempt_id"],
                StoppedProcessEvidence(
                    process=process, observed_at=107.0 + len(jobs),
                    exit_code=0, reason="completed"),
            )
            jobs.append((owner, raw, job, claim))

        first_owner, first_raw, first_job, first_claim = jobs[0]
        second_owner, second_raw, second_job, second_claim = jobs[1]
        first_receipt = ValidatedOutputReceipt(
            owner_scope=first_owner, hub_job_id=first_job["id"],
            attempt_id=first_claim["attempt_id"],
            accepted_request_sha256=hashlib.sha256(first_raw).hexdigest(),
            receipt_digest="d" * 64, output_sha256="e" * 64,
            output_size_bytes=1234, profile_digest=PROFILE_DIGEST,
            tools_digest=TOOLS_DIGEST, compiler_digest=COMPILER_DIGEST,
            qualification_digest=QUALIFICATION_DIGEST,
            generated_command_digest="f" * 64,
            selected_tier="software-h264", feature_losses=(),
        )
        with self.assertRaises(RenderJobConflict):
            self.runtime.publish_output(second_owner, second_job["id"], first_receipt)
        self.assertTrue(
            self.runtime.get(second_owner, second_job["id"])["leases"]["compute"])

        second_receipt = ValidatedOutputReceipt(
            owner_scope=second_owner, hub_job_id=second_job["id"],
            attempt_id=second_claim["attempt_id"],
            accepted_request_sha256=hashlib.sha256(second_raw).hexdigest(),
            receipt_digest="1" * 64, output_sha256="2" * 64,
            output_size_bytes=1234, profile_digest=PROFILE_DIGEST,
            tools_digest=TOOLS_DIGEST, compiler_digest=COMPILER_DIGEST,
            qualification_digest=QUALIFICATION_DIGEST,
            generated_command_digest="3" * 64,
            selected_tier="software-h264", feature_losses=(),
        )
        published = self.runtime.publish_output(
            second_owner, second_job["id"], second_receipt)
        self.assertEqual(published["state"], "output_ready")
        self.assertFalse(published["leases"]["compute"])
        self.assertEqual(
            self.runtime.publish_output(
                second_owner, second_job["id"], second_receipt)["output_receipt"],
            published["output_receipt"],
        )

    def test_validated_immutable_receipt_releases_compute_but_file_leases_remain_separate(self) -> None:
        raw = request(dispatch_key="dispatch-output")
        job = self.runtime.accept("owner-a", "dispatch-output", raw)
        claim = self.runtime.claim_launch(
            "owner-a", job["id"], machine="worker-m4", dispatch_generation=23)
        process = ProcessIdentity(pid=703, process_group_id=703, start_token="boot-a:703:12")
        self.runtime.record_process_started(
            "owner-a", job["id"], claim["attempt_id"], claim["launch_token"], process)
        self.runtime.record_process_stopped(
            "owner-a", job["id"], claim["attempt_id"],
            StoppedProcessEvidence(
                process=process, observed_at=102.0, exit_code=0, reason="completed"),
        )
        self.runtime.acquire_file_lease("owner-a", job["id"], "reader", "download-1")
        self.runtime.acquire_file_lease("owner-a", job["id"], "ack", "gateway-ack-1")

        with self.assertRaises(TypeError):
            self.runtime.publish_output("owner-a", job["id"], {"sha256": "5" * 64})
        with self.assertRaises(ValueError):
            ValidatedOutputReceipt(
                owner_scope="owner-a", hub_job_id=job["id"],
                attempt_id=claim["attempt_id"],
                accepted_request_sha256=hashlib.sha256(raw).hexdigest(),
                receipt_digest="Z" * 64, output_sha256="6" * 64,
                output_size_bytes=-1, profile_digest=PROFILE_DIGEST,
                tools_digest=TOOLS_DIGEST, compiler_digest=COMPILER_DIGEST,
                qualification_digest=QUALIFICATION_DIGEST,
                generated_command_digest="7" * 64, selected_tier="software-h264",
                feature_losses=(),
            )
        receipt = ValidatedOutputReceipt(
            owner_scope="owner-a", hub_job_id=job["id"],
            attempt_id=claim["attempt_id"],
            accepted_request_sha256=hashlib.sha256(raw).hexdigest(),
            receipt_digest="5" * 64,
            output_sha256="6" * 64,
            output_size_bytes=1234,
            profile_digest=PROFILE_DIGEST,
            tools_digest=TOOLS_DIGEST,
            compiler_digest=COMPILER_DIGEST,
            qualification_digest=QUALIFICATION_DIGEST,
            generated_command_digest="7" * 64,
            selected_tier="software-h264",
            feature_losses=(),
        )
        ready = self.runtime.publish_output("owner-a", job["id"], receipt)

        self.assertEqual(ready["state"], "output_ready")
        self.assertFalse(ready["leases"]["compute"])
        self.assertEqual(ready["leases"]["reader"], 1)
        self.assertEqual(ready["leases"]["ack"], 1)
        self.assertEqual(
            self.runtime.publish_output(
                "owner-a", job["id"], receipt)["output_receipt"],
            ready["output_receipt"],
        )
        changed = ValidatedOutputReceipt(
            owner_scope="owner-a", hub_job_id=job["id"],
            attempt_id=claim["attempt_id"],
            accepted_request_sha256=hashlib.sha256(raw).hexdigest(),
            receipt_digest="8" * 64, output_sha256="6" * 64,
            output_size_bytes=1234, profile_digest=PROFILE_DIGEST,
            tools_digest=TOOLS_DIGEST, compiler_digest=COMPILER_DIGEST,
            qualification_digest=QUALIFICATION_DIGEST,
            generated_command_digest="7" * 64, selected_tier="software-h264",
            feature_losses=(),
        )
        with self.assertRaises(RenderJobConflict):
            self.runtime.publish_output("owner-a", job["id"], changed)

    def test_low_space_refuses_launch_without_cleanup_or_trash_mutation(self) -> None:
        sentinel = self.root / "_trash/owner-kept.txt"
        sentinel.parent.mkdir()
        sentinel.write_text("keep", encoding="utf-8")
        job = self.runtime.accept(
            "owner-a", "dispatch-low-disk", request(dispatch_key="dispatch-low-disk"))
        self.free_bytes = 1

        with self.assertRaises(RenderJobUnavailable):
            self.runtime.claim_launch(
                "owner-a", job["id"], machine="worker-m4", dispatch_generation=24)

        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
        self.assertEqual(self.runtime.get("owner-a", job["id"])["state"], "accepted")


if __name__ == "__main__":
    unittest.main()
