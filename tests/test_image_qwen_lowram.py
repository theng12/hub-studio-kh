from __future__ import annotations

from pathlib import Path
import json
import math
import sys
import types
import unittest
from unittest.mock import patch

from components.image.app.backend import generation, qwen_lowram


def fake_mflux():
    names = (
        "mlx", "mlx.core", "mflux", "mflux.models", "mflux.models.qwen21",
        "mflux.models.qwen21.variants", "mflux.models.qwen21.variants.txt2img",
        "mflux.models.qwen21.variants.txt2img.qwen_image_21",
        "mflux.models.qwen21.weights", "mflux.models.qwen21.weights.qwen21_weight_definition",
        "mflux.models.common", "mflux.models.common.vae",
        "mflux.models.common.vae.tiling_config", "mflux.callbacks",
        "mflux.callbacks.instances", "mflux.callbacks.instances.memory_saver",
    )
    modules = {name: types.ModuleType(name) for name in names}
    observed: dict = {"eval": [], "cache_limit": 987_654_321, "cache_calls": []}

    class Component:
        def __init__(self, name, skip):
            self.name = name
            self.skip_quantization = skip

    class WeightDefinition:
        @staticmethod
        def get_components():
            return [Component("text_encoder", True), Component("transformer", False)]

    class Registry:
        def __init__(self):
            self.registered = []

        def register(self, callback):
            self.registered.append(callback)

    class QwenImage21:
        def __init__(self, *, quantize, model_path):
            observed["constructor"] = {
                "quantize": quantize, "model_path": model_path,
                "encoder_skip": WeightDefinition.get_components()[0].skip_quantization,
            }
            self.callbacks = Registry()
            self.prompt_cache = {"prompt": ("embeds", "mask")}
            self.text_encoder = object()
            self.transformer = object()
            self.vae = object()

        def generate_image(self, **kwargs):
            if kwargs.pop("fail", False):
                raise RuntimeError("fixture generation failed")
            observed["generate"] = kwargs
            return "image"

    class TilingConfig:
        pass

    class MemorySaver:
        def __init__(self, **kwargs):
            observed["memory_saver"] = kwargs

    modules["mflux.models.qwen21.weights.qwen21_weight_definition"].Qwen21WeightDefinition = WeightDefinition
    modules["mflux.models.qwen21.variants.txt2img.qwen_image_21"].QwenImage21 = QwenImage21
    modules["mflux.models.common.vae.tiling_config"].TilingConfig = TilingConfig
    modules["mflux.callbacks.instances.memory_saver"].MemorySaver = MemorySaver
    modules["mlx.core"].eval = lambda *arrays: observed["eval"].append(arrays)
    modules["mlx.core"].clear_cache = lambda: None
    modules["mlx.core"].reset_peak_memory = lambda: None
    modules["mlx.core"].get_peak_memory = lambda: 0
    def set_cache_limit(value):
        previous = observed["cache_limit"]
        observed["cache_limit"] = value
        observed["cache_calls"].append(value)
        return previous
    modules["mlx.core"].set_cache_limit = set_cache_limit
    return modules, observed, WeightDefinition, MemorySaver, TilingConfig


class QwenLowRamTests(unittest.TestCase):
    def test_free_start_floor_is_the_stored_peak_plus_two_gb(self):
        """Owner decision 2026-10-08: the floor is measured peak + 2 GB (rounded up to 0.5 GB), so a
        16 GB Mac (8-10 GB free when idle) can start it; the measured starts were all >= 14.697 GB."""
        root = Path(__file__).resolve().parents[1]
        evidence = root / "qualification/evidence/qwen-image-21-20261006/measurement.json"
        rows = json.loads(evidence.read_text())["low_rows"]
        peak = max(max(row["peak_process_tree_gb"], row["available_gb_start"] - row["available_gb_min"])
                   for row in rows)
        model = generation.QWEN_IMAGE_21_LOW_RAM
        self.assertEqual(model.min_free_memory_gb, math.ceil((peak + 2.0) * 2) / 2)
        self.assertEqual(model.min_free_memory_gb, 8.0)
        self.assertIn(evidence.name, model.qualification["min_free_memory_note"])

    def test_candidate_has_a_distinct_pinned_identity_and_the_owners_16_gb_tier(self) -> None:
        model = generation.QWEN_IMAGE_21_LOW_RAM
        self.assertIs(generation.MODELS[model.repo], model)
        self.assertEqual(model.repo, "mlx-community/Qwen-Image-2.1-mflux-q4")
        self.assertEqual(model.revision, "746a58556820933a2df5c75887a2570f1ad200c0")
        self.assertEqual(model.package_id, "qwen-image-2-1-mflux-q4")
        self.assertEqual(model.engine, "image-mflux")
        self.assertEqual(model.qualified_minimum_memory_gb, 16.0)
        self.assertIn("owner's decision", model.qualification["fleet_16gb"])
        self.assertIn("without a physical 16 GB", model.qualification["fleet_16gb"])
        self.assertEqual(model.qualification["fleet_8gb"], "not qualified")
        self.assertFalse(model.resident_allowed)
        self.assertTrue(model.guidance_ignored)
        guidance = next(parameter for parameter in generation.lane_parameters(model)
                        if parameter["name"] == "guidance")
        self.assertTrue(guidance["ignored"])

    def test_wrapper_restores_global_weight_definition_and_installs_bounded_callbacks(self) -> None:
        modules, observed, definition, memory_saver, tiling = fake_mflux()
        with patch.dict(sys.modules, modules):
            instance = qwen_lowram.QwenLowRam(
                model_path="/private/model", quantize=None,
                lora_paths=None, lora_scales=None,
            )

            self.assertFalse(observed["constructor"]["encoder_skip"])
            self.assertTrue(definition.get_components()[0].skip_quantization,
                            "the process-global mflux definition must be restored")
            self.assertIsInstance(instance._model.tiling_config, tiling)
            saver = observed["memory_saver"]
            self.assertIs(saver["model"], instance._model)
            self.assertFalse(saver["keep_transformer"])
            self.assertIsNone(saver["cache_limit_bytes"])
            self.assertEqual(saver["num_seeds"], 1)
            callbacks = instance._model.callbacks.registered
            self.assertIsInstance(callbacks[0], qwen_lowram.PromptMaterializer)
            self.assertIsInstance(callbacks[-1], memory_saver)
            callbacks[0].call_before_loop(prompt="prompt")
            self.assertEqual(observed["eval"], [("embeds", "mask")])
            self.assertEqual(instance.generate_image(prompt="p"), "image")
            self.assertEqual(observed["generate"], {"prompt": "p"})

    def test_cache_zero_is_bracketed_and_restored_before_the_next_normal_model(self) -> None:
        modules, observed, *_ = fake_mflux()
        with patch.dict(sys.modules, modules), \
             patch.object(generation, "_ORIGINAL_CACHE_LIMIT", None):
            instance = qwen_lowram.QwenLowRam(model_path="/private/model")
            self.assertEqual(instance.generate_image(prompt="p"), "image")
            self.assertEqual(observed["cache_calls"], [0, 987_654_321])
            self.assertEqual(observed["cache_limit"], 987_654_321)
            self.assertIsNone(generation._ORIGINAL_CACHE_LIMIT)

            with self.assertRaisesRegex(RuntimeError, "fixture generation failed"):
                instance.generate_image(prompt="p", fail=True)
            self.assertEqual(observed["cache_calls"],
                             [0, 987_654_321, 0, 987_654_321])
            self.assertEqual(observed["cache_limit"], 987_654_321)
            self.assertIsNone(generation._ORIGINAL_CACHE_LIMIT)

            normal_plan = generation.memory_plan(
                generation.FLUX2_KLEIN, 32 * 1000 ** 3)
            generation.apply_memory_plan(object(), normal_plan)
            self.assertEqual(observed["cache_limit"], 987_654_321)
            self.assertIsNone(generation._ORIGINAL_CACHE_LIMIT)

    def test_wrapper_refuses_lora_constructor_arguments(self) -> None:
        with self.assertRaisesRegex(ValueError, "LoRA"):
            qwen_lowram.QwenLowRam(model_path="/private/model", quantize=None,
                                   lora_paths=[Path("x")], lora_scales=None)


if __name__ == "__main__":
    unittest.main()
