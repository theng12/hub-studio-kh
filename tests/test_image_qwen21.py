from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from components.image.app.backend import generation, main


TOKEN = "qwen_image_owner_" * 3


class QwenImageModelTests(unittest.TestCase):
    def test_image_app_shutdown_cancels_jobs_and_stops_the_native_leaf(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = generation.GenerationManager(
                root / "data", root / "models", {}, "a" * 64,
                total_memory_bytes=32_000_000_000,
            )
            event = threading.Event()
            manager.jobs["active"] = {"id": "active", "state": "running"}
            manager.cancel_events["active"] = event
            app = main.create_app(manager, TOKEN)
            with patch("components.image.app.backend.mlx_serve_runner.stop_active_leaf") as stop:
                with TestClient(app):
                    pass
            self.assertTrue(event.is_set())
            stop.assert_called_once_with(timeout_s=5)

    def test_normal_qwen_model_has_its_pinned_identity_and_safe_defaults(self) -> None:
        model = generation.QWEN_IMAGE_21

        self.assertIs(generation.MODELS[model.repo], model)
        self.assertEqual(model.repo, "ddalcu/Qwen-Image-2.1-MLX-Serve-4bit")
        self.assertEqual(model.revision, "88eb1b3bb5591ed59b68a6e1a1c2d9baade73e38")
        self.assertEqual(model.package_id, "qwen-image-2-1-mlx-serve-4bit")
        self.assertEqual(model.engine, "image-mlx-serve")
        self.assertEqual(model.label, "Qwen Image 2.1")
        self.assertEqual(model.defaults["steps"], 16)
        self.assertEqual(model.defaults["guidance"], 1.0)
        self.assertEqual(model.defaults["seed"], -1)
        self.assertEqual(model.limits["max_seed"], 2_147_483_647)
        self.assertEqual(model.qualified_minimum_memory_gb, 24.0)
        self.assertEqual(model.min_free_memory_gb, 12.5)
        self.assertEqual(model.minimum_macos, (26, 2))

    def test_qwen_offers_only_bounded_one_megapixel_presets(self) -> None:
        model = generation.QWEN_IMAGE_21
        presets = generation.size_presets(model)

        self.assertEqual(set(presets), {"16:9", "9:16", "1:1"})
        self.assertTrue(all(width * height <= 1024 * 1024
                            for width, height in presets.values()))
        for ratio in ("16:9", "9:16", "1:1"):
            width, height = presets[ratio]
            accepted = generation.validate_request({
                "repo": model.repo, "prompt": "a quiet temple at dawn",
                "aspect_ratio": ratio, "width": width, "height": height,
            })
            self.assertEqual((accepted["width"], accepted["height"]), (width, height))

        with self.assertRaisesRegex(ValueError, "qualified 1K aspect ratio"):
            generation.validate_request({
                "repo": model.repo, "prompt": "too wide",
                "aspect_ratio": "21:9", "width": 1680, "height": 720,
            })

    def test_native_model_readiness_requires_binary_and_macos_26_2(self) -> None:
        model = generation.QWEN_IMAGE_21
        manifest = {
            "package_id": model.package_id, "model_id": model.repo,
            "revision": model.revision, "engine": model.engine,
        }
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = generation.GenerationManager(
                root / "data", root / "models", {}, "a" * 64,
                manifests=[manifest], total_memory_bytes=32_000_000_000,
            )
            app = main.create_app(manager, TOKEN)
            client = TestClient(app)
            headers = {"X-Studio-Token": TOKEN}

            with patch("components.model_packages.preflight",
                       return_value={"state": "files_verified", "folder": str(root)}), \
                 patch.object(generation, "mlx_serve_problem", return_value="native binary missing"), \
                 patch.object(main, "_host_facts", return_value={
                     "eligible": True, "macos": "26.2", "unified_memory_bytes": 32_000_000_000,
                 }):
                row = next(item for item in client.get("/api/catalog", headers=headers).json()["models"]
                           if item["repo"] == model.repo)
                self.assertEqual(row["engine"], "image-mlx-serve")
                self.assertFalse(row["execution_ready"])
                self.assertEqual(row["readiness"]["files"]["state"], "runtime_unavailable")
                self.assertIn("native binary missing", row["readiness"]["reason"])

            with patch("components.model_packages.preflight",
                       return_value={"state": "files_verified", "folder": str(root)}), \
                 patch.object(generation, "mlx_serve_problem", return_value=None), \
                 patch.object(main, "_host_facts", return_value={
                     "eligible": True, "macos": "26.1", "unified_memory_bytes": 32_000_000_000,
                 }):
                row = next(item for item in client.get("/api/catalog", headers=headers).json()["models"]
                           if item["repo"] == model.repo)
                self.assertFalse(row["execution_ready"])
                self.assertIn("macOS 26.2", row["readiness"]["reason"])

    def test_native_dispatch_releases_mflux_residency_and_uses_existing_delivery(self) -> None:
        model = generation.QWEN_IMAGE_21
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            model_folder = root / "package"
            (model_folder / generation.WEIGHTS_SUBDIRECTORY).mkdir(parents=True)
            manager = generation.GenerationManager(
                root / "data", root / "models", {}, "a" * 64,
                total_memory_bytes=32_000_000_000,
            )
            manager.preflight = lambda _repo: {
                "state": "files_verified", "folder": str(model_folder),
            }
            manager._resident = {
                "repo": generation.FLUX2_KLEIN.repo, "instance": object(),
                "key": ("default", False, False, None), "idle_since": time.time(),
            }
            queued: list[tuple] = []
            manager._enqueue = lambda function, *args: queued.append((function, args))
            job = manager.submit({"repo": model.repo, "prompt": "a calm moonlit temple"})

            calls: list[dict] = []

            def fake_run(weights, params, destination, cancel_event, timeout_s=900):
                calls.append({"weights": weights, "params": params,
                              "cancel_event": cancel_event, "timeout_s": timeout_s})
                Image.new("RGB", (params["width"], params["height"]), (9, 17, 25)).save(
                    destination, format="PNG")

            with patch("components.image.app.backend.mlx_serve_runner.run_image", fake_run), \
                 patch.object(generation.random, "randint", return_value=123) as choose_seed, \
                 patch.object(generation, "release_mlx_memory") as release:
                function, args = queued.pop()
                function(*args)
                for _ in range(100):
                    finished = manager.get(job["id"])
                    if finished["state"] in generation.TERMINAL:
                        break
                    time.sleep(0.01)

            self.assertEqual(finished["state"], "done", finished)
            self.assertIsNone(manager._resident)
            release.assert_called_once_with()
            choose_seed.assert_called_once_with(0, model.limits["max_seed"])
            self.assertEqual(calls[0]["weights"], model_folder / "model")
            self.assertEqual(calls[0]["params"]["steps"], 16)
            self.assertFalse(calls[0]["cancel_event"].is_set())
            self.assertTrue(Path(finished["output_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
