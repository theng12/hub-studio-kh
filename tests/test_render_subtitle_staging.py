#!/usr/bin/env python3
"""Offline checks for optional v4 subtitle authority staging."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "_trash" / "render-subtitle-staging-tests"
ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
RUN_ROOT = Path(tempfile.mkdtemp(prefix="run-", dir=ARTIFACT_ROOT))
MODULE_PATH = ROOT / "packaging" / "stage_render_tools.py"
SPEC = importlib.util.spec_from_file_location("studiohub_stage_render_subtitle_tools", MODULE_PATH)
stage_render_tools = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = stage_render_tools
SPEC.loader.exec_module(stage_render_tools)

sys.path.insert(0, str(ROOT))

# The frozen v4 candidate reuses the existing TOOLS.json bytes unchanged.
TOOLS_BYTES = (ROOT / "packaging/render-authority-late-label/TOOLS.json").read_bytes()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def compiler_bytes(implementation: str = "hub-final-render-subtitle-v1") -> bytes:
    return canonical({
        "format": "studio.render.compiler.v1",
        "implementation": implementation,
        "python_runtime_sha256": "1" * 64,
        "render_hash_sha256": "2" * 64,
        "render_contract_sha256": "3" * 64,
        "final_render_compiler_sha256": "4" * 64,
    })


def profile_bytes(compiler: bytes, tools: bytes = TOOLS_BYTES) -> bytes:
    return canonical({
        "format": "studio.render.profile.v4",
        "implementation": "story-classic-subtitle-v1",
        "recipe_namespace": "studio.render.final.v1",
        "compiler_artifact_sha256": sha256(compiler),
        "tools_artifact_sha256": sha256(tools),
        "combination_set": "retained-classic-subtitle-candidate-v1",
        "tier_policy": "story-final-chain-v1",
        "output_policy": "story-final-output-v1",
        "execution_bounds": {
            "bounds_version": 2,
            "canvas_range": {"max_edge": 3840, "max_pixels": 8294400},
            "working_fps": [24, 25, 30, 60],
            "max_duration_sec": 10800,
            "max_operations": 32,
            "max_assets": 32,
            "max_asset_bytes": 8589934592,
            "max_total_input_bytes": 17179869184,
            "max_pixel_frames": 5374771200000,
            "scratch_reserve_bytes": 17179869184,
            "output_limit_bytes": 8589934592,
            "process_tree_rss_limit_bytes": 10737418240,
            "cumulative_wall_seconds": 14400,
            "max_subtitle_fonts": 8,
            "max_subtitle_font_bytes": 33554432,
            "max_subtitle_ass_bytes": 4194304,
        },
        "hardware_qualification_record_sha256": None,
        "fidelity_record_sha256": None,
        "production_eligible": False,
    })


class SubtitleStagingTests(unittest.TestCase):
    _case_number = 0

    def setUp(self) -> None:
        type(self)._case_number += 1
        self.case = RUN_ROOT / f"{type(self)._case_number:02d}-{self._testMethodName}"
        self.case.mkdir(mode=0o700)
        self.resources = self.case / "Resources"
        self.tools_root = self.resources / "render-tools"
        self.tools_root.mkdir(parents=True, mode=0o700)
        self.scratch = self.case / "scratch"
        self.scratch.mkdir(mode=0o700)

    def stage(self, mode, pins, **flags):
        return stage_render_tools._stage_render_authority_into(
            self.resources, self.scratch, mode, pins, **flags)

    def pins(self, documents: dict[str, bytes]):
        source = self.case / "authority-source"
        pins = []
        for relative, payload in documents.items():
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            path.chmod(0o644)
            pins.append(stage_render_tools._AuthorityPin(
                relative, path, len(payload), sha256(payload)))
        return tuple(pins)

    def subtitle_documents(self) -> dict[str, bytes]:
        compiler = compiler_bytes()
        return {
            "PROFILE.json": profile_bytes(compiler),
            "TOOLS.json": TOOLS_BYTES,
            "COMPILER.json": compiler,
        }

    def stage_legacy(self) -> dict[str, bytes]:
        self.stage("descriptor-only", stage_render_tools.PRODUCTION_AUTHORITY_PINS)
        self.stage("descriptor-only", stage_render_tools.LATE_LABEL_AUTHORITY_PINS,
                   late_labels=True)
        legacy = {}
        for root in (self.tools_root, self.tools_root / "late-label"):
            for path in root.iterdir():
                if path.is_file():
                    legacy[path.relative_to(self.tools_root).as_posix()] = path.read_bytes()
        return legacy

    def test_subtitle_pins_match_the_canonical_triple_and_legacy_stays_absent(self) -> None:
        pins = stage_render_tools.SUBTITLE_AUTHORITY_PINS
        self.assertEqual(
            tuple(pin.relative_path for pin in pins),
            ("PROFILE.json", "TOOLS.json", "COMPILER.json"),
        )
        for pin in pins:
            self.assertEqual(pin.size_bytes, len(pin.source.read_bytes()))
            self.assertEqual(pin.sha256, sha256(pin.source.read_bytes()))
        self.stage_legacy()
        self.assertFalse((self.tools_root / "subtitle").exists())

    def test_real_canonical_subtitle_triple_stages_unchanged(self) -> None:
        legacy = self.stage_legacy()
        pins = stage_render_tools.SUBTITLE_AUTHORITY_PINS

        result = self.stage("descriptor-only", pins, subtitles=True)

        self.assertEqual(len(result.staged_paths), 3)
        for pin in pins:
            self.assertEqual(
                (self.tools_root / "subtitle" / pin.relative_path).read_bytes(),
                pin.source.read_bytes(),
            )
        for relative, payload in legacy.items():
            self.assertEqual((self.tools_root / relative).read_bytes(), payload)
        compiler = json.loads((self.tools_root / "subtitle/COMPILER.json").read_bytes())
        self.assertEqual(compiler["format"], "studio.render.compiler.v1")
        self.assertEqual(compiler["implementation"], "hub-final-render-subtitle-v1")
        self.assertFalse((self.tools_root / "subtitle/qualification-vectors").exists())

    def test_v4_subtitle_triple_stages_beside_unchanged_legacy(self) -> None:
        legacy = self.stage_legacy()
        documents = self.subtitle_documents()

        result = self.stage(
            "descriptor-only", self.pins(documents), subtitles=True)

        self.assertEqual(
            tuple(path.relative_to(self.tools_root).as_posix() for path in result.staged_paths),
            ("subtitle/PROFILE.json", "subtitle/TOOLS.json", "subtitle/COMPILER.json"),
        )
        for relative, payload in documents.items():
            self.assertEqual((self.tools_root / "subtitle" / relative).read_bytes(), payload)
        for relative, payload in legacy.items():
            self.assertEqual((self.tools_root / relative).read_bytes(), payload)
        self.assertFalse((self.tools_root / "subtitle/qualification-vectors").exists())
        self.assertFalse((self.tools_root / "subtitle/QUALIFICATION.json").exists())

    def test_subtitle_staging_refuses_a_non_v4_pair(self) -> None:
        self.stage_legacy()
        v3 = {
            relative: (ROOT / "packaging/render-authority-late-label" / relative).read_bytes()
            for relative in ("PROFILE.json", "TOOLS.json", "COMPILER.json")
        }
        with self.assertRaisesRegex(ValueError, "exact v4 descriptor pair"):
            self.stage("descriptor-only", self.pins(v3), subtitles=True)
        self.assertFalse((self.tools_root / "subtitle").exists())

    def test_subtitle_staging_refuses_a_disk_computed_hash_that_differs_from_the_pin(self) -> None:
        self.stage_legacy()
        pins = list(self.pins(self.subtitle_documents()))
        pins[0] = stage_render_tools._AuthorityPin(
            pins[0].relative_path, pins[0].source, pins[0].size_bytes, "0" * 64)
        with self.assertRaisesRegex(ValueError, "content mismatch"):
            self.stage("descriptor-only", tuple(pins), subtitles=True)
        self.assertFalse((self.tools_root / "subtitle").exists())

    def test_subtitle_staging_requires_descriptor_only_mode(self) -> None:
        pins = self.pins(self.subtitle_documents())
        with self.assertRaisesRegex(ValueError, "descriptors only"):
            self.stage("sealed-candidate", pins, subtitles=True)
        with self.assertRaisesRegex(ValueError, "separately"):
            self.stage("descriptor-only", pins, subtitles=True, late_labels=True)
        self.assertFalse((self.tools_root / "subtitle").exists())


if __name__ == "__main__":
    unittest.main()
