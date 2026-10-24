#!/usr/bin/env python3
"""Qualification receipt and enabled-record authority integrity.

Lives in its own module so lifecycle regressions in test_render_executor.py stay disjoint.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from components.hub.backend.render_executor import (  # noqa: E402
    InstalledRenderAuthorityLoader,
    RenderExecutor,
)

INTENT_ID = "0b6c8d1e-3f4a-4b5c-8d7e-9f0a1b2c3d4e"
HARDWARE_CLASS = "m4-16gb"


def compact(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class QualificationReceiptAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / "_trash/render-executor-testroots" / uuid4().hex
        self.root.mkdir(parents=True)
        self.resources = self.root / "resources"
        self.tools_root = self.resources / "render-tools"
        self.tools_root.mkdir(parents=True)
        self.data_root = self.root / "data"
        (self.data_root / "render-authority").mkdir(parents=True)
        (self.data_root / "render-authority").chmod(0o700)
        self.now = 1_000

        tool_files = {}
        for name, payload in (("ffmpeg", b"sealed-ffmpeg"),
                              ("ffprobe", b"sealed-ffprobe")):
            path = self.tools_root / name
            path.write_bytes(payload)
            path.chmod(0o755)
            tool_files[name] = {
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        self.tool_files = tool_files
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
        (self.tools_root / "TOOLS.json").write_bytes(tool_raw)
        backend = ROOT / "components/hub/backend"
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
        (self.tools_root / "COMPILER.json").write_bytes(compiler_raw)
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
        self.profile_raw = compact(profile_descriptor)
        (self.tools_root / "PROFILE.json").write_bytes(self.profile_raw)
        manifest = {
            "format": "studiohub.render-tools.payload.v1", "tool_version": "8.1",
            "payload_format": "macos-universal", "files": [
                {"name": name, "path": f"render-tools/{name}", **tool_files[name]}
                for name in ("ffmpeg", "ffprobe")
            ], "production_eligible": False,
        }
        (self.tools_root / "MANIFEST.json").write_bytes(compact(manifest))
        self.document = {
            "format": "studio.render.qualification-receipt.v1",
            "production_eligible": False,
            "profile_digest": hashlib.sha256(self.profile_raw).hexdigest(),
            "tools_digest": hashlib.sha256(tool_raw).hexdigest(),
            "compiler_digest": hashlib.sha256(compiler_raw).hexdigest(),
            "hardware_class": HARDWARE_CLASS, "selected_tier": "hardware-h264",
            "exact_encoder": "h264_videotoolbox", "font_support": False,
            "bounds": {
                "max_rss_bytes": 100_000_000, "max_wall_seconds": 30,
                "min_available_memory_bytes": 2_000_000_000,
                "output_limit_bytes": 1_000_000,
            },
            "files": tool_files,
        }
        self.document["qualification_digest"] = (
            InstalledRenderAuthorityLoader._record_digest(self.document))
        self.loader = InstalledRenderAuthorityLoader(
            self.resources, data_root=self.data_root, clock=lambda: self.now,
            hardware_identity=lambda: {"chip": "Apple M4", "memory_gb": 16},
        )

    def write_record(self, *names: str, value) -> Path:
        directory = self.data_root / "render-authority"
        for name in names[:-1]:
            directory = directory / name
            directory.mkdir(mode=0o700, exist_ok=True)
            directory.chmod(0o700)
        path = directory / names[-1]
        if path.exists():
            # The owner's rule is never to delete: preserve the prior fixture bytes
            # as a versioned record under the project's _trash before replacing it.
            preserved = (
                ROOT / "_trash/render-executor-testroots/preserved"
                / uuid4().hex / names[-1])
            preserved.parent.mkdir(parents=True, exist_ok=True)
            path.rename(preserved)
        path.write_bytes(compact(value))
        path.chmod(0o600)
        return path

    def measured(self, **changes) -> dict:
        value = {
            "peak_rss_bytes": 1, "wall_seconds": 1, "min_free_bytes": 1,
            "output_bytes": 1, "hub_job_id": "job-0", "receipt_digest": "5" * 64,
        }
        value.update(changes)
        return value

    def enabled_record(self, **changes) -> dict:
        return {**self.document, "intent_id": INTENT_ID,
                "measured": self.measured(), **changes}

    def test_authority_record_read_ignores_finder_neighbors(self) -> None:
        from components.hub.backend.render_authority_files import read_record
        import os

        path = self.write_record("enabled", f"{HARDWARE_CLASS}.json", value=self.enabled_record())
        for directory in (self.data_root, path.parent.parent, path.parent):
            for name in (".DS_Store", "._x"):
                (directory / name).write_bytes(b"finder")
        self.assertEqual(read_record(self.data_root, "enabled", path.name,
                                     expected_uid=os.geteuid()), path.read_bytes())
        # The named record retains the exact existing permissions guard.
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            read_record(self.data_root, "enabled", path.name, expected_uid=os.geteuid())

    def test_enabled_measured_evidence_is_typed_and_within_declared_bounds(self) -> None:
        for label, measured in {
            "string peak": self.measured(peak_rss_bytes="1"),
            "negative peak": self.measured(peak_rss_bytes=-1),
            "peak over bound": self.measured(peak_rss_bytes=100_000_001),
            "zero wall": self.measured(wall_seconds=0),
            "wall over bound": self.measured(wall_seconds=31),
            "zero free": self.measured(min_free_bytes=0),
            "zero output": self.measured(output_bytes=0),
            "output over bound": self.measured(output_bytes=1_000_001),
            "empty job": self.measured(hub_job_id=""),
            "non-printable job": self.measured(hub_job_id="job\n"),
            "uppercase digest": self.measured(receipt_digest="A" * 64),
            "missing digest": {key: value for key, value in self.measured().items()
                               if key != "receipt_digest"},
        }.items():
            with self.subTest(label=label):
                self.write_record(
                    "enabled", f"{HARDWARE_CLASS}.json", value=self.enabled_record(measured=measured))
                self.assertIsNone(self.loader.snapshot())

        self.write_record("enabled", f"{HARDWARE_CLASS}.json", value=self.enabled_record())
        self.assertIsNotNone(self.loader.snapshot())

    def test_enabled_intent_id_must_be_a_canonical_uuid(self) -> None:
        for label, intent_id in {
            "short": "intent", "uppercase": INTENT_ID.upper(), "non-string": 7,
        }.items():
            with self.subTest(label=label):
                self.write_record(
                    "enabled", f"{HARDWARE_CLASS}.json",
                    value=self.enabled_record(intent_id=intent_id))
                self.assertIsNone(self.loader.snapshot())

    def test_non_null_hardware_or_fidelity_pins_are_refused_not_ignored(self) -> None:
        self.assertIsNotNone(self.loader._authority(self.document))
        for pin in ("hardware_qualification_record_sha256", "fidelity_record_sha256"):
            with self.subTest(pin=pin):
                profile = json.loads(self.profile_raw)
                profile[pin] = "3" * 64
                raw = compact(profile)
                (self.tools_root / "PROFILE.json").write_bytes(raw)
                changed = {**self.document, "profile_digest": hashlib.sha256(raw).hexdigest()}
                changed["qualification_digest"] = (
                    InstalledRenderAuthorityLoader._record_digest(changed))
                self.assertIsNone(self.loader._authority(changed))
                (self.tools_root / "PROFILE.json").write_bytes(self.profile_raw)

    def test_write_receipt_refuses_invalid_documents_and_measured_evidence(self) -> None:
        receipts = self.data_root / "render-authority/receipts"
        invalid = {
            "tampered qualification digest": (
                {**self.document, "qualification_digest": "0" * 64}, self.measured()),
            "extra document field": ({**self.document, "extra": 1}, self.measured()),
            "non-canonical intent": (self.document, self.measured(), "intent"),
            "measured string": (self.document, self.measured(peak_rss_bytes="1")),
            "measured out of bounds": (self.document, self.measured(output_bytes=0)),
        }
        for label, case in invalid.items():
            with self.subTest(label=label):
                document, measured, *intent = case
                self.assertFalse(self.loader.write_receipt(
                    document, intent[0] if intent else INTENT_ID, measured))
                self.assertFalse(receipts.exists() and any(receipts.iterdir()))

        self.assertTrue(self.loader.write_receipt(self.document, INTENT_ID, self.measured()))
        expected = receipts / InstalledRenderAuthorityLoader._receipt_name(
            HARDWARE_CLASS, INTENT_ID, self.document["qualification_digest"])
        self.assertEqual(json.loads(expected.read_bytes())["measured"], self.measured())

    def test_each_intent_retains_its_own_receipt(self) -> None:
        receipts = self.data_root / "render-authority/receipts"
        second_intent = str(uuid4())
        self.assertTrue(self.loader.write_receipt(self.document, INTENT_ID, self.measured()))
        self.assertTrue(self.loader.write_receipt(
            self.document, second_intent, self.measured(hub_job_id="job-2")))
        self.assertEqual(
            sorted(path.name for path in receipts.iterdir()),
            sorted([
                InstalledRenderAuthorityLoader._receipt_name(
                    HARDWARE_CLASS, INTENT_ID, self.document["qualification_digest"]),
                InstalledRenderAuthorityLoader._receipt_name(
                    HARDWARE_CLASS, second_intent, self.document["qualification_digest"]),
            ]))
        # A repeated intent can never replace the evidence it already retained.
        self.assertFalse(self.loader.write_receipt(
            self.document, INTENT_ID, self.measured(hub_job_id="job-3")))

    def test_post_validation_wall_over_bound_fails_without_clamping(self) -> None:
        measured = {"peak_rss_bytes": 1, "min_free_bytes": 1}
        receipt = SimpleNamespace(output_size_bytes=1, receipt_digest="5" * 64)
        context = SimpleNamespace(
            purpose="qualification",
            qualification_permit=SimpleNamespace(vector_id=INTENT_ID),
            qualification=SimpleNamespace(document=self.document), hub_job_id="job-1")
        clock = SimpleNamespace(value=0.0)
        executor = RenderExecutor(
            runtime=SimpleNamespace(), assets=None, authority=self.loader,
            machine="local", broker_api=SimpleNamespace(), monotonic=lambda: clock.value)

        with mock.patch.object(self.loader, "write_receipt", return_value=True) as writer:
            clock.value = 20.2
            self.assertTrue(executor._record_qualification(context, receipt, measured, 0.0))
            self.assertEqual(writer.call_args.args[2]["wall_seconds"], 21,
                             "the actual wall is recorded, never clamped to a rounder value")
            clock.value = 30.1
            self.assertIs(
                executor._record_qualification(context, receipt, measured, 0.0), False)
            self.assertEqual(writer.call_count, 1,
                             "a post-validation wall over the bound must not write a receipt")

    def test_qualification_without_a_durable_receipt_is_not_success(self) -> None:
        measured = {"peak_rss_bytes": 1, "min_free_bytes": 1}
        receipt = SimpleNamespace(output_size_bytes=1, receipt_digest="5" * 64)
        context = SimpleNamespace(
            purpose="qualification", qualification_permit=SimpleNamespace(vector_id=INTENT_ID),
            qualification=SimpleNamespace(document=self.document), hub_job_id="job-1")
        # The document's explicit max_wall_seconds (30) bounds the real clock; each call
        # starts its own stopwatch immediately beforehand, so the actual elapsed time is
        # measured rather than replaced by a fake one.
        executor = RenderExecutor(
            runtime=SimpleNamespace(), assets=None, authority=self.loader,
            machine="local", broker_api=SimpleNamespace())

        with mock.patch.object(self.loader, "write_receipt", return_value=False):
            self.assertIs(executor._record_qualification(
                context, receipt, measured, time.monotonic()), False)

        no_writer = RenderExecutor(
            runtime=SimpleNamespace(), assets=None, authority=SimpleNamespace(),
            machine="local", broker_api=SimpleNamespace())
        self.assertFalse(no_writer._record_qualification(
            context, receipt, measured, time.monotonic()))

        production = SimpleNamespace(
            purpose="production", qualification_permit=None,
            qualification=SimpleNamespace(document=None), hub_job_id="job-1")
        self.assertTrue(executor._record_qualification(
            production, receipt, measured, time.monotonic()))

        # A successful writer is the only path that reports a durable receipt.
        with mock.patch.object(self.loader, "write_receipt", return_value=True):
            self.assertTrue(executor._record_qualification(
                context, receipt, measured, time.monotonic()))


if __name__ == "__main__":
    unittest.main()
