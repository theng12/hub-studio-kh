"""Receiver confinement and cleanup using fake processes and a private test workspace."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from pathlib import Path
import unittest
from unittest import mock
import zlib

from test_late_label_v1 import raw, vector_packet
import test_render_executor as executor_fixtures
from test_render_executor import FakeAssets, FakeBroker, FakeProcess, FakeRuntime
from components.hub.backend.final_render_compiler import compile_final_render_packet
from components.hub.backend.render_executor import (
    InstalledRenderAuthorityLoader, RenderExecutor, _confined_argv,
)
from components.hub.backend.render_hash_v1 import render_hash
from components.hub.backend.render_jobs import (
    ProcessIdentity, RenderJobUnavailable, ValidatedOutputReceipt,
)


def png(width=240, height=80):
    def chunk(kind, payload):
        crc = zlib.crc32(kind + payload).to_bytes(4, "big")
        return len(payload).to_bytes(4, "big") + kind + payload + crc
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big")
                    + bytes((8, 6, 0, 0, 0)))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff" * (width * 4)) * height))
            + chunk(b"IEND", b""))


class LateLabelReceiverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = executor_fixtures.RenderExecutorTests()
        self.fixture.setUp()
        # A borrowed case is not run by unittest, so move its cleanups (the render-authority root
        # anchor and its temp roots) onto this one; otherwise they never run and every later test
        # sees a fake root.
        for function, args, kwargs in self.fixture._cleanups:
            self.addCleanup(function, *args, **kwargs)
        self.fixture._cleanups.clear()
        self.root = self.fixture.root

    def setup_run(self, *, allowed=True, corrupt=False, workspace_outside=False):
        packet = vector_packet("positive-wipe")
        files, handles = {}, {}
        for index, entry in enumerate(packet["manifest"]):
            payload = (self.fixture.mp4_bytes() if entry["role"] == "base_video" else
                       self.fixture.wav_bytes() if entry["role"] == "premixed_audio" else png())
            entry.update(size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
            handle = f"{index:032x}"
            path = self.root / handle
            path.write_bytes(payload)
            files[handle] = path
            handles[entry["logical_id"]] = handle
        packet["manifest_digest"] = render_hash("manifest", packet["manifest"])
        packet["packet_digest"] = render_hash("packet", {
            "recipe_digest": packet["recipe_digest"], "manifest_digest": packet["manifest_digest"],
        })
        if corrupt:
            files[handles["cue_0000_r0000"]].write_bytes(b"x" * packet["manifest"][2]["size_bytes"])
        request = raw({"packet": packet, "assets": handles})
        authority = self.fixture.authority()
        plan = compile_final_render_packet(
            raw(packet), {key: "/dev/fd/9" for key in handles}, "/tmp/receiver-test.mp4",
            late_labels_allowed=True)
        authority = replace(authority, selected_tier=plan.tiers[0].label, exact_encoder="libx264")
        events = []
        runtime = FakeRuntime(self.root, request, authority.qualification, events)
        runtime.claim_launch("site-a:node-a:fleet", "job-1", machine="local", dispatch_generation=1)
        context = runtime.execution_context("site-a:node-a:fleet", "job-1", "attempt-1", "test")
        if workspace_outside:
            outside = self.root / "outside"
            outside.mkdir()
            context = replace(context, workspace=str(outside), output_path=str(outside / "final.mp4"))
        loader = mock.Mock(spec=InstalledRenderAuthorityLoader)
        loader.late_labels_allowed.return_value = allowed
        loader.compiler_for.return_value = compile_final_render_packet
        assets = mock.Mock(wraps=FakeAssets(files))
        self.seen = {}

        async def spawn(*argv, **kwargs):
            patterns = [argv[index + 1] for index, value in enumerate(argv) if value == "-i"
                        and "%04d.png" in argv[index + 1]]
            self.assertEqual(len(patterns), 1)
            directory = Path(patterns[0]).parent
            self.assertEqual(directory.parent.parent, Path(context.workspace))
            self.assertEqual(sorted(path.name for path in directory.iterdir()),
                             [f"{index:04d}.png" for index in range(9)])
            self.assertTrue(all(path.read_bytes() == png() for path in directory.iterdir()))
            self.assertEqual(directory.parent.stat().st_mode & 0o777, 0o700)
            self.seen.update(argv=argv, patterns=patterns)
            return FakeProcess(Path(context.output_path))

        def validate(ctx, auth, command_digest):
            content = Path(ctx.output_path).read_bytes()
            return ValidatedOutputReceipt(
                owner_scope=ctx.owner_scope, hub_job_id=ctx.hub_job_id,
                attempt_id=ctx.attempt_id, accepted_request_sha256=ctx.accepted_request_sha256,
                receipt_digest="5" * 64, output_sha256=hashlib.sha256(content).hexdigest(),
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
                pid=process.pid, process_group_id=process.pid, start_token=f"boot:1:{executable}"),
            group_exists=lambda _pgid: False)

        async def wait(process, *_args, **_kwargs):
            await process.wait()
            return 0, False
        executor._wait_with_bounds = wait
        return executor, context, authority, assets

    async def test_matched_v3_sequences_are_consumed_and_removed_on_success(self):
        executor, context, authority, _assets = self.setup_run()
        await executor._run(context, authority, "test-owner")
        self.assertEqual(executor.runtime.job["state"], "output_ready")
        self.assertIn("image2", self.seen["argv"])
        self.assertFalse(Path(context.output_path + ".late-label").exists())

    async def test_staged_sequences_are_removed_on_spawn_failure(self):
        executor, context, authority, _assets = self.setup_run()
        original = executor._spawn
        async def fail(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("fake spawn failed")
        executor._spawn = fail
        with self.assertRaisesRegex(RuntimeError, "fake spawn failed"):
            await executor._run(context, authority, "test-owner")
        self.assertFalse(Path(context.output_path + ".late-label").exists())

    async def test_staged_sequences_are_removed_on_cancel(self):
        executor, context, authority, _assets = self.setup_run()
        async def cancel(*_args, **_kwargs):
            executor.runtime.request_cancel(context.owner_scope, context.hub_job_id)
            return -15, False
        executor._wait_with_bounds = cancel
        await executor._run(context, authority, "test-owner")
        self.assertEqual(executor.runtime.job["state"], "cancelled")
        self.assertFalse(Path(context.output_path + ".late-label").exists())

    async def test_v1_refuses_before_opening_or_staging(self):
        executor, context, authority, assets = self.setup_run(allowed=False)
        with self.assertRaisesRegex(RenderJobUnavailable, "late_label_profile_required"):
            await executor._run(context, authority, "test-owner")
        assets.open_reader.assert_not_called()
        executor._authority.compiler_for.assert_not_called()
        self.assertFalse(Path(context.output_path + ".late-label").exists())

    async def test_manifest_identity_is_checked_before_staging(self):
        executor, context, authority, _assets = self.setup_run(corrupt=True)
        with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
            await executor._run(context, authority, "test-owner")
        self.assertFalse(self.seen)
        self.assertFalse(Path(context.output_path + ".late-label").exists())

    async def test_workspace_outside_owned_attempt_is_rejected_before_readers(self):
        executor, context, authority, assets = self.setup_run(workspace_outside=True)
        with self.assertRaisesRegex(RuntimeError, "Hub-owned workspace"):
            await executor._run(context, authority, "test-owner")
        assets.open_reader.assert_not_called()

    def test_unmanaged_image2_pattern_is_not_an_accepted_input(self):
        with self.assertRaisesRegex(RuntimeError, "escaped the accepted asset set"):
            _confined_argv(("-i", "/tmp/sender/%04d.png"), {"manifest": []}, {}, {})


if __name__ == "__main__":
    unittest.main()
