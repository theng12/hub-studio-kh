#!/usr/bin/env python3
"""The packaged Hub staging caller stages the v5 cue-overlay descriptor triple."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
for entry in (str(ROOT), str(PACKAGING)):
    if entry not in sys.path:
        sys.path.insert(0, entry)


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve the defining module by name
    spec.loader.exec_module(module)
    return module


stage_voice = load("stage_voice_under_test", PACKAGING / "stage_voice.py")
stage_render_tools = load("stage_render_tools", PACKAGING / "stage_render_tools.py")


class StageVoiceCueAuthorityTests(unittest.TestCase):
    def stage(self, mode: str) -> Path:
        with tempfile.TemporaryDirectory() as tmp:
            resources = Path(tmp) / "Contents" / "Resources"
            (resources / "render-tools").mkdir(parents=True)
            # The two pinned ffmpeg payloads are ~264 MB; everything else is the real caller.
            with mock.patch.object(
                    stage_render_tools, "_stage_render_tools_into", return_value=None):
                stage_voice.stage_hub_render_authority(resources, mode, ())
            staged = {}
            for path in sorted((resources / "render-tools").rglob("*")):
                if path.is_file():
                    staged[str(path.relative_to(resources / "render-tools"))] = path.read_bytes()
            self.assertFalse((resources / "render-tools" / "cue-overlay"
                              / "qualification-vectors").exists())
            return staged

    def test_descriptor_only_caller_stages_the_v5_cue_overlay_triple(self):
        staged = self.stage("descriptor-only")
        pinned = PACKAGING / "render-authority-cue-overlay"
        for name in ("PROFILE.json", "TOOLS.json", "COMPILER.json"):
            self.assertEqual(staged[f"cue-overlay/{name}"], (pinned / name).read_bytes())
        # Existing authority states are untouched by the added caller.
        for name in ("PROFILE.json", "TOOLS.json", "COMPILER.json"):
            self.assertIn(f"late-label/{name}", staged)
            self.assertIn(f"subtitle/{name}", staged)
            self.assertEqual(staged[f"subtitle/{name}"],
                             (PACKAGING / "render-authority-subtitle" / name).read_bytes())

    def test_cue_overlay_descriptors_bind_the_committed_compiler_and_stay_non_production(self):
        compiler = json.loads((PACKAGING / "render-authority-cue-overlay"
                               / "COMPILER.json").read_text())
        self.assertEqual(
            compiler["final_render_compiler_sha256"],
            hashlib.sha256(
                (ROOT / "components/hub/backend/final_render_compiler.py").read_bytes()
            ).hexdigest())
        self.assertEqual(compiler["implementation"], "hub-final-render-cue-overlay-v1")
        profile = json.loads((PACKAGING / "render-authority-cue-overlay"
                              / "PROFILE.json").read_text())
        self.assertEqual(profile["format"], "studio.render.profile.v5")
        self.assertEqual(profile["implementation"], "story-classic-cue-overlay-v1")
        self.assertEqual(profile["combination_set"],
                         "retained-classic-cue-overlay-candidate-v1")
        self.assertIs(profile["production_eligible"], False)
        self.assertEqual(
            profile["compiler_artifact_sha256"],
            hashlib.sha256((PACKAGING / "render-authority-cue-overlay"
                            / "COMPILER.json").read_bytes()).hexdigest())
        self.assertEqual(
            profile["execution_bounds"],
            json.loads((PACKAGING / "render-authority-subtitle"
                        / "PROFILE.json").read_text())["execution_bounds"])

    def test_tool_only_caller_stages_no_authority_descriptors(self):
        staged = self.stage("tool-only")
        self.assertEqual(staged, {})


if __name__ == "__main__":
    unittest.main()
