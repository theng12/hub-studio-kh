from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from components.image.app.backend import generation, qwen_turbo


SIGMAS = [1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568]


class Scalar(float):
    @property
    def dtype(self):
        return "float32"

    def astype(self, _dtype):
        return self

    def __add__(self, other):
        return Scalar(float(self) + float(other))

    def __radd__(self, other):
        return Scalar(float(other) + float(self))

    def __sub__(self, other):
        return Scalar(float(self) - float(other))

    def __mul__(self, other):
        return Scalar(float(self) * float(other))

    def __rmul__(self, other):
        return Scalar(float(other) * float(self))


class Array(list):
    def __init__(self, values, dtype="float32"):
        super().__init__(Scalar(value) for value in values)
        self.dtype = dtype

    def __mul__(self, value):
        return Array([item * value for item in self], self.dtype)


class Target:
    def __init__(self, to_pattern, from_pattern, transform=None, required=True):
        self.to_pattern = to_pattern
        self.from_pattern = [from_pattern]
        self.transform = transform
        self.required = required


def fake_mflux(*, fail_load=False):
    names = (
        "mlx", "mlx.core", "mflux", "mflux.callbacks", "mflux.callbacks.instances",
        "mflux.callbacks.instances.memory_saver", "mflux.models", "mflux.models.common",
        "mflux.models.common.schedulers", "mflux.models.common.schedulers.base_scheduler",
        "mflux.models.common.vae",
        "mflux.models.common.vae.tiling_config", "mflux.models.common.weights",
        "mflux.models.common.weights.mapping", "mflux.models.common.weights.mapping.weight_mapper",
        "mflux.models.qwen21", "mflux.models.qwen21.qwen21_initializer",
        "mflux.models.qwen21.variants", "mflux.models.qwen21.variants.txt2img",
        "mflux.models.qwen21.variants.txt2img.qwen_image_21", "mflux.models.qwen21.weights",
        "mflux.models.qwen21.weights.qwen21_weight_definition",
        "mflux.models.qwen21.weights.qwen21_weight_mapping",
    )
    modules = {name: types.ModuleType(name) for name in names}
    observed = {
        "cache_limit": 123_456,
        "cache_calls": [],
        "constructor_calls": 0,
        "generate_calls": [],
    }

    class Component:
        def __init__(self, name, skip):
            self.name = name
            self.skip_quantization = skip

    class WeightDefinition:
        @staticmethod
        def get_components():
            return [Component("text_encoder", True), Component("transformer", False)]

    class WeightMapping:
        @staticmethod
        def get_transformer_mapping():
            return [Target("modulation.layers.1.weight", "modulation.1.weight")]

        @staticmethod
        def get_text_encoder_mapping():
            return [Target("embed_tokens.weight", "model.language_model.embed_tokens.weight")]

        @staticmethod
        def get_vae_mapping():
            return [
                Target("encoder.conv_in.conv.weight", "encoder.conv_in.weight",
                       lambda value: ("transposed", value)),
                Target("encoder.norm_out.weight", "encoder.norm_out.gamma",
                       lambda value: ("reshaped", value)),
            ]

    class WeightMapper:
        @staticmethod
        def apply_mapping(hf_weights, mapping, **_kwargs):
            result = {}
            for target in mapping:
                source = target.from_pattern[0]
                if source not in hf_weights:
                    continue
                value = hf_weights[source]
                if target.transform:
                    value = target.transform(value)
                current = result
                parts = target.to_pattern.split(".")
                for part in parts[:-1]:
                    current = current.setdefault(part, {})
                current[parts[-1]] = value
            return result

    class LoadedWeights:
        def __init__(self):
            self.components = {
                "transformer": {
                    "modulation": {"layers": [{}, {"weight": "tw", "scales": "ts", "biases": "tb"}]}
                },
                "text_encoder": {
                    "language_model": {"embed_tokens": {"weight": "ew", "scales": "es", "biases": "eb"}},
                    "visual": {"unused": "visual"},
                    "lm_head": {"unused": "head"},
                },
                "vae": {"encoder": {"conv_in": {"weight": "vw"}, "norm_out": {"gamma": "vg"}}},
            }

    class Initializer:
        @staticmethod
        def _load_weights(_path):
            return LoadedWeights()

    class Registry:
        def __init__(self):
            self.registered = []

        def register(self, callback):
            self.registered.append(callback)

    class QwenImage21:
        def __init__(self, *, quantize, model_path):
            observed["constructor_calls"] += 1
            weights = Initializer._load_weights(model_path)
            observed["loaded_components"] = weights.components
            if fail_load:
                raise RuntimeError("fixture load failed")
            observed["constructor"] = {
                "quantize": quantize,
                "model_path": model_path,
                "encoder_skip": WeightDefinition.get_components()[0].skip_quantization,
            }
            self.callbacks = Registry()
            self.prompt_cache = {"prompt": ("embeds", "mask")}
            self.text_encoder = object()
            self.transformer = object()
            self.vae = object()

        def generate_image(self, **kwargs):
            observed["generate_calls"].append(dict(kwargs))
            scheduler_name = kwargs["scheduler"]
            scheduler = schedulers[scheduler_name](types.SimpleNamespace(num_inference_steps=8))
            observed["uses_base_scheduler"] = isinstance(scheduler, BaseScheduler)
            observed["scheduler_sigmas"] = [float(value) for value in scheduler.sigmas]
            observed["scheduler_timesteps"] = [float(value) for value in scheduler.timesteps]
            observed["euler"] = float(scheduler.step(Scalar(2), 0, Scalar(10)))
            if kwargs.pop("fail", False):
                raise RuntimeError("fixture generation failed")
            return "image"

    class TilingConfig:
        pass

    class BaseScheduler:
        pass

    class MemorySaver:
        def __init__(self, **kwargs):
            observed["memory_saver"] = kwargs

    schedulers = {"sentinel": object()}
    modules["mflux.models.common.schedulers"].SCHEDULER_REGISTRY = schedulers
    modules["mflux.models.common.schedulers.base_scheduler"].BaseScheduler = BaseScheduler
    modules["mflux.models.qwen21.qwen21_initializer"].Qwen21Initializer = Initializer
    modules["mflux.models.qwen21.weights.qwen21_weight_definition"].Qwen21WeightDefinition = WeightDefinition
    modules["mflux.models.qwen21.weights.qwen21_weight_mapping"].Qwen21WeightMapping = WeightMapping
    modules["mflux.models.common.weights.mapping.weight_mapper"].WeightMapper = WeightMapper
    modules["mflux.models.qwen21.variants.txt2img.qwen_image_21"].QwenImage21 = QwenImage21
    modules["mflux.models.common.vae.tiling_config"].TilingConfig = TilingConfig
    modules["mflux.callbacks.instances.memory_saver"].MemorySaver = MemorySaver
    modules["mlx.core"].float32 = "float32"
    modules["mlx.core"].array = lambda values, dtype=None: Array(values, dtype or "float32")
    modules["mlx.core"].eval = lambda *_arrays: None
    modules["mlx.core"].clear_cache = lambda: None
    modules["mlx.core"].reset_peak_memory = lambda: None
    modules["mlx.core"].get_peak_memory = lambda: 0

    def set_cache_limit(value):
        previous = observed["cache_limit"]
        observed["cache_limit"] = value
        observed["cache_calls"].append(value)
        return previous

    modules["mlx.core"].set_cache_limit = set_cache_limit
    return modules, observed, Initializer, schedulers


def write_checkpoint(root: Path, *, sigmas=SIGMAS, mutate=None) -> None:
    documents = {
        "model_index.json": {
            "_class_name": "QwenImage21Pipeline",
            "scheduler": ["diffusers", "FlowMatchEulerDiscreteScheduler"],
            "sample_sigmas": sigmas,
        },
        "transformer/model.safetensors.index.json": {
            "metadata": {"quantization_level": "4", "mflux_version": "0.22.0"},
            "weight_map": {
                "modulation.layers.1.weight": "0.safetensors",
                "modulation.layers.1.scales": "0.safetensors",
                "modulation.layers.1.biases": "0.safetensors",
            },
        },
        "text_encoder/model.safetensors.index.json": {
            "metadata": {"quantization_level": "4", "mflux_version": "0.22.0"},
            "weight_map": {
                "language_model.embed_tokens.weight": "0.safetensors",
                "language_model.embed_tokens.scales": "0.safetensors",
                "language_model.embed_tokens.biases": "0.safetensors",
                "visual.unused": "1.safetensors",
                "lm_head.unused": "2.safetensors",
            },
        },
        "vae/model.safetensors.index.json": {
            "metadata": {"quantization_level": "4", "mflux_version": "0.22.0"},
            "weight_map": {
                "encoder.conv_in.weight": "0.safetensors",
                "encoder.norm_out.gamma": "0.safetensors",
            },
        },
    }
    if mutate:
        mutate(documents)
    for relative, document in documents.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document), encoding="utf-8")


class QwenTurboRuntimeTests(unittest.TestCase):
    def test_exact_checkpoint_schedule_drives_unshifted_first_order_euler(self) -> None:
        modules, observed, _initializer, schedulers = fake_mflux()
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, modules), \
             patch.object(generation, "_ORIGINAL_CACHE_LIMIT", None):
            write_checkpoint(Path(temp))
            instance = qwen_turbo.QwenTurbo(model_path=temp)
            self.assertEqual(instance.generate_image(
                seed=7, prompt="p", num_inference_steps=8,
                height=1024, width=1024, guidance=1.0,
            ), "image")

        self.assertEqual(observed["scheduler_sigmas"], SIGMAS + [0.0])
        self.assertTrue(observed["uses_base_scheduler"])
        self.assertEqual(observed["scheduler_timesteps"], [value * 1000 for value in SIGMAS])
        self.assertAlmostEqual(observed["euler"], 10 + (SIGMAS[1] - SIGMAS[0]) * 2, places=7)
        self.assertNotIn(qwen_turbo.SCHEDULER_NAME, schedulers)
        self.assertEqual(observed["cache_calls"], [0, 123_456])

    def test_checkpoint_layout_is_renamed_without_retransposing_saved_mlx_arrays(self) -> None:
        modules, observed, _initializer, _schedulers = fake_mflux()
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, modules):
            write_checkpoint(Path(temp))
            qwen_turbo.QwenTurbo(model_path=temp)

        components = observed["loaded_components"]
        self.assertEqual(components["text_encoder"], {
            "embed_tokens": {"weight": "ew", "scales": "es", "biases": "eb"}
        })
        self.assertEqual(components["vae"], {
            "encoder": {
                "conv_in": {"conv": {"weight": "vw"}},
                "norm_out": {"weight": "vg"},
            }
        })
        self.assertEqual(
            components["transformer"]["modulation"]["layers"][1],
            {"weight": "tw", "scales": "ts", "biases": "tb"},
        )

    def test_invalid_steps_cfg_and_negative_prompt_are_rejected_before_generation(self) -> None:
        modules, observed, *_ = fake_mflux()
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, modules):
            write_checkpoint(Path(temp))
            instance = qwen_turbo.QwenTurbo(model_path=temp)
            for kwargs, message in (
                ({"num_inference_steps": 7, "guidance": 1.0}, "exactly 8"),
                ({"num_inference_steps": 8, "guidance": 1.1}, "guidance=1.0"),
                ({"num_inference_steps": 8, "guidance": 1.0, "negative_prompt": "bad"}, "negative prompt"),
            ):
                with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, message):
                    instance.generate_image(seed=1, prompt="p", height=1024, width=1024, **kwargs)
        self.assertEqual(observed["generate_calls"], [])

    def test_malformed_or_mixed_checkpoint_layout_fails_before_model_construction(self) -> None:
        mutations = {
            "schedule": lambda docs: docs["model_index.json"].update(sample_sigmas=[1.0] * 8),
            "missing quant sidecar": lambda docs: docs[
                "text_encoder/model.safetensors.index.json"
            ]["weight_map"].pop("language_model.embed_tokens.biases"),
            "old text root": lambda docs: docs[
                "text_encoder/model.safetensors.index.json"
            ]["weight_map"].update({"embed_tokens.weight": "0.safetensors"}),
            "mixed vae": lambda docs: docs[
                "vae/model.safetensors.index.json"
            ]["weight_map"].update({"encoder.conv_in.conv.weight": "0.safetensors"}),
        }
        for name, mutation in mutations.items():
            modules, observed, *_ = fake_mflux()
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, modules):
                write_checkpoint(Path(temp), mutate=mutation)
                with self.assertRaisesRegex(ValueError, "Qwen Image 2.1 Turbo 4-bit checkpoint"):
                    qwen_turbo.QwenTurbo(model_path=temp)
            self.assertEqual(observed["constructor_calls"], 0)

    def test_load_and_generation_failures_restore_process_globals_and_cache(self) -> None:
        modules, observed, initializer, schedulers = fake_mflux(fail_load=True)
        original_loader = initializer._load_weights
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, modules):
            write_checkpoint(Path(temp))
            with self.assertRaisesRegex(RuntimeError, "fixture load failed"):
                qwen_turbo.QwenTurbo(model_path=temp)
        self.assertIs(initializer._load_weights, original_loader)

        modules, observed, _initializer, schedulers = fake_mflux()
        sentinel = object()
        schedulers[qwen_turbo.SCHEDULER_NAME] = sentinel
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, modules), \
             patch.object(generation, "_ORIGINAL_CACHE_LIMIT", None):
            write_checkpoint(Path(temp))
            instance = qwen_turbo.QwenTurbo(model_path=temp)
            with self.assertRaisesRegex(RuntimeError, "fixture generation failed"):
                instance.generate_image(
                    seed=1, prompt="p", num_inference_steps=8,
                    height=1024, width=1024, guidance=1.0, fail=True,
                )
        self.assertIs(schedulers[qwen_turbo.SCHEDULER_NAME], sentinel)
        self.assertEqual(observed["cache_calls"], [0, 123_456])

    def test_turbo_reuses_low_ram_cancellation_callback(self) -> None:
        modules, _observed, *_ = fake_mflux()
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, modules):
            write_checkpoint(Path(temp))
            instance = qwen_turbo.QwenTurbo(model_path=temp)
            event = types.SimpleNamespace(is_set=lambda: True)
            instance.set_cancel_event(event)
            cancel = next(callback for callback in instance._model.callbacks.registered
                          if callback.__class__.__name__ == "_CancelReporter")
            with self.assertRaisesRegex(RuntimeError, "Generation cancelled"):
                cancel.call_in_loop()


if __name__ == "__main__":
    unittest.main()
