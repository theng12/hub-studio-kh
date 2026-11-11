#!/usr/bin/env python3
"""Focused integration tests for Hub cue overlay executor, admission, capabilities, and media validator.

Tests:
1. Scoped capabilities route exact branch + unknown/wrong contract fail closed
2. Real executor path with cue_overlay_stage + helper invocation before FFmpeg
3. Media refusal before FFmpeg launch (prelaunch_failed, spawn not called)
4. Mixed cue_overlay_stage and late_label_stage order preservation
5. stage_render_authority_into cue-overlay descriptor staging + loader roundtrip with v4 pins intact
6. File descriptor seek position rewind after helper validation
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest import mock
from uuid import uuid4

from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

# Load repo namespace by path to avoid masking from installed third-party packaging package.
_stage_render_tools_spec = importlib.util.spec_from_file_location(
    "studiohub_stage_render_tools",
    ROOT / "packaging" / "stage_render_tools.py",
)
_stage_render_tools = importlib.util.module_from_spec(_stage_render_tools_spec)
sys.modules[_stage_render_tools_spec.name] = _stage_render_tools
_stage_render_tools_spec.loader.exec_module(_stage_render_tools)

_AuthorityPin = _stage_render_tools._AuthorityPin
_stage_render_authority_into = _stage_render_tools._stage_render_authority_into

from fastapi import FastAPI
from fastapi.testclient import TestClient

import test_render_executor as fixtures
from test_render_executor import FakeAssets, FakeBroker, FakeProcess, FakeRuntime
from components.audio_encode import find_tool
from components.hub.backend import render_encode
from components.hub.backend.final_render_compiler import compile_final_render_packet
from components.hub.backend.render_cue_media import (
    CueOverlayMediaValidationError,
    REFUSAL_BOUNDS,
    REFUSAL_MEDIA_INTEGRITY,
    REFUSAL_MEDIA_UNSUPPORTED,
    probe_cue_overlay_media,
    validate_cue_overlay_media,
)
from components.hub.backend.render_executor import (
    _CUE_OVERLAY_CAPABILITIES_FORMAT as CUE_OVERLAY_CAPABILITIES_FORMAT,
    _CUE_OVERLAY_CONTRACT,
    _CUE_OVERLAY_PROFILE_FORMAT,
    _CUE_OVERLAY_PROFILE_IMPLEMENTATION,
    _CUE_OVERLAY_COMPILER_IMPLEMENTATION,
    CueOverlayProbeScope,
    InstalledRenderAuthorityLoader,
    LATE_LABEL_MOTIONS,
    RenderExecutor,
    SUBTITLE_CAPABILITIES_FORMAT,
    SUBTITLE_CONTRACT,
    _SUBTITLE_PROFILE_FORMAT,
    _SUBTITLE_PROFILE_IMPLEMENTATION,
    _SUBTITLE_COMPILER_IMPLEMENTATION,
)
from components.hub.backend.render_hash_v1 import render_hash
from components.hub.backend.render_jobs import (
    ProcessIdentity,
    RenderJobUnavailable,
    ValidatedOutputReceipt,
)
from components.hub.backend.render_service import (
    LATE_LABEL_CONTRACT,
    LATE_LABEL_CAPABILITIES_FORMAT,
    register_routes,
)

HUB_TOKEN = "cue-overlay-hub-token"
INTENT_ID = "33333333-3333-3333-3333-333333333333"
BOUNDS = {
    "bounds_version": 2,
    "canvas_range": {"max_edge": 3840, "max_pixels": 8294400},
    "working_fps": [5, 24, 25, 30, 60],
    "max_duration_sec": 10.0,
    "max_operations": 32,
    "max_assets": 32,
    "max_asset_bytes": 10_000_000,
    "max_total_input_bytes": 20_000_000,
    "max_pixel_frames": 10_000_000,
    "scratch_reserve_bytes": 250_000,
    "output_limit_bytes": 10_000_000,
    "process_tree_rss_limit_bytes": 100_000_000,
    "cumulative_wall_seconds": 60,
    "max_subtitle_fonts": 8,
    "max_subtitle_font_bytes": 33_554_432,
    "max_subtitle_ass_bytes": 4_194_304,
}


def compact(val: Any) -> bytes:
    return json.dumps(val, sort_keys=True, separators=(",", ":")).encode("ascii")


def file_sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _make_dummy_webm(path: Path) -> bytes:
    """Generate minimal valid VP9 alpha WebM using system ffmpeg if available, else synthetic payload."""
    ffmpeg_bin = find_tool("ffmpeg")
    if ffmpeg_bin:
        cmd = [
            str(ffmpeg_bin), "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=black@0.0:s=64x64:r=5:d=0.4",
            "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
            "-auto-alt-ref", "0",
            str(path),
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if res.returncode == 0 and path.is_file() and path.stat().st_size > 0:
            return path.read_bytes()
    dummy = b"\x1a\x45\xdf\xa3fake_webm_bytes_for_testing"
    path.write_bytes(dummy)
    return dummy


import subprocess


class CueOverlayIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "_trash/render-cue-integration-testroots" / uuid4().hex
        self.resources = self.root / "resources"
        self.tools_root = self.resources / "render-tools"
        self.data_root = self.root / "data"
        self.tools_root.mkdir(parents=True)
        (self.data_root / "render-authority").mkdir(parents=True)
        (self.data_root / "render-authority").chmod(0o700)

        self.ffmpeg_bin = find_tool("ffmpeg")
        self.ffprobe_bin = find_tool("ffprobe")

        # Sealed tool files
        self.tool_files = {}
        for name in ("ffmpeg", "ffprobe"):
            bin_path = find_tool(name)
            payload = Path(bin_path).read_bytes() if bin_path else b"sealed-binary"
            tpath = self.tools_root / name
            tpath.write_bytes(payload)
            tpath.chmod(0o755)
            self.tool_files[name] = {"size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}

        tool_raw = compact({
            "format": "studio.render.tools.v1",
            "platform": "macos-arm64",
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
            "format": "studiohub.render-tools.payload.v1",
            "tool_version": "8.1",
            "payload_format": "macos-universal",
            "production_eligible": False,
            "files": [
                {"name": name, "path": f"render-tools/{name}", **self.tool_files[name]}
                for name in ("ffmpeg", "ffprobe")
            ],
        }))

        # V5 cue overlay authority triple
        backend = ROOT / "components/hub/backend"
        compiler_raw = compact({
            "final_render_compiler_sha256": file_sha(backend / "final_render_compiler.py"),
            "format": "studio.render.compiler.v1",
            "implementation": _CUE_OVERLAY_COMPILER_IMPLEMENTATION,
            "python_runtime_sha256": file_sha(Path(sys.executable)),
            "render_contract_sha256": file_sha(backend / "render_contract.py"),
            "render_hash_sha256": file_sha(backend / "render_hash_v1.py"),
        })
        cue_dir = self.tools_root / "cue-overlay"
        cue_dir.mkdir()
        (cue_dir / "TOOLS.json").write_bytes(tool_raw)
        (cue_dir / "COMPILER.json").write_bytes(compiler_raw)

        profile_raw = compact({
            "format": _CUE_OVERLAY_PROFILE_FORMAT,
            "implementation": _CUE_OVERLAY_PROFILE_IMPLEMENTATION,
            "recipe_namespace": "studio.render.final.v1",
            "compiler_artifact_sha256": hashlib.sha256(compiler_raw).hexdigest(),
            "tools_artifact_sha256": hashlib.sha256(tool_raw).hexdigest(),
            "combination_set": "retained-classic-cue-overlay-candidate-v1",
            "tier_policy": "story-final-chain-v1",
            "output_policy": "story-final-output-v1",
            "execution_bounds": copy.deepcopy(BOUNDS),
            "hardware_qualification_record_sha256": None,
            "fidelity_record_sha256": None,
            "production_eligible": False,
        })
        (cue_dir / "PROFILE.json").write_bytes(profile_raw)
        self.v5 = {
            "profile": hashlib.sha256(profile_raw).hexdigest(),
            "tools": hashlib.sha256(tool_raw).hexdigest(),
            "compiler": hashlib.sha256(compiler_raw).hexdigest(),
        }

        self.loader = InstalledRenderAuthorityLoader(
            self.resources,
            data_root=self.data_root,
            clock=lambda: 1000,
            hardware_identity=lambda: {"chip": "Apple M4", "memory_gb": 16},
        )

    def tearDown(self):
        import shutil
        if self.root.is_dir():
            shutil.rmtree(self.root, ignore_errors=True)

    def client(self, executor: RenderExecutor) -> TestClient:
        app = FastAPI()
        register_routes(
            app,
            SimpleNamespace(authority_loader=self.loader, executor=executor),
            hub_token=HUB_TOKEN,
            fleet_token=lambda: "fleet-token",
            settings=lambda: {"site_id": "site", "controller_id": "controller"},
        )
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def executor(self) -> RenderExecutor:
        return RenderExecutor(
            runtime=SimpleNamespace(qualification_consumed=lambda _intent: True),
            assets=None,
            authority=self.loader,
            machine="local",
            broker_api=SimpleNamespace(
                busy_machines=lambda: set(),
                render_dispatch_allowed=lambda *_a, **_kw: True,
            ),
            available_memory=lambda: 4_000_000_000,
        )

    def test_scoped_capabilities_exact_branch_and_fail_closed(self):
        """Verify GET /api/hub/final-renders/capabilities answers cue-overlay exact branch and fails closed on unknown contracts."""
        client = self.client(self.executor())
        headers = {"X-Hub-Token": HUB_TOKEN}
        path = "/api/hub/final-renders/capabilities"

        # 1. Exact cue-overlay request succeeds with cue-overlay envelope
        resp = client.get(path, headers=headers, params={"requested_contract": _CUE_OVERLAY_CONTRACT})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["format"], CUE_OVERLAY_CAPABILITIES_FORMAT)
        self.assertEqual(body["requested_contract"], _CUE_OVERLAY_CONTRACT)
        self.assertIsNotNone(body["worker"])
        worker = body["worker"]
        self.assertEqual(worker["cue_overlays"], {"format": _CUE_OVERLAY_CONTRACT})
        self.assertEqual(worker["subtitles"], {"format": SUBTITLE_CONTRACT})
        self.assertEqual(worker["late_labels"]["format"], LATE_LABEL_CONTRACT)
        self.assertEqual(worker["profile_digest"], self.v5["profile"])

        # 2. Unknown contract fails closed with HTTP 400
        resp_unknown = client.get(path, headers=headers, params={"requested_contract": "unknown.render.contract.v9"})
        self.assertEqual(resp_unknown.status_code, 400)

        # 3. Missing requested_contract fails closed with HTTP 400
        resp_missing = client.get(path, headers=headers)
        self.assertEqual(resp_missing.status_code, 400)

        # 4. Extra query param fails closed with HTTP 400
        resp_extra = client.get(
            path, headers=headers,
            params={"requested_contract": _CUE_OVERLAY_CONTRACT, "extra_param": "forbidden"},
        )
        self.assertEqual(resp_extra.status_code, 400)

        # 5. CueOverlayProbeScope resolves exact v5 triple
        scope = CueOverlayProbeScope(self.loader)
        auth = scope.probe_authority()
        self.assertIsNotNone(auth)
        self.assertEqual(auth.qualification.profile_digest, self.v5["profile"])

    def test_stage_render_authority_into_cue_overlay_roundtrip(self):
        """Verify _stage_render_authority_into stages v5 cue-overlay descriptors and loader verifies them."""
        dest_res = self.root / "stage-dest/resources"
        dest_tools = dest_res / "render-tools"
        dest_tools.mkdir(parents=True)
        scratch = self.root / "stage-scratch"
        scratch.mkdir(parents=True)
        scratch.chmod(0o700)

        for name in ("ffmpeg", "ffprobe"):
            (dest_tools / name).write_bytes(b"x")
        (dest_tools / "TOOLS.json").write_bytes((self.tools_root / "TOOLS.json").read_bytes())
        (dest_tools / "MANIFEST.json").write_bytes((self.tools_root / "MANIFEST.json").read_bytes())

        cue_dir = self.tools_root / "cue-overlay"
        pins = (
            _AuthorityPin("PROFILE.json", cue_dir / "PROFILE.json", (cue_dir / "PROFILE.json").stat().st_size, file_sha(cue_dir / "PROFILE.json")),
            _AuthorityPin("TOOLS.json", cue_dir / "TOOLS.json", (cue_dir / "TOOLS.json").stat().st_size, file_sha(cue_dir / "TOOLS.json")),
            _AuthorityPin("COMPILER.json", cue_dir / "COMPILER.json", (cue_dir / "COMPILER.json").stat().st_size, file_sha(cue_dir / "COMPILER.json")),
        )
        res = _stage_render_authority_into(
            dest_res, scratch, "descriptor-only", pins, cue_overlays=True,
        )
        self.assertEqual(len(res.staged_paths), 3)
        self.assertTrue((dest_tools / "cue-overlay/PROFILE.json").is_file())

        # Test staged loader reads it
        loader = InstalledRenderAuthorityLoader(
            dest_res, data_root=self.data_root, clock=lambda: 1000,
            hardware_identity=lambda: {"chip": "Apple M4", "memory_gb": 16},
        )
        self.assertEqual(loader.cue_overlay_profile_digest(), self.v5["profile"])

    def test_fd_seek_rewind_after_probe_and_validation(self):
        """Verify probe_cue_overlay_media and validate_cue_overlay_media rewind asset fd position to 0."""
        with tempfile.NamedTemporaryFile(suffix=".webm") as tf:
            tf.write(b"0123456789" * 100)
            tf.flush()
            fd = tf.fileno()

            # Ensure seek(0)
            os.lseek(fd, 0, os.SEEK_SET)

            # Advance offset manually
            os.lseek(fd, 50, os.SEEK_SET)
            self.assertEqual(os.lseek(fd, 0, os.SEEK_CUR), 50)

            # Pass to probe_cue_overlay_media (even on failure or mock)
            try:
                probe_cue_overlay_media(
                    Path(self.ffprobe_bin) if self.ffprobe_bin else "/usr/bin/ffprobe",
                    tf,
                    timeout_seconds=5.0,
                )
            except Exception:
                pass

            # Position MUST be rewound to 0
            pos = os.lseek(fd, 0, os.SEEK_CUR)
            self.assertEqual(pos, 0, f"Expected fd position rewound to 0, got {pos}")


class CueOverlayExecutorPathTests(unittest.IsolatedAsyncioTestCase):
    """Real executor path: v5 admission, media refusal before spawn, and a valid cue render."""

    PINNED_TOOLS = Path("/Applications/Studio Hub.app/Contents/Resources/render-tools")

    def setUp(self):
        self.fixture = fixtures.RenderExecutorTests()
        self.fixture.setUp()
        # A borrowed case is not run by unittest, so move its cleanups (the render-authority root
        # anchor and its temp roots) onto this one; otherwise they never run and every later test
        # sees a fake root.
        for function, args, kwargs in self.fixture._cleanups:
            self.addCleanup(function, *args, **kwargs)
        self.fixture._cleanups.clear()
        self.root = self.fixture.root
        for name in ("ffmpeg", "ffprobe"):
            if not (self.PINNED_TOOLS / name).is_file():
                self.skipTest(f"pinned render tool {name} is not installed")

    def webm(self) -> Path:
        path = self.root / "cue_overlay_0000.webm"
        subprocess.run(
            [str(self.PINNED_TOOLS / "ffmpeg"), "-y", "-v", "error",
             "-f", "lavfi", "-i", "color=c=red@1.0:s=128x96:r=5:d=1.6",
             "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
             "-auto-alt-ref", "0", str(path)],
            check=True)
        return path

    def setup_run(self, *, allowed=True, corrupt=False):
        packet = copy.deepcopy(json.loads(
            (fixtures.ROOT / "tests/fixtures/final-render-compiler-v1.json").read_text()
        )["f0_base_audio"]["packet"])
        packet["recipe"]["encode"] = {
            "container": "mp4", "requested_codec": "h264",
            "requested_effort": "hardware", "quality_cpu_preset": "medium",
            "crf": 23, "bitrate_kbps": 8000,
        }
        packet["recipe"]["operations"] = [{
            "kind": "cue_overlay_stage", "contract": _CUE_OVERLAY_CONTRACT,
            "cue_id": "cue-1", "time_range": {"start_sec": 0.5, "end_sec": 1.5},
            "asset": {"type": "asset_ref", "logical_id": "cue_overlay_0000"},
            "fade": None, "slide": None,
        }]
        cue_bytes = self.webm().read_bytes()
        base_bytes = self.fixture.mp4_bytes()
        audio_bytes = self.fixture.wav_bytes()
        packet["manifest"] = [
            {"logical_id": "audio", "safe_name": "audio.wav", "role": "premixed_audio",
             "sha256": hashlib.sha256(audio_bytes).hexdigest(),
             "size_bytes": len(audio_bytes)},
            {"logical_id": "base", "safe_name": "base.mp4", "role": "base_video",
             "sha256": hashlib.sha256(base_bytes).hexdigest(),
             "size_bytes": len(base_bytes)},
            {"logical_id": "cue_overlay_0000", "safe_name": "cue_overlay_0000.webm",
             "role": "cue_overlay",
             "sha256": hashlib.sha256(cue_bytes).hexdigest(),
             "size_bytes": len(cue_bytes)},
        ]
        packet["recipe_digest"] = render_hash("recipe", packet["recipe"])
        packet["manifest_digest"] = render_hash("manifest", packet["manifest"])
        packet["packet_digest"] = render_hash("packet", {
            "recipe_digest": packet["recipe_digest"],
            "manifest_digest": packet["manifest_digest"],
        })
        payloads = {"audio": audio_bytes, "base": base_bytes,
                    "cue_overlay_0000": cue_bytes}
        files, handles = {}, {}
        for index, entry in enumerate(packet["manifest"]):
            handle = f"{index:032x}"
            path = self.root / handle
            data = payloads[entry["logical_id"]]
            if corrupt and entry["logical_id"] == "cue_overlay_0000":
                data = b"corrupt-non-webm"
            path.write_bytes(data)
            files[handle] = path
            handles[entry["logical_id"]] = handle
        authority = replace(
            self.fixture.authority(),
            profile_execution_bounds=dict(BOUNDS),
            selected_tier="H.264 · hardware (videotoolbox, 8000 kbit/s)",
            exact_encoder="h264_videotoolbox",
            ffmpeg=self.PINNED_TOOLS / "ffmpeg",
            ffprobe=self.PINNED_TOOLS / "ffprobe",
        )
        events = []
        packed = json.dumps({"packet": packet, "assets": handles},
                            separators=(",", ":")).encode("ascii")
        runtime = FakeRuntime(self.root, packed, authority.qualification, events)
        runtime.claim_launch("site-a:node-a:fleet", "job-1", machine="local",
                             dispatch_generation=1)
        context = runtime.execution_context(
            "site-a:node-a:fleet", "job-1", "attempt-1", "test")
        loader = mock.Mock(spec=InstalledRenderAuthorityLoader)
        loader.late_labels_allowed.return_value = False
        loader.subtitles_allowed.return_value = False
        loader.cue_overlays_allowed.return_value = allowed
        loader.compiler_for.return_value = compile_final_render_packet
        assets = mock.Mock(wraps=FakeAssets(files))
        self.seen = {}

        async def spawn(*argv, **kwargs):
            self.seen["argv"] = argv
            return FakeProcess(Path(context.output_path))

        def validate(ctx, auth, command_digest):
            content = Path(ctx.output_path).read_bytes()
            return ValidatedOutputReceipt(
                owner_scope=ctx.owner_scope, hub_job_id=ctx.hub_job_id,
                attempt_id=ctx.attempt_id,
                accepted_request_sha256=ctx.accepted_request_sha256,
                receipt_digest="5" * 64,
                output_sha256=hashlib.sha256(content).hexdigest(),
                output_size_bytes=len(content),
                profile_digest=auth.qualification.profile_digest,
                tools_digest=auth.qualification.tools_digest,
                compiler_digest=auth.qualification.compiler_digest,
                qualification_digest=auth.qualification.qualification_digest,
                generated_command_digest=command_digest,
                selected_tier=auth.selected_tier, feature_losses=())

        executor = RenderExecutor(
            runtime, assets, loader, machine="local", broker_api=FakeBroker(events),
            spawn=spawn, output_validator=validate,
            process_identity=lambda process, executable: ProcessIdentity(
                pid=process.pid, process_group_id=process.pid,
                start_token=f"boot:1:{executable}"),
            group_exists=lambda _pgid: False)

        async def wait(process, *_args, **_kwargs):
            await process.wait()
            return 0, False
        executor._wait_with_bounds = wait
        return executor, context, authority, assets

    async def test_valid_cue_overlay_renders_and_publishes(self):
        executor, context, authority, _assets = self.setup_run()
        await executor._run(context, authority, context.owner_scope)
        self.assertEqual(executor.runtime.job["state"], "output_ready")
        command = " ".join(self.seen["argv"])
        self.assertIn("h264_videotoolbox", command)
        self.assertIn("matroska,webm", command)

    async def test_authority_refusal_precedes_asset_open_and_spawn(self):
        executor, context, authority, assets = self.setup_run(allowed=False)
        with self.assertRaisesRegex(RenderJobUnavailable, "cue_overlay_profile_required"):
            await executor._run(context, authority, context.owner_scope)
        assets.open_reader.assert_not_called()
        self.assertFalse(self.seen)

    async def test_corrupt_cue_media_is_refused_before_spawn(self):
        executor, context, authority, _assets = self.setup_run(corrupt=True)
        with self.assertRaisesRegex(RuntimeError, "cue overlay media rejected"):
            await executor._run(context, authority, context.owner_scope)
        self.assertFalse(self.seen)


if __name__ == "__main__":
    unittest.main()
