from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from components.image.app.backend import generation, main
from components.hub.backend import broker, memory_admission


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "qwen_turbo_owner_" * 3


class QwenTurboRegistryTests(unittest.TestCase):
    def test_registry_publishes_only_the_turbo_eight_step_contract(self) -> None:
        model = generation.QWEN_IMAGE_21_TURBO

        self.assertIs(generation.MODELS[model.repo], model)
        self.assertEqual(model.repo, "mflux-community/qwen-image-2-1-turbo-mflux-q4")
        self.assertEqual(model.revision, "bda8951ec6b8cc288a961432fe15c22433521a1d")
        self.assertEqual(model.package_id, "qwen-image-2-1-turbo-mflux-q4")
        self.assertEqual(model.model_class,
                         "components.image.app.backend.qwen_turbo:QwenTurbo")
        self.assertEqual(model.label, "Qwen Image 2.1 Turbo Low-RAM MLX 4-bit")
        self.assertEqual(model.defaults["steps"], 8)
        self.assertEqual(model.limits["min_steps"], 8)
        self.assertEqual(model.limits["max_steps"], 8)
        self.assertEqual(model.defaults["guidance"], 1.0)
        self.assertEqual(model.limits["min_guidance"], 1.0)
        self.assertEqual(model.limits["max_guidance"], 1.0)
        self.assertEqual(generation.size_presets(model), generation.QWEN_MFLUX_SIZE_PRESETS)
        self.assertEqual(model.dimension_multiple, 32)
        self.assertEqual(model.qualified_minimum_memory_gb, 16.0)
        self.assertEqual(model.min_free_memory_gb, 8.0)
        self.assertIsNone(model.seconds_per_step)
        self.assertFalse(model.resident_allowed)
        self.assertEqual(model.qualification["adoption_basis"], "owner decision 2026-10-13")
        self.assertEqual(model.qualification["turbo_measurements"], "unmeasured")
        self.assertIn("regular Qwen Image 2.1 Low-RAM", model.qualification["min_free_memory_note"])
        reason = model.memory.single_mode_reason or ""
        for control in ("prompt materialization", "zero cache", "tiled VAE", "eviction"):
            self.assertIn(control, reason)

    def test_validator_refuses_non_turbo_steps_cfg_geometry_and_optional_inputs(self) -> None:
        model = generation.QWEN_IMAGE_21_TURBO
        accepted = generation.validate_request({
            "repo": model.repo, "prompt": "A temple at blue hour", "steps": 8,
            "guidance": 1.0, "aspect_ratio": "1:1", "width": 1024, "height": 1024,
        })
        self.assertEqual((accepted["steps"], accepted["guidance"]), (8, 1.0))

        for changes, message in (
            ({"steps": 7}, "Steps must be between 8 and 8"),
            ({"steps": 9}, "Steps must be between 8 and 8"),
            ({"guidance": 0.0}, "Guidance must be finite and between 1 and 1"),
            ({"guidance": 1.1}, "Guidance must be finite and between 1 and 1"),
            ({"aspect_ratio": "4:3", "width": 1152, "height": 864},
             "qualified 1K aspect ratio"),
            ({"negative_prompt": "fog"}, "negative prompt"),
            ({"lora_paths": ["turbo.safetensors"]}, "LoRAs"),
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message):
                generation.validate_request({
                    "repo": model.repo, "prompt": "A temple at blue hour",
                    "aspect_ratio": "1:1", "width": 1024, "height": 1024,
                    **changes,
                })

    def test_the_reference_lane_accepts_references_and_names_a_missing_file(self) -> None:
        """The row is the second Reference-to-Image model (owner, 2026-10-26).

        A reference request is an *edit* request on this row now, so it is validated as one: an
        unreadable reference is a blocking error naming the file, never permission to fall back
        to text-to-image.
        """
        model = generation.QWEN_IMAGE_21_TURBO
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / 'reference.png'
            reference.write_bytes(b'not a png')
            with self.assertRaisesRegex(ValueError, 'not a readable PNG, JPEG or WebP image'):
                generation.validate_request({
                    'repo': model.repo, 'prompt': 'A temple at blue hour',
                    'aspect_ratio': '1:1', 'reference_images': [str(reference)]})
            with self.assertRaisesRegex(ValueError, 'missing or unreadable'):
                generation.validate_request({
                    'repo': model.repo, 'prompt': 'A temple at blue hour',
                    'aspect_ratio': '1:1', 'reference_images': [str(root / 'absent.png')]})

    def test_explicit_false_synthetic_candidate_still_blocks_submit_and_readiness(self) -> None:
        model = generation.QWEN_IMAGE_21_TURBO
        manifest = json.loads(
            (ROOT / "model-manifests/qwen-image-2-1-turbo-mflux-q4.json").read_text())
        manifest["production_allowed"] = False
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = generation.GenerationManager(
                root / "data", root / "models", {}, "a" * 64,
                manifests=[manifest], total_memory_bytes=16_000_000_000)
            manager.preflight = lambda _repo: {"state": "files_verified", "folder": str(root)}
            manager._enqueue = lambda *_args: None
            app = main.create_app(manager, TOKEN)
            headers = {"X-Studio-Token": TOKEN}
            request = {"repo": model.repo, "prompt": "A temple at blue hour"}

            with patch.object(main, "_host_facts", return_value={
                "eligible": True, "macos": "26.6", "unified_memory_bytes": 16_000_000_000,
            }):
                client = TestClient(app)
                row = next(item for item in client.get("/api/catalog", headers=headers).json()["models"]
                           if item["repo"] == model.repo)
                response = client.post("/api/generate/txt2img", headers=headers, json=request)

            self.assertFalse(row["production_allowed"])
            self.assertFalse(row["execution_ready"])
            self.assertEqual(row["readiness"]["state"], "production_blocked")
            self.assertEqual(response.status_code, 409)
            self.assertIn("MODEL_UNQUALIFIED", response.text)

    def test_normal_production_manifest_submits_without_private_permission_on_16gb(self) -> None:
        turbo = generation.QWEN_IMAGE_21_TURBO
        turbo_manifest = json.loads(
            (ROOT / "model-manifests/qwen-image-2-1-turbo-mflux-q4.json").read_text())
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = generation.GenerationManager(
                root / "data", root / "models", {}, "a" * 64,
                manifests=[turbo_manifest], total_memory_bytes=16_000_000_000)
            manager.preflight = lambda _repo: {"state": "files_verified", "folder": str(root)}
            manager._enqueue = lambda *_args: None

            job = manager.submit({"repo": turbo.repo, "prompt": "A temple at blue hour"})

        self.assertEqual(job["state"], "queued")
        self.assertEqual(job["params"]["steps"], 8)

    def test_hub_admission_uses_manifest_floor_to_accept_16gb_and_refuse_8gb(self) -> None:
        turbo = generation.QWEN_IMAGE_21_TURBO
        memory_admission.reset_for_tests()
        row = {"repo": turbo.repo, "min_unified_memory_gb": turbo.qualified_minimum_memory_gb}
        self.assertEqual(memory_admission.requirement_gb(
            turbo.repo, row, use_override=False), (16.0, "manifest"))
        for total_gb, expected in ((8.59, "skip"), (17.18, "run")):
            with self.subTest(total_gb=total_gb):
                published = memory_admission.publish_floor(
                    {**row, "memory_eligible": True}, total_gb)
                self.assertIs(published["memory_eligible"], expected == "run")
                self.assertEqual(broker._memory_gate(
                    {"min_total": 16.0, "min_free": 8.0},
                    {"total_gb": total_gb, "available_gb": 9.0})[0], expected)


    def test_mflux_presets_are_engine_valid_and_catalog_names_both_modes(self) -> None:
        """Both mflux Qwen rows (text lane and reference lane) publish only canvases their
        engine accepts; the 720-height family table stays on the mlx-serve row that measured
        it. The catalog's `image_modes` carries the two owner-visible labels, this Mac's live
        availability, and the reference contract nested on the reference entry — the same
        object `lane_contract` publishes, never a second copy.
        """
        model = generation.QWEN_IMAGE_21_TURBO
        presets = generation.size_presets(model)
        self.assertEqual(presets, generation.QWEN_MFLUX_SIZE_PRESETS)
        self.assertEqual(model.dimension_multiple, 32)
        for width, height in presets.values():
            self.assertEqual(width % 32, 0)
            self.assertEqual(height % 32, 0)
        lowram = generation.QWEN_IMAGE_21_LOW_RAM
        self.assertEqual(generation.size_presets(lowram), generation.QWEN_MFLUX_SIZE_PRESETS)
        self.assertEqual(lowram.dimension_multiple, 32)
        mlxserve = generation.QWEN_IMAGE_21
        self.assertEqual(generation.size_presets(mlxserve), generation.QWEN_SIZE_PRESETS)
        self.assertIsNone(mlxserve.dimension_multiple)

        manifest = json.loads(
            (ROOT / "model-manifests/qwen-image-2-1-turbo-mflux-q4.json").read_text())
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = generation.GenerationManager(
                root / "data", root / "models", {}, "a" * 64,
                manifests=[manifest], total_memory_bytes=32_000_000_000)
            manager.preflight = lambda _repo: {"state": "files_verified", "folder": str(root)}
            manager._enqueue = lambda *_args: None
            app = main.create_app(manager, TOKEN)
            headers = {"X-Studio-Token": TOKEN}
            with patch.object(main, "_host_facts", return_value={
                    "eligible": True, "macos": "26.6",
                    "unified_memory_bytes": 32_000_000_000}):
                client = TestClient(app)
                rows = {item["repo"]: item for item in
                        client.get("/api/catalog", headers=headers).json()["models"]}

        turbo = rows[model.repo]
        modes = turbo["image_modes"]
        self.assertEqual([entry["id"] for entry in modes], ["txt2img", "edit"])
        self.assertEqual([entry["label"] for entry in modes],
                         ["Text to image", "Reference to image"])
        self.assertEqual(modes[0]["available"], turbo["execution_ready"])
        self.assertIs(modes[1]["available"], turbo["reference_lane"]["available"])
        self.assertEqual(modes[1]["reason"], turbo["reference_lane"]["reason"])
        self.assertEqual(modes[1]["references"], turbo["lane_contract"]["references"])
        # A text-only model publishes Text to image alone — no reference entry, no nested
        # contract, whatever this Mac's live state is.
        zimage = rows.get(generation.Z_IMAGE_TURBO.repo)
        if zimage is not None:
            self.assertEqual([entry["id"] for entry in zimage["image_modes"]], ["txt2img"])
            self.assertEqual(zimage["image_modes"][0]["label"], "Text to image")


if __name__ == "__main__":
    unittest.main()
