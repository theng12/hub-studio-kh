#!/usr/bin/env python3
"""Scoped late-label v3 capabilities publication, probe scope and packet-profile selection.

Fake-only. Every descriptor is self-built at fixture time (source pins computed the same
way the loader checks them), tools are sealed fake bytes, and no probe, FFmpeg or real
descriptor from packaging is executed - the frozen packaging triple is only read to pin
its digests and its max-assets bound.
"""
from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import json
import shutil
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.hub.backend import auth  # noqa: E402
from components.hub.backend.fleet_nodes import _render_relay_segment  # noqa: E402
from components.hub.backend.render_encode import DEFAULT_OPT_IN_ENCODE  # noqa: E402
from components.hub.backend.render_hash_v1 import render_hash  # noqa: E402
from components.hub.backend.render_executor import (  # noqa: E402
    InstalledRenderAuthorityLoader,
    LateLabelProbeScope,
    RenderExecutor,
)
from components.hub.backend.render_service import (  # noqa: E402
    LATE_LABEL_CAPABILITIES_FORMAT,
    LATE_LABEL_CONTRACT,
    register_relay_routes,
    register_routes,
)

HUB_TOKEN = "late-label-scope-hub"
INTENT_ID = "11111111-1111-1111-1111-111111111111"
PACKAGING = ROOT / "packaging/render-authority-late-label"
FROZEN_PINS = {
    "PROFILE.json": "b39f239092d8854be82b4a99df0cd591f9cdef05553d7741765b35ac72ca2b3c",
    "COMPILER.json": "8eb1f962735692144ef8f3627ee8080d0d338c6295781bcde3550d6eef7834e3",
    "TOOLS.json": "98c25ae582d4ee43ee1cf665192365149f8d9f860c229a0be9da131df43eba8a",
}
BASE_ROW_KEYS = {
    "operation", "physical_machine_id", "hardware_class", "profile_digest", "tools_digest",
    "compiler_digest", "qualification_digest", "selected_tier", "exact_encoder", "font_support",
    "bounds", "profile_execution_bounds", "production_eligible", "production_enabled",
    "available_capacity", "qualification_candidates",
}


def compact(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def packet_for(profile_digest: str) -> dict:
    """The fixture base packet claiming one exact profile digest."""
    packet = copy.deepcopy(json.loads(
        (ROOT / "tests/fixtures/final-render-compiler-v1.json").read_text()
    )["f0_base_audio"]["packet"])
    packet["recipe"]["profile_digest"] = profile_digest
    packet["recipe_digest"] = render_hash("recipe", packet["recipe"])
    packet["manifest_digest"] = render_hash("manifest", packet["manifest"])
    packet["packet_digest"] = render_hash("packet", {
        "recipe_digest": packet["recipe_digest"],
        "manifest_digest": packet["manifest_digest"],
    })
    return packet


def _fake_compiler(implementation: str, backend: Path) -> dict:
    return {
        "format": "studio.render.compiler.v1",
        "implementation": implementation,
        "python_runtime_sha256": file_sha(sys.executable),
        "render_hash_sha256": file_sha(backend / "render_hash_v1.py"),
        "render_contract_sha256": file_sha(backend / "render_contract.py"),
        "final_render_compiler_sha256": file_sha(backend / "final_render_compiler.py"),
    }


def _fake_profile(*, fmt: str, implementation: str, combination: str,
                  compiler_raw: bytes, tools_raw: bytes, bounds: dict) -> dict:
    return {
        "format": fmt,
        "implementation": implementation,
        "recipe_namespace": "studio.render.final.v1",
        "compiler_artifact_sha256": hashlib.sha256(compiler_raw).hexdigest(),
        "tools_artifact_sha256": hashlib.sha256(tools_raw).hexdigest(),
        "combination_set": combination,
        "tier_policy": "story-final-chain-v1",
        "output_policy": "story-final-output-v1",
        "execution_bounds": bounds,
        "hardware_qualification_record_sha256": None,
        "fidelity_record_sha256": None,
        "production_eligible": False,
    }


V1_BOUNDS = {
    "canvas_pairs": [{"width": 128, "height": 96}], "working_fps": [5],
    "max_duration_sec": 2.5, "max_operations": 9, "max_assets": 6,
    "max_asset_bytes": 1_000_000, "max_total_input_bytes": 2_000_000,
    "max_pixel_frames": 1_000_000, "scratch_reserve_bytes": 250_000,
    "output_limit_bytes": 1_000_000, "process_tree_rss_limit_bytes": 100_000_000,
    "cumulative_wall_seconds": 30,
}
# The published v3 bound: max assets stays 4 (reveals may exceed it and must refuse).
V3_BOUNDS = {**V1_BOUNDS, "max_operations": 8, "max_assets": 4}


class FakeBroker:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def higher_priority_work_waiting(self, **_kwargs) -> bool:
        return False

    def render_dispatch_allowed(self, _machine: str, _owner: str | None = None) -> bool:
        return True

    def acquire_external_machine(self, machine: str, owner: str) -> bool:
        self.events.append("physical")
        return True

    def release_external_machine(self, machine: str, owner: str) -> None:
        self.events.append("release")

    def busy_machines(self) -> set:
        return set()


class ScopedCase(unittest.TestCase):
    """One staged install: fake legacy v1 root + fake late-label v3 triple + data root."""

    def setUp(self) -> None:
        self.now = 1_000
        self.root = ROOT / "_trash/render-late-label-scoped-testroots" / uuid4().hex
        # Retain synthetic fixtures under _trash; that directory is never emptied by tests.
        self.resources = self.root / "resources"
        self.tools_root = self.resources / "render-tools"
        self.data_root = self.root / "data"
        self.tools_root.mkdir(parents=True)
        (self.data_root / "render-authority").mkdir(parents=True)
        (self.data_root / "render-authority").chmod(0o700)

        self.tool_files = {}
        for name, payload in (("ffmpeg", b"sealed-ffmpeg"), ("ffprobe", b"sealed-ffprobe")):
            path = self.tools_root / name
            path.write_bytes(payload)
            path.chmod(0o755)
            self.tool_files[name] = {
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        tool_raw = compact({
            "format": "studio.render.tools.v1", "platform": "macos-arm64",
            "ffmpeg_sha256": self.tool_files["ffmpeg"]["sha256"],
            "ffmpeg_size_bytes": self.tool_files["ffmpeg"]["size_bytes"],
            "ffprobe_sha256": self.tool_files["ffprobe"]["sha256"],
            "ffprobe_size_bytes": self.tool_files["ffprobe"]["size_bytes"],
            "architecture_evidence_sha256": "1" * 64,
            "minimum_os_evidence_sha256": "2" * 64,
            "linked_libraries_evidence_sha256": "3" * 64,
            "build_configuration_evidence_sha256": "4" * 64,
            "capability_inventory_evidence_sha256": "5" * 64,
        })
        (self.tools_root / "TOOLS.json").write_bytes(tool_raw)
        (self.tools_root / "MANIFEST.json").write_bytes(compact({
            "format": "studiohub.render-tools.payload.v1", "tool_version": "8.1",
            "payload_format": "macos-universal", "production_eligible": False,
            "files": [
                {"name": name, "path": f"render-tools/{name}", **self.tool_files[name]}
                for name in ("ffmpeg", "ffprobe")
            ],
        }))

        backend = ROOT / "components/hub/backend"
        legacy = backend / "render_legacy_v1"
        compiler_v1 = compact(_fake_compiler("hub-final-render-v1", legacy))
        (self.tools_root / "COMPILER.json").write_bytes(compiler_v1)
        profile_v1 = compact(_fake_profile(
            fmt="studio.render.profile.v1", implementation="story-classic-no-font-v1",
            combination="retained-classic-no-font-candidate-v1",
            compiler_raw=compiler_v1, tools_raw=tool_raw, bounds=V1_BOUNDS))
        (self.tools_root / "PROFILE.json").write_bytes(profile_v1)

        late = self.tools_root / "late-label"
        late.mkdir()
        # Production ships the same TOOLS bytes in both roots; mirror that exactly.
        (late / "TOOLS.json").write_bytes(tool_raw)
        compiler_v3 = compact(_fake_compiler("hub-final-render-late-label-v3", backend))
        (late / "COMPILER.json").write_bytes(compiler_v3)
        profile_v3 = compact(_fake_profile(
            fmt="studio.render.profile.v3", implementation="story-classic-late-label-v3",
            combination="retained-classic-late-label-candidate-v3",
            compiler_raw=compiler_v3, tools_raw=tool_raw, bounds=V3_BOUNDS))
        (late / "PROFILE.json").write_bytes(profile_v3)

        self.v1 = {"profile": hashlib.sha256(profile_v1).hexdigest(),
                   "tools": hashlib.sha256(tool_raw).hexdigest(),
                   "compiler": hashlib.sha256(compiler_v1).hexdigest()}
        self.v3 = {"profile": hashlib.sha256(profile_v3).hexdigest(),
                   "tools": hashlib.sha256(tool_raw).hexdigest(),
                   "compiler": hashlib.sha256(compiler_v3).hexdigest()}
        self.v1_packet = packet_for(self.v1["profile"])
        self.v3_packet = packet_for(self.v3["profile"])
        self.v1_receipt = self.receipt(self.v1, V1_BOUNDS)
        self.v3_receipt = self.receipt(self.v3, V3_BOUNDS)
        self.loader = InstalledRenderAuthorityLoader(
            self.resources, data_root=self.data_root, clock=lambda: self.now,
            hardware_identity=lambda: {"chip": "Apple M4", "memory_gb": 16},
        )

    def receipt(self, digests: dict, bounds: dict) -> dict:
        document = {
            "format": "studio.render.qualification-receipt.v1",
            "production_eligible": False,
            "profile_digest": digests["profile"],
            "tools_digest": digests["tools"],
            "compiler_digest": digests["compiler"],
            "hardware_class": "m4-16gb",
            "selected_tier": "hardware-h264",
            "exact_encoder": "h264_videotoolbox",
            "font_support": False,
            "bounds": {
                "max_rss_bytes": bounds["process_tree_rss_limit_bytes"],
                "max_wall_seconds": bounds["cumulative_wall_seconds"],
                "min_available_memory_bytes": 2_000_000_000,
                "output_limit_bytes": bounds["output_limit_bytes"],
            },
            "files": self.tool_files,
        }
        document["qualification_digest"] = hashlib.sha256(compact(document)).hexdigest()
        return document

    @staticmethod
    def enabled_record(document: dict) -> dict:
        return {
            **document, "intent_id": INTENT_ID,
            "measured": {
                "peak_rss_bytes": 1, "wall_seconds": 1, "min_free_bytes": 1,
                "output_bytes": 1, "hub_job_id": "job-0", "receipt_digest": "5" * 64,
            },
        }

    @staticmethod
    def opt_in(enabled: bool) -> dict:
        return {
            "format": "studio.render.accept-unqualified-setting.v1",
            "enabled": enabled, "encode": dict(DEFAULT_OPT_IN_ENCODE),
            "set_by": "owner", "set_at": 900,
        }

    def write_record(self, *names: str, value) -> Path:
        directory = self.data_root / "render-authority"
        for name in names[:-1]:
            directory = directory / name
            directory.mkdir(mode=0o700, exist_ok=True)
            directory.chmod(0o700)
        path = directory / names[-1]
        path.unlink(missing_ok=True)
        path.write_bytes(compact(value))
        path.chmod(0o600)
        return path

    def executor(self) -> RenderExecutor:
        return RenderExecutor(
            runtime=SimpleNamespace(qualification_consumed=lambda _intent: True),
            assets=None, authority=self.loader, machine="local",
            broker_api=FakeBroker(), available_memory=lambda: 4_000_000_000,
        )

    def tree_state(self) -> dict:
        return {
            str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(self.root.rglob("*")) if path.is_file()
        }


class ScopedCapabilityTests(ScopedCase):
    def test_row_pins_the_installed_v3_triple_and_zero_capacity(self) -> None:
        row, reason = self.executor().late_label_capability()
        self.assertIsNone(reason)
        self.assertEqual(row["profile_digest"], self.v3["profile"])
        self.assertEqual(row["tools_digest"], self.v3["tools"])
        self.assertEqual(row["compiler_digest"], self.v3["compiler"])
        self.assertEqual(row["profile_execution_bounds"]["max_assets"], 4)
        self.assertEqual(row["available_capacity"], {"slots": 0, "slots_total": 1})
        self.assertFalse(row["production_enabled"])
        self.assertFalse(row["production_eligible"])
        self.assertEqual(row["qualification_candidates"], [])
        self.assertTrue(BASE_ROW_KEYS <= set(row), set(row))
        # Never the v1 grant keys: opt-in evidence and encode support stay out of scope.
        self.assertNotIn("accepted_unqualified", row)
        self.assertNotIn("encode_support", row)

    def test_granted_v3_record_opens_capacity_in_scope(self) -> None:
        self.write_record("enabled", "m4-16gb.json", value=self.enabled_record(self.v3_receipt))
        row, reason = self.executor().late_label_capability()
        self.assertIsNone(reason)
        self.assertTrue(row["production_enabled"])
        self.assertTrue(row["production_eligible"])
        self.assertEqual(row["available_capacity"], {"slots": 1, "slots_total": 1})
        self.assertIsNotNone(self.loader.late_label_snapshot())

    def test_invalid_or_missing_triple_is_null_with_a_loud_reason(self) -> None:
        executor = self.executor()
        (self.tools_root / "late-label").rename(self.tools_root / "late-label-gone")
        row, reason = executor.late_label_capability()
        self.assertIsNone(row)
        self.assertIsInstance(reason, str)
        self.assertTrue(reason)

        (self.tools_root / "late-label-gone").rename(self.tools_root / "late-label")
        (self.tools_root / "late-label/COMPILER.json").write_bytes(b"{not json")
        row, reason = executor.late_label_capability()
        self.assertIsNone(row)
        self.assertTrue(reason)

        # A v1-shaped profile dropped into the late-label slot is not a v3 scope either.
        shutil.copyfile(self.tools_root / "PROFILE.json",
                        self.tools_root / "late-label/PROFILE.json")
        row, reason = executor.late_label_capability()
        self.assertIsNone(row)
        self.assertTrue(reason)
        self.assertIsNone(self.loader.late_label_profile_digest())

    def test_owner_off_stays_off_in_scope(self) -> None:
        for record in (None, self.opt_in(False)):
            with self.subTest(opt_in=record):
                path = self.data_root / "render-authority/opt-in/unqualified.json"
                if record is None:
                    path.unlink(missing_ok=True)
                else:
                    self.write_record("opt-in", "unqualified.json", value=record)
                row, reason = self.executor().late_label_capability()
                self.assertIsNone(reason)
                self.assertFalse(row["production_enabled"])
                self.assertEqual(row["available_capacity"], {"slots": 0, "slots_total": 1})
                self.assertIsNone(self.loader.late_label_snapshot())

    def test_owner_opt_in_on_is_still_never_v3_production(self) -> None:
        # A v1 opt-in grant on: the legacy scope offers it, the v3 scope refuses it.
        self.write_record("opt-in", "unqualified.json", value=self.opt_in(True))
        self.assertIsNotNone(self.loader.snapshot())
        self.assertIsNone(self.loader.late_label_snapshot())
        row, reason = self.executor().late_label_capability()
        self.assertIsNone(reason)
        self.assertFalse(row["production_enabled"])
        self.assertEqual(row["available_capacity"], {"slots": 0, "slots_total": 1})

    def test_capability_and_probe_lookups_write_nothing(self) -> None:
        before = self.tree_state()
        executor = self.executor()
        executor.late_label_capability()
        self.loader.late_label_snapshot()
        self.loader.probe_authority(late_labels=True)
        self.loader.late_label_profile_digest()
        self.assertEqual(self.tree_state(), before)


class PacketProfileSelectionTests(ScopedCase):
    def test_submitting_packet_selects_its_own_profile_authority(self) -> None:
        self.assertIsNone(self.loader.authority_for_packet(self.v3_packet))
        self.write_record("enabled", "m4-16gb.json", value=self.enabled_record(self.v3_receipt))

        v3_authority = self.loader.authority_for_packet(self.v3_packet)
        self.assertIsNotNone(v3_authority)
        self.assertEqual(v3_authority.qualification.profile_digest, self.v3["profile"])
        self.assertTrue(v3_authority.qualification.production_eligible)

        resolved = self.loader.resolve(self.v3_packet)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.profile_digest, self.v3["profile"])
        self.assertEqual(self.loader.resolve_probed(self.v3_packet).profile_digest,
                         self.v3["profile"])

        # The installed v3 grant never answers a v1-profile packet.
        self.assertIsNone(self.loader.authority_for_packet(self.v1_packet))

    def test_legacy_enabled_record_never_answers_a_v3_packet(self) -> None:
        self.write_record("enabled", "m4-16gb.json", value=self.enabled_record(self.v1_receipt))
        self.assertIsNotNone(self.loader.snapshot())
        self.assertIsNone(self.loader.late_label_snapshot())
        self.assertIsNone(self.loader.authority_for_packet(self.v3_packet))
        self.assertIsNone(self.loader.resolve(self.v3_packet))
        self.assertIsNone(self.loader.resolve_probed(self.v3_packet))

    def test_unqualified_opt_in_never_answers_a_v3_packet(self) -> None:
        self.write_record("opt-in", "unqualified.json", value=self.opt_in(True))
        self.assertIsNotNone(self.loader.snapshot())
        self.assertIsNone(self.loader.authority_for_packet(self.v3_packet))
        self.assertIsNone(self.loader.resolve(self.v3_packet))

    def test_published_bound_is_four_and_a_reveal_over_it_refuses(self) -> None:
        profile = json.loads((self.tools_root / "late-label/PROFILE.json").read_bytes())
        self.assertEqual(profile["execution_bounds"]["max_assets"], 4)
        row, _reason = self.executor().late_label_capability()
        self.assertEqual(row["profile_execution_bounds"]["max_assets"], 4)
        # A packet's manifest over the bound is refused by the same check the v3
        # authority runs; a reveal (extra manifest rows) is exactly that case.
        within = copy.deepcopy(self.v3_packet)
        over = copy.deepcopy(self.v3_packet)
        over["manifest"] = [dict(row_item) for row_item in over["manifest"]]
        while len(over["manifest"]) < 5:
            over["manifest"].append(dict(over["manifest"][-1]))
        self.assertTrue(InstalledRenderAuthorityLoader._profile_allows(within, V3_BOUNDS))
        self.assertFalse(InstalledRenderAuthorityLoader._profile_allows(over, V3_BOUNDS))


class ProbeScopeTests(ScopedCase):
    def test_scope_view_reads_only_v3_evidence(self) -> None:
        scope = LateLabelProbeScope(self.loader)
        self.assertIsNone(scope.snapshot())
        authority = scope.probe_authority()
        self.assertIsNotNone(authority)
        self.assertEqual(authority.qualification.profile_digest, self.v3["profile"])
        self.assertEqual(authority.qualification.compiler_digest, self.v3["compiler"])
        self.assertFalse(authority.qualification.production_eligible)

        # Legacy records and grants stay visible only to the legacy scope.
        self.write_record("enabled", "m4-16gb.json", value=self.enabled_record(self.v1_receipt))
        self.write_record("opt-in", "unqualified.json", value=self.opt_in(True))
        legacy = self.loader.snapshot()
        self.assertIsNotNone(legacy)
        self.assertEqual(legacy.qualification.profile_digest, self.v1["profile"])
        self.assertIsNone(scope.snapshot())
        # The legacy probe root still builds a v1 identity when no scope is asked for.
        self.assertEqual(self.loader.probe_authority().qualification.profile_digest,
                         self.v1["profile"])
        self.assertEqual(scope.probe_authority().qualification.profile_digest,
                         self.v3["profile"])


class _State:
    @staticmethod
    def make(encode, digest="b" * 64) -> dict:
        return {"format": "studio.render.encode-probe-state.v1",
                "requested_encode": encode, "state": "supported",
                "probe_key": "a" * 64, "reason": None,
                "evidence": {}, "authority_digest": digest}


class EncodeProbeScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[str] = []
        state = lambda encode: _State.make(encode)

        async def probe(encode, attempt=None):
            self.events.append("scoped-probe")
            return state(encode)

        async def legacy_probe(encode, attempt=None):
            self.events.append("legacy-probe")
            return state(encode)

        def legacy_inspect(encode):
            self.events.append("legacy-inspect")
            return state(encode)

        def scoped_inspect(encode):
            self.events.append("scoped-inspect")
            return state(encode)

        self.loader = SimpleNamespace(
            encode_probe_control=SimpleNamespace(inspect=legacy_inspect, probe=legacy_probe),
            late_label_encode_probe_control=SimpleNamespace(
                inspect=scoped_inspect, probe=probe),
            migrate_probed_grant=lambda: self.events.append("migrate"),
        )
        app = FastAPI()
        register_routes(
            app, SimpleNamespace(authority_loader=self.loader, executor=None),
            hub_token=HUB_TOKEN, fleet_token=lambda: "fleet-token",
            settings=lambda: {"site_id": "site", "controller_id": "controller"},
        )
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.path = "/api/hub/final-renders/encode-probes"
        self.headers = {"X-Hub-Token": HUB_TOKEN}
        self.encode = json.dumps(DEFAULT_OPT_IN_ENCODE)

    def test_no_contract_keeps_the_legacy_control_and_response(self) -> None:
        response = self.client.get(self.path, headers=self.headers,
                                   params={"requested_encode": self.encode})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.events, ["legacy-inspect"])
        self.assertIn("execution_authority", response.json())
        self.client.post(self.path, headers=self.headers, json={
            "format": "studio.render.encode-probe-request.v1",
            "requested_encode": DEFAULT_OPT_IN_ENCODE})
        self.assertEqual(self.events, ["legacy-inspect", "legacy-probe", "migrate"])

    def test_the_single_contract_selects_the_scoped_control(self) -> None:
        response = self.client.get(
            self.path, headers=self.headers,
            params={"requested_encode": self.encode,
                    "requested_contract": LATE_LABEL_CONTRACT})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.events, ["scoped-inspect"])
        # No stored v3 grant exists: the scoped answer never borrows the v1 execution bind.
        self.loader.execution_for_encode = lambda encode: (_ for _ in ()).throw(
            AssertionError("scoped probe must not read the v1 execution selector"))
        response = self.client.get(
            self.path, headers=self.headers,
            params={"requested_encode": self.encode,
                    "requested_contract": LATE_LABEL_CONTRACT})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["execution_authority"])

    def test_scoped_post_never_migrates_the_v1_grant(self) -> None:
        response = self.client.post(
            self.path, headers=self.headers, params={"requested_contract": LATE_LABEL_CONTRACT},
            json={"format": "studio.render.encode-probe-request.v1",
                  "requested_encode": DEFAULT_OPT_IN_ENCODE})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.events, ["scoped-probe"])
        self.assertIsNone(response.json()["execution_authority"])

    def test_malformed_or_unknown_contract_is_refused(self) -> None:
        for params in (
            {"requested_encode": self.encode,
             "requested_contract": "studio.render.late-label.v4"},
            {"requested_encode": self.encode, "requested_contract": ""},
            {"requested_contract": LATE_LABEL_CONTRACT},
        ):
            with self.subTest(params=params):
                response = self.client.get(self.path, headers=self.headers, params=params)
                self.assertEqual(response.status_code, 400)
        duplicate = [("requested_encode", self.encode),
                     ("requested_contract", LATE_LABEL_CONTRACT),
                     ("requested_contract", LATE_LABEL_CONTRACT)]
        self.assertEqual(self.client.get(self.path, headers=self.headers,
                                         params=duplicate).status_code, 400)
        post_bad = self.client.post(
            self.path, headers=self.headers, params={"requested_contract": "studio.render.v9"},
            json={"format": "studio.render.encode-probe-request.v1",
                  "requested_encode": DEFAULT_OPT_IN_ENCODE})
        self.assertEqual(post_bad.status_code, 400)
        self.assertEqual(self.events, [])

    def test_scoped_control_absent_is_loud_not_legacy(self) -> None:
        del self.loader.late_label_encode_probe_control
        response = self.client.get(
            self.path, headers=self.headers,
            params={"requested_encode": self.encode,
                    "requested_contract": LATE_LABEL_CONTRACT})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.events, [])


class CapabilitiesRouteTests(ScopedCase):
    def client(self, executor) -> TestClient:
        app = FastAPI()
        register_routes(
            app, SimpleNamespace(authority_loader=self.loader, executor=executor),
            hub_token=HUB_TOKEN, fleet_token=lambda: "fleet-token",
            settings=lambda: {"site_id": "site", "controller_id": "controller"},
        )
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def path(self) -> str:
        return "/api/hub/final-renders/capabilities"

    def test_envelope_is_closed_read_only_and_pinned(self) -> None:
        client = self.client(self.executor())
        before = self.tree_state()
        response = client.get(self.path(), headers={"X-Hub-Token": HUB_TOKEN},
                              params={"requested_contract": LATE_LABEL_CONTRACT})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.tree_state(), before, "capabilities GET must write nothing")
        body = response.json()
        self.assertEqual(set(body), {"format", "requested_contract", "server_time",
                                     "worker", "reason"})
        self.assertEqual(body["format"], LATE_LABEL_CAPABILITIES_FORMAT)
        self.assertEqual(body["requested_contract"], LATE_LABEL_CONTRACT)
        self.assertIs(type(body["server_time"]), int)
        self.assertGreater(body["server_time"], 1)
        self.assertIsNone(body["reason"])
        self.assertEqual(body["worker"]["profile_digest"], self.v3["profile"])
        self.assertEqual(body["worker"]["compiler_digest"], self.v3["compiler"])
        self.assertEqual(body["worker"]["tools_digest"], self.v3["tools"])

    def test_null_worker_carries_a_nonempty_reason(self) -> None:
        no_executor = self.client(None).get(
            self.path(), headers={"X-Hub-Token": HUB_TOKEN},
            params={"requested_contract": LATE_LABEL_CONTRACT})
        self.assertEqual(no_executor.status_code, 200, no_executor.text)
        self.assertIsNone(no_executor.json()["worker"])
        self.assertTrue(no_executor.json()["reason"])

        (self.tools_root / "late-label").rename(self.tools_root / "late-label-gone")
        broken = self.client(self.executor()).get(
            self.path(), headers={"X-Hub-Token": HUB_TOKEN},
            params={"requested_contract": LATE_LABEL_CONTRACT})
        self.assertEqual(broken.status_code, 200, broken.text)
        self.assertIsNone(broken.json()["worker"])
        self.assertTrue(broken.json()["reason"])

    def test_unknown_duplicate_or_missing_contract_is_strict(self) -> None:
        client = self.client(self.executor())
        headers = {"X-Hub-Token": HUB_TOKEN}
        self.assertEqual(client.get(self.path(), headers=headers).status_code, 400)
        for contract in ("studio.render.late-label.v4", "", "studio.render.late-label.v3 "):
            with self.subTest(contract=contract):
                self.assertEqual(client.get(
                    self.path(), headers=headers,
                    params={"requested_contract": contract}).status_code, 400)
        duplicate = [("requested_contract", LATE_LABEL_CONTRACT)] * 2
        self.assertEqual(client.get(self.path(), headers=headers,
                                    params=duplicate).status_code, 400)
        extra = [("requested_contract", LATE_LABEL_CONTRACT), ("node", "x")]
        self.assertEqual(client.get(self.path(), headers=headers,
                                    params=extra).status_code, 400)
        self.assertEqual(client.get(self.path()).status_code, 401)

    def test_registered_paths_stay_fleet_only(self) -> None:
        self.assertTrue(auth.fleet_path_allowed("GET", self.path()))
        self.assertFalse(auth.node_path_allowed("GET", self.path()))
        relay = "/api/hub/fleet/nodes/leaf/final-renders/capabilities"
        self.assertTrue(auth.fleet_path_allowed("GET", relay))
        self.assertFalse(auth.node_path_allowed("GET", relay))


class RelayScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple] = []

        async def relay(node_id, operation, **kwargs):
            self.calls.append((node_id, operation, kwargs))
            return SimpleNamespace(status_code=200, headers={}, body=b"{}")

        app = FastAPI()
        register_relay_routes(
            app, relay, hub_token=HUB_TOKEN, fleet_token=lambda: "fleet-token",
            settings=lambda: {"site_id": "site", "controller_id": "controller"},
        )
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {"X-Hub-Token": HUB_TOKEN}
        self.encode = json.dumps(DEFAULT_OPT_IN_ENCODE)

    def test_registered_relay_query_is_preserved_exactly(self) -> None:
        response = self.client.get(
            "/api/hub/fleet/nodes/leaf/final-renders/capabilities",
            headers=self.headers, params={"requested_contract": LATE_LABEL_CONTRACT})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.calls), 1)
        node_id, operation, kwargs = self.calls[0]
        self.assertEqual(node_id, "leaf")
        self.assertEqual(operation, "late_label_capabilities")
        self.assertEqual(kwargs["identity"], "")
        self.assertEqual(kwargs["requested_contract"], LATE_LABEL_CONTRACT)
        self.assertNotIn("requested_encode", kwargs)

    def test_relay_probe_scope_travels_without_changing_legacy(self) -> None:
        path = "/api/hub/fleet/nodes/leaf/final-renders/encode-probes"
        legacy = self.client.get(path, headers=self.headers,
                                 params={"requested_encode": self.encode})
        self.assertEqual(legacy.status_code, 200, legacy.text)
        _, _, kwargs = self.calls[-1]
        self.assertEqual(kwargs["requested_encode"], self.encode)
        self.assertNotIn("requested_contract", kwargs)

        scoped = self.client.get(
            path, headers=self.headers,
            params={"requested_encode": self.encode,
                    "requested_contract": LATE_LABEL_CONTRACT})
        self.assertEqual(scoped.status_code, 200, scoped.text)
        _, operation, kwargs = self.calls[-1]
        self.assertEqual(operation, "encode_probe_get")
        self.assertEqual(kwargs["requested_contract"], LATE_LABEL_CONTRACT)

        posted = self.client.post(
            path, headers={**self.headers, "Content-Type": "application/json"},
            params={"requested_contract": LATE_LABEL_CONTRACT},
            json={"format": "studio.render.encode-probe-request.v1",
                  "requested_encode": DEFAULT_OPT_IN_ENCODE})
        self.assertEqual(posted.status_code, 200, posted.text)
        _, operation, kwargs = self.calls[-1]
        self.assertEqual(operation, "encode_probe_post")
        self.assertEqual(kwargs["requested_contract"], LATE_LABEL_CONTRACT)

    def test_malformed_relay_contract_never_reaches_the_relay(self) -> None:
        capabilities = "/api/hub/fleet/nodes/leaf/final-renders/capabilities"
        probes = "/api/hub/fleet/nodes/leaf/final-renders/encode-probes"
        for path, params in (
            (capabilities, None),
            (capabilities, {"requested_contract": "studio.render.late-label.v9"}),
            (probes, {"requested_encode": self.encode,
                      "requested_contract": "studio.render.late-label.v9"}),
        ):
            with self.subTest(path=path, params=params):
                response = self.client.get(path, headers=self.headers, params=params)
                self.assertEqual(response.status_code, 400)
        duplicate = [("requested_contract", LATE_LABEL_CONTRACT)] * 2
        self.assertEqual(self.client.get(capabilities, headers=self.headers,
                                         params=duplicate).status_code, 400)
        self.assertEqual(self.calls, [])

    def test_relay_segment_treats_capabilities_as_identity_less(self) -> None:
        self.assertEqual(_render_relay_segment("late_label_capabilities", ""), "")
        with self.assertRaises(ValueError):
            _render_relay_segment("late_label_capabilities", "job-1")


class ProbeShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_fences_and_drains_both_probe_scopes(self) -> None:
        tree = ast.parse((ROOT / "components/hub/standalone.py").read_text())
        shutdown = next(node for node in ast.walk(tree) if isinstance(node, ast.Try)
                        and node.finalbody
                        and any(isinstance(call, ast.Call)
                                and isinstance(call.func, ast.Attribute)
                                and call.func.attr == "begin_shutdown"
                                for call in ast.walk(node.finalbody[0])))
        events = []
        both_closing = asyncio.Event()

        class Control:
            async def stop(self):
                events.append("closing")
                if events.count("closing") == 2:
                    both_closing.set()
                await both_closing.wait()
                events.append("drained")

        namespace = {
            "_asyncio": asyncio,
            "final_render_executor": SimpleNamespace(
                begin_shutdown=lambda: events.append("fenced")),
            "encode_probe_control": Control(),
            "late_label_probe_control": Control(),
        }
        wrapper = ast.parse("async def close(): pass")
        wrapper.body[0].body = copy.deepcopy(shutdown.finalbody[:2])
        exec(compile(ast.fix_missing_locations(wrapper), "<probe-shutdown>", "exec"), namespace)
        await asyncio.wait_for(namespace["close"](), timeout=1)
        self.assertEqual(events, ["fenced", "closing", "closing", "drained", "drained"])


class FrozenTriplePinTests(unittest.TestCase):
    def test_packaging_triple_is_exactly_the_frozen_pins(self) -> None:
        for name, digest in FROZEN_PINS.items():
            with self.subTest(name=name):
                self.assertEqual(file_sha(PACKAGING / name), digest)

    def test_profile_bound_keeps_max_assets_at_four(self) -> None:
        profile = json.loads((PACKAGING / "PROFILE.json").read_bytes())
        self.assertEqual(profile["format"], "studio.render.profile.v3")
        self.assertEqual(profile["implementation"], "story-classic-late-label-v3")
        self.assertEqual(profile["execution_bounds"]["max_assets"], 4)
        compiler = json.loads((PACKAGING / "COMPILER.json").read_bytes())
        self.assertEqual(compiler["implementation"], "hub-final-render-late-label-v3")


if __name__ == "__main__":
    unittest.main()
