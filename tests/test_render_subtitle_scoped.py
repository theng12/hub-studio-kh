#!/usr/bin/env python3
"""Scoped v4 subtitle capability publication: fake triple, frozen wire, no live fleet."""
from __future__ import annotations

import copy
import hashlib
import json
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
from components.hub.backend.render_encode import DEFAULT_OPT_IN_ENCODE  # noqa: E402
from components.hub.backend.render_hash_v1 import render_hash  # noqa: E402
from components.hub.backend.render_executor import (  # noqa: E402
    LATE_LABEL_MOTIONS,
    SUBTITLE_CAPABILITIES_FORMAT,
    SUBTITLE_CONTRACT,
    InstalledRenderAuthorityLoader,
    RenderExecutor,
    SubtitleProbeScope,
)
from components.hub.backend.render_service import (  # noqa: E402
    LATE_LABEL_CAPABILITIES_FORMAT,
    LATE_LABEL_CONTRACT,
    register_relay_routes,
    register_routes,
)

HUB_TOKEN = "subtitle-scope-hub"
INTENT_ID = "22222222-2222-2222-2222-222222222222"
BOUNDS = {
    "bounds_version": 2, "canvas_range": {"max_edge": 3840, "max_pixels": 8294400},
    "working_fps": [5], "max_duration_sec": 2.5, "max_operations": 32,
    "max_assets": 32, "max_asset_bytes": 1_000_000, "max_total_input_bytes": 2_000_000,
    "max_pixel_frames": 1_000_000, "scratch_reserve_bytes": 250_000,
    "output_limit_bytes": 1_000_000, "process_tree_rss_limit_bytes": 100_000_000,
    "cumulative_wall_seconds": 30, "max_subtitle_fonts": 8,
    "max_subtitle_font_bytes": 33_554_432, "max_subtitle_ass_bytes": 4_194_304,
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


class ScopedCase(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 1_000
        self.root = ROOT / "_trash/render-subtitle-scoped-testroots" / uuid4().hex
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
                "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
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
            "files": [{"name": name, "path": f"render-tools/{name}", **self.tool_files[name]}
                      for name in ("ffmpeg", "ffprobe")]}))
        backend = ROOT / "components/hub/backend"
        compiler_raw = compact({
            "format": "studio.render.compiler.v1",
            "implementation": "hub-final-render-subtitle-v1",
            "python_runtime_sha256": file_sha(sys.executable),
            "render_hash_sha256": file_sha(backend / "render_hash_v1.py"),
            "render_contract_sha256": file_sha(backend / "render_contract.py"),
            "final_render_compiler_sha256": file_sha(backend / "final_render_compiler.py"),
        })
        subtitle = self.tools_root / "subtitle"
        subtitle.mkdir()
        (subtitle / "TOOLS.json").write_bytes(tool_raw)
        (subtitle / "COMPILER.json").write_bytes(compiler_raw)
        profile_raw = compact({
            "format": "studio.render.profile.v4",
            "implementation": "story-classic-subtitle-v1",
            "recipe_namespace": "studio.render.final.v1",
            "compiler_artifact_sha256": hashlib.sha256(compiler_raw).hexdigest(),
            "tools_artifact_sha256": hashlib.sha256(tool_raw).hexdigest(),
            "combination_set": "retained-classic-subtitle-candidate-v1",
            "tier_policy": "story-final-chain-v1", "output_policy": "story-final-output-v1",
            "execution_bounds": copy.deepcopy(BOUNDS),
            "hardware_qualification_record_sha256": None,
            "fidelity_record_sha256": None, "production_eligible": False,
        })
        (subtitle / "PROFILE.json").write_bytes(profile_raw)
        self.v4 = {"profile": hashlib.sha256(profile_raw).hexdigest(),
                   "tools": hashlib.sha256(tool_raw).hexdigest(),
                   "compiler": hashlib.sha256(compiler_raw).hexdigest()}
        self.loader = InstalledRenderAuthorityLoader(
            self.resources, data_root=self.data_root, clock=lambda: self.now,
            hardware_identity=lambda: {"chip": "Apple M4", "memory_gb": 16})

    def receipt(self) -> dict:
        document = {
            "format": "studio.render.qualification-receipt.v1", "production_eligible": False,
            "profile_digest": self.v4["profile"], "tools_digest": self.v4["tools"],
            "compiler_digest": self.v4["compiler"], "hardware_class": "m4-16gb",
            "selected_tier": "hardware-h264", "exact_encoder": "h264_videotoolbox",
            "font_support": True,
            "bounds": {
                "max_rss_bytes": BOUNDS["process_tree_rss_limit_bytes"],
                "max_wall_seconds": BOUNDS["cumulative_wall_seconds"],
                "min_available_memory_bytes": 2_000_000_000,
                "output_limit_bytes": BOUNDS["output_limit_bytes"]},
            "files": self.tool_files,
        }
        document["qualification_digest"] = hashlib.sha256(compact(document)).hexdigest()
        return document

    def enabled(self) -> dict:
        return {**self.receipt(), "intent_id": INTENT_ID, "measured": {
            "peak_rss_bytes": 1, "wall_seconds": 1, "min_free_bytes": 1,
            "output_bytes": 1, "hub_job_id": "job-0", "receipt_digest": "5" * 64}}

    def write_enabled(self) -> None:
        directory = self.data_root / "render-authority/enabled"
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / "m4-16gb.json"
        path.write_bytes(compact(self.enabled()))
        path.chmod(0o600)

    def executor(self) -> RenderExecutor:
        return RenderExecutor(
            runtime=SimpleNamespace(qualification_consumed=lambda _intent: True),
            assets=None, authority=self.loader, machine="local",
            broker_api=SimpleNamespace(
                busy_machines=lambda: set(),
                render_dispatch_allowed=lambda *_args, **_kwargs: True),
            available_memory=lambda: 4_000_000_000)

    def tree_state(self) -> dict:
        return {str(path.relative_to(self.root)): file_sha(path)
                for path in sorted(self.root.rglob("*")) if path.is_file()}


class ScopedCapabilityTests(ScopedCase):
    def test_row_pins_installed_v4_triple_and_both_markers(self) -> None:
        row, reason = self.executor().subtitle_capability()
        self.assertIsNone(reason)
        self.assertEqual(row["profile_digest"], self.v4["profile"])
        self.assertEqual(row["tools_digest"], self.v4["tools"])
        self.assertEqual(row["compiler_digest"], self.v4["compiler"])
        self.assertTrue(row["font_support"])
        self.assertEqual(row["subtitles"], {"format": SUBTITLE_CONTRACT})
        self.assertEqual(row["late_labels"],
                         {"format": LATE_LABEL_CONTRACT, "motions": list(LATE_LABEL_MOTIONS)})
        self.assertEqual(row["available_capacity"], {"slots": 0, "slots_total": 1})
        self.assertFalse(row["production_enabled"])
        self.assertTrue(BASE_ROW_KEYS <= set(row))

    def test_granted_v4_record_opens_capacity(self) -> None:
        self.write_enabled()
        row, reason = self.executor().subtitle_capability()
        self.assertIsNone(reason)
        self.assertTrue(row["production_enabled"])
        self.assertTrue(row["production_eligible"])
        self.assertEqual(row["available_capacity"], {"slots": 1, "slots_total": 1})
        self.assertIsNotNone(self.loader.subtitle_snapshot())

    def test_absent_triple_is_negative_not_fabricated(self) -> None:
        (self.tools_root / "subtitle").rename(self.tools_root / "subtitle-gone")
        row, reason = self.executor().subtitle_capability()
        self.assertIsNone(row)
        self.assertTrue(reason)
        self.assertIsNone(self.loader.subtitle_profile_digest())
        self.assertIsNone(self.loader.probe_authority(subtitles=True))

    def test_other_scopes_never_answer_subtitles(self) -> None:
        (self.tools_root / "subtitle").rename(self.tools_root / "subtitle-gone")
        self.write_legacy_scope()
        # A v3 directory is not a v4 subtitle install, so the scope stays negative.
        self.assertIsNone(self.loader.subtitle_profile_digest())
        self.assertIsNone(self.loader.subtitle_snapshot())
        self.assertEqual(self.executor().subtitle_capability()[0], None)

    def write_legacy_scope(self) -> str:
        legacy = self.tools_root / "late-label"
        legacy.mkdir()
        (legacy / "TOOLS.json").write_bytes((self.tools_root / "TOOLS.json").read_bytes())
        (legacy / "COMPILER.json").write_bytes(compact({
            "format": "studio.render.compiler.v1",
            "implementation": "hub-final-render-late-label-v3",
            "python_runtime_sha256": file_sha(sys.executable),
            "render_hash_sha256": file_sha(ROOT / "components/hub/backend/render_late_label_v3/render_hash_v1.py"),
            "render_contract_sha256": file_sha(ROOT / "components/hub/backend/render_late_label_v3/render_contract.py"),
            "final_render_compiler_sha256": file_sha(ROOT / "components/hub/backend/render_late_label_v3/final_render_compiler.py"),
        }))
        (legacy / "PROFILE.json").write_bytes(compact({
            "format": "studio.render.profile.v3",
            "implementation": "story-classic-late-label-v3",
            "recipe_namespace": "studio.render.final.v1",
            "compiler_artifact_sha256": file_sha(legacy / "COMPILER.json"),
            "tools_artifact_sha256": file_sha(self.tools_root / "TOOLS.json"),
            "combination_set": "retained-classic-late-label-candidate-v3",
            "tier_policy": "story-final-chain-v1", "output_policy": "story-final-output-v1",
            "execution_bounds": {**{k: v for k, v in BOUNDS.items()
                                    if not k.startswith("max_subtitle") and k != "bounds_version"}},
            "hardware_qualification_record_sha256": None,
            "fidelity_record_sha256": None, "production_eligible": False,
        }))
        return file_sha(legacy / "PROFILE.json")

    def test_probe_scope_reads_only_v4(self) -> None:
        self.write_enabled()
        scope = SubtitleProbeScope(self.loader)
        authority = scope.probe_authority()
        self.assertEqual(authority.qualification.profile_digest, self.v4["profile"])
        self.assertEqual(scope.snapshot().qualification.profile_digest, self.v4["profile"])


class RouteTests(ScopedCase):
    def client(self, executor) -> TestClient:
        app = FastAPI()
        register_routes(
            app, SimpleNamespace(authority_loader=self.loader, executor=executor),
            hub_token=HUB_TOKEN, fleet_token=lambda: "fleet-token",
            settings=lambda: {"site_id": "site", "controller_id": "controller"})
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def test_subtitle_envelope_is_closed_and_pinned(self) -> None:
        path = "/api/hub/final-renders/capabilities"
        client = self.client(self.executor())
        before = self.tree_state()
        response = client.get(path, headers={"X-Hub-Token": HUB_TOKEN},
                              params={"requested_contract": SUBTITLE_CONTRACT})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.tree_state(), before)
        body = response.json()
        self.assertEqual(set(body), {"format", "requested_contract", "server_time",
                                     "worker", "reason"})
        self.assertEqual(body["format"], SUBTITLE_CAPABILITIES_FORMAT)
        self.assertEqual(body["requested_contract"], SUBTITLE_CONTRACT)
        self.assertIsNone(body["reason"])
        self.assertEqual(body["worker"]["subtitles"], {"format": SUBTITLE_CONTRACT})

    def test_late_label_read_stays_byte_identical(self) -> None:
        client = self.client(self.executor())
        body = client.get("/api/hub/final-renders/capabilities",
                          headers={"X-Hub-Token": HUB_TOKEN},
                          params={"requested_contract": LATE_LABEL_CONTRACT}).json()
        self.assertEqual(body["format"], LATE_LABEL_CAPABILITIES_FORMAT)
        self.assertIsNone(body["worker"])
        self.assertTrue(body["reason"])

    def test_unknown_duplicate_and_extra_queries_are_refused(self) -> None:
        client = self.client(self.executor())
        headers = {"X-Hub-Token": HUB_TOKEN}
        path = "/api/hub/final-renders/capabilities"
        self.assertEqual(client.get(path, headers=headers).status_code, 400)
        for params in ({"requested_contract": "studio.render.subtitle.v2"},
                       [("requested_contract", SUBTITLE_CONTRACT)] * 2,
                       [("requested_contract", SUBTITLE_CONTRACT), ("node", "x")]):
            with self.subTest(params=params):
                self.assertEqual(client.get(path, headers=headers,
                                            params=params).status_code, 400)

    def test_relay_preserves_subtitle_contract(self) -> None:
        calls = []

        async def relay(node_id, operation, **kwargs):
            calls.append((node_id, operation, kwargs))
            return SimpleNamespace(status_code=200, headers={}, body=b"{}")

        app = FastAPI()
        register_relay_routes(
            app, relay, hub_token=HUB_TOKEN, fleet_token=lambda: "fleet-token",
            settings=lambda: {"site_id": "site", "controller_id": "controller"})
        client = TestClient(app)
        self.addCleanup(client.close)
        response = client.get(
            "/api/hub/fleet/nodes/leaf/final-renders/capabilities",
            headers={"X-Hub-Token": HUB_TOKEN},
            params={"requested_contract": SUBTITLE_CONTRACT})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(calls[0][2]["requested_contract"], SUBTITLE_CONTRACT)
        probe = "/api/hub/fleet/nodes/leaf/final-renders/encode-probes"
        encode = json.dumps(DEFAULT_OPT_IN_ENCODE)
        response = client.get(probe, headers={"X-Hub-Token": HUB_TOKEN},
                              params={"requested_encode": encode,
                                      "requested_contract": SUBTITLE_CONTRACT})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(calls[-1][2]["requested_contract"], SUBTITLE_CONTRACT)
        self.assertTrue(auth.fleet_path_allowed(
            "GET", "/api/hub/fleet/nodes/leaf/final-renders/capabilities"))


if __name__ == "__main__":
    unittest.main()
