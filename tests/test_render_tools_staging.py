#!/usr/bin/env python3
"""Offline checks for the fixed render-tool staging copier."""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "_trash" / "render-tools-staging-tests"
ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
RUN_ROOT = Path(tempfile.mkdtemp(prefix="run-", dir=ARTIFACT_ROOT))
MODULE_PATH = ROOT / "packaging" / "stage_render_tools.py"
SPEC = importlib.util.spec_from_file_location("studiohub_stage_render_tools", MODULE_PATH)
stage_render_tools = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = stage_render_tools
SPEC.loader.exec_module(stage_render_tools)

sys.path.insert(0, str(ROOT))
from components.hub.backend.render_hash_v1 import render_hash  # noqa: E402


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


class RenderToolsStagingTests(unittest.TestCase):
    _case_number = 0

    def setUp(self) -> None:
        type(self)._case_number += 1
        label = f"{type(self)._case_number:02d}-{self._testMethodName}"
        self.case = RUN_ROOT / label
        self.case.mkdir(mode=0o700)
        self.build_root = self.case / "build"
        self.scratch_root = self.case / "scratch"
        self.build_root.mkdir(mode=0o700)
        self.scratch_root.mkdir(mode=0o700)

    def fixtures(self, first: bytes = b"fixture-ffmpeg", second: bytes = b"fixture-ffprobe"):
        sources = self.case / "sources"
        sources.mkdir(mode=0o700, exist_ok=True)
        ffmpeg = sources / "ffmpeg"
        ffprobe = sources / "ffprobe"
        ffmpeg.write_bytes(first)
        ffprobe.write_bytes(second)
        pins = (
            stage_render_tools._ToolPin("ffmpeg", ffmpeg, len(first), sha256(first)),
            stage_render_tools._ToolPin("ffprobe", ffprobe, len(second), sha256(second)),
        )
        return ffmpeg, ffprobe, pins

    def stage(self, pins):
        return stage_render_tools._stage_render_tools(
            self.build_root, self.scratch_root, pins
        )

    def authority_fixtures(self):
        _, _, tool_pins = self.fixtures()
        tool_raw = canonical({
            "format": "studio.render.tools.v1", "platform": "macos-arm64",
            "ffmpeg_sha256": tool_pins[0].sha256,
            "ffmpeg_size_bytes": tool_pins[0].size_bytes,
            "ffprobe_sha256": tool_pins[1].sha256,
            "ffprobe_size_bytes": tool_pins[1].size_bytes,
            "architecture_evidence_sha256": "1" * 64,
            "minimum_os_evidence_sha256": "2" * 64,
            "linked_libraries_evidence_sha256": "3" * 64,
            "build_configuration_evidence_sha256": "4" * 64,
            "capability_inventory_evidence_sha256": "5" * 64,
        })
        compiler_raw = canonical({
            "format": "studio.render.compiler.v1",
            "implementation": "hub-final-render-v1",
            "python_runtime_sha256": "6" * 64,
            "render_hash_sha256": "7" * 64,
            "render_contract_sha256": "8" * 64,
            "final_render_compiler_sha256": "9" * 64,
        })
        execution_bounds = {
            "canvas_pairs": [{"width": 128, "height": 96}], "working_fps": [5],
            "max_duration_sec": 2.5, "max_operations": 9, "max_assets": 6,
            "max_asset_bytes": 1_000_000, "max_total_input_bytes": 2_000_000,
            "max_pixel_frames": 1_000_000, "scratch_reserve_bytes": 250_000,
            "output_limit_bytes": 1_000_000,
            "process_tree_rss_limit_bytes": 100_000_000,
            "cumulative_wall_seconds": 30,
        }
        profile_raw = canonical({
            "format": "studio.render.profile.v1",
            "implementation": "story-classic-no-font-v1",
            "recipe_namespace": "studio.render.final.v1",
            "compiler_artifact_sha256": sha256(compiler_raw),
            "tools_artifact_sha256": sha256(tool_raw),
            "combination_set": "retained-classic-no-font-candidate-v1",
            "tier_policy": "story-final-chain-v1",
            "output_policy": "story-final-output-v1",
            "execution_bounds": execution_bounds,
            "hardware_qualification_record_sha256": None,
            "fidelity_record_sha256": None,
            "production_eligible": False,
        })
        packet = json.loads(
            (ROOT / "tests/fixtures/final-render-compiler-v1.json").read_text()
        )["f0_base_audio"]["packet"]
        packet["recipe"]["profile_digest"] = sha256(profile_raw)
        packet["recipe_digest"] = render_hash("recipe", packet["recipe"])
        packet["manifest_digest"] = render_hash("manifest", packet["manifest"])
        packet["packet_digest"] = render_hash("packet", {
            "recipe_digest": packet["recipe_digest"],
            "manifest_digest": packet["manifest_digest"],
        })
        vector = {
            "format": "studio.render.qualification-vector.v1",
            "production_eligible": False,
            "profile_digest": sha256(profile_raw),
            "tools_digest": sha256(tool_raw),
            "compiler_digest": sha256(compiler_raw),
            "qualification_digest": "0" * 64,
            "hardware_class": "m4-16gb", "selected_tier": "hardware-h264",
            "exact_encoder": "h264_videotoolbox", "font_support": False,
            "bounds": {
                "max_rss_bytes": execution_bounds["process_tree_rss_limit_bytes"],
                "max_wall_seconds": execution_bounds["cumulative_wall_seconds"],
                "min_available_memory_bytes": 2_000_000_000,
                "output_limit_bytes": execution_bounds["output_limit_bytes"],
            },
            "files": {
                pin.name: {"size_bytes": pin.size_bytes, "sha256": pin.sha256}
                for pin in tool_pins
            },
            "vector_id": "m4-hardware-v1", "packet": packet,
            "packet_digest": packet["packet_digest"],
            "recipe_digest": packet["recipe_digest"],
            "manifest_digest": packet["manifest_digest"],
            "assets": [{
                "logical_id": row["logical_id"], "sha256": row["sha256"],
                "size_bytes": row["size_bytes"],
            } for row in packet["manifest"]],
        }
        vector["qualification_digest"] = sha256(canonical({
            key: value for key, value in vector.items() if key != "qualification_digest"
        }))
        raw = {
            "PROFILE.json": profile_raw,
            "TOOLS.json": tool_raw,
            "COMPILER.json": compiler_raw,
            "qualification-vectors/qualification-vector.json": canonical(vector),
        }
        source = self.case / "authority-source"
        pins = []
        pin_type = getattr(stage_render_tools, "_AuthorityPin", None)
        self.assertIsNotNone(pin_type, "stage_render_tools must expose a separate _AuthorityPin")
        for relative, payload in raw.items():
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            path.chmod(0o644)
            pins.append(pin_type(
                relative, path, len(payload), sha256(payload)
            ))
        return raw, tuple(pins), tool_pins

    def stage_authority(self, mode, pins):
        helper = getattr(stage_render_tools, "_stage_render_authority_into", None)
        self.assertIsNotNone(helper, "authority staging must use its own helper")
        return helper(
            self.staged_root / "Contents/Resources", self.scratch_root, mode, pins
        )

    def replace_authority_pin(self, pins, relative: str, payload: bytes):
        updated = []
        for pin in pins:
            if pin.relative_path == relative:
                pin.source.write_bytes(payload)
                pin.source.chmod(0o644)
                pin = stage_render_tools._AuthorityPin(
                    relative, pin.source, len(payload), sha256(payload)
                )
            updated.append(pin)
        return tuple(updated)

    @property
    def staged_root(self) -> Path:
        return self.build_root / stage_render_tools.STAGING_LEAF

    @property
    def tools_root(self) -> Path:
        return self.staged_root / "Contents" / "Resources" / "render-tools"

    @property
    def scratch_leaf(self) -> Path:
        return self.scratch_root / stage_render_tools.SCRATCH_LEAF

    def test_public_wrapper_has_fixed_universal_81_pins_and_only_a_source_directory(self) -> None:
        self.assertEqual(
            tuple(inspect.signature(stage_render_tools.stage_render_tools).parameters),
            ("build_root", "scratch_root", "tools_dir"),
        )
        self.assertFalse(hasattr(stage_render_tools, "PRODUCTION_PINS"))
        pins = stage_render_tools.production_pins(self.case)
        self.assertEqual(
            [(pin.name, pin.source, pin.size_bytes, pin.sha256) for pin in pins],
            [
                (
                    "ffmpeg",
                    self.case / "ffmpeg",
                    132141880,
                    "ba3a9d9d20b43492a85649d7e03122144905e3f07b713279fb5a0e659cf73e84",
                ),
                (
                    "ffprobe",
                    self.case / "ffprobe",
                    131770248,
                    "f9827709561c11162a9fe93380fe6ecd92648461faf53c5bf5eb3a037442621e",
                ),
            ],
        )
        self.assertEqual(stage_render_tools.TOOL_VERSION, "8.1")
        self.assertEqual(stage_render_tools.PAYLOAD_FORMAT, "macos-universal")
        self.assertNotIn("/Users/", MODULE_PATH.read_text(encoding="utf-8"))

    def test_source_directory_must_be_an_existing_absolute_directory(self) -> None:
        for bad in (self.case / "missing", Path("relative/dir"), self.case / "not-a-dir"):
            (self.case / "not-a-dir").write_bytes(b"x")
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                stage_render_tools.production_pins(bad)

    def test_public_wrapper_refuses_any_binary_that_is_not_the_pinned_one(self) -> None:
        tools = self.case / "tools"
        tools.mkdir(mode=0o700)
        (tools / "ffmpeg").write_bytes(b"not the pinned ffmpeg")
        (tools / "ffprobe").write_bytes(b"not the pinned ffprobe")
        with self.assertRaisesRegex(ValueError, "size mismatch"):
            stage_render_tools.stage_render_tools(self.build_root, self.scratch_root, tools)
        shutil.rmtree(self.build_root / stage_render_tools.STAGING_LEAF)
        (tools / "ffmpeg").unlink()
        with self.assertRaises(FileNotFoundError):  # a missing source never falls back
            stage_render_tools.stage_render_tools(self.build_root, self.scratch_root, tools)

    def test_stages_exact_verified_payloads_modes_and_closed_manifest(self) -> None:
        first, second = b"one-known-payload", b"another-known-payload"
        _, _, pins = self.fixtures(first, second)

        result = self.stage(pins)

        self.assertEqual(result.state, "complete")
        self.assertEqual(result.staging_root, self.staged_root)
        self.assertEqual(result.manifest_path, self.tools_root / "MANIFEST.json")
        self.assertEqual(self.tools_root.joinpath("ffmpeg").read_bytes(), first)
        self.assertEqual(self.tools_root.joinpath("ffprobe").read_bytes(), second)
        self.assertEqual(stat.S_IMODE(self.staged_root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.scratch_leaf.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.tools_root / "ffmpeg").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((self.tools_root / "ffprobe").stat().st_mode), 0o755)
        manifest_path = self.tools_root / "MANIFEST.json"
        self.assertEqual(stat.S_IMODE(manifest_path.stat().st_mode), 0o644)
        raw = manifest_path.read_bytes()
        self.assertEqual(result.manifest_sha256, sha256(raw))
        manifest = json.loads(raw)
        self.assertEqual(
            list(manifest),
            ["format", "tool_version", "payload_format", "files", "production_eligible"],
        )
        self.assertEqual(manifest["format"], "studiohub.render-tools.payload.v1")
        self.assertEqual(manifest["tool_version"], "8.1")
        self.assertEqual(manifest["payload_format"], "macos-universal")
        self.assertIs(manifest["production_eligible"], False)
        self.assertEqual(
            manifest["files"],
            [
                {
                    "name": "ffmpeg",
                    "path": "render-tools/ffmpeg",
                    "sha256": sha256(first),
                    "size_bytes": len(first),
                },
                {
                    "name": "ffprobe",
                    "path": "render-tools/ffprobe",
                    "sha256": sha256(second),
                    "size_bytes": len(second),
                },
            ],
        )
        self.assertEqual(
            sorted(path.name for path in self.tools_root.iterdir()),
            ["MANIFEST.json", "ffmpeg", "ffprobe"],
        )

    def test_missing_source_fails_before_manifest(self) -> None:
        _, second, pins = self.fixtures()
        missing = self.case / "sources" / "missing-ffmpeg"
        pins = (stage_render_tools._ToolPin("ffmpeg", missing, 1, sha256(b"x")), pins[1])

        with self.assertRaises(FileNotFoundError):
            self.stage(pins)

        self.assertFalse((self.tools_root / "MANIFEST.json").exists())
        self.assertTrue(second.exists())

    def test_symlink_and_dangling_source_are_refused_without_manifest(self) -> None:
        for dangling in (False, True):
            with self.subTest(dangling=dangling):
                _, _, pins = self.fixtures(
                    first=f"first-{dangling}".encode(), second=f"second-{dangling}".encode()
                )
                target = self.case / "sources" / f"target-{dangling}"
                if not dangling:
                    target.write_bytes(b"linked")
                link = self.case / "sources" / f"link-{dangling}"
                link.symlink_to(target)
                bad = stage_render_tools._ToolPin("ffmpeg", link, 6, sha256(b"linked"))
                isolated_build = self.case / f"build-{dangling}"
                isolated_scratch = self.case / f"scratch-{dangling}"
                isolated_build.mkdir(mode=0o700)
                isolated_scratch.mkdir(mode=0o700)
                with self.assertRaises(OSError):
                    stage_render_tools._stage_render_tools(
                        isolated_build, isolated_scratch, (bad, pins[1])
                    )
                final = isolated_build / stage_render_tools.STAGING_LEAF
                self.assertFalse(
                    (final / "Contents/Resources/render-tools/MANIFEST.json").exists()
                )

    def test_fifo_and_device_are_refused_without_blocking(self) -> None:
        _, _, pins = self.fixtures()
        fifo = self.case / "sources" / "fifo"
        os.mkfifo(fifo)
        cases = (fifo, Path("/dev/null"))
        for number, source in enumerate(cases):
            with self.subTest(source=source):
                build = self.case / f"nonregular-build-{number}"
                scratch = self.case / f"nonregular-scratch-{number}"
                build.mkdir(mode=0o700)
                scratch.mkdir(mode=0o700)
                bad = stage_render_tools._ToolPin("ffmpeg", source, 1, sha256(b"x"))
                with self.assertRaisesRegex(ValueError, "regular file"):
                    stage_render_tools._stage_render_tools(build, scratch, (bad, pins[1]))
                final = build / stage_render_tools.STAGING_LEAF
                self.assertFalse(
                    (final / "Contents/Resources/render-tools/MANIFEST.json").exists()
                )

    def test_source_mutation_before_first_read_is_rejected(self) -> None:
        source, _, pins = self.fixtures(first=b"original-source")
        source_inode = source.stat().st_ino
        real_read = stage_render_tools.os.read
        changed = False

        def mutate_then_read(fd, size):
            nonlocal changed
            if not changed and os.fstat(fd).st_ino == source_inode:
                changed = True
                source.write_bytes(b"mutated-source!")
            return real_read(fd, size)

        with mock.patch.object(stage_render_tools.os, "read", side_effect=mutate_then_read):
            with self.assertRaises(ValueError):
                self.stage(pins)

        self.assertTrue(changed)
        self.assertFalse((self.tools_root / "MANIFEST.json").exists())
        self.assertTrue((self.tools_root / "ffmpeg").exists())

    def test_source_mutation_after_streaming_is_rejected_by_same_fd_metadata(self) -> None:
        source, _, pins = self.fixtures(first=b"stable-through-read")
        source_inode = source.stat().st_ino
        real_read = stage_render_tools.os.read
        changed = False

        def mutate_at_eof(fd, size):
            nonlocal changed
            data = real_read(fd, size)
            if not changed and not data and os.fstat(fd).st_ino == source_inode:
                changed = True
                source.write_bytes(b"changed-after-read")
            return data

        with mock.patch.object(stage_render_tools.os, "read", side_effect=mutate_at_eof):
            with self.assertRaisesRegex(ValueError, "changed while being copied"):
                self.stage(pins)

        self.assertTrue(changed)
        self.assertFalse((self.tools_root / "MANIFEST.json").exists())

    def test_pathname_swap_does_not_redirect_the_held_source_descriptor(self) -> None:
        original = b"held-descriptor-bytes"
        source, _, pins = self.fixtures(first=original)
        source_inode = source.stat().st_ino
        retained_original = source.with_name("ffmpeg-opened")
        real_read = stage_render_tools.os.read
        swapped = False

        def swap_then_read(fd, size):
            nonlocal swapped
            if not swapped and os.fstat(fd).st_ino == source_inode:
                swapped = True
                source.rename(retained_original)
                source.write_bytes(b"replacement-path-bytes")
            return real_read(fd, size)

        with mock.patch.object(stage_render_tools.os, "read", side_effect=swap_then_read):
            self.stage(pins)

        self.assertTrue(swapped)
        self.assertEqual((self.tools_root / "ffmpeg").read_bytes(), original)
        self.assertEqual(source.read_bytes(), b"replacement-path-bytes")

    def test_existing_staging_leaf_collisions_are_untouched(self) -> None:
        kinds = ("directory", "file", "symlink", "dangling")
        for kind in kinds:
            with self.subTest(kind=kind):
                _, _, pins = self.fixtures(
                    first=f"first-{kind}".encode(), second=f"second-{kind}".encode()
                )
                build = self.case / f"collision-build-{kind}"
                scratch = self.case / f"collision-scratch-{kind}"
                build.mkdir(mode=0o700)
                scratch.mkdir(mode=0o700)
                collision = build / stage_render_tools.STAGING_LEAF
                if kind == "directory":
                    collision.mkdir()
                    marker = collision / "owner-marker"
                    marker.write_bytes(b"keep")
                elif kind == "file":
                    collision.write_bytes(b"keep")
                else:
                    target = self.case / f"collision-target-{kind}"
                    if kind == "symlink":
                        target.write_bytes(b"keep")
                    collision.symlink_to(target)
                with self.assertRaises(FileExistsError):
                    stage_render_tools._stage_render_tools(build, scratch, pins)
                if kind == "directory":
                    self.assertEqual(marker.read_bytes(), b"keep")
                elif kind == "file":
                    self.assertEqual(collision.read_bytes(), b"keep")
                else:
                    self.assertTrue(collision.is_symlink())

    def test_output_collision_is_untouched_and_no_manifest_is_created(self) -> None:
        _, _, pins = self.fixtures()
        real_open = stage_render_tools.os.open
        injected = False

        def collide(path, flags, mode=0o777, *, dir_fd=None):
            nonlocal injected
            if path == "ffprobe" and flags & os.O_EXCL and not injected:
                injected = True
                (self.tools_root / "ffprobe").write_bytes(b"owner-collision")
            return real_open(path, flags, mode, dir_fd=dir_fd)

        with mock.patch.object(stage_render_tools.os, "open", side_effect=collide):
            with self.assertRaises(FileExistsError):
                self.stage(pins)

        self.assertTrue(injected)
        self.assertEqual((self.tools_root / "ffprobe").read_bytes(), b"owner-collision")
        self.assertFalse((self.tools_root / "MANIFEST.json").exists())

    def test_failed_output_reverification_precedes_manifest_creation(self) -> None:
        _, _, pins = self.fixtures()
        real_verify = stage_render_tools._verify_output

        def fail_second(directory_fd, pin):
            if pin.name == "ffprobe":
                raise ValueError("injected output verification failure")
            return real_verify(directory_fd, pin)

        with mock.patch.object(stage_render_tools, "_verify_output", side_effect=fail_second):
            with self.assertRaisesRegex(ValueError, "output verification"):
                self.stage(pins)

        self.assertTrue((self.tools_root / "ffmpeg").exists())
        self.assertTrue((self.tools_root / "ffprobe").exists())
        self.assertFalse((self.tools_root / "MANIFEST.json").exists())
        self.assertFalse(self.scratch_leaf.exists())

    def test_changed_output_bytes_and_mode_are_rejected_before_manifest(self) -> None:
        for change in ("bytes", "mode"):
            with self.subTest(change=change):
                build = self.case / f"changed-output-build-{change}"
                scratch = self.case / f"changed-output-scratch-{change}"
                build.mkdir(mode=0o700)
                scratch.mkdir(mode=0o700)
                _, _, pins = self.fixtures(
                    first=f"first-{change}".encode(), second=f"second-{change}".encode()
                )
                real_copy = stage_render_tools._copy_payload

                def corrupt(directory_fd, pin):
                    real_copy(directory_fd, pin)
                    if pin.name == "ffprobe":
                        fd = os.open(pin.name, os.O_WRONLY, dir_fd=directory_fd)
                        try:
                            if change == "bytes":
                                os.write(fd, b"X")
                            else:
                                os.fchmod(fd, 0o700)
                        finally:
                            os.close(fd)

                with mock.patch.object(stage_render_tools, "_copy_payload", side_effect=corrupt):
                    with self.assertRaisesRegex(ValueError, "wrong mode|verification failed"):
                        stage_render_tools._stage_render_tools(build, scratch, pins)
                final = build / stage_render_tools.STAGING_LEAF
                self.assertFalse(
                    (final / "Contents/Resources/render-tools/MANIFEST.json").exists()
                )

    def test_short_write_fails_and_retains_partial_output_without_manifest(self) -> None:
        _, _, pins = self.fixtures(first=b"payload-long-enough-for-a-short-write")
        real_write = stage_render_tools.os.write
        shortened = False

        def short_once(fd, data):
            nonlocal shortened
            if not shortened and len(data) > 1:
                shortened = True
                return real_write(fd, data[: len(data) // 2])
            return real_write(fd, data)

        with mock.patch.object(stage_render_tools.os, "write", side_effect=short_once):
            with self.assertRaisesRegex(OSError, "short write"):
                self.stage(pins)

        self.assertTrue(shortened)
        self.assertTrue((self.tools_root / "ffmpeg").exists())
        self.assertFalse((self.tools_root / "MANIFEST.json").exists())

    def test_scratch_directory_fsync_failure_is_prepublication_and_retained(self) -> None:
        _, _, pins = self.fixtures()
        real_fsync = stage_render_tools.os.fsync

        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("injected scratch directory fsync")
            return real_fsync(fd)

        with mock.patch.object(stage_render_tools.os, "fsync", side_effect=fail_directory):
            with self.assertRaisesRegex(OSError, "scratch directory fsync"):
                self.stage(pins)

        self.assertFalse((self.tools_root / "MANIFEST.json").exists())
        self.assertTrue((self.scratch_leaf / stage_render_tools.SCRATCH_MANIFEST).exists())

    def test_prelink_failure_retains_payloads_and_scratch_without_final_manifest(self) -> None:
        _, _, pins = self.fixtures()

        with mock.patch.object(stage_render_tools.os, "link", side_effect=OSError("injected link")):
            with self.assertRaisesRegex(OSError, "injected link"):
                self.stage(pins)

        self.assertTrue((self.tools_root / "ffmpeg").exists())
        self.assertTrue((self.tools_root / "ffprobe").exists())
        self.assertFalse((self.tools_root / "MANIFEST.json").exists())
        self.assertTrue((self.scratch_leaf / stage_render_tools.SCRATCH_MANIFEST).exists())

    def test_manifest_publication_collision_is_untouched(self) -> None:
        _, _, pins = self.fixtures()
        real_link = stage_render_tools.os.link
        injected = False

        def collide(*args, **kwargs):
            nonlocal injected
            if not injected:
                injected = True
                (self.tools_root / "MANIFEST.json").write_bytes(b"owner-manifest")
            return real_link(*args, **kwargs)

        with mock.patch.object(stage_render_tools.os, "link", side_effect=collide):
            with self.assertRaises(FileExistsError):
                self.stage(pins)

        self.assertTrue(injected)
        self.assertEqual((self.tools_root / "MANIFEST.json").read_bytes(), b"owner-manifest")
        self.assertTrue((self.scratch_leaf / stage_render_tools.SCRATCH_MANIFEST).exists())

    def test_postlink_directory_fsync_failure_is_typed_and_keeps_complete_publication(self) -> None:
        _, _, pins = self.fixtures()
        real_link = stage_render_tools.os.link
        real_fsync = stage_render_tools.os.fsync
        published = False

        def record_link(*args, **kwargs):
            nonlocal published
            result = real_link(*args, **kwargs)
            published = True
            return result

        def fail_published_directory(fd):
            if published and stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("injected destination directory fsync")
            return real_fsync(fd)

        with mock.patch.object(stage_render_tools.os, "link", side_effect=record_link), mock.patch.object(
            stage_render_tools.os, "fsync", side_effect=fail_published_directory
        ):
            with self.assertRaises(
                stage_render_tools.PublishedButDurabilityUnconfirmed
            ) as raised:
                self.stage(pins)

        error = raised.exception
        manifest_path = self.tools_root / "MANIFEST.json"
        self.assertEqual(error.state, "published_but_durability_unconfirmed")
        self.assertEqual(error.manifest_path, manifest_path)
        self.assertEqual(error.manifest_sha256, sha256(manifest_path.read_bytes()))
        self.assertTrue((self.tools_root / "ffmpeg").exists())
        self.assertTrue((self.tools_root / "ffprobe").exists())
        self.assertEqual(json.loads(manifest_path.read_bytes())["production_eligible"], False)
        self.assertTrue((self.scratch_leaf / stage_render_tools.SCRATCH_MANIFEST).exists())

    def test_staging_does_not_modify_existing_component_or_stager_sources(self) -> None:
        watched = (
            ROOT / "packaging" / "stage_voice.py",
            ROOT / "packaging" / "image_worker.py",
            ROOT / "packaging" / "music_worker.py",
            ROOT / "packaging" / "video_worker.py",
        )
        before = {path: sha256(path.read_bytes()) for path in watched}
        _, _, pins = self.fixtures()

        self.stage(pins)

        self.assertEqual({path: sha256(path.read_bytes()) for path in watched}, before)
        self.assertFalse((ROOT / "packaging" / "MANIFEST.json").exists())

    def test_same_copier_can_stage_into_an_existing_app_resources_tree(self) -> None:
        resources = self.case / "Studio Hub.app/Contents/Resources"
        resources.mkdir(parents=True)
        sentinel = resources / "release-notes.md"
        sentinel.write_text("keep", encoding="utf-8")
        _, _, pins = self.fixtures()

        result = stage_render_tools._stage_render_tools_into(
            resources, self.scratch_root, pins)

        tools = resources / "render-tools"
        self.assertEqual(result.staging_root, tools)
        self.assertEqual(result.manifest_path, tools / "MANIFEST.json")
        self.assertEqual((tools / "ffmpeg").read_bytes(), b"fixture-ffmpeg")
        self.assertEqual((tools / "ffprobe").read_bytes(), b"fixture-ffprobe")
        self.assertFalse(json.loads((tools / "MANIFEST.json").read_text())["production_eligible"])
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_render_authority_selector_is_closed_and_tool_only_ignores_present_leaves(self) -> None:
        raw, authority_pins, tool_pins = self.authority_fixtures()
        self.stage(tool_pins)
        manifest_before = (self.tools_root / "MANIFEST.json").read_bytes()

        result = self.stage_authority("tool-only", authority_pins)

        self.assertEqual(stage_render_tools.RENDER_AUTHORITY_MODES, (
            "tool-only", "descriptor-only", "sealed-candidate",
        ))
        self.assertEqual(result.staged_paths, ())
        self.assertEqual((self.tools_root / "MANIFEST.json").read_bytes(), manifest_before)
        self.assertFalse((self.tools_root / "PROFILE.json").exists())
        self.assertFalse((self.tools_root / "qualification-vectors").exists())
        self.assertFalse((self.tools_root / "QUALIFICATION.json").exists())
        with self.assertRaises(ValueError):
            self.stage_authority("other", ())

        spec = importlib.util.spec_from_file_location(
            "studiohub_stage_voice_contract", ROOT / "packaging/stage_voice.py"
        )
        stage_voice = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = stage_voice
        spec.loader.exec_module(stage_voice)
        parameters = inspect.signature(stage_voice.stage).parameters
        self.assertIn("render_authority", parameters)
        parameter = parameters["render_authority"]
        self.assertEqual(parameter.default, "tool-only")
        self.assertEqual(parameter.annotation, "RenderAuthorityMode")
        source = (ROOT / "packaging/stage_voice.py").read_text()
        self.assertIn("parser.add_argument('--render-authority'", source)
        self.assertIn("choices=RENDER_AUTHORITY_MODES", source)

    def test_descriptor_only_stages_exact_three_without_changing_tool_manifest(self) -> None:
        raw, authority_pins, tool_pins = self.authority_fixtures()
        self.stage(tool_pins)
        manifest_before = (self.tools_root / "MANIFEST.json").read_bytes()

        result = self.stage_authority("descriptor-only", authority_pins[:3])

        self.assertEqual(
            tuple(path.relative_to(self.tools_root).as_posix() for path in result.staged_paths),
            ("PROFILE.json", "TOOLS.json", "COMPILER.json"),
        )
        for relative in ("PROFILE.json", "TOOLS.json", "COMPILER.json"):
            self.assertEqual((self.tools_root / relative).read_bytes(), raw[relative])
            self.assertEqual(stat.S_IMODE((self.tools_root / relative).stat().st_mode), 0o644)
        self.assertEqual((self.tools_root / "MANIFEST.json").read_bytes(), manifest_before)
        self.assertFalse((self.tools_root / "qualification-vectors").exists())
        self.assertFalse((self.tools_root / "QUALIFICATION.json").exists())

    def test_frozen_authority_pins_are_the_packaged_descriptor_bytes(self) -> None:
        """Descriptor-only builds only if the pins and the packaged files are one identity."""
        pins = stage_render_tools.PRODUCTION_AUTHORITY_PINS
        self.assertEqual(
            tuple((pin.relative_path, pin.size_bytes, pin.sha256) for pin in pins),
            (
                ("PROFILE.json", 970,
                 "ad3f1c9d47d5f59400b1b1d03596a59fba6dc2f26817ac5cc2c77f8837f24863"),
                ("TOOLS.json", 796,
                 "98c25ae582d4ee43ee1cf665192365149f8d9f860c229a0be9da131df43eba8a"),
                ("COMPILER.json", 447,
                 "1239374f6cf60d025ee897c4c6ea394892d2bc146b0f1b2df94e5eaecc86b4a7"),
            ),
        )
        for pin in pins:
            self.assertTrue(pin.source.is_absolute())
            self.assertEqual(pin.source.stat().st_size, pin.size_bytes)
            self.assertEqual(sha256(pin.source.read_bytes()), pin.sha256)
            self.assertEqual(stat.S_IMODE(pin.source.stat().st_mode), 0o644)
        self.assertEqual(
            stage_render_tools._validate_authority_pins("descriptor-only", pins), pins)
        self.assertEqual(stage_render_tools._validate_authority_pins("tool-only", ()), ())
        # No qualification vector exists or ships: a selected sealed state fails closed.
        with self.assertRaisesRegex(ValueError, "qualification-vector"):
            stage_render_tools._validate_authority_pins("sealed-candidate", pins)

    def test_descriptor_only_stages_the_real_packaged_descriptors_unchanged(self) -> None:
        _, _, tool_pins = self.fixtures()
        self.stage(tool_pins)
        manifest_before = (self.tools_root / "MANIFEST.json").read_bytes()
        pins = stage_render_tools.PRODUCTION_AUTHORITY_PINS

        result = self.stage_authority("descriptor-only", pins)

        self.assertEqual(
            tuple(path.relative_to(self.tools_root).as_posix() for path in result.staged_paths),
            ("PROFILE.json", "TOOLS.json", "COMPILER.json"),
        )
        for pin in pins:
            self.assertEqual((self.tools_root / pin.relative_path).read_bytes(),
                             pin.source.read_bytes())
        self.assertEqual((self.tools_root / "MANIFEST.json").read_bytes(), manifest_before)
        self.assertFalse((self.tools_root / "qualification-vectors").exists())
        self.assertFalse((self.tools_root / "QUALIFICATION.json").exists())

    def test_late_label_triple_stages_alongside_unchanged_legacy(self) -> None:
        _, _, tool_pins = self.fixtures()
        self.stage(tool_pins)
        self.stage_authority("descriptor-only", stage_render_tools.PRODUCTION_AUTHORITY_PINS)
        legacy = {pin.relative_path: (self.tools_root / pin.relative_path).read_bytes()
                  for pin in stage_render_tools.PRODUCTION_AUTHORITY_PINS}
        manifest = (self.tools_root / "MANIFEST.json").read_bytes()
        pins = stage_render_tools.LATE_LABEL_AUTHORITY_PINS
        result = stage_render_tools._stage_render_authority_into(
            self.staged_root / "Contents/Resources", self.scratch_root, "descriptor-only", pins, late_labels=True)
        self.assertEqual(len(result.staged_paths), 3)
        for pin in pins:
            self.assertEqual(pin.size_bytes, len(pin.source.read_bytes()))
            self.assertEqual(pin.sha256, sha256(pin.source.read_bytes()))
            self.assertEqual((self.tools_root / "late-label" / pin.relative_path).read_bytes(),
                             pin.source.read_bytes())
        for name, data in legacy.items():
            self.assertEqual((self.tools_root / name).read_bytes(), data)
        self.assertEqual((self.tools_root / "MANIFEST.json").read_bytes(), manifest)
        compiler = json.loads((self.tools_root / "late-label/COMPILER.json").read_bytes())
        for key, name in (("final_render_compiler_sha256", "final_render_compiler.py"),
                          ("render_contract_sha256", "render_contract.py"),
                          ("render_hash_sha256", "render_hash_v1.py")):
            self.assertEqual(compiler[key], sha256(
                (ROOT / "components/hub/backend/render_late_label_v3" / name).read_bytes()))
        self.assertFalse((self.tools_root / "late-label/QUALIFICATION.json").exists())

    def test_late_label_staging_refuses_v1_and_non_descriptor_modes(self) -> None:
        _, _, tool_pins = self.fixtures()
        self.stage(tool_pins)
        with self.assertRaisesRegex(ValueError, "exact v3 descriptor pair"):
            stage_render_tools._stage_render_authority_into(
                self.staged_root / "Contents/Resources", self.scratch_root, "descriptor-only",
                stage_render_tools.PRODUCTION_AUTHORITY_PINS, late_labels=True)
        self.assertFalse((self.tools_root / "late-label").exists())
        with self.assertRaisesRegex(ValueError, "descriptors only"):
            stage_render_tools._stage_render_authority_into(
                self.staged_root / "Contents/Resources", self.scratch_root, "sealed-candidate",
                stage_render_tools.LATE_LABEL_AUTHORITY_PINS, late_labels=True)

    def test_stage_voice_and_the_stager_publish_one_authority_mode_vocabulary(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "studiohub_stage_voice_modes", ROOT / "packaging/stage_voice.py"
        )
        stage_voice = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = stage_voice
        spec.loader.exec_module(stage_voice)
        self.assertEqual(
            stage_voice.RENDER_AUTHORITY_MODES,
            stage_render_tools.RENDER_AUTHORITY_MODES,
        )

    def test_selected_authority_state_refuses_partial_extra_changed_and_unsafe_inputs(self) -> None:
        _, authority_pins, tool_pins = self.authority_fixtures()
        self.stage(tool_pins)
        cases = [
            ("descriptor-only", authority_pins[:1]),
            ("descriptor-only", authority_pins[:2]),
            ("descriptor-only", authority_pins),
            ("sealed-candidate", authority_pins[:3]),
            ("sealed-candidate", authority_pins + (authority_pins[-1],)),
        ]
        for mode, pins in cases:
            with self.subTest(mode=mode, count=len(pins)), self.assertRaises(ValueError):
                self.stage_authority(mode, pins)
        changed = authority_pins[1]
        changed.source.write_bytes(changed.source.read_bytes() + b" ")
        with self.assertRaises(ValueError):
            self.stage_authority("descriptor-only", authority_pins[:3])
        self.assertFalse((self.tools_root / "PROFILE.json").exists())

    def test_descriptor_only_rejects_duplicate_profile_keys_and_retains_bytes(self) -> None:
        raw, authority_pins, tool_pins = self.authority_fixtures()
        duplicate = b'{"format":"studio.render.profile.v1",' + raw["PROFILE.json"][1:]
        authority_pins = self.replace_authority_pin(
            authority_pins, "PROFILE.json", duplicate
        )
        self.stage(tool_pins)

        with self.assertRaises(ValueError):
            self.stage_authority("descriptor-only", authority_pins[:3])

        evidence = self.scratch_root / stage_render_tools.AUTHORITY_SCRATCH_LEAF
        self.assertEqual((evidence / "PROFILE.json").read_bytes(), duplicate)
        self.assertFalse((self.tools_root / "PROFILE.json").exists())

    def test_descriptor_only_rejects_open_profile_schema_and_retains_bytes(self) -> None:
        raw, authority_pins, tool_pins = self.authority_fixtures()
        profile = json.loads(raw["PROFILE.json"])
        profile["unexpected"] = None
        opened = canonical(profile)
        authority_pins = self.replace_authority_pin(
            authority_pins, "PROFILE.json", opened
        )
        self.stage(tool_pins)

        with self.assertRaises(ValueError):
            self.stage_authority("descriptor-only", authority_pins[:3])

        evidence = self.scratch_root / stage_render_tools.AUTHORITY_SCRATCH_LEAF
        self.assertEqual((evidence / "PROFILE.json").read_bytes(), opened)
        self.assertFalse((self.tools_root / "PROFILE.json").exists())

    def test_descriptor_only_rejects_malformed_trailing_json_and_retains_bytes(self) -> None:
        raw, authority_pins, tool_pins = self.authority_fixtures()
        malformed = raw["PROFILE.json"] + b"{"
        authority_pins = self.replace_authority_pin(
            authority_pins, "PROFILE.json", malformed
        )
        self.stage(tool_pins)

        with self.assertRaises(ValueError):
            self.stage_authority("descriptor-only", authority_pins[:3])

        evidence = self.scratch_root / stage_render_tools.AUTHORITY_SCRATCH_LEAF
        self.assertEqual((evidence / "PROFILE.json").read_bytes(), malformed)
        self.assertFalse((self.tools_root / "PROFILE.json").exists())

    def test_sealed_candidate_rejects_invalid_vector_self_digest(self) -> None:
        raw, authority_pins, tool_pins = self.authority_fixtures()
        vector = json.loads(raw["qualification-vectors/qualification-vector.json"])
        vector["qualification_digest"] = "f" * 64
        malformed = canonical(vector)
        authority_pins = self.replace_authority_pin(
            authority_pins, "qualification-vectors/qualification-vector.json", malformed
        )
        self.stage(tool_pins)

        with self.assertRaises(ValueError):
            self.stage_authority("sealed-candidate", authority_pins)

        evidence = self.scratch_root / stage_render_tools.AUTHORITY_SCRATCH_LEAF
        self.assertEqual(
            (evidence / "qualification-vectors/qualification-vector.json").read_bytes(),
            malformed,
        )
        self.assertFalse((self.tools_root / "PROFILE.json").exists())

    def test_sealed_candidate_rejects_packet_identity_with_valid_vector_digest(self) -> None:
        raw, authority_pins, tool_pins = self.authority_fixtures()
        vector = json.loads(raw["qualification-vectors/qualification-vector.json"])
        vector["packet"]["recipe"]["target_duration_sec"] = 1
        vector["qualification_digest"] = sha256(canonical({
            key: value for key, value in vector.items() if key != "qualification_digest"
        }))
        malformed = canonical(vector)
        authority_pins = self.replace_authority_pin(
            authority_pins, "qualification-vectors/qualification-vector.json", malformed
        )
        self.stage(tool_pins)

        with self.assertRaises(ValueError):
            self.stage_authority("sealed-candidate", authority_pins)

        evidence = self.scratch_root / stage_render_tools.AUTHORITY_SCRATCH_LEAF
        self.assertEqual(
            (evidence / "qualification-vectors/qualification-vector.json").read_bytes(),
            malformed,
        )
        self.assertFalse((self.tools_root / "PROFILE.json").exists())

    def test_sealed_candidate_stages_one_vector_and_retains_failed_publish_evidence(self) -> None:
        raw, authority_pins, tool_pins = self.authority_fixtures()
        self.stage(tool_pins)
        with mock.patch.object(
            stage_render_tools.os, "link", side_effect=OSError("injected authority publish")
        ):
            with self.assertRaisesRegex(OSError, "authority publish"):
                self.stage_authority("sealed-candidate", authority_pins)
        self.assertFalse((self.tools_root / "PROFILE.json").exists())
        evidence = self.scratch_root / stage_render_tools.AUTHORITY_SCRATCH_LEAF
        self.assertTrue(evidence.is_dir())
        self.assertEqual(
            sorted(path.relative_to(evidence).as_posix() for path in evidence.rglob("*") if path.is_file()),
            sorted(raw),
        )

    def test_sealed_candidate_stages_exact_three_plus_one_vector(self) -> None:
        raw, authority_pins, tool_pins = self.authority_fixtures()
        self.stage(tool_pins)

        result = self.stage_authority("sealed-candidate", authority_pins)

        self.assertEqual(
            tuple(path.relative_to(self.tools_root).as_posix() for path in result.staged_paths),
            tuple(raw),
        )
        for relative, payload in raw.items():
            self.assertEqual((self.tools_root / relative).read_bytes(), payload)
        self.assertFalse((self.tools_root / "QUALIFICATION.json").exists())


if __name__ == "__main__":
    unittest.main()
