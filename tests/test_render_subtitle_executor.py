#!/usr/bin/env python3
"""Subtitle executor staging: private ASS/font files, real compiler paths, fake processes."""
from __future__ import annotations

import asyncio
import copy
import hashlib
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest import mock
import zlib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import test_render_executor as fixtures
from test_render_executor import FakeAssets, FakeBroker, FakeProcess, FakeRuntime
from components.hub.backend.final_render_compiler import EncodeTier, FinalRenderPlan
from components.hub.backend.render_executor import (
    InstalledRenderAuthorityLoader, RenderExecutor, _stage_subtitle_inputs,
)
from components.hub.backend.render_hash_v1 import render_hash
from components.hub.backend.render_jobs import (
    ProcessIdentity, RenderJobUnavailable, ValidatedOutputReceipt,
)

ASS = b"[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\n"
FONT = b"\x00\x01\x00\x00fake-sfnt-font-bytes"


def raw(value):
    import json
    return json.dumps(value, separators=(",", ":")).encode()


def make_png(width, height):
    def chunk(kind, payload):
        crc = zlib.crc32(kind + payload).to_bytes(4, "big")
        return len(payload).to_bytes(4, "big") + kind + payload + crc
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big")
                    + bytes((8, 6, 0, 0, 0)))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff" * (width * 4)) * height))
            + chunk(b"IEND", b""))


class SubtitleExecutorTests(unittest.IsolatedAsyncioTestCase):
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

    def packet(self):
        packet = copy.deepcopy(__import__("json").loads(
            (fixtures.ROOT / "tests/fixtures/final-render-compiler-v1.json").read_text()
        )["f0_base_audio"]["packet"])
        packet["recipe"]["operations"].append({
            "kind": "subtitle_stage", "contract": "studio.render.subtitle.v1",
            "ass": {"type": "asset_ref", "logical_id": "subtitle_ass"},
            "fonts": [{
                "asset": {"type": "asset_ref", "logical_id": "subtitle_font_0000"},
                "family": "Anton", "sha256": hashlib.sha256(FONT).hexdigest(),
            }],
        })
        packet["manifest"].extend([
            {"logical_id": "subtitle_ass", "safe_name": "../../escape.ass",
             "role": "subtitle_ass", "sha256": hashlib.sha256(ASS).hexdigest(),
             "size_bytes": len(ASS)},
            {"logical_id": "subtitle_font_0000", "safe_name": "/evil/0000.ttf",
             "role": "subtitle_font", "sha256": hashlib.sha256(FONT).hexdigest(),
             "size_bytes": len(FONT)},
        ])
        packet["recipe_digest"] = render_hash("recipe", packet["recipe"])
        packet["manifest_digest"] = render_hash("manifest", packet["manifest"])
        packet["packet_digest"] = render_hash("packet", {
            "recipe_digest": packet["recipe_digest"],
            "manifest_digest": packet["manifest_digest"]})
        return packet

    def setup_run(self, *, allowed=True, corrupt=False, limits=None):
        packet = self.packet()
        files, handles = {}, {}
        payloads = {"subtitle_ass": ASS, "subtitle_font_0000": FONT}
        for index, entry in enumerate(packet["manifest"]):
            payload = (self.fixture.mp4_bytes() if entry["role"] == "base_video" else
                       self.fixture.wav_bytes() if entry["role"] == "premixed_audio" else
                       payloads[entry["logical_id"]])
            entry.update(size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
            handle = f"{index:032x}"
            path = self.root / handle
            path.write_bytes(payload + (b"corrupt" if corrupt
                                        and entry["logical_id"] == "subtitle_font_0000"
                                        else b""))
            files[handle] = path
            handles[entry["logical_id"]] = handle
        packet["manifest_digest"] = render_hash("manifest", packet["manifest"])
        packet["packet_digest"] = render_hash("packet", {
            "recipe_digest": packet["recipe_digest"],
            "manifest_digest": packet["manifest_digest"]})
        authority = self.fixture.authority()
        bounds = {
            "bounds_version": 2, "canvas_range": {"max_edge": 3840, "max_pixels": 8294400},
            "working_fps": [5],
            "max_duration_sec": 2.5, "max_operations": 9, "max_assets": 6,
            "max_asset_bytes": 1_000_000, "max_total_input_bytes": 2_000_000,
            "max_pixel_frames": 1_000_000, "scratch_reserve_bytes": 250_000,
            "output_limit_bytes": 1_000_000, "process_tree_rss_limit_bytes": 100_000_000,
            "cumulative_wall_seconds": 30, "max_subtitle_fonts": 8,
            "max_subtitle_font_bytes": 33_554_432, "max_subtitle_ass_bytes": 4_194_304,
        }
        if limits is not None:
            bounds.update(limits)
        authority = replace(authority, font_support=True, profile_execution_bounds=bounds)
        events = []
        runtime = FakeRuntime(self.root, raw({"packet": packet, "assets": handles}),
                              authority.qualification, events)
        runtime.claim_launch("site-a:node-a:fleet", "job-1", machine="local",
                             dispatch_generation=1)
        context = runtime.execution_context(
            "site-a:node-a:fleet", "job-1", "attempt-1", "test")
        loader = mock.Mock(spec=InstalledRenderAuthorityLoader)
        loader.late_labels_allowed.return_value = False
        loader.subtitles_allowed.return_value = allowed
        loader.compiler_for.return_value = self.compiler(context)
        assets = mock.Mock(wraps=FakeAssets(files))
        self.seen = {}

        async def spawn(*argv, **kwargs):
            self.seen.update(argv=argv)
            return FakeProcess(Path(context.output_path))

        def validate(ctx, auth, command_digest):
            content = Path(ctx.output_path).read_bytes()
            return ValidatedOutputReceipt(
                owner_scope=ctx.owner_scope, hub_job_id=ctx.hub_job_id,
                attempt_id=ctx.attempt_id, accepted_request_sha256=ctx.accepted_request_sha256,
                receipt_digest="5" * 64,
                output_sha256=hashlib.sha256(content).hexdigest(),
                output_size_bytes=len(content), profile_digest=auth.qualification.profile_digest,
                tools_digest=auth.qualification.tools_digest,
                compiler_digest=auth.qualification.compiler_digest,
                qualification_digest=auth.qualification.qualification_digest,
                generated_command_digest=command_digest, selected_tier=auth.selected_tier,
                feature_losses=())

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
        return executor, context, authority, assets, packet

    def compiler(self, context):
        def compile_packet(packet_raw, assets, output_path, **options):
            stage = Path(output_path + ".subtitle")
            ass = Path(assets["subtitle_ass"])
            font = Path(assets["subtitle_font_0000"])
            fonts_dir = Path(options["subtitle_fonts_dir"])
            self.seen.update(
                options=options, ass=ass, font=font, fonts_dir=fonts_dir,
                fonts_mode=fonts_dir.stat().st_mode & 0o777,
                ass_bytes=ass.read_bytes(), font_bytes=font.read_bytes())
            base = assets["base"]
            audio = assets["audio"]
            argv = ("-i", base, "-i", audio, "-c:v", "h264_videotoolbox", str(output_path))
            return FinalRenderPlan(
                tiers=(EncodeTier("hardware-h264", argv),), requested_burn_ins=(),
                input_uses=(), generated_inputs=())
        return compile_packet

    async def test_private_ass_and_fonts_reach_the_compiler_and_are_removed(self):
        executor, context, authority, _assets, _packet = self.setup_run()
        await executor._run(context, authority, "test-owner")
        self.assertEqual(executor.runtime.job["state"], "output_ready")
        self.assertTrue(self.seen["ass"].is_relative_to(
            Path(context.output_path + ".subtitle")))
        self.assertEqual(self.seen["ass"].name, "subtitle.ass")
        self.assertEqual(self.seen["font"].name, "0000.ttf")
        self.assertEqual(self.seen["font"].parent, self.seen["fonts_dir"])
        self.assertEqual(self.seen["ass_bytes"], ASS)
        self.assertEqual(self.seen["font_bytes"], FONT)
        self.assertTrue(self.seen["options"]["subtitles_allowed"])
        self.assertEqual(self.seen["options"]["subtitle_limits"]["max_subtitle_fonts"], 8)
        self.assertEqual(self.seen["fonts_mode"], 0o700)
        self.assertFalse(Path(context.output_path + ".subtitle").exists())

    async def test_corrupt_font_bytes_are_refused_and_cleaned(self):
        executor, context, authority, _assets, _packet = self.setup_run(corrupt=True)
        with self.assertRaisesRegex(RuntimeError, "changed during staging|differs"):
            await executor._run(context, authority, "test-owner")
        self.assertFalse(self.seen)
        self.assertFalse(Path(context.output_path + ".subtitle").exists())

    async def test_v1_scope_refuses_before_opening_or_staging(self):
        executor, context, authority, assets, _packet = self.setup_run(allowed=False)
        with self.assertRaisesRegex(RenderJobUnavailable, "subtitle_profile_required"):
            await executor._run(context, authority, "test-owner")
        assets.open_reader.assert_not_called()
        executor._authority.compiler_for.assert_not_called()
        self.assertFalse(Path(context.output_path + ".subtitle").exists())

    async def test_declared_font_over_the_shared_asset_cap_is_refused(self):
        executor, context, authority, _assets, packet = self.setup_run(
            limits={"max_asset_bytes": 4})
        with self.assertRaisesRegex(RuntimeError, "over the published limit"):
            await executor._run(context, authority, "test-owner")
        self.assertFalse(Path(context.output_path + ".subtitle").exists())

    async def test_staging_refuses_missing_v2_bounds(self):
        executor, context, authority, _assets, _packet = self.setup_run()
        bounds = {key: value for key, value in
                  authority.profile_execution_bounds.items()
                  if not key.startswith("max_subtitle")}
        with self.assertRaisesRegex(RuntimeError, "bounds"):
            _stage_subtitle_inputs(
                mock.MagicMock(), context,
                ("subtitle_ass", ("subtitle_font_0000",)),
                {entry["logical_id"]: mock.Mock(fileno=lambda: 0)
                 for entry in context.packet["manifest"]}, limits=bounds)

    async def test_combined_late_label_and_subtitle_execution(self):
        executor, context, authority, assets, packet = self.setup_run()
        # 1. Modify safe_names to be contract-compliant
        for entry in packet["manifest"]:
            if entry["logical_id"] == "subtitle_ass":
                entry["safe_name"] = "subtitle_ass.ass"
            elif entry["logical_id"] == "subtitle_font_0000":
                entry["safe_name"] = "subtitle_font_0000.ttf"
        # 2. Create valid late-label PNG for the 128x96 canvas
        png_bytes = make_png(40, 30)
        packet["recipe"]["operations"].append({
            "kind": "late_label_stage", "contract": "studio.render.late-label.v3",
            "cue_id": "cue1", "enable": {"start_sec": 0.4, "end_sec": 1.5},
            "placement": {"x_px": 10, "y_px": 10},
            "motion": {"kind": "none", "entrance_sec": 0.3},
            "label": {"asset": {"type": "asset_ref", "logical_id": "cue_0001_label"}},
            "reveal": None, "line": None, "line_reveal": None,
        })
        packet["manifest"].append({
            "logical_id": "cue_0001_label", "safe_name": "cue_0001_label.png",
            "role": "cue_label", "sha256": hashlib.sha256(png_bytes).hexdigest(),
            "size_bytes": len(png_bytes),
        })
        packet["manifest"].sort(key=lambda entry: entry["logical_id"])
        packet["recipe_digest"] = render_hash("recipe", packet["recipe"])
        packet["manifest_digest"] = render_hash("manifest", packet["manifest"])
        packet["packet_digest"] = render_hash("packet", {
            "recipe_digest": packet["recipe_digest"],
            "manifest_digest": packet["manifest_digest"]})
        # 3. Inject asset file handle
        handle = "11111111111111111111111111111111"
        path = self.root / handle
        path.write_bytes(png_bytes)
        assets._mock_wraps.files[handle] = path
        new_assets = dict(context.assets, cue_0001_label=handle)
        context = replace(context, packet=packet, assets=new_assets,
                          accepted_request_sha256=hashlib.sha256(
                              raw({"packet": packet, "assets": new_assets})).hexdigest())
        executor._authority.late_labels_allowed.return_value = True
        executor._authority.subtitles_allowed.return_value = True
        # 4. Compile combined plan and run
        def combined_compiler(ctx):
            def compile_packet(packet_raw, assets_map, output_path, **options):
                self.seen.update(
                    options=options,
                    has_cue="cue_0001_label" in assets_map,
                    has_ass="subtitle_ass" in assets_map)
                base = assets_map["base"]
                audio = assets_map["audio"]
                label = assets_map["cue_0001_label"]
                argv = ("-i", base, "-i", audio, "-i", label,
                        "-c:v", "h264_videotoolbox", str(output_path))
                return FinalRenderPlan(
                    tiers=(EncodeTier("hardware-h264", argv),),
                    requested_burn_ins=("subtitles", "late_labels"),
                    input_uses=(), generated_inputs=())
            return compile_packet
        executor._authority.compiler_for.return_value = combined_compiler(context)
        await executor._run(context, authority, "test-owner")
        self.assertEqual(executor.runtime.job["state"], "output_ready")
        self.assertTrue(self.seen["options"]["late_labels_allowed"])
        self.assertTrue(self.seen["options"]["subtitles_allowed"])
        self.assertTrue(self.seen["has_cue"])
        self.assertTrue(self.seen["has_ass"])
        self.assertFalse(Path(context.output_path + ".subtitle").exists())
        self.assertFalse(Path(context.output_path + ".late-label").exists())


if __name__ == "__main__":
    unittest.main()
