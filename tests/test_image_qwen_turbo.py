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
        self.assertEqual(generation.size_presets(model), generation.QWEN_SIZE_PRESETS)
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
            ({"reference_images": ["reference.png"]}, "Reference images"),
            ({"lora_paths": ["turbo.safetensors"]}, "LoRAs"),
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message):
                generation.validate_request({
                    "repo": model.repo, "prompt": "A temple at blue hour",
                    "aspect_ratio": "1:1", "width": 1024, "height": 1024,
                    **changes,
                })

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


if __name__ == "__main__":
    unittest.main()
