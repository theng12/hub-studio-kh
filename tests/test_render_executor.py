#!/usr/bin/env python3
"""Closed executor behavior with fake tools/process/probe only."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import signal
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.hub.backend import render_encode  # noqa: E402
from components.hub.backend.final_render_compiler import (  # noqa: E402
    EncodeTier, FinalRenderPlan, compile_final_render_packet,
)
from components.hub.backend.render_hash_v1 import render_hash  # noqa: E402
from components.hub.backend.render_executor import (  # noqa: E402
    InstalledRenderAuthority,
    InstalledRenderAuthorityLoader,
    RenderExecutor,
    ResourceBounds,
)
from components.hub.backend.render_jobs import (  # noqa: E402
    ProcessIdentity,
    RenderExecutionContext,
    RenderJobUnavailable,
    RuntimeQualification,
    ValidatedOutputReceipt,
)
from components.hub.backend.render_service import RenderService  # noqa: E402
from _render_authority_root import anchor_root  # noqa: E402


class FakeBroker:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.owner = None

    async def higher_priority_work_waiting(self, **_kwargs) -> bool:
        return False

    def render_dispatch_allowed(self, _machine: str, _owner: str | None = None) -> bool:
        return True

    def acquire_external_machine(self, machine: str, owner: str) -> bool:
        self.events.append("physical")
        self.owner = (machine, owner)
        return True

    def release_external_machine(self, machine: str, owner: str) -> None:
        self.events.append("release")
        if self.owner == (machine, owner):
            self.owner = None


class FakeRuntime:
    def __init__(self, root: Path, raw: bytes, qualification: RuntimeQualification,
                 events: list[str]) -> None:
        self.events = events
        self.raw = raw
        self.qualification = qualification
        self.root = root
        self.job = {
            "id": "job-1", "owner_scope": "site-a:node-a:fleet", "state": "accepted",
            "created_at": 1.0, "purpose": "production",
        }
        self.claim = None
        self.process = None
        self.published = None
        self.compute_held = False
        self.recovery = None

    def dispatchable_jobs(self):
        return [self.job] if self.job["state"] == "accepted" else []

    def claim_launch(self, owner_scope, job_id, *, machine, dispatch_generation):
        self.events.append("claim")
        workspace = self.root / "jobs/job-1/attempt-1"
        workspace.mkdir(parents=True)
        self.job.update(state="launch_pending", attempt_id="attempt-1", machine=machine)
        self.compute_held = True
        self.claim = {
            "job_id": job_id, "attempt_id": "attempt-1", "launch_token": "launch-1",
            "machine": machine, "workspace": str(workspace),
            "output_path": str(workspace / "final.mp4"),
            "accepted_request_sha256": hashlib.sha256(self.raw).hexdigest(),
        }
        return dict(self.claim)

    def execution_context(self, owner_scope, job_id, attempt_id, launch_token):
        self.events.append("context")
        return RenderExecutionContext(
            owner_scope=owner_scope, hub_job_id=job_id, attempt_id=attempt_id,
            launch_token=launch_token, accepted_request_sha256=hashlib.sha256(self.raw).hexdigest(),
            raw_request=self.raw, packet=json.loads(self.raw)["packet"],
            assets=json.loads(self.raw)["assets"], qualification=self.qualification,
            qualification_permit=None, purpose="production",
            workspace=self.claim["workspace"], output_path=self.claim["output_path"],
            machine="local", dispatch_generation=1,
        )

    def record_process_started(self, owner_scope, job_id, attempt_id, launch_token, process):
        self.events.append("bind")
        self.process = process
        self.job["state"] = "rendering"
        return dict(self.job)

    def record_process_stopped(
        self, owner_scope, job_id, attempt_id, evidence, *, validation_pending=False,
    ):
        self.events.append("stopped")
        self.job["state"] = (
            "cancelled" if self.job.get("cancel_requested_at") is not None
            else "stopped" if evidence.reason == "completed" else "failed"
        )
        if self.job["state"] != "stopped":
            self.compute_held = False
        elif validation_pending:
            self.job["stopped_evidence_digest"] = "a" * 64
        return dict(self.job)

    def acquire_file_lease(self, *_args):
        self.events.append("ack-lease")

    def publish_output(self, owner_scope, job_id, receipt):
        self.events.append("publish")
        self.published = receipt
        self.job["state"] = "output_ready"
        self.compute_held = False
        return dict(self.job)

    def record_execution_failed(
        self, owner_scope, job_id, attempt_id, launch_token, phase,
    ):
        self.events.append(phase)
        self.job["state"] = (
            "cancelled" if self.job.get("cancel_requested_at") is not None else "failed")
        self.compute_held = False
        return self.get(owner_scope, job_id)

    def get(self, owner_scope, job_id):
        if owner_scope != self.job["owner_scope"] or job_id != self.job["id"]:
            return None
        return {**self.job, "leases": {"compute": self.compute_held}}

    def request_cancel(self, owner_scope, job_id):
        self.job["state"] = "cancel_requested"
        self.job["cancel_requested_at"] = 1.0
        return self.get(owner_scope, job_id)

    def request_cancel_pending(self, owner_scope, job_id):
        self.job["state"] = "cancel_requested"
        self.job["cancel_requested_at"] = 1.0
        return self.get(owner_scope, job_id)

    def request_cancel_by_key(self, owner_scope, key, expected_request_sha256):
        self.job["state"] = "cancel_requested"
        self.job["cancel_requested_at"] = 1.0
        return self.get(owner_scope, self.job["id"])

    def recovery_record(self, _owner_scope, _job_id):
        return dict(self.recovery) if self.recovery is not None else None

    def request_cancel_unsettled(self, owner_scope, job_id):
        self.events.append("cancel-unsettled")
        self.job["cancel_requested_at"] = 1.0
        return self.get(owner_scope, job_id)

    def settle_recovered(self, owner_scope, job_id, *, proof):
        self.events.append("settle-recovered")
        self.proof = proof
        self.job["state"] = (
            "cancelled" if self.job.get("cancel_requested_at") is not None else "failed")
        self.compute_held = False
        return self.get(owner_scope, job_id)


class Reader:
    def __init__(self, path: Path) -> None:
        self.file = path.open("rb")

    def __enter__(self):
        return self

    def fileno(self):
        return self.file.fileno()

    def __exit__(self, *_args):
        self.file.close()


class FakeAssets:
    def __init__(self, files: dict[str, Path]) -> None:
        self.files = files

    def open_reader(self, _owner_scope: str, handle: str) -> Reader:
        return Reader(self.files[handle])


class FakeProcess:
    # Above macOS's pid range: a fake must never collide with a live process, or the
    # resource probe reads a stranger's pid and the render takes the failed path.
    pid = 2**31 - 1
    returncode = None

    def __init__(self, output: Path) -> None:
        self.output = output

    async def wait(self) -> int:
        self.output.write_bytes(b"validated-output")
        self.returncode = 0
        return 0


class BoundedStream:
    def __init__(self, payload: bytes, *, on_read=None) -> None:
        self.payload = payload
        self.offset = 0
        self.bytes_read = 0
        self.on_read = on_read

    async def read(self, size: int = -1) -> bytes:
        if self.on_read is not None:
            self.on_read()
        if self.offset >= len(self.payload):
            return b""
        if size < 0:
            raise AssertionError("probe output must be read in bounded chunks")
        chunk = self.payload[self.offset:self.offset + size]
        self.offset += len(chunk)
        self.bytes_read += len(chunk)
        return chunk


class FakeProbe:
    pid = 732
    returncode = 0

    def __init__(self, document: dict) -> None:
        self.document = document
        self.stdout = BoundedStream(json.dumps(document).encode())
        self.stderr = BoundedStream(b"")

    async def communicate(self):
        raise AssertionError("probe output must not use unbounded communicate()")

    async def wait(self):
        return self.returncode


class StreamingProbe:
    pid = 733

    def __init__(self, stdout: bytes, stderr: bytes = b"") -> None:
        self.stdout = BoundedStream(stdout)
        self.stderr = BoundedStream(stderr)
        self.returncode = 0

    async def wait(self) -> int:
        return self.returncode

    async def communicate(self):
        raise AssertionError("probe output must not use unbounded communicate()")


class RenderExecutorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.root = ROOT / "_trash/render-executor-testroots" / uuid4().hex
        self.root.mkdir(parents=True)
        anchor_root(self, self.root)
        self.raw = (ROOT / "tests/fixtures/final-render-compiler-v1.json").read_bytes()

    def authority(self) -> InstalledRenderAuthority:
        qualification = RuntimeQualification(
            profile_digest="1" * 64, tools_digest="2" * 64,
            compiler_digest="3" * 64, qualification_digest="4" * 64,
            hardware_class="m4-16gb", production_eligible=True,
            min_free_bytes=1_250_000, scratch_reserve_bytes=250_000,
            output_limit_bytes=1_000_000,
        )

        tools = self.root / "tools"
        tools.mkdir(exist_ok=True)
        for name in ("ffmpeg", "ffprobe"):
            path = tools / name
            path.write_bytes(b"fixed-fake-tool")
            path.chmod(0o755)
        return InstalledRenderAuthority(
            qualification=qualification, ffmpeg=tools / "ffmpeg", ffprobe=tools / "ffprobe",
            selected_tier="hardware-h264", exact_encoder="h264_videotoolbox",
            bounds=ResourceBounds(max_rss_bytes=100_000_000, max_wall_seconds=30,
                                  min_available_memory_bytes=1, output_limit_bytes=1_000_000),
            font_support=False,
            profile_execution_bounds={
                "canvas_pairs": [{"width": 128, "height": 96}], "working_fps": [5],
                "max_duration_sec": 2.5, "max_operations": 9, "max_assets": 6,
                "max_asset_bytes": 1_000_000, "max_total_input_bytes": 2_000_000,
                "max_pixel_frames": 1_000_000, "scratch_reserve_bytes": 250_000,
                "output_limit_bytes": 1_000_000,
                "process_tree_rss_limit_bytes": 100_000_000,
                "cumulative_wall_seconds": 30,
            },
        )

    @staticmethod
    def mp4_bytes() -> bytes:
        return b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2dref"

    @staticmethod
    def wav_bytes() -> bytes:
        return (b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt "
                + (16).to_bytes(4, "little") + b"\x01\x00\x01\x00"
                + (48_000).to_bytes(4, "little") + (96_000).to_bytes(4, "little")
                + b"\x02\x00\x10\x00data\x00\x00\x00\x00")

    INTENT_ID = "0b6c8d1e-3f4a-4b5c-8d7e-9f0a1b2c3d4e"

    @staticmethod
    def compact(value) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

    def write_record(self, *names: str, value) -> Path:
        directory = self.data_root / "render-authority"
        for name in names[:-1]:
            directory = directory / name
            directory.mkdir(mode=0o700, exist_ok=True)
            directory.chmod(0o700)
        path = directory / names[-1]
        path.unlink(missing_ok=True)
        path.write_bytes(self.compact(value) if not isinstance(value, bytes) else value)
        path.chmod(0o600)
        return path

    def intent(self, **changes) -> dict:
        return {
            "format": "studio.render.qualification-intent.v1", "intent_id": self.INTENT_ID,
            "accept_before": 2_000, "selected_tier": "hardware-h264",
            "exact_encoder": "h264_videotoolbox", "font_support": False,
            "min_available_memory_bytes": 2_000_000_000, **changes,
        }

    def candidate_loader(self, *, memory_gb: int = 16, profile_bounds=None):
        resources = self.root / "candidate-resources"
        tools_root = resources / "render-tools"
        tools_root.mkdir(parents=True)
        self.data_root = self.root / "data"
        (self.data_root / "render-authority").mkdir(parents=True)
        (self.data_root / "render-authority").chmod(0o700)
        self.now = 1_000

        tool_files = {}
        for name, payload in (("ffmpeg", b"sealed-ffmpeg"),
                              ("ffprobe", b"sealed-ffprobe")):
            path = tools_root / name
            path.write_bytes(payload)
            path.chmod(0o755)
            tool_files[name] = {
                "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
            }
        compact = self.compact
        tool_descriptor = {
            "format": "studio.render.tools.v1", "platform": "macos-arm64",
            "ffmpeg_sha256": tool_files["ffmpeg"]["sha256"],
            "ffmpeg_size_bytes": tool_files["ffmpeg"]["size_bytes"],
            "ffprobe_sha256": tool_files["ffprobe"]["sha256"],
            "ffprobe_size_bytes": tool_files["ffprobe"]["size_bytes"],
            "architecture_evidence_sha256": "1" * 64,
            "minimum_os_evidence_sha256": "2" * 64,
            "linked_libraries_evidence_sha256": "3" * 64,
            "build_configuration_evidence_sha256": "4" * 64,
            "capability_inventory_evidence_sha256": "5" * 64,
        }
        tool_raw = compact(tool_descriptor)
        (tools_root / "TOOLS.json").write_bytes(tool_raw)
        backend = ROOT / "components/hub/backend/render_legacy_v1"
        file_sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
        compiler_descriptor = {
            "format": "studio.render.compiler.v1",
            "implementation": "hub-final-render-v1",
            "python_runtime_sha256": file_sha(sys.executable),
            "render_hash_sha256": file_sha(backend / "render_hash_v1.py"),
            "render_contract_sha256": file_sha(backend / "render_contract.py"),
            "final_render_compiler_sha256": file_sha(backend / "final_render_compiler.py"),
        }
        compiler_raw = compact(compiler_descriptor)
        (tools_root / "COMPILER.json").write_bytes(compiler_raw)
        profile_descriptor = {
            "format": "studio.render.profile.v1",
            "implementation": "story-classic-no-font-v1",
            "recipe_namespace": "studio.render.final.v1",
            "compiler_artifact_sha256": hashlib.sha256(compiler_raw).hexdigest(),
            "tools_artifact_sha256": hashlib.sha256(tool_raw).hexdigest(),
            "combination_set": "retained-classic-no-font-candidate-v1",
            "tier_policy": "story-final-chain-v1",
            "output_policy": "story-final-output-v1",
            "execution_bounds": {
                "canvas_pairs": [{"width": 128, "height": 96}], "working_fps": [5],
                "max_duration_sec": 2.5, "max_operations": 9, "max_assets": 6,
                "max_asset_bytes": 1_000_000, "max_total_input_bytes": 2_000_000,
                "max_pixel_frames": 1_000_000, "scratch_reserve_bytes": 250_000,
                "output_limit_bytes": 1_000_000,
                "process_tree_rss_limit_bytes": 100_000_000,
                "cumulative_wall_seconds": 30,
            },
            "hardware_qualification_record_sha256": None,
            "fidelity_record_sha256": None, "production_eligible": False,
        }
        if profile_bounds is not None:
            profile_descriptor["execution_bounds"] = copy.deepcopy(profile_bounds)
        profile_raw = compact(profile_descriptor)
        (tools_root / "PROFILE.json").write_bytes(profile_raw)
        packet = copy.deepcopy(json.loads(
            (ROOT / "tests/fixtures/final-render-compiler-v1.json").read_text()
        )["f0_base_audio"]["packet"])
        packet["recipe"]["profile_digest"] = hashlib.sha256(profile_raw).hexdigest()
        packet["recipe_digest"] = render_hash("recipe", packet["recipe"])
        packet["manifest_digest"] = render_hash("manifest", packet["manifest"])
        packet["packet_digest"] = render_hash("packet", {
            "recipe_digest": packet["recipe_digest"],
            "manifest_digest": packet["manifest_digest"],
        })
        manifest = {
            "format": "studiohub.render-tools.payload.v1", "tool_version": "8.1",
            "payload_format": "macos-universal", "files": [
                {"name": name, "path": f"render-tools/{name}", **tool_files[name]}
                for name in ("ffmpeg", "ffprobe")
            ], "production_eligible": False,
        }
        (tools_root / "MANIFEST.json").write_bytes(compact(manifest))
        # What Hub must derive by itself from the installed descriptors plus qualify.json.
        document = {
            "format": "studio.render.qualification-receipt.v1",
            "production_eligible": False,
            "profile_digest": hashlib.sha256(profile_raw).hexdigest(),
            "tools_digest": hashlib.sha256(tool_raw).hexdigest(),
            "compiler_digest": hashlib.sha256(compiler_raw).hexdigest(),
            "hardware_class": "m4-16gb", "selected_tier": "hardware-h264",
            "exact_encoder": "h264_videotoolbox", "font_support": False,
            "bounds": {
                "max_rss_bytes": 100_000_000, "max_wall_seconds": 30,
                "min_available_memory_bytes": 2_000_000_000,
                "output_limit_bytes": 1_000_000,
            },
            "files": tool_files,
        }
        document["qualification_digest"] = hashlib.sha256(compact(document)).hexdigest()
        self.write_record("qualify.json", value=self.intent())
        loader = InstalledRenderAuthorityLoader(
            resources, data_root=self.data_root, clock=lambda: self.now,
            hardware_identity=lambda: {"chip": "Apple M4", "memory_gb": memory_gb},
        )
        return loader, document, packet

    def enabled_record(self, document: dict, **changes) -> dict:
        record = {
            **document, "intent_id": self.INTENT_ID,
            "measured": {
                "peak_rss_bytes": 1, "wall_seconds": 1, "min_free_bytes": 1,
                "output_bytes": 1, "hub_job_id": "job-0", "receipt_digest": "5" * 64,
            },
            **changes,
        }
        return record

    @staticmethod
    def qualification_request(packet: dict, dispatch_key: str = "dispatch-1") -> bytes:
        dispatch = {
            "story_uuid": "story", "intent_id": "intent", "attempt_no": 1,
            "gateway_attempt_key": "gateway-1", "destination_site": "site-a",
            "destination_controller": "controller-a", "destination_node": "node-a",
            "dispatch_key": dispatch_key, "dispatch_fence": "fence-1",
        }
        return json.dumps({
            "format": "studio.render.hub-request.v1", "dispatch": dispatch,
            "packet": packet,
            "assets": {row["logical_id"]: f"{index + 1:032x}"
                       for index, row in enumerate(packet["manifest"])},
        }, sort_keys=True, separators=(",", ":")).encode()

    def test_loader_is_default_off_without_reviewed_receipt(self) -> None:
        resources = self.root / "empty-resources"
        resources.mkdir()
        loader = InstalledRenderAuthorityLoader(resources, hardware_identity=lambda: {
            "chip": "Apple M4", "memory_gb": 16, "machine_type": "Mac mini",
        })

        self.assertIsNone(loader.snapshot())
        self.assertIsNone(loader.resolve({"recipe": {"profile_digest": "1" * 64}}))
        self.assertIsNone(loader.qualification_permit(
            "unknown", "site-a:node-a:fleet", b"{}", {}))
        self.assertFalse(loader.write_receipt({}, self.INTENT_ID, {}))

    def test_v1_authority_selects_retained_compiler_and_preserves_its_plan(self) -> None:
        loader, document, packet = self.candidate_loader()
        authority = loader._authority(document, packet)
        self.assertIsNotNone(authority)
        compiler = loader.compiler_for(authority)
        self.assertEqual(compiler.__module__,
                         "components.hub.backend.render_legacy_v1.final_render_compiler")
        assets = {row["logical_id"]: str(self.root / row["safe_name"])
                  for row in packet["manifest"]}
        output = str(self.root / "legacy-output.mp4")
        legacy = compiler(self.compact(packet), assets, output)
        current = compile_final_render_packet(self.compact(packet), assets, output)
        self.assertEqual([tier.argv for tier in legacy.tiers],
                         [tier.argv for tier in current.tiers])
        self.assertIsNone(loader.snapshot(), "compiler selection grants no production authority")

    def test_v1_late_label_refusal_precedes_asset_or_job_staging(self) -> None:
        loader, document, packet = self.candidate_loader()
        authority = loader._authority(document, packet)
        self.assertIsNotNone(authority)
        packet["recipe"]["operations"].append({"kind": "late_label_stage"})
        runtime, assets = mock.Mock(), mock.Mock()
        service = RenderService(runtime, assets, None, loader)
        with mock.patch.object(loader, "snapshot", return_value=authority):
            with self.assertRaisesRegex(RenderJobUnavailable, "late_label_profile_required"):
                service._require_supported_encode(packet, self.compact(packet).decode(), None)
        assets.assert_not_called()
        self.assertEqual(assets.method_calls, [])
        self.assertEqual(runtime.method_calls, [])

    def test_only_exact_matched_v3_descriptors_allow_late_labels(self) -> None:
        loader, document, packet = self.candidate_loader()
        legacy_authority = loader._authority(document, packet)
        self.assertIsNotNone(legacy_authority)
        self.assertFalse(loader.late_labels_allowed(legacy_authority, packet))
        legacy_packet = copy.deepcopy(packet)
        late_root = loader.root / "late-label"
        late_root.mkdir()
        compiler = json.loads((loader.root / "COMPILER.json").read_bytes())
        compiler["implementation"] = "hub-final-render-late-label-v3"
        backend = ROOT / "components/hub/backend/render_late_label_v3"
        for name, key in (("render_contract.py", "render_contract_sha256"),
                          ("final_render_compiler.py", "final_render_compiler_sha256"),
                          ("render_hash_v1.py", "render_hash_sha256")):
            compiler[key] = hashlib.sha256((backend / name).read_bytes()).hexdigest()
        compiler_raw = self.compact(compiler)
        (late_root / "COMPILER.json").write_bytes(compiler_raw)
        profile = json.loads((loader.root / "PROFILE.json").read_bytes())
        profile.update(format="studio.render.profile.v3",
                       implementation="story-classic-late-label-v3",
                       combination_set="retained-classic-late-label-candidate-v3",
                       compiler_artifact_sha256=hashlib.sha256(compiler_raw).hexdigest())
        profile_raw = self.compact(profile)
        (late_root / "PROFILE.json").write_bytes(profile_raw)
        (late_root / "TOOLS.json").write_bytes((loader.root / "TOOLS.json").read_bytes())
        document.update(profile_digest=hashlib.sha256(profile_raw).hexdigest(),
                        compiler_digest=hashlib.sha256(compiler_raw).hexdigest())
        document["qualification_digest"] = loader._record_digest(document)
        packet["recipe"]["profile_digest"] = document["profile_digest"]
        packet["recipe_digest"] = render_hash("recipe", packet["recipe"])
        packet["packet_digest"] = render_hash("packet", {
            "recipe_digest": packet["recipe_digest"],
            "manifest_digest": packet["manifest_digest"],
        })
        authority = loader._authority(document, packet)
        self.assertIsNotNone(authority)
        self.assertTrue(loader.late_labels_allowed(authority, packet))
        assets = {row["logical_id"]: str(self.root / row["safe_name"])
                  for row in packet["manifest"]}
        output = str(self.root / "no-label-output.mp4")
        legacy_plan = loader.compiler_for(legacy_authority, legacy_packet)(
            self.compact(legacy_packet), assets, output)
        late_plan = loader.compiler_for(authority, packet)(
            self.compact(packet), assets, output, late_labels_allowed=True)
        self.assertEqual([tier.argv for tier in legacy_plan.tiers],
                         [tier.argv for tier in late_plan.tiers])
        wrong = copy.deepcopy(packet)
        wrong["recipe"]["profile_digest"] = "0" * 64
        self.assertFalse(loader.late_labels_allowed(authority, wrong))
        self.assertIsNone(loader.snapshot(), "descriptor support enables no production grant")

    def test_candidate_capability_is_exact_and_separate_from_production_capacity(self) -> None:
        loader, document, packet = self.candidate_loader()
        raw = self.qualification_request(packet)

        self.assertIsNone(loader.snapshot())
        self.assertEqual(loader.resolve(packet).qualification_digest,
                         document["qualification_digest"])
        self.assertFalse(loader.resolve(packet).production_eligible)
        self.assertEqual(loader.resolve(packet).document, document)
        self.assertIsNotNone(loader.qualification_permit(
            self.INTENT_ID, "site-a:node-a:fleet", raw, {}))
        self.assertIsNone(loader.qualification_permit(
            str(uuid4()), "site-a:node-a:fleet", raw, {}))
        broker = FakeBroker([])
        broker.busy_machines = lambda: set()
        consumed = set()
        executor = RenderExecutor(
            runtime=SimpleNamespace(qualification_consumed=consumed.__contains__),
            assets=None, authority=loader, machine="local", broker_api=broker,
            available_memory=lambda: 4_000_000_000,
        )

        capability = executor.capability()

        self.assertEqual(capability["available_capacity"], {"slots": 0, "slots_total": 1})
        self.assertIsNone(capability["hardware_class"])
        self.assertFalse(capability["production_enabled"])
        self.assertEqual(len(capability["qualification_candidates"]), 1)
        candidate = capability["qualification_candidates"][0]
        self.assertEqual(set(candidate), {
            "vector_id", "hardware_class", "profile_digest", "tools_digest",
            "compiler_digest", "qualification_digest", "selected_tier", "exact_encoder",
            "font_support", "bounds", "profile_execution_bounds", "production_eligible",
            "qualification_only", "available_capacity",
        })
        self.assertEqual(candidate["vector_id"], self.INTENT_ID)
        self.assertEqual(candidate["qualification_digest"], document["qualification_digest"])
        self.assertFalse(candidate["production_eligible"])
        self.assertTrue(candidate["qualification_only"])
        self.assertEqual(candidate["available_capacity"], {"slots": 1, "slots_total": 1})
        self.assertEqual(
            candidate["profile_execution_bounds"],
            json.loads((loader.root / "PROFILE.json").read_bytes())["execution_bounds"],
        )
        # Consumed or past accept_before: never advertised; unknown consumption: fail closed.
        consumed.add(self.INTENT_ID)
        self.assertEqual(executor.capability()["qualification_candidates"], [])
        consumed.clear()
        self.now = 2_000
        self.assertEqual(executor.capability()["qualification_candidates"], [])
        self.now = 1_000
        executor.runtime = None
        self.assertEqual(executor.capability()["qualification_candidates"], [])

        compact = self.compact
        compiler = json.loads((loader.root / "COMPILER.json").read_bytes())
        compiler["final_render_compiler_sha256"] = "0" * 64
        compiler_raw = compact(compiler)
        (loader.root / "COMPILER.json").write_bytes(compiler_raw)
        profile = json.loads((loader.root / "PROFILE.json").read_bytes())
        profile["compiler_artifact_sha256"] = hashlib.sha256(compiler_raw).hexdigest()
        (loader.root / "PROFILE.json").write_bytes(compact(profile))
        self.assertEqual(loader.qualification_candidates(), [])

    def test_enabled_record_for_own_hardware_class_is_the_production_authority(self) -> None:
        loader, document, packet = self.candidate_loader()
        broker = FakeBroker([])
        broker.busy_machines = lambda: set()
        executor = RenderExecutor(
            runtime=SimpleNamespace(qualification_consumed=lambda _intent: True),
            assets=None, authority=loader, machine="local", broker_api=broker,
            available_memory=lambda: 4_000_000_000,
        )
        self.assertEqual(executor.capability()["available_capacity"]["slots"], 0)

        self.write_record("enabled", "m4-16gb.json", value=self.enabled_record(document))

        production = loader.snapshot()
        self.assertTrue(production.qualification.production_eligible)
        self.assertEqual(production.qualification.qualification_digest,
                         document["qualification_digest"])
        self.assertTrue(loader.resolve(packet).production_eligible)
        capability = executor.capability()
        self.assertEqual(capability["available_capacity"], {"slots": 1, "slots_total": 1})
        self.assertTrue(capability["production_enabled"])
        self.assertTrue(capability["production_eligible"])
        self.assertEqual(capability["hardware_class"], "m4-16gb")

        # Revocation is moving the record away.
        (self.data_root / "render-authority/enabled/m4-16gb.json").unlink()
        self.assertIsNone(loader.snapshot())
        self.assertEqual(executor.capability()["available_capacity"]["slots"], 0)
        self.assertFalse(executor.capability()["production_enabled"])

    def test_wrong_hardware_class_or_altered_enabled_record_is_ignored(self) -> None:
        loader, document, _packet = self.candidate_loader()
        other = {**document, "hardware_class": "m4-24gb"}
        other["qualification_digest"] = hashlib.sha256(self.compact({
            key: value for key, value in other.items() if key != "qualification_digest"
        })).hexdigest()
        self.write_record("enabled", "m4-24gb.json", value=self.enabled_record(other))
        self.assertIsNone(loader.snapshot())
        # Another class's record renamed to this Mac's class is still refused.
        self.write_record("enabled", "m4-16gb.json", value=self.enabled_record(other))
        self.assertIsNone(loader.snapshot())
        for changes in (
            {"production_eligible": True},
            {"selected_tier": "software-fallback"},
            {"measured": {"peak_rss_bytes": 1}},
            {"extra": 1},
        ):
            with self.subTest(changes=changes):
                self.write_record(
                    "enabled", "m4-16gb.json", value=self.enabled_record(document, **changes))
                self.assertIsNone(loader.snapshot())
        self.write_record("enabled", "m4-16gb.json", value=self.enabled_record(document))
        self.assertIsNotNone(loader.snapshot())
        # A 24 GB Mac reads only its own class's record, never the 16 GB one.
        loader._hardware_identity = lambda: {"chip": "Apple M4", "memory_gb": 24}
        self.assertEqual(loader.snapshot().qualification.hardware_class, "m4-24gb")
        (self.data_root / "render-authority/enabled/m4-24gb.json").unlink()
        self.assertIsNone(loader.snapshot())

    def test_intent_is_consumed_once_and_not_renewed_by_reimport(self) -> None:
        from components.hub.backend.render_jobs import RenderJobRuntime, RenderJobUnavailable

        loader, document, packet = self.candidate_loader()
        runtime = RenderJobRuntime(
            self.root / "jobs", database_path=self.root / "hub.db",
            qualification_resolver=loader.resolve, free_bytes=lambda _path: 10**12,
        )
        broker = FakeBroker([])
        broker.busy_machines = lambda: set()
        executor = RenderExecutor(
            runtime=runtime, assets=None, authority=loader, machine="local",
            broker_api=broker, available_memory=lambda: 4_000_000_000,
        )
        raw = self.qualification_request(packet)
        permit = loader.qualification_permit(self.INTENT_ID, "owner-a", raw, {})
        accepted = runtime.accept("owner-a", "dispatch-1", raw, qualification_permit=permit)
        self.assertEqual(accepted["purpose"], "qualification")
        self.assertEqual(executor.capability()["qualification_candidates"], [])

        # The operator writes the very same intent again: it stays spent.
        self.write_record("qualify.json", value=self.intent(accept_before=5_000))
        second = self.qualification_request(packet, "dispatch-2")
        reimported = loader.qualification_permit(self.INTENT_ID, "owner-a", second, {})
        with self.assertRaises(RenderJobUnavailable):
            runtime.accept("owner-a", "dispatch-2", second, qualification_permit=reimported)
        self.assertEqual(executor.capability()["qualification_candidates"], [])
        # Without a permit a candidate is never production.
        with self.assertRaises(RenderJobUnavailable):
            runtime.accept("owner-a", "dispatch-2", second)

        # The accepted job still claims after the window closes and survives file removal.
        self.now = 6_000
        claim = runtime.claim_launch(
            "owner-a", accepted["id"], machine="local", dispatch_generation=1)
        context = runtime.execution_context(
            "owner-a", accepted["id"], claim["attempt_id"], claim["launch_token"])
        self.assertEqual(context.qualification.document, document)
        (self.data_root / "render-authority/qualify.json").unlink()
        authority = loader.authority_for({"qualification": context.qualification})
        self.assertEqual(authority.qualification, context.qualification)

        # A fresh intent is a new, separately consumable qualification.
        fresh = str(uuid4())
        self.write_record(
            "qualify.json", value=self.intent(intent_id=fresh, accept_before=7_000))
        self.assertEqual(
            [row["vector_id"] for row in executor.capability()["qualification_candidates"]],
            [fresh])

    def runtime_executor(self, loader):
        from components.hub.backend.render_jobs import RenderJobRuntime

        runtime = RenderJobRuntime(
            self.root / "jobs", database_path=self.root / "hub.db",
            qualification_resolver=loader.resolve, free_bytes=lambda _path: 10**12,
        )
        broker = FakeBroker([])
        broker.busy_machines = lambda: set()
        executor = RenderExecutor(
            runtime=runtime, assets=None, authority=loader, machine="local",
            broker_api=broker, available_memory=lambda: 4_000_000_000,
        )
        return runtime, broker, executor

    async def test_revoked_production_job_is_skipped_and_never_wedges_dispatch(self) -> None:
        from components.hub.backend.render_jobs import RenderJobUnavailable

        loader, document, packet = self.candidate_loader()
        self.write_record("enabled", "m4-16gb.json", value=self.enabled_record(document))
        runtime, broker, executor = self.runtime_executor(loader)
        job = runtime.accept("owner-a", "dispatch-1", self.qualification_request(packet))
        self.assertEqual(job["purpose"], "production")

        # Revoked, while an expired qualify.json with the same digest stays on the Mac.
        (self.data_root / "render-authority/enabled/m4-16gb.json").unlink()
        self.now = 9_999
        self.assertFalse(executor.pending_for_machine("local"))
        self.assertIsNone(await executor.dispatchable_candidate("local"))
        for _ in range(2):
            self.assertFalse(await executor.dispatch_ready())
        self.assertEqual(runtime.get("owner-a", job["id"])["state"], "accepted")

        # Any per-row claim refusal is "not ready": the hold is released, nothing raises.
        self.write_record("enabled", "m4-16gb.json", value=self.enabled_record(document))
        self.assertIsNotNone(await executor.dispatchable_candidate("local"))
        with mock.patch.object(runtime, "claim_launch",
                               side_effect=RenderJobUnavailable("not ready")):
            self.assertFalse(await executor.dispatch_ready())
        self.assertEqual(broker.events, ["physical", "release"])
        self.assertIsNone(broker.owner)
        self.assertEqual(runtime.get("owner-a", job["id"])["state"], "accepted")

    async def test_queued_qualification_job_claims_after_same_digest_record_swap(self) -> None:
        loader, document, packet = self.candidate_loader()
        original = self.enabled_record(document)
        self.write_record("enabled", "m4-16gb.json", value=original)
        runtime, _broker, executor = self.runtime_executor(loader)
        raw = self.qualification_request(packet)
        permit = loader.qualification_permit(self.INTENT_ID, "owner-a", raw, {})
        job = runtime.accept("owner-a", "dispatch-1", raw, qualification_permit=permit)
        self.assertEqual(job["purpose"], "qualification")

        replacement = self.enabled_record(
            document, intent_id=str(uuid4()),
            measured={**original["measured"], "hub_job_id": "job-9", "peak_rss_bytes": 7})
        self.write_record("enabled", "m4-16gb.json", value=replacement)
        self.assertEqual(loader.snapshot().qualification.document, replacement)
        self.assertIsNotNone(await executor.dispatchable_candidate("local"))

        claim = runtime.claim_launch(
            "owner-a", job["id"], machine="local", dispatch_generation=1)
        context = runtime.execution_context(
            "owner-a", job["id"], claim["attempt_id"], claim["launch_token"])
        self.assertEqual(context.qualification.document, original)
        self.assertIsNotNone(executor._authority_for({"qualification": context.qualification}))

    async def test_qualification_run_writes_its_measured_receipt_exactly_once(self) -> None:
        loader, document, packet = self.candidate_loader()
        raw = self.qualification_request(packet)
        permit = loader.qualification_permit(self.INTENT_ID, "site-a:node-a:fleet", raw, {})
        candidate = loader.resolve(packet)
        events: list[str] = []
        runtime = FakeRuntime(self.root, raw, candidate, events)
        runtime.job.update(
            owner_scope="site-a:node-a:fleet", purpose="qualification",
            qualification_digest=candidate.qualification_digest)
        base_context = runtime.execution_context
        runtime.execution_context = lambda *args: replace(
            base_context(*args), qualification_permit=permit, purpose="qualification")
        asset_files = {}
        for index, row in enumerate(packet["manifest"]):
            path = self.root / f"asset-{index}"
            path.write_bytes(self.mp4_bytes() if row["logical_id"] == "base"
                             else self.wav_bytes())
            asset_files[f"{index + 1:032x}"] = path

        def compile_plan(_packet_raw, local_assets, output_path):
            return FinalRenderPlan(
                tiers=(EncodeTier("hardware-h264", (
                    "-i", local_assets["base"], "-i", local_assets["audio"],
                    "-c:v", "h264_videotoolbox", output_path)),),
                requested_burn_ins=(), input_uses=(),
            )

        spawned = asyncio.Event()

        async def spawn(*_argv, **_kwargs):
            spawned.set()
            return FakeProcess(Path(runtime.claim["output_path"]))

        def validate(context, authority, command_digest):
            output = Path(context.output_path).read_bytes()
            return ValidatedOutputReceipt(
                owner_scope=context.owner_scope, hub_job_id=context.hub_job_id,
                attempt_id=context.attempt_id,
                accepted_request_sha256=context.accepted_request_sha256,
                receipt_digest="5" * 64, output_sha256=hashlib.sha256(output).hexdigest(),
                output_size_bytes=len(output),
                profile_digest=authority.qualification.profile_digest,
                tools_digest=authority.qualification.tools_digest,
                compiler_digest=authority.qualification.compiler_digest,
                qualification_digest=authority.qualification.qualification_digest,
                generated_command_digest=command_digest,
                selected_tier=authority.selected_tier, feature_losses=(),
            )

        executor = RenderExecutor(
            runtime, FakeAssets(asset_files), authority=loader, machine="local",
            broker_api=FakeBroker(events), compile_plan=compile_plan, spawn=spawn,
            output_validator=validate,
            process_identity=lambda process, executable: ProcessIdentity(
                pid=process.pid, process_group_id=process.pid,
                start_token=f"boot:1:{executable}"),
            group_exists=lambda _pgid: False, available_memory=lambda: 4_000_000_000,
            free_bytes=lambda _path: 9_000_000,
        )
        with mock.patch.object(RenderExecutor, "_process_tree_rss",
                               staticmethod(lambda _pid: 4_096)):
            self.assertTrue(await executor.dispatch_ready())
            await spawned.wait()
            await executor.stop()

        self.assertIn("publish", events)
        path = self.data_root / "render-authority/receipts" / (
            InstalledRenderAuthorityLoader._receipt_name(
                document["hardware_class"], self.INTENT_ID,
                document["qualification_digest"]))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        receipt = json.loads(path.read_bytes())
        self.assertEqual(receipt, {
            **document, "intent_id": self.INTENT_ID,
            "measured": {
                "peak_rss_bytes": 4_096, "wall_seconds": receipt["measured"]["wall_seconds"],
                "min_free_bytes": 9_000_000, "output_bytes": len(b"validated-output"),
                "hub_job_id": "job-1", "receipt_digest": "5" * 64,
            },
        })
        self.assertIs(type(receipt["measured"]["wall_seconds"]), int)
        self.assertFalse(receipt["production_eligible"])
        before = path.read_bytes()
        context = runtime.execution_context(
            "site-a:node-a:fleet", "job-1", "attempt-1", "launch-1")
        executor._record_qualification(
            context, runtime.published, {"peak_rss_bytes": 1, "min_free_bytes": 1}, 0.0)
        self.assertEqual(path.read_bytes(), before)

        # The coordinator's copy into enabled/ grants production with the same digest.
        self.write_record("enabled", "m4-16gb.json", value=before)
        self.assertEqual(loader.snapshot().qualification.qualification_digest,
                         candidate.qualification_digest)

    async def run_opt_in_render(self, *, receipt_already_exists: bool = False):
        """One real executor run on an owner-opted-in authority (no qualification intent)."""
        loader, _document, packet = self.candidate_loader()
        loader.set_opt_in(True, "owner@gateway")
        raw = self.qualification_request(packet)
        qualification = loader.resolve(packet)
        job_id = str(uuid4())
        events: list[str] = []
        runtime = FakeRuntime(self.root, raw, qualification, events)
        runtime.job.update(
            id=job_id, owner_scope="site-a:node-a:fleet", purpose="production",
            qualification_digest=qualification.qualification_digest)
        asset_files = {}
        for index, row in enumerate(packet["manifest"]):
            path = self.root / f"asset-{index}"
            path.write_bytes(self.mp4_bytes() if row["logical_id"] == "base"
                             else self.wav_bytes())
            asset_files[f"{index + 1:032x}"] = path
        tier_label, encoder = render_encode.primary_pair(render_encode.DEFAULT_OPT_IN_ENCODE)
        expected = loader.receipt_document_for(qualification.document)
        receipt_path = self.data_root / "render-authority/receipts" / (
            InstalledRenderAuthorityLoader._receipt_name(
                expected["hardware_class"], job_id, expected["qualification_digest"]))
        if receipt_already_exists:
            receipt_path.parent.mkdir(mode=0o700)
            receipt_path.write_bytes(b"{}")

        def compile_plan(_packet_raw, local_assets, output_path):
            return FinalRenderPlan(
                tiers=(EncodeTier(tier_label, (
                    "-i", local_assets["base"], "-i", local_assets["audio"],
                    "-c:v", encoder, output_path)),),
                requested_burn_ins=(), input_uses=(),
            )

        spawned = asyncio.Event()

        async def spawn(*_argv, **_kwargs):
            spawned.set()
            return FakeProcess(Path(runtime.claim["output_path"]))

        def validate(context, authority, command_digest):
            output = Path(context.output_path).read_bytes()
            return ValidatedOutputReceipt(
                owner_scope=context.owner_scope, hub_job_id=context.hub_job_id,
                attempt_id=context.attempt_id,
                accepted_request_sha256=context.accepted_request_sha256,
                receipt_digest="5" * 64, output_sha256=hashlib.sha256(output).hexdigest(),
                output_size_bytes=len(output),
                profile_digest=authority.qualification.profile_digest,
                tools_digest=authority.qualification.tools_digest,
                compiler_digest=authority.qualification.compiler_digest,
                qualification_digest=authority.qualification.qualification_digest,
                generated_command_digest=command_digest,
                selected_tier=authority.selected_tier, feature_losses=(),
            )

        executor = RenderExecutor(
            runtime, FakeAssets(asset_files), authority=loader, machine="local",
            broker_api=FakeBroker(events), compile_plan=compile_plan, spawn=spawn,
            output_validator=validate,
            process_identity=lambda process, executable: ProcessIdentity(
                pid=process.pid, process_group_id=process.pid,
                start_token=f"boot:1:{executable}"),
            group_exists=lambda _pgid: False, available_memory=lambda: 4_000_000_000,
            free_bytes=lambda _path: 9_000_000,
        )
        with mock.patch.object(RenderExecutor, "_process_tree_rss",
                               staticmethod(lambda _pid: 4_096)):
            self.assertTrue(await executor.dispatch_ready())
            await spawned.wait()
            await executor.stop()
        return events, qualification, expected, job_id, receipt_path

    async def test_opt_in_run_writes_its_own_real_measured_receipt(self) -> None:
        events, qualification, expected, job_id, path = await self.run_opt_in_render()

        self.assertTrue(qualification.production_eligible)
        self.assertEqual(qualification.document["opt_in"]["accepted_by"], "owner@gateway")
        self.assertNotIn("measured", qualification.document,
                         "the authority itself never claims a measurement")
        self.assertIn("publish", events)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        receipt = json.loads(path.read_bytes())
        wall = receipt["measured"]["wall_seconds"]
        self.assertIs(type(wall), int)
        self.assertGreaterEqual(wall, 1)
        self.assertEqual(receipt, {
            **expected, "intent_id": job_id,
            "measured": {
                "peak_rss_bytes": 4_096, "wall_seconds": wall,
                "min_free_bytes": 9_000_000, "output_bytes": len(b"validated-output"),
                "hub_job_id": job_id, "receipt_digest": "5" * 64,
            },
        })
        self.assertFalse(receipt["production_eligible"])

    async def test_opt_in_run_without_a_durable_receipt_is_not_published(self) -> None:
        events, _qualification, _expected, _job_id, path = await self.run_opt_in_render(
            receipt_already_exists=True)

        self.assertNotIn("publish", events)
        self.assertIn("output_validation_failed", events)
        self.assertEqual(path.read_bytes(), b"{}", "an existing receipt is never replaced")

    def test_strict_reader_refuses_links_loose_modes_and_oversize(self) -> None:
        from components.hub.backend import render_authority_files as files

        loader, _document, _packet = self.candidate_loader()
        uid = os.getuid()
        read = lambda *names: files.read_record(self.data_root, *names, expected_uid=uid)
        authority = self.data_root / "render-authority"
        good = read("qualify.json")
        self.assertEqual(json.loads(good), self.intent())
        self.assertIsNotNone(loader._intent())

        def refused(code: str) -> None:
            with self.assertRaises(files.RenderAuthorityRecordError) as raised:
                read("qualify.json")
            self.assertEqual(raised.exception.code, code)
            self.assertIsNone(loader._intent())

        (authority / "qualify.json").chmod(0o644)
        refused("UNSAFE_FILE")
        (authority / "qualify.json").chmod(0o600)
        authority.chmod(0o755)
        refused("UNSAFE_PATH")
        authority.chmod(0o700)
        os.link(authority / "qualify.json", self.root / "second-link")
        refused("UNSAFE_FILE")
        (self.root / "second-link").unlink()
        target = self.root / "elsewhere.json"
        target.write_bytes(good)
        target.chmod(0o600)
        (authority / "qualify.json").unlink()
        (authority / "qualify.json").symlink_to(target)
        refused("UNSAFE_FILE")
        (authority / "qualify.json").unlink()
        self.write_record("qualify.json", value=b" " * (files.MAX_RECORD_BYTES + 1))
        refused("UNSAFE_FILE")
        (authority / "qualify.json").unlink()
        refused("MISSING")
        self.root.chmod(0o775)
        try:
            refused("UNSAFE_PATH")
        finally:
            self.root.chmod(0o700)
        real = self.root / "real-authority"
        authority.rename(real)
        authority.symlink_to(real)
        self.write_record("qualify.json", value=self.intent())
        refused("UNSAFE_PATH")

    def test_data_root_through_a_symlinked_ancestor_is_refused(self) -> None:
        loader, document, _packet = self.candidate_loader()
        self.write_record("enabled", "m4-16gb.json", value=self.enabled_record(document))
        self.assertIsNotNone(loader.snapshot())
        link = self.root / "linked-ancestor"
        link.symlink_to(self.root, target_is_directory=True)

        linked = InstalledRenderAuthorityLoader(
            loader.root.parent, data_root=link / "data", clock=lambda: self.now,
            hardware_identity=lambda: {"chip": "Apple M4", "memory_gb": 16},
        )

        self.assertEqual(linked.data_root, link / "data")
        self.assertIsNone(linked._intent())
        self.assertIsNone(linked.snapshot())
        self.assertEqual(linked.qualification_candidates(), [])

    def test_failed_receipt_write_leaves_nothing_and_existing_receipt_wins(self) -> None:
        from components.hub.backend import render_authority_files as files

        loader, document, _packet = self.candidate_loader()
        receipts = self.data_root / "render-authority/receipts"
        measured = {"peak_rss_bytes": 1, "wall_seconds": 1, "min_free_bytes": 1,
                    "output_bytes": 1, "hub_job_id": "job-1", "receipt_digest": "5" * 64}

        with mock.patch.object(files.os, "write", side_effect=OSError(28, "No space left")):
            self.assertFalse(loader.write_receipt(document, self.INTENT_ID, measured))
        self.assertEqual(list(receipts.iterdir()), [])

        self.assertTrue(loader.write_receipt(document, self.INTENT_ID, measured))
        final = receipts / InstalledRenderAuthorityLoader._receipt_name(
            document["hardware_class"], self.INTENT_ID,
            document["qualification_digest"])
        first = final.read_bytes()
        self.assertEqual(json.loads(first)["measured"], measured)
        self.assertFalse(loader.write_receipt(
            document, self.INTENT_ID, {**measured, "hub_job_id": "job-2"}))
        self.assertEqual(final.read_bytes(), first)
        self.assertEqual([path.name for path in receipts.iterdir()], [final.name])
        self.assertEqual(final.stat().st_nlink, 1)
        self.assertEqual(final.stat().st_mode & 0o777, 0o600)

    async def test_missing_broker_admission_authority_fails_closed(self) -> None:
        runtime = FakeRuntime(
            self.root, b'{"packet":{},"assets":{}}',
            self.authority().qualification, [],
        )
        executor = RenderExecutor(
            runtime, FakeAssets({}), authority=lambda: self.authority(),
            machine="local", broker_api=SimpleNamespace(busy_machines=lambda: set()),
        )

        self.assertIsNone(await executor.dispatchable_candidate("local"))
        self.assertFalse(executor.pending_for_machine("local"))
        self.assertEqual(executor.capability()["available_capacity"]["slots"], 0)

    def test_v2_loader_publishes_and_admits_the_packaged_envelope(self) -> None:
        bounds = json.loads((ROOT / "packaging/render-authority/PROFILE.json").read_bytes())["execution_bounds"]
        loader, _, packet = self.candidate_loader(profile_bounds=bounds)
        broker = FakeBroker([])
        broker.busy_machines = lambda: set()
        executor = RenderExecutor(
            runtime=SimpleNamespace(qualification_consumed=lambda _intent: False),
            assets=None, authority=loader, machine="local", broker_api=broker,
            available_memory=lambda: 16_000_000_000,
        )
        capability = executor.capability()
        self.assertFalse(capability["production_enabled"])
        candidate = capability["qualification_candidates"][0]
        self.assertFalse(candidate["production_eligible"])
        self.assertEqual(candidate["profile_execution_bounds"], bounds)
        for fps in (24, 25, 30, 60, 5, 50):
            with self.subTest(fps=fps):
                packet["recipe"]["canvas"] = {
                    "width": 1080, "height": 1920, "working_fps": fps,
                }
                packet["recipe_digest"] = render_hash("recipe", packet["recipe"])
                packet["packet_digest"] = render_hash("packet", {
                    "recipe_digest": packet["recipe_digest"],
                    "manifest_digest": packet["manifest_digest"],
                })
                qualification = loader.resolve(packet)
                if fps in (5, 50):
                    self.assertIsNone(qualification)
                else:
                    self.assertIsNotNone(qualification)
                    self.assertFalse(qualification.production_eligible)
        authority = loader.qualification_candidates()[0][1]
        self.assertEqual(authority.bounds.max_rss_bytes, bounds["process_tree_rss_limit_bytes"])
        self.assertEqual(authority.bounds.max_wall_seconds, bounds["cumulative_wall_seconds"])
        self.assertEqual(authority.bounds.output_limit_bytes, bounds["output_limit_bytes"])
        self.assertEqual(authority.qualification.scratch_reserve_bytes, bounds["scratch_reserve_bytes"])
        self.assertEqual(authority.qualification.min_free_bytes,
                         bounds["scratch_reserve_bytes"] + bounds["output_limit_bytes"])

    def test_v2_profile_admission_accepts_requested_rates_and_canvases(self) -> None:
        bounds = json.loads((ROOT / "packaging/render-authority/PROFILE.json").read_bytes())["execution_bounds"]
        packet = {"recipe": {"operations": [], "target_duration_sec": 10800}, "manifest": []}
        for fps in (24, 25, 30, 60):
            for width, height in ((1280, 720), (1920, 1080), (2560, 1440),
                                  (3840, 2160), (1080, 1920), (2160, 3840),
                                  (2048, 2048), (1672, 942), (2, 2)):
                with self.subTest(fps=fps, width=width, height=height):
                    packet["recipe"]["canvas"] = {"width": width, "height": height,
                                                    "working_fps": fps}
                    self.assertTrue(InstalledRenderAuthorityLoader._profile_allows(packet, bounds))

    def test_v2_profile_admission_refuses_invalid_rates_canvases_and_versions(self) -> None:
        bounds = json.loads((ROOT / "packaging/render-authority/PROFILE.json").read_bytes())["execution_bounds"]
        packet = {"recipe": {"operations": [], "target_duration_sec": 1}, "manifest": []}
        for fps, width, height in ((5, 1920, 1080), (50, 1920, 1080),
                                   (24, 1919, 1080), (24, 1920, 1079),
                                   (24, 3842, 2160), (24, 2160, 3842),
                                   (24, 3840, 3840), (24, 2880, 2882),
                                   (24, 3842, 2), (24, 2, 3842),
                                   (24, 0, 1080), (24, 1920, 0),
                                   (24, -2, 1080), (24, 1920, -2),
                                   (24, True, 1080), (24, 1920, True),
                                   (24, 1920.0, 1080), (24, 1920, 1080.0)):
            with self.subTest(fps=fps, width=width, height=height):
                packet["recipe"]["canvas"] = {"width": width, "height": height,
                                                "working_fps": fps}
                self.assertFalse(InstalledRenderAuthorityLoader._profile_allows(packet, bounds))
        packet["recipe"]["canvas"] = {"width": 1920, "height": 1080, "working_fps": 24}
        for version in (1, 3, True, 2.0):
            with self.subTest(bounds_version=version):
                self.assertFalse(InstalledRenderAuthorityLoader._profile_allows(
                    packet, {**bounds, "bounds_version": version}))

    def test_v2_profile_ceil_budget_and_retained_packet_fences(self) -> None:
        bounds = json.loads((ROOT / "packaging/render-authority/PROFILE.json").read_bytes())["execution_bounds"]
        packet = {"recipe": {"operations": [], "target_duration_sec": 1.001,
                             "canvas": {"width": 1920, "height": 1080, "working_fps": 24}},
                  "manifest": []}
        pixels = 1920 * 1080
        self.assertTrue(InstalledRenderAuthorityLoader._profile_allows(
            packet, {**bounds, "max_pixel_frames": 25 * pixels}))
        self.assertFalse(InstalledRenderAuthorityLoader._profile_allows(
            packet, {**bounds, "max_pixel_frames": 25 * pixels - 1}))
        full_length = copy.deepcopy(packet)
        full_length["recipe"].update(
            canvas={"width": 3840, "height": 2160, "working_fps": 60},
            target_duration_sec=10800,
        )
        self.assertTrue(InstalledRenderAuthorityLoader._profile_allows(full_length, bounds))
        full_length["recipe"]["target_duration_sec"] += 1 / 120
        # Relax only duration so this refusal proves the packaged pixel-frame budget.
        self.assertFalse(InstalledRenderAuthorityLoader._profile_allows(
            full_length, {**bounds, "max_duration_sec": 10801}))
        cases = (
            ("max_duration_sec", lambda p: p["recipe"].update(target_duration_sec=10801)),
            ("max_operations", lambda p: p["recipe"].update(operations=[{}] * 9)),
            ("max_assets", lambda p: p.update(manifest=[{"size_bytes": 1}] * 5)),
            ("max_asset_bytes", lambda p: p.update(manifest=[{"size_bytes": bounds["max_asset_bytes"] + 1}])),
            ("max_total_input_bytes", lambda p: p.update(manifest=[{"size_bytes": bounds["max_asset_bytes"]}] * 3)),
        )
        for limit, mutate in cases:
            with self.subTest(limit=limit):
                value = json.loads(json.dumps(packet))
                mutate(value)
                self.assertFalse(InstalledRenderAuthorityLoader._profile_allows(value, bounds))

    async def test_render_process_retains_wall_rss_output_and_free_disk_fences(self) -> None:
        authority = self.authority()
        output = self.root / "bounded.mp4"
        payload = b"validated-output"
        output_limit = len(payload)
        authority = replace(
            authority,
            bounds=replace(authority.bounds, output_limit_bytes=output_limit),
            qualification=replace(
                authority.qualification, output_limit_bytes=output_limit,
                min_free_bytes=authority.qualification.scratch_reserve_bytes + output_limit),
        )
        context = SimpleNamespace(output_path=str(output), workspace=str(self.root))
        identity = ProcessIdentity(pid=731, process_group_id=731, start_token="fake:1")
        for fence in (None, "wall", "rss", "output", "scratch"):
            with self.subTest(fence=fence):
                executor = RenderExecutor(
                    runtime=None, assets=None, authority=lambda: authority,
                    machine="local", broker_api=None, group_exists=lambda _pgid: False,
                    monotonic=lambda: 30 if fence == "wall" else 29,
                    free_bytes=lambda _path: authority.qualification.min_free_bytes
                        - (1 if fence == "scratch" else 0),
                )
                executor._process_tree_rss = lambda _pid: authority.bounds.max_rss_bytes + (
                    1 if fence == "rss" else 0)
                executor._stop_exact_group = mock.AsyncMock(return_value=0)
                bounded = replace(authority, bounds=replace(
                    authority.bounds,
                    output_limit_bytes=output_limit - (1 if fence == "output" else 0)))
                self.assertEqual(await executor._wait_with_bounds(
                    FakeProcess(output), identity, context, bounded, attempt_deadline=30),
                    (0, fence is not None))
                self.assertEqual(executor._stop_exact_group.await_count,
                                 0 if fence is None else 1)

    async def test_physical_claim_precedes_one_exact_hardware_spawn_and_validated_publish(self) -> None:
        packet = json.loads((ROOT / "tests/fixtures/final-render-compiler-v1.json").read_text())["f0_base_audio"]["packet"]
        raw = json.dumps({
            "format": "studio.render.hub-request.v1",
            "dispatch": {
                "story_uuid": "story", "intent_id": "intent", "attempt_no": 1,
                "gateway_attempt_key": "gateway-1", "destination_site": "site-a",
                "destination_controller": "controller-a", "destination_node": "node-a",
                "dispatch_key": "dispatch-1", "dispatch_fence": "fence-1",
            },
            "packet": packet,
            "assets": {"audio": "b" * 32, "base": "a" * 32},
        }, sort_keys=True, separators=(",", ":")).encode()
        events: list[str] = []
        runtime = FakeRuntime(self.root, raw, self.authority().qualification, events)
        asset_files = {}
        for handle, payload in (("a" * 32, self.mp4_bytes()),
                                ("b" * 32, self.wav_bytes())):
            path = self.root / handle
            path.write_bytes(payload)
            asset_files[handle] = path
        broker = FakeBroker(events)
        seen = {}
        spawned = asyncio.Event()

        def compile_plan(_packet_raw, local_assets, output_path):
            events.append("compile")
            self.assertTrue(all(path.startswith("/dev/fd/") for path in local_assets.values()))
            return FinalRenderPlan(
                tiers=(
                    EncodeTier("hardware-h264", (
                        "-i", local_assets["base"], "-i", local_assets["audio"],
                        "-c:v", "h264_videotoolbox", output_path)),
                    EncodeTier("software-fallback", ("-c:v", "libx264", output_path)),
                ),
                requested_burn_ins=(), input_uses=(),
            )

        async def spawn(*argv, **kwargs):
            events.append("spawn")
            seen.update(argv=argv, kwargs=kwargs)
            spawned.set()
            return FakeProcess(Path(runtime.claim["output_path"]))

        def validate(context, authority, command_digest):
            events.append("validate")
            output = Path(context.output_path).read_bytes()
            return ValidatedOutputReceipt(
                owner_scope=context.owner_scope, hub_job_id=context.hub_job_id,
                attempt_id=context.attempt_id,
                accepted_request_sha256=context.accepted_request_sha256,
                receipt_digest="5" * 64, output_sha256=hashlib.sha256(output).hexdigest(),
                output_size_bytes=len(output), profile_digest=authority.qualification.profile_digest,
                tools_digest=authority.qualification.tools_digest,
                compiler_digest=authority.qualification.compiler_digest,
                qualification_digest=authority.qualification.qualification_digest,
                generated_command_digest=command_digest, selected_tier=authority.selected_tier,
                feature_losses=(),
            )

        executor = RenderExecutor(
            runtime, FakeAssets(asset_files), authority=lambda: self.authority(),
            machine="local", broker_api=broker, compile_plan=compile_plan,
            spawn=spawn, output_validator=validate,
            process_identity=lambda process, executable: ProcessIdentity(
                pid=process.pid, process_group_id=process.pid,
                start_token=f"boot:1:{executable}"),
            group_exists=lambda _pgid: False,
        )

        self.assertTrue(await executor.dispatch_ready())
        await spawned.wait()
        await executor.stop()

        self.assertEqual(events[:5], ["physical", "claim", "context", "compile", "spawn"])
        self.assertEqual(seen["argv"][0], str(self.authority().ffmpeg))
        self.assertIn("h264_videotoolbox", seen["argv"])
        self.assertNotIn("libx264", seen["argv"])
        inputs = [index for index, value in enumerate(seen["argv"]) if value == "-i"]
        self.assertEqual(len(inputs), 2)
        self.assertEqual(
            seen["argv"][inputs[0] - 8:inputs[0]],
            ("-f", "mov", "-enable_drefs", "0", "-use_absolute_path", "0",
             "-protocol_whitelist", "file,pipe"),
        )
        self.assertEqual(
            seen["argv"][inputs[1] - 4:inputs[1]],
            ("-f", "wav", "-protocol_whitelist", "file,pipe"),
        )
        self.assertTrue(seen["kwargs"]["start_new_session"])
        self.assertEqual(
            events[-5:], ["stopped", "validate", "ack-lease", "publish", "release"])
        self.assertIs(type(runtime.published), ValidatedOutputReceipt)
        self.assertIsNone(broker.owner)

    async def test_playlist_and_concat_inputs_are_rejected_before_spawn(self) -> None:
        packet = json.loads(
            (ROOT / "tests/fixtures/final-render-compiler-v1.json").read_text()
        )["f0_base_audio"]["packet"]
        raw = json.dumps({
            "format": "studio.render.hub-request.v1", "dispatch": {}, "packet": packet,
            "assets": {"audio": "b" * 32, "base": "a" * 32},
        }, sort_keys=True, separators=(",", ":")).encode()
        for unsafe in (
            b"#EXTM3U\n#EXT-X-KEY:URI=file:///private/key\nsegment.ts\n",
            b"ffconcat version 1.0\nfile '/private/other.mov'\n",
        ):
            with self.subTest(prefix=unsafe[:8]):
                events = []
                runtime = FakeRuntime(
                    self.root / uuid4().hex, raw, self.authority().qualification, events)
                files = {}
                for handle, payload in (("a" * 32, unsafe),
                                        ("b" * 32, self.wav_bytes())):
                    path = runtime.root / handle
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(payload)
                    files[handle] = path
                spawned = []

                async def spawn(*_args, **_kwargs):
                    spawned.append(True)
                    raise AssertionError("unsupported input reached process spawn")

                executor = RenderExecutor(
                    runtime, FakeAssets(files), authority=lambda: self.authority(),
                    machine="local", broker_api=FakeBroker(events), spawn=spawn,
                    compile_plan=lambda *_args: FinalRenderPlan(
                        (EncodeTier("hardware-h264", (
                            "-i", "/dev/fd/1", "-i", "/dev/fd/2",
                            "-c:v", "h264_videotoolbox", runtime.claim["output_path"])),),
                        (), ()),
                    available_memory=lambda: 1_000_000_000,
                )

                self.assertTrue(await executor.dispatch_ready())
                await executor.stop()
                self.assertEqual(spawned, [])
                self.assertEqual(runtime.job["state"], "failed")

    async def test_validation_failure_and_unknown_ledger_evidence_never_release_physical_hold(self) -> None:
        packet = json.loads(
            (ROOT / "tests/fixtures/final-render-compiler-v1.json").read_text()
        )["f0_base_audio"]["packet"]
        raw = json.dumps({
            "packet": packet, "assets": {"audio": "b" * 32, "base": "a" * 32},
        }, sort_keys=True, separators=(",", ":")).encode()

        for evidence in (None, {}, RuntimeError("ledger unavailable")):
            with self.subTest(evidence=type(evidence).__name__):
                events: list[str] = []
                runtime = FakeRuntime(self.root / uuid4().hex, raw, self.authority().qualification, events)
                broker = FakeBroker(events)
                files = {}
                for handle, payload in (("a" * 32, self.mp4_bytes()),
                                        ("b" * 32, self.wav_bytes())):
                    path = runtime.root / handle
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(payload)
                    files[handle] = path

                def get(_owner_scope, _job_id):
                    if isinstance(evidence, BaseException):
                        raise evidence
                    return evidence

                async def spawn(*_args, **_kwargs):
                    return FakeProcess(Path(runtime.claim["output_path"]))

                def reject_output(*_args):
                    events.append("validate")
                    runtime.get = get
                    raise RuntimeError("probe rejected output")

                executor = RenderExecutor(
                    runtime, FakeAssets(files), authority=lambda: self.authority(),
                    machine="local", broker_api=broker,
                    compile_plan=lambda _raw, local, output: FinalRenderPlan(
                        tiers=(EncodeTier("hardware-h264", (
                            "-i", local["base"], "-i", local["audio"],
                            "-c:v", "h264_videotoolbox", output)),),
                        requested_burn_ins=(), input_uses=()),
                    spawn=spawn,
                    output_validator=reject_output,
                    process_identity=lambda process, executable: ProcessIdentity(
                        pid=process.pid, process_group_id=process.pid,
                        start_token=f"boot:1:{executable}"),
                    group_exists=lambda _pgid: False,
                )

                self.assertTrue(await executor.dispatch_ready())
                outcomes = await asyncio.gather(*executor._tasks, return_exceptions=True)
                await executor.stop()

                self.assertEqual(events.count("validate"), 1)
                self.assertIn("stopped", events)
                self.assertEqual(len(outcomes), 1)
                self.assertIsInstance(outcomes[0], RuntimeError)
                self.assertEqual(str(outcomes[0]), "probe rejected output")
                self.assertNotIn("publish", events)
                self.assertIsNotNone(broker.owner)
                self.assertNotIn("release", events)

    async def test_reaped_captured_child_with_empty_group_skips_identity_and_signals(self) -> None:
        class ReapedProcess:
            pid = 734
            returncode = -signal.SIGTERM

            def __init__(self) -> None:
                self.waits = 0

            async def wait(self) -> int:
                self.waits += 1
                return self.returncode

        process = ReapedProcess()
        identity = ProcessIdentity(
            pid=process.pid, process_group_id=process.pid,
            start_token="boot:1:/fixed/ffmpeg",
        )
        identity_checks = []
        group_checks = []
        signals = []

        def missing_identity(*_args):
            identity_checks.append(process.pid)
            raise psutil.NoSuchProcess(process.pid)

        executor = RenderExecutor(
            runtime=None, assets=None, authority=lambda: self.authority(),
            machine="local", broker_api=None,
            process_identity=missing_identity,
            group_exists=lambda pgid: group_checks.append(pgid) or False,
            kill_group=lambda pgid, sent_signal: signals.append((pgid, sent_signal)),
        )

        result = await executor._stop_exact_group(
            process, identity, self.authority().ffmpeg)

        self.assertEqual(
            (result, process.waits, identity_checks, group_checks, signals),
            (-signal.SIGTERM, 1, [], [process.pid], []),
        )

    async def test_cancel_refuses_pid_reuse_and_surviving_descendants(self) -> None:
        events: list[str] = []
        runtime = FakeRuntime(self.root, b'{"packet":{},"assets":{}}',
                              self.authority().qualification, events)
        runtime.compute_held = True
        runtime.job.update(state="rendering", attempt_id="attempt-1", machine="local")
        broker = FakeBroker(events)
        broker.owner = ("local", "render:job-1")
        context = RenderExecutionContext(
            owner_scope=runtime.job["owner_scope"], hub_job_id="job-1",
            attempt_id="attempt-1", launch_token="launch-1",
            accepted_request_sha256="a" * 64, raw_request=runtime.raw,
            packet={}, assets={}, qualification=runtime.qualification,
            qualification_permit=None, purpose="production", workspace=str(self.root),
            output_path=str(self.root / "final.mp4"), machine="local",
            dispatch_generation=1,
        )
        process = FakeProcess(self.root / "final.mp4")
        owned = ProcessIdentity(
            pid=process.pid, process_group_id=process.pid,
            start_token=f"boot:1:{self.authority().ffmpeg}")
        runtime.process = owned

        for observed, descendants in (
            (ProcessIdentity(pid=process.pid, process_group_id=process.pid,
                             start_token="boot:2:/changed"), False),
            (owned, True),
        ):
            with self.subTest(observed=observed.start_token):
                killed = []
                executor = RenderExecutor(
                    runtime, FakeAssets({}), authority=lambda: self.authority(),
                    machine="local", broker_api=broker,
                    process_identity=lambda *_args: observed,
                    kill_group=lambda pgid, sig: killed.append((pgid, sig)),
                    group_exists=lambda _pgid: descendants,
                    cancel_grace_seconds=0,
                )
                executor._active["job-1"] = (
                    process, owned, context, self.authority().ffmpeg)

                with mock.patch.object(runtime, "record_process_stopped") as stopped:
                    await executor.request_cancel(runtime.job["owner_scope"], "job-1")

                if observed != owned:
                    self.assertEqual(killed, [])
                self.assertFalse(stopped.called)
                self.assertEqual(broker.owner, ("local", "render:job-1"))

    def test_boot_change_proves_even_an_unidentified_launch_cannot_still_run(self) -> None:
        executor = RenderExecutor(
            runtime=None, assets=None, authority=lambda: self.authority(),
            machine="local", broker_api=None,
        )
        with mock.patch(
                "components.hub.backend.render_executor.psutil.boot_time",
                return_value=5_000.0):
            self.assertTrue(executor._recorded_process_cannot_run(None, 1_000.0))
            self.assertFalse(executor._recorded_process_cannot_run(None, 4_999.0))

    async def test_stop_signals_only_exact_interruptible_owned_process_groups(self) -> None:
        class BlockedOwnedProcess:
            pid = 739
            returncode = None

            def __init__(self) -> None:
                self.stopped = asyncio.Event()

            async def wait(self) -> int:
                await self.stopped.wait()
                self.returncode = -signal.SIGTERM
                return self.returncode

        process = BlockedOwnedProcess()
        identity = ProcessIdentity(
            pid=process.pid, process_group_id=process.pid,
            start_token=f"boot:1:{self.authority().ffmpeg}")
        killed = []

        def kill_group(process_group_id, sent_signal):
            killed.append((process_group_id, sent_signal))
            process.stopped.set()

        executor = RenderExecutor(
            runtime=None, assets=None, authority=lambda: self.authority(),
            machine="local", broker_api=None,
            process_identity=lambda *_args: identity,
            process_interruptible=lambda _process: True,
            kill_group=kill_group, group_exists=lambda _pgid: False,
        )
        executor._active["job-1"] = (
            process, identity, None, self.authority().ffmpeg)

        await executor.stop()

        self.assertEqual(killed, [(process.pid, signal.SIGTERM)])
        self.assertEqual(process.returncode, -signal.SIGTERM)

    async def test_cancel_uncertain_gone_record_settles_and_releases_without_signal(self) -> None:
        events: list[str] = []
        runtime = FakeRuntime(
            self.root, b'{"packet":{},"assets":{}}', self.authority().qualification, events)
        runtime.compute_held = True
        runtime.job.update(
            state="uncertain", attempt_id="attempt-1", machine="local")
        identity = ProcessIdentity(
            pid=999_999, process_group_id=999_999,
            start_token=f"boot:1:{self.authority().ffmpeg}")
        runtime.recovery = {
            "id": "job-1", "owner_scope": runtime.job["owner_scope"],
            "machine": "local", "state": "uncertain", "attempt_id": "attempt-1",
            "compute_held": True, "cancel_requested_at": None,
            "stopped_evidence_digest": None, "created_at": 0.0,
            "process": identity,
        }
        broker = FakeBroker(events)
        broker.owner = ("local", "render:job-1")
        killed = []

        executor = RenderExecutor(
            runtime, FakeAssets({}), authority=lambda: self.authority(),
            machine="local", broker_api=broker,
            kill_group=lambda pgid, sent_signal: killed.append((pgid, sent_signal)),
            group_exists=lambda _pgid: False,
            recorded_process_state=lambda _identity: "gone",
        )

        cancelled = await executor.request_cancel(runtime.job["owner_scope"], "job-1")

        self.assertEqual(cancelled["state"], "cancelled")
        self.assertFalse(cancelled["leases"]["compute"])
        self.assertEqual(killed, [])
        self.assertIn("settle-recovered", events)
        self.assertIn("release", events)
        self.assertIsNone(broker.owner)

    async def test_cancel_uncertain_live_record_stops_exact_group_then_settles(self) -> None:
        events: list[str] = []
        runtime = FakeRuntime(
            self.root, b'{"packet":{},"assets":{}}', self.authority().qualification, events)
        runtime.compute_held = True
        runtime.job.update(
            state="uncertain", attempt_id="attempt-1", machine="local")
        identity = ProcessIdentity(
            pid=740, process_group_id=740,
            start_token=f"boot:1:{self.authority().ffmpeg}")
        runtime.recovery = {
            "id": "job-1", "owner_scope": runtime.job["owner_scope"],
            "machine": "local", "state": "uncertain", "attempt_id": "attempt-1",
            "compute_held": True, "cancel_requested_at": None,
            "stopped_evidence_digest": None, "created_at": 0.0,
            "process": identity,
        }
        broker = FakeBroker(events)
        broker.owner = ("local", "render:job-1")
        killed = []

        executor = RenderExecutor(
            runtime, FakeAssets({}), authority=lambda: self.authority(),
            machine="local", broker_api=broker,
            kill_group=lambda pgid, sent_signal: killed.append((pgid, sent_signal)),
            group_exists=lambda _pgid: False,
            recorded_process_state=lambda _identity: "live",
            cancel_grace_seconds=0,
        )

        cancelled = await executor.request_cancel(runtime.job["owner_scope"], "job-1")

        self.assertEqual(killed, [(identity.process_group_id, signal.SIGTERM)])
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertFalse(cancelled["leases"]["compute"])
        self.assertIn("settle-recovered", events)
        self.assertIsNone(broker.owner)

    async def test_cancel_uncertain_unproved_record_keeps_compute_and_physical_hold(self) -> None:
        events: list[str] = []
        runtime = FakeRuntime(
            self.root, b'{"packet":{},"assets":{}}', self.authority().qualification, events)
        runtime.compute_held = True
        runtime.job.update(
            state="uncertain", attempt_id="attempt-1", machine="local")
        identity = ProcessIdentity(
            pid=741, process_group_id=741,
            start_token=f"boot:1:{self.authority().ffmpeg}")
        runtime.recovery = {
            "id": "job-1", "owner_scope": runtime.job["owner_scope"],
            "machine": "local", "state": "uncertain", "attempt_id": "attempt-1",
            "compute_held": True, "cancel_requested_at": None,
            "stopped_evidence_digest": None, "created_at": 0.0,
            "process": identity,
        }
        broker = FakeBroker(events)
        broker.owner = ("local", "render:job-1")
        killed = []

        executor = RenderExecutor(
            runtime, FakeAssets({}), authority=lambda: self.authority(),
            machine="local", broker_api=broker,
            kill_group=lambda pgid, sent_signal: killed.append((pgid, sent_signal)),
            group_exists=lambda _pgid: True,
            recorded_process_state=lambda _identity: "unknown",
        )

        pending = await executor.request_cancel(runtime.job["owner_scope"], "job-1")

        self.assertEqual(killed, [])
        self.assertEqual(pending["state"], "uncertain")
        self.assertIsNotNone(pending["cancel_requested_at"])
        self.assertTrue(pending["leases"]["compute"])
        self.assertNotIn("settle-recovered", events)
        self.assertEqual(broker.owner, ("local", "render:job-1"))

    async def test_release_hook_is_best_effort_after_durable_settle(self) -> None:
        events: list[str] = []
        runtime = FakeRuntime(
            self.root, b'{"packet":{},"assets":{}}', self.authority().qualification, events)
        runtime.compute_held = True
        runtime.job.update(
            state="uncertain", attempt_id="attempt-1", machine="local")
        identity = ProcessIdentity(
            pid=999_998, process_group_id=999_998,
            start_token=f"boot:1:{self.authority().ffmpeg}")
        runtime.recovery = {
            "id": "job-1", "owner_scope": runtime.job["owner_scope"],
            "machine": "local", "state": "uncertain", "attempt_id": "attempt-1",
            "compute_held": True, "cancel_requested_at": None,
            "stopped_evidence_digest": None, "created_at": 0.0,
            "process": identity,
        }
        broker = FakeBroker(events)
        broker.owner = ("local", "render:job-1")
        released: list[tuple[str, str]] = []

        def release_hook(owner_scope, job_id):
            released.append((owner_scope, job_id))
            raise RuntimeError("asset retirement unavailable")

        executor = RenderExecutor(
            runtime, FakeAssets({}), authority=lambda: self.authority(),
            machine="local", broker_api=broker,
            group_exists=lambda _pgid: False,
            recorded_process_state=lambda _identity: "gone",
        )
        executor.attach_release_hook(release_hook)

        cancelled = await executor.request_cancel(runtime.job["owner_scope"], "job-1")

        self.assertEqual(released, [(runtime.job["owner_scope"], "job-1")])
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertFalse(cancelled["leases"]["compute"])
        self.assertIsNone(broker.owner)
        self.assertIn("settle-recovered", events)

    async def test_active_validator_cancel_settles_only_after_exact_group_stop(self) -> None:
        events: list[str] = []
        runtime = FakeRuntime(
            self.root, b'{"packet":{},"assets":{}}', self.authority().qualification, events)
        runtime.compute_held = True
        runtime.job.update(
            state="stopped", attempt_id="attempt-1", machine="local",
            stopped_evidence_digest="a" * 64,
        )
        broker = FakeBroker(events)
        broker.owner = ("local", "render:job-1")
        context = RenderExecutionContext(
            owner_scope=runtime.job["owner_scope"], hub_job_id="job-1",
            attempt_id="attempt-1", launch_token="launch-1",
            accepted_request_sha256="a" * 64, raw_request=runtime.raw,
            packet={}, assets={}, qualification=runtime.qualification,
            qualification_permit=None, purpose="production", workspace=str(self.root),
            output_path=str(self.root / "final.mp4"), machine="local",
            dispatch_generation=1,
        )
        probe = FakeProcess(self.root / "final.mp4")
        identity = ProcessIdentity(
            pid=probe.pid, process_group_id=probe.pid,
            start_token=f"boot:1:{self.authority().ffprobe}")
        executor = RenderExecutor(
            runtime, FakeAssets({}), authority=lambda: self.authority(),
            machine="local", broker_api=broker,
            process_identity=lambda *_args: identity,
            kill_group=lambda _pgid, _signal: None,
            group_exists=lambda _pgid: False,
        )
        executor._active["job-1"] = (
            probe, identity, context, self.authority().ffprobe)

        cancelled = await executor.request_cancel(runtime.job["owner_scope"], "job-1")

        self.assertEqual(cancelled["state"], "cancelled")
        self.assertIn("output_validation_failed", events)
        self.assertIn("release", events)
        self.assertFalse(runtime.compute_held)
        self.assertIsNone(broker.owner)

    async def test_original_key_cancel_stops_exact_active_main_and_probe_groups(self) -> None:
        owner_scope = "site-a:node-a:fleet"
        for phase in ("main", "probe"):
            with self.subTest(phase=phase):
                events: list[str] = []
                runtime = FakeRuntime(
                    self.root / uuid4().hex, b'{"packet":{},"assets":{}}',
                    self.authority().qualification, events)
                runtime.compute_held = True
                runtime.job.update(
                    state="rendering" if phase == "main" else "stopped",
                    attempt_id="attempt-1", machine="local",
                )
                if phase == "probe":
                    runtime.job["stopped_evidence_digest"] = "a" * 64
                broker = FakeBroker(events)
                broker.owner = ("local", "render:job-1")
                stopped = asyncio.Event()

                class BlockedOwnedProcess:
                    pid = 740 if phase == "main" else 741
                    returncode = None

                    async def wait(self):
                        await stopped.wait()
                        self.returncode = -signal.SIGTERM
                        return self.returncode

                process = BlockedOwnedProcess()
                executable = (
                    self.authority().ffmpeg if phase == "main" else self.authority().ffprobe)
                identity = ProcessIdentity(
                    pid=process.pid, process_group_id=process.pid,
                    start_token=f"boot:1:{executable}")
                context = RenderExecutionContext(
                    owner_scope=owner_scope, hub_job_id="job-1", attempt_id="attempt-1",
                    launch_token="launch-1", accepted_request_sha256="a" * 64,
                    raw_request=runtime.raw, packet={}, assets={},
                    qualification=runtime.qualification, qualification_permit=None,
                    purpose="production", workspace=str(runtime.root),
                    output_path=str(runtime.root / "final.mp4"), machine="local",
                    dispatch_generation=1,
                )
                killed = []

                def kill_group(process_group_id, sent_signal):
                    killed.append((process_group_id, sent_signal))
                    stopped.set()

                executor = RenderExecutor(
                    runtime, FakeAssets({}), authority=lambda: self.authority(),
                    machine="local", broker_api=broker,
                    process_identity=lambda *_args: identity, kill_group=kill_group,
                    group_exists=lambda _pgid: False,
                )
                executor._active["job-1"] = (process, identity, context, executable)
                service = RenderService(
                    runtime, FakeAssets({}), executor=executor, authority_loader=None)

                cancelled = await service.cancel_by_key(
                    owner_scope, "dispatch-1", hashlib.sha256(runtime.raw).hexdigest())

                self.assertEqual(killed, [(process.pid, signal.SIGTERM)])
                self.assertEqual(cancelled["state"], "cancelled")
                self.assertFalse(runtime.compute_held)
                self.assertIsNone(broker.owner)

    async def test_unproved_validator_identity_retains_durable_and_physical_compute(self) -> None:
        packet = json.loads(
            (ROOT / "tests/fixtures/final-render-compiler-v1.json").read_text()
        )["f0_base_audio"]["packet"]
        raw = json.dumps({
            "format": "studio.render.hub-request.v1",
            "dispatch": {}, "packet": packet,
            "assets": {"audio": "b" * 32, "base": "a" * 32},
        }, sort_keys=True, separators=(",", ":")).encode()
        events: list[str] = []
        runtime = FakeRuntime(self.root, raw, self.authority().qualification, events)
        files = {}
        for handle, payload in (("a" * 32, self.mp4_bytes()),
                                ("b" * 32, self.wav_bytes())):
            path = self.root / handle
            path.write_bytes(payload)
            files[handle] = path
        broker = FakeBroker(events)
        calls = 0
        probe_identity_checked = asyncio.Event()

        async def spawn(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return FakeProcess(Path(runtime.claim["output_path"]))
            return FakeProbe({
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "width": 128,
                     "height": 96, "avg_frame_rate": "5/1", "pix_fmt": "yuv420p",
                     "duration": "2.0"},
                    {"codec_type": "audio", "codec_name": "aac", "duration": "2.0"},
                ],
                "format": {"duration": "2.0"},
            })

        def identity(process, executable):
            if process.pid == FakeProbe.pid:
                probe_identity_checked.set()
                raise RuntimeError("validator identity is unproved")
            return ProcessIdentity(
                pid=process.pid, process_group_id=process.pid,
                start_token=f"boot:1:{executable}")

        executor = RenderExecutor(
            runtime, FakeAssets(files), authority=lambda: self.authority(),
            machine="local", broker_api=broker, spawn=spawn,
            compile_plan=lambda _raw, local, output: FinalRenderPlan(
                (EncodeTier("hardware-h264", (
                    "-i", local["base"], "-i", local["audio"],
                    "-c:v", "h264_videotoolbox", output)),), (), ()),
            process_identity=identity, group_exists=lambda _pgid: False,
        )

        self.assertTrue(await executor.dispatch_ready())
        await probe_identity_checked.wait()
        await executor.stop()

        self.assertEqual(runtime.job["state"], "stopped")
        self.assertTrue(runtime.compute_held)
        self.assertIsNotNone(broker.owner)
        self.assertNotIn("output_validation_failed", events)

    async def test_probe_spawn_without_returned_handle_retains_compute(self) -> None:
        packet = json.loads(
            (ROOT / "tests/fixtures/final-render-compiler-v1.json").read_text()
        )["f0_base_audio"]["packet"]
        raw = json.dumps({
            "format": "studio.render.hub-request.v1", "dispatch": {}, "packet": packet,
            "assets": {"audio": "b" * 32, "base": "a" * 32},
        }, sort_keys=True, separators=(",", ":")).encode()
        events: list[str] = []
        runtime = FakeRuntime(self.root, raw, self.authority().qualification, events)
        files = {}
        for handle, payload in (("a" * 32, self.mp4_bytes()),
                                ("b" * 32, self.wav_bytes())):
            path = self.root / handle
            path.write_bytes(payload)
            files[handle] = path
        broker = FakeBroker(events)
        calls = 0
        probe_spawned = asyncio.Event()

        async def spawn(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return FakeProcess(Path(runtime.claim["output_path"]))
            events.append("probe-spawn-invoked")
            probe_spawned.set()
            raise RuntimeError("adapter lost the probe handle after invocation")

        executor = RenderExecutor(
            runtime, FakeAssets(files), authority=lambda: self.authority(),
            machine="local", broker_api=broker, spawn=spawn,
            compile_plan=lambda _raw, local, output: FinalRenderPlan(
                (EncodeTier("hardware-h264", (
                    "-i", local["base"], "-i", local["audio"],
                    "-c:v", "h264_videotoolbox", output)),), (), ()),
            process_identity=lambda process, executable: ProcessIdentity(
                pid=process.pid, process_group_id=process.pid,
                start_token=f"boot:1:{executable}"),
            group_exists=lambda _pgid: False,
        )

        self.assertTrue(await executor.dispatch_ready())
        await probe_spawned.wait()
        await executor.stop()

        self.assertIn("probe-spawn-invoked", events)
        self.assertEqual(runtime.job["state"], "stopped")
        self.assertTrue(runtime.compute_held)
        self.assertIsNotNone(broker.owner)
        self.assertNotIn("output_validation_failed", events)

    async def test_by_key_cancel_during_probe_settles_after_validator_returns(self) -> None:
        packet = json.loads(
            (ROOT / "tests/fixtures/final-render-compiler-v1.json").read_text()
        )["f0_base_audio"]["packet"]
        raw = json.dumps({
            "packet": packet, "assets": {"audio": "b" * 32, "base": "a" * 32},
        }, sort_keys=True, separators=(",", ":")).encode()
        events: list[str] = []
        runtime = FakeRuntime(self.root, raw, self.authority().qualification, events)
        asset_files = {}
        for handle, payload in (("a" * 32, self.mp4_bytes()),
                                ("b" * 32, self.wav_bytes())):
            path = self.root / handle
            path.write_bytes(payload)
            asset_files[handle] = path
        broker = FakeBroker(events)
        validation_ran = asyncio.Event()

        def validate(context, _authority, _command_digest):
            runtime.request_cancel_by_key(
                context.owner_scope, "dispatch-1",
                context.accepted_request_sha256)
            validation_ran.set()
            return ValidatedOutputReceipt(
                owner_scope=context.owner_scope, hub_job_id=context.hub_job_id,
                attempt_id=context.attempt_id,
                accepted_request_sha256=context.accepted_request_sha256,
                receipt_digest="5" * 64, output_sha256="6" * 64,
                output_size_bytes=1, profile_digest="1" * 64,
                tools_digest="2" * 64, compiler_digest="3" * 64,
                qualification_digest="4" * 64, generated_command_digest="7" * 64,
                selected_tier="hardware-h264", feature_losses=(),
            )

        executor = RenderExecutor(
            runtime, FakeAssets(asset_files), authority=lambda: self.authority(),
            machine="local", broker_api=broker,
            compile_plan=lambda _raw, local, output: FinalRenderPlan(
                (EncodeTier("hardware-h264", (
                    "-i", local["base"], "-i", local["audio"],
                    "-c:v", "h264_videotoolbox", output)),), (), ()),
            spawn=lambda *_args, **_kwargs: asyncio.sleep(
                0, result=FakeProcess(Path(runtime.claim["output_path"]))),
            output_validator=validate,
            process_identity=lambda process, executable: ProcessIdentity(
                pid=process.pid, process_group_id=process.pid,
                start_token=f"boot:1:{executable}"),
            group_exists=lambda _pgid: False,
        )

        self.assertTrue(await executor.dispatch_ready())
        await asyncio.wait_for(validation_ran.wait(), 10)
        await executor.stop()

        self.assertEqual(runtime.job["state"], "cancelled")
        self.assertFalse(runtime.compute_held)
        self.assertIsNone(broker.owner)
        self.assertNotIn("publish", events)

    async def test_known_prelaunch_and_poststop_failures_settle_before_physical_release(self) -> None:
        packet = json.loads(
            (ROOT / "tests/fixtures/final-render-compiler-v1.json").read_text()
        )["f0_base_audio"]["packet"]
        raw = json.dumps({
            "packet": packet, "assets": {"audio": "b" * 32, "base": "a" * 32},
        }, sort_keys=True, separators=(",", ":")).encode()
        for phase in ("prelaunch_failed", "output_validation_failed"):
            with self.subTest(phase=phase):
                events = []
                runtime = FakeRuntime(
                    self.root / uuid4().hex, raw, self.authority().qualification, events)
                asset_files = {}
                for handle, payload in (("a" * 32, self.mp4_bytes()),
                                        ("b" * 32, self.wav_bytes())):
                    path = runtime.root / handle
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(payload)
                    asset_files[handle] = path
                broker = FakeBroker(events)
                validation_failed = asyncio.Event()

                async def spawn(*_args, **_kwargs):
                    return FakeProcess(Path(runtime.claim["output_path"]))

                def fail_validation(*_args):
                    validation_failed.set()
                    raise RuntimeError("invalid output")

                executor = RenderExecutor(
                    runtime, FakeAssets(asset_files), authority=lambda: self.authority(),
                    machine="local", broker_api=broker,
                    compile_plan=(
                        (lambda *_args: (_ for _ in ()).throw(RuntimeError("compile")))
                        if phase == "prelaunch_failed" else
                        (lambda _raw, local, output: FinalRenderPlan(
                            (EncodeTier("hardware-h264", (
                                "-i", local["base"], "-i", local["audio"],
                                "-c:v", "h264_videotoolbox", output)),),
                            (), ()))
                    ),
                    spawn=spawn,
                    output_validator=fail_validation,
                    process_identity=lambda process, executable: ProcessIdentity(
                        pid=process.pid, process_group_id=process.pid,
                        start_token=f"boot:1:{executable}"),
                    group_exists=lambda _pgid: False,
                )

                self.assertTrue(await executor.dispatch_ready())
                if phase == "output_validation_failed":
                    await validation_failed.wait()
                await executor.stop()

                self.assertIn(phase, events)
                self.assertEqual(runtime.job["state"], "failed")
                self.assertFalse(runtime.compute_held)
                self.assertIsNone(broker.owner)

    async def test_measured_memory_floor_refuses_before_physical_or_durable_claim(self) -> None:
        events: list[str] = []
        runtime = FakeRuntime(self.root, b'{"packet":{},"assets":{}}',
                              self.authority().qualification, events)
        broker = FakeBroker(events)
        executor = RenderExecutor(
            runtime, FakeAssets({}), authority=lambda: self.authority(), machine="local",
            broker_api=broker,
            available_memory=lambda: self.authority().bounds.min_available_memory_bytes - 1,
        )

        self.assertFalse(await executor.dispatch_ready())
        self.assertEqual(events, [])
        self.assertEqual(runtime.job["state"], "accepted")

    async def test_shutdown_fence_prevents_second_claim_and_drains_owned_task(self) -> None:
        packet = json.loads(
            (ROOT / "tests/fixtures/final-render-compiler-v1.json").read_text()
        )["f0_base_audio"]["packet"]
        raw = json.dumps({
            "packet": packet, "assets": {"audio": "b" * 32, "base": "a" * 32},
        }, sort_keys=True, separators=(",", ":")).encode()
        events: list[str] = []

        class QueueRuntime(FakeRuntime):
            def __init__(self, *args):
                super().__init__(*args)
                self.second = {
                    "id": "job-2", "owner_scope": self.job["owner_scope"],
                    "state": "accepted", "created_at": 2.0, "purpose": "production",
                }

            def dispatchable_jobs(self):
                rows = super().dispatchable_jobs()
                return rows or ([self.second] if self.second["state"] == "accepted" else [])

        runtime = QueueRuntime(self.root, raw, self.authority().qualification, events)
        asset_files = {}
        for handle, payload in (("a" * 32, self.mp4_bytes()),
                                ("b" * 32, self.wav_bytes())):
            path = self.root / handle
            path.write_bytes(payload)
            asset_files[handle] = path
        broker = FakeBroker(events)
        spawned = asyncio.Event()
        release = asyncio.Event()

        class BlockingProcess(FakeProcess):
            async def wait(self):
                spawned.set()
                await release.wait()
                return await super().wait()

        async def spawn(*_args, **_kwargs):
            return BlockingProcess(Path(runtime.claim["output_path"]))

        def validate(context, authority, command_digest):
            output = Path(context.output_path).read_bytes()
            return ValidatedOutputReceipt(
                owner_scope=context.owner_scope, hub_job_id=context.hub_job_id,
                attempt_id=context.attempt_id,
                accepted_request_sha256=context.accepted_request_sha256,
                receipt_digest="5" * 64, output_sha256=hashlib.sha256(output).hexdigest(),
                output_size_bytes=len(output), profile_digest=authority.qualification.profile_digest,
                tools_digest=authority.qualification.tools_digest,
                compiler_digest=authority.qualification.compiler_digest,
                qualification_digest=authority.qualification.qualification_digest,
                generated_command_digest=command_digest, selected_tier=authority.selected_tier,
                feature_losses=(),
            )

        executor = RenderExecutor(
            runtime, FakeAssets(asset_files), authority=lambda: self.authority(),
            machine="local", broker_api=broker, spawn=spawn, output_validator=validate,
            compile_plan=lambda _raw, local, output: FinalRenderPlan(
                (EncodeTier("hardware-h264", (
                    "-i", local["base"], "-i", local["audio"],
                    "-c:v", "h264_videotoolbox", output)),), (), ()),
            process_identity=lambda process, executable: ProcessIdentity(
                pid=process.pid, process_group_id=process.pid,
                start_token=f"boot:1:{executable}"),
            group_exists=lambda _pgid: False,
        )

        self.assertTrue(await executor.dispatch_ready())
        await spawned.wait()
        executor.begin_shutdown()
        self.assertIsNone(await executor.dispatchable_candidate("local"))
        self.assertFalse(await executor.dispatch_ready())
        draining = asyncio.create_task(executor.stop())
        await asyncio.sleep(0)
        self.assertFalse(draining.done())
        self.assertEqual(events.count("claim"), 1)
        release.set()
        await draining
        self.assertEqual(events.count("claim"), 1)

    async def test_media_validation_rejects_wrong_dimensions_fps_and_duration(self) -> None:
        authority = self.authority()
        output = self.root / "wrong.mp4"
        output.write_bytes(b"fixed-output")
        context = RenderExecutionContext(
            owner_scope="site-a:node-a:fleet", hub_job_id="job-1",
            attempt_id="attempt-1", launch_token="launch-1",
            accepted_request_sha256="a" * 64, raw_request=b"{}", packet={
                "recipe": {
                    "canvas": {"width": 128, "height": 96, "working_fps": 5},
                    "target_duration_sec": 2,
                },
            }, assets={}, qualification=authority.qualification,
            qualification_permit=None, purpose="production", workspace=str(self.root),
            output_path=str(output), machine="local", dispatch_generation=1,
        )
        wrong = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 640,
                 "height": 480, "avg_frame_rate": "30/1", "pix_fmt": "yuv420p",
                 "duration": "9.0"},
                {"codec_type": "audio", "codec_name": "aac", "duration": "9.0"},
            ],
            "format": {"duration": "9.0"},
        }

        async def spawn(*_args, **_kwargs):
            return FakeProbe(wrong)

        executor = RenderExecutor(
            runtime=None, assets=None, authority=lambda: authority, machine="local",
            broker_api=None, spawn=spawn,
            process_identity=lambda process, executable: ProcessIdentity(
                pid=process.pid, process_group_id=process.pid,
                start_token=f"boot:1:{executable}"),
            group_exists=lambda _pgid: False,
        )

        with self.assertRaisesRegex(RuntimeError, "qualified profile"):
            await executor._validate_output(context, authority, "b" * 64)

    async def test_media_validation_requires_independent_timing_and_exact_stream_count(self) -> None:
        authority = self.authority()
        output = self.root / "ambiguous.mp4"
        output.write_bytes(b"fixed-output")
        context = RenderExecutionContext(
            owner_scope="site-a:node-a:fleet", hub_job_id="job-1",
            attempt_id="attempt-1", launch_token="launch-1",
            accepted_request_sha256="a" * 64, raw_request=b"{}", packet={
                "recipe": {
                    "canvas": {"width": 128, "height": 96, "working_fps": 5},
                    "target_duration_sec": 2,
                },
            }, assets={}, qualification=authority.qualification,
            qualification_permit=None, purpose="production", workspace=str(self.root),
            output_path=str(output), machine="local", dispatch_generation=1,
        )
        video = {
            "codec_type": "video", "codec_name": "h264", "width": 128, "height": 96,
            "avg_frame_rate": "5/1", "pix_fmt": "yuv420p", "duration": "2.0",
        }
        audio = {"codec_type": "audio", "codec_name": "aac"}
        cases = (
            {"streams": [video, audio], "format": {"duration": "2.0"}},
            {"streams": [video, {**audio, "duration": "2.0"}, {**audio, "duration": "2.0"}],
             "format": {"duration": "2.0"}},
        )
        for document in cases:
            with self.subTest(streams=len(document["streams"])):
                async def spawn(*_args, **_kwargs):
                    return FakeProbe(document)

                executor = RenderExecutor(
                    runtime=None, assets=None, authority=lambda: authority,
                    machine="local", broker_api=None, spawn=spawn,
                    process_identity=lambda process, executable: ProcessIdentity(
                        pid=process.pid, process_group_id=process.pid,
                        start_token=f"boot:1:{executable}"),
                    group_exists=lambda _pgid: False,
                )
                with self.assertRaisesRegex(RuntimeError, "timing|stream"):
                    await executor._validate_output(context, authority, "b" * 64)

    async def test_probe_enforces_remaining_attempt_rss_scratch_and_metadata_budgets(self) -> None:
        authority = self.authority()
        output = self.root / "budget.mp4"
        output.write_bytes(b"fixed-output")
        context = RenderExecutionContext(
            owner_scope="site-a:node-a:fleet", hub_job_id="job-budget",
            attempt_id="attempt-1", launch_token="launch-1",
            accepted_request_sha256="a" * 64, raw_request=b"{}", packet={
                "recipe": {
                    "canvas": {"width": 128, "height": 96, "working_fps": 5},
                    "target_duration_sec": 2,
                },
            }, assets={}, qualification=authority.qualification,
            qualification_permit=None, purpose="production", workspace=str(self.root),
            output_path=str(output), machine="local", dispatch_generation=1,
        )
        valid = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 128,
                 "height": 96, "avg_frame_rate": "5/1", "pix_fmt": "yuv420p",
                 "duration": "2.0"},
                {"codec_type": "audio", "codec_name": "aac", "duration": "2.0"},
            ],
            "format": {"duration": "2.0"},
        }

        class Clock:
            value = 29.5

            def __call__(self):
                return self.value

        clock = Clock()

        class AdvancingProbe(FakeProbe):
            def __init__(self, document):
                super().__init__(document)
                self.stdout.on_read = lambda: setattr(clock, "value", 30.1)

        async def spawn(*_args, **_kwargs):
            return AdvancingProbe(valid)

        executor = RenderExecutor(
            runtime=None, assets=None, authority=lambda: authority,
            machine="local", broker_api=None, spawn=spawn,
            monotonic=clock, free_bytes=lambda _path: (
                authority.qualification.scratch_reserve_bytes
                + authority.qualification.output_limit_bytes),
            process_identity=lambda process, executable: ProcessIdentity(
                pid=process.pid, process_group_id=process.pid,
                start_token=f"boot:1:{executable}"),
            group_exists=lambda _pgid: False,
        )
        executor._process_tree_rss = lambda _pid: authority.bounds.max_rss_bytes + 1

        with self.assertRaisesRegex(RuntimeError, "attempt|memory|scratch|metadata"):
            await executor._validate_output(
                context, authority, "b" * 64, attempt_deadline=30.0,
                metadata_limit_bytes=1024 * 1024, diagnostic_tail_bytes=64 * 1024)

    async def test_probe_bounded_streaming_checks_each_rss_scratch_and_metadata_limit(self) -> None:
        authority = self.authority()
        output = self.root / "stream-budget.mp4"
        output.write_bytes(b"fixed-output")
        context = RenderExecutionContext(
            owner_scope="site-a:node-a:fleet", hub_job_id="job-stream-budget",
            attempt_id="attempt-1", launch_token="launch-1",
            accepted_request_sha256="a" * 64, raw_request=b"{}", packet={
                "recipe": {
                    "canvas": {"width": 128, "height": 96, "working_fps": 5},
                    "target_duration_sec": 2,
                },
            }, assets={}, qualification=authority.qualification,
            qualification_permit=None, purpose="production", workspace=str(self.root),
            output_path=str(output), machine="local", dispatch_generation=1,
        )
        valid = json.dumps({
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 128,
                 "height": 96, "avg_frame_rate": "5/1", "pix_fmt": "yuv420p",
                 "duration": "2.0"},
                {"codec_type": "audio", "codec_name": "aac", "duration": "2.0"},
            ],
            "format": {"duration": "2.0"},
        }).encode()
        metadata_limit = 1024 * 1024
        diagnostic_tail = 64 * 1024
        cases = (
            ("memory", valid, authority.bounds.max_rss_bytes + 1,
             authority.qualification.scratch_reserve_bytes
             + authority.qualification.output_limit_bytes),
            ("scratch", valid, 1,
             authority.qualification.scratch_reserve_bytes
             + authority.qualification.output_limit_bytes - 1),
            ("metadata", b"x" * (metadata_limit + diagnostic_tail + 1), 1,
             authority.qualification.scratch_reserve_bytes
             + authority.qualification.output_limit_bytes),
        )
        for expected, stdout, rss, free_bytes in cases:
            with self.subTest(expected=expected):
                probe = StreamingProbe(stdout, b"diagnostic")

                async def spawn(*_args, **_kwargs):
                    return probe

                executor = RenderExecutor(
                    runtime=None, assets=None, authority=lambda: authority,
                    machine="local", broker_api=None, spawn=spawn,
                    monotonic=lambda: 1.0, free_bytes=lambda _path: free_bytes,
                    process_identity=lambda process, executable: ProcessIdentity(
                        pid=process.pid, process_group_id=process.pid,
                        start_token=f"boot:1:{executable}"),
                    group_exists=lambda _pgid: False,
                )
                executor._process_tree_rss = lambda _pid: rss

                with self.assertRaisesRegex(RuntimeError, expected):
                    await executor._validate_output(
                        context, authority, "b" * 64, attempt_deadline=30.0,
                        metadata_limit_bytes=metadata_limit,
                        diagnostic_tail_bytes=diagnostic_tail)
                self.assertLessEqual(probe.stdout.bytes_read, metadata_limit + 64 * 1024)

    async def test_media_validation_rejects_output_changed_during_probe(self) -> None:
        authority = self.authority()
        output = self.root / "changed.mp4"
        output.write_bytes(b"first-output")
        context = RenderExecutionContext(
            owner_scope="site-a:node-a:fleet", hub_job_id="job-1",
            attempt_id="attempt-1", launch_token="launch-1",
            accepted_request_sha256="a" * 64, raw_request=b"{}", packet={
                "recipe": {
                    "canvas": {"width": 128, "height": 96, "working_fps": 5},
                    "target_duration_sec": 2,
                },
            }, assets={}, qualification=authority.qualification,
            qualification_permit=None, purpose="production", workspace=str(self.root),
            output_path=str(output), machine="local", dispatch_generation=1,
        )
        valid = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 128,
                 "height": 96, "avg_frame_rate": "5/1", "pix_fmt": "yuv420p",
                 "duration": "2.0"},
                {"codec_type": "audio", "codec_name": "aac", "duration": "2.0"},
            ],
            "format": {"duration": "2.0"},
        }

        class MutatingProbe(FakeProbe):
            def __init__(self, document):
                super().__init__(document)
                self.stdout.on_read = lambda: output.write_bytes(b"changed-after-hash")

        async def spawn(*_args, **_kwargs):
            return MutatingProbe(valid)

        executor = RenderExecutor(
            runtime=None, assets=None, authority=lambda: authority, machine="local",
            broker_api=None, spawn=spawn,
            process_identity=lambda process, executable: ProcessIdentity(
                pid=process.pid, process_group_id=process.pid,
                start_token=f"boot:1:{executable}"),
            group_exists=lambda _pgid: False,
        )

        with self.assertRaisesRegex(RuntimeError, "changed during validation"):
            await executor._validate_output(context, authority, "b" * 64)


    def test_activity_publishes_the_configured_executor_render_lane(self) -> None:
        from components.hub.backend import activity, render_executor
        authority = self.authority()
        runtime = FakeRuntime(self.root, self.raw, authority.qualification, [])
        executor = RenderExecutor(
            runtime=runtime, assets=None, authority=authority,
            machine="local", broker_api=FakeBroker([]))
        previous = render_executor._configured_executor
        self.addCleanup(render_executor.configure, previous)
        render_executor.configure(executor)

        merged = activity.live_batches()
        self.assertIn("render:job-1", merged,
                      "the executor's live job never reached the activity view")
        row = merged["render:job-1"]
        self.assertEqual(row["modality"], "render")
        self.assertEqual(row["items"][0]["operation"], "video.final_render")
        self.assertEqual(row["items"][0]["state"], "accepted")

        render_executor.configure(None)
        self.assertEqual(
            [key for key in activity.live_batches() if key.startswith("render:")], [],
            "an unconfigured Hub must not advertise a render lane it does not have")


class DefaultAnchorTests(unittest.TestCase):
    """The reader's own root stays the real "/"; foreign and escaping roots are refused."""

    def test_the_default_anchor_is_the_real_root_and_foreign_or_escaping_roots_are_refused(
            self) -> None:
        from components.hub.backend import render_authority_files as files

        self.assertEqual(files._ROOT_ANCHOR, Path("/"),
                         "production must keep walking every real ancestor from /")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            anchor = root / "anchor"
            anchor.mkdir()
            data_root = root / "data"
            (data_root / "render-authority").mkdir(parents=True)
            (data_root / "render-authority").chmod(0o700)
            for candidate in (
                    data_root,                              # outside the anchor
                    anchor / ".." / "data"):                # ".." escaped the anchor
                with mock.patch.object(files, "_ROOT_ANCHOR", anchor):
                    with self.assertRaises(files.RenderAuthorityRecordError) as raised:
                        files.read_record(candidate, "qualify.json", expected_uid=os.getuid())
                self.assertEqual(raised.exception.code, "UNSAFE_PATH", candidate)


if __name__ == "__main__":
    unittest.main()
