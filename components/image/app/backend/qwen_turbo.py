"""Pinned Qwen Image 2.1 Turbo 4-bit adapter for MFLUX 0.20.0."""
from __future__ import annotations

import json
from pathlib import Path

from .qwen_lowram import QwenLowRam


SCHEDULER_NAME = "studiohub_qwen_image_21_turbo"
OFFICIAL_SAMPLE_SIGMAS = (
    1.0, 0.978453, 0.95418, 0.926626,
    0.89508, 0.845148, 0.704534, 0.414568,
)
_MISSING = object()


def _flatten(value, prefix="") -> dict[str, object]:
    flat = {}
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            flat.update(_flatten(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            path = f"{prefix}.{index}" if prefix else str(index)
            flat.update(_flatten(child, path))
    else:
        flat[prefix] = value
    return flat


def _unflatten(flat: dict[str, object]) -> dict:
    root = {}
    for path, value in flat.items():
        current = root
        parts = path.split(".")
        for index, part in enumerate(parts[:-1]):
            next_is_list = parts[index + 1].isdigit()
            if isinstance(current, list):
                position = int(part)
                while len(current) <= position:
                    current.append({})
                current = current[position]
            else:
                current = current.setdefault(part, [] if next_is_list else {})
        if isinstance(current, list):
            position = int(parts[-1])
            while len(current) <= position:
                current.append(None)
            current[position] = value
        else:
            current[parts[-1]] = value
    return root


def _read_json(path: Path, label: str) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Qwen Image 2.1 Turbo 4-bit checkpoint has no valid {label}: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"Qwen Image 2.1 Turbo 4-bit checkpoint {label} is not an object")
    return document


def _required(mapping) -> list:
    return [target for target in mapping if getattr(target, "required", True)]


def _quantized_keys(keys: set[str], weight: str, label: str) -> bool:
    stem = weight.removesuffix(".weight")
    scales = f"{stem}.scales" in keys
    biases = f"{stem}.biases" in keys
    if scales != biases:
        raise ValueError(
            f"Qwen Image 2.1 Turbo 4-bit checkpoint has an incomplete quantized tensor in {label}: {stem}"
        )
    return scales


def _one_source(target, transform, keys: set[str], label: str) -> str:
    candidates = [transform(source) for source in target.from_pattern]
    present = [source for source in candidates if source in keys]
    if len(present) != 1:
        raise ValueError(
            f"Qwen Image 2.1 Turbo 4-bit checkpoint {label} needs exactly one source for "
            f"{target.to_pattern}; found {present}"
        )
    return present[0]


def _validate_layout(model_path: Path):
    from mflux.models.qwen21.weights.qwen21_weight_mapping import Qwen21WeightMapping

    mappings = {
        "transformer": Qwen21WeightMapping.get_transformer_mapping(),
        "text_encoder": Qwen21WeightMapping.get_text_encoder_mapping(),
        "vae": Qwen21WeightMapping.get_vae_mapping(),
    }
    indexes = {}
    for component in mappings:
        document = _read_json(
            model_path / component / "model.safetensors.index.json",
            f"{component} weight index",
        )
        metadata = document.get("metadata")
        keys = document.get("weight_map")
        if metadata != {"quantization_level": "4", "mflux_version": "0.22.0"}:
            raise ValueError(
                f"Qwen Image 2.1 Turbo 4-bit checkpoint has unsupported {component} metadata: {metadata!r}"
            )
        if not isinstance(keys, dict) or not keys:
            raise ValueError(f"Qwen Image 2.1 Turbo 4-bit checkpoint {component} index is empty")
        indexes[component] = set(keys)

    transformer_keys = indexes["transformer"]
    for target in _required(mappings["transformer"]):
        weight = target.to_pattern
        if weight not in transformer_keys:
            raise ValueError(
                f"Qwen Image 2.1 Turbo 4-bit checkpoint transformer is missing {weight}"
            )
        _quantized_keys(transformer_keys, weight, "transformer")

    text_keys = indexes["text_encoder"]
    for target in _required(mappings["text_encoder"]):
        source = _one_source(target, lambda key: key.removeprefix("model."), text_keys, "text encoder")
        if target.to_pattern in text_keys and target.to_pattern != source:
            raise ValueError("Qwen Image 2.1 Turbo 4-bit checkpoint mixes text-encoder layouts")
        _quantized_keys(text_keys, source, "text encoder")

    vae_keys = indexes["vae"]
    for target in _required(mappings["vae"]):
        source = _one_source(target, lambda key: key, vae_keys, "VAE")
        if target.to_pattern != source and target.to_pattern in vae_keys:
            raise ValueError("Qwen Image 2.1 Turbo 4-bit checkpoint mixes VAE layouts")
        if _quantized_keys(vae_keys, source, "VAE"):
            raise ValueError(
                "Qwen Image 2.1 Turbo 4-bit checkpoint has quantized VAE tensors that MFLUX 0.20.0 "
                "cannot map without changing their packed layout"
            )
    return mappings


class QwenTurboScheduler:
    """The checkpoint's unshifted, first-order Euler schedule."""

    sample_sigmas = OFFICIAL_SAMPLE_SIGMAS

    def __init__(self, config) -> None:
        if config.num_inference_steps != len(self.sample_sigmas):
            raise ValueError("Qwen Image 2.1 Turbo 4-bit requires exactly 8 inference steps")
        import mlx.core as mx

        self._sigmas = mx.array((*self.sample_sigmas, 0.0), dtype=mx.float32)
        self._timesteps = mx.array([sigma * 1000.0 for sigma in self.sample_sigmas], dtype=mx.float32)

    @property
    def sigmas(self):
        return self._sigmas

    @property
    def timesteps(self):
        return self._timesteps

    def scale_model_input(self, latents, _t: int):
        return latents

    def step(self, noise, timestep: int, latents, **_kwargs):
        delta = (self._sigmas[timestep + 1] - self._sigmas[timestep]).astype(latents.dtype)
        return latents + delta * noise.astype(latents.dtype)


class QwenTurbo(QwenLowRam):
    def __init__(self, *, model_path: str, quantize=None, lora_paths=None, lora_scales=None):
        path = Path(model_path)
        model_index = _read_json(path / "model_index.json", "model_index.json")
        if (
            model_index.get("_class_name") != "QwenImage21Pipeline"
            or model_index.get("scheduler") != ["diffusers", "FlowMatchEulerDiscreteScheduler"]
            or tuple(model_index.get("sample_sigmas") or ()) != OFFICIAL_SAMPLE_SIGMAS
        ):
            raise ValueError("Qwen Image 2.1 Turbo 4-bit checkpoint has an unsupported sampling recipe")
        self._sample_sigmas = tuple(float(value) for value in model_index["sample_sigmas"])
        self._mappings = _validate_layout(path)

        from mflux.models.qwen21.qwen21_initializer import Qwen21Initializer

        original_loader = Qwen21Initializer._load_weights

        def load_normalized(checkpoint_path):
            return self._normalize_weights(original_loader(checkpoint_path))

        Qwen21Initializer._load_weights = staticmethod(load_normalized)
        try:
            super().__init__(
                model_path=model_path,
                quantize=quantize,
                lora_paths=lora_paths,
                lora_scales=lora_scales,
            )
        finally:
            Qwen21Initializer._load_weights = staticmethod(original_loader)

    def _normalize_weights(self, weights):
        components = weights.components
        text = components.get("text_encoder")
        language_model = text.get("language_model") if isinstance(text, dict) else None
        if not isinstance(language_model, dict):
            raise ValueError("Qwen Image 2.1 Turbo 4-bit checkpoint text encoder has no language_model subtree")
        components["text_encoder"] = language_model

        vae = components.get("vae")
        if not isinstance(vae, dict):
            raise ValueError("Qwen Image 2.1 Turbo 4-bit checkpoint has no VAE weights")
        # MFLUX 0.22 saved these arrays in MLX layout under its newer HF-style module
        # names. The 0.20 mapping supplies the exact old target paths, but applying its
        # HF conversion transforms would transpose already-transposed convolution weights.
        vae_flat = _flatten(vae)
        renamed = {}
        for target in self._mappings["vae"]:
            present = [source for source in target.from_pattern if source in vae_flat]
            if len(present) == 1:
                renamed[target.to_pattern] = vae_flat[present[0]]
        components["vae"] = _unflatten(renamed)

        normalized = {
            "transformer": set(_flatten(components.get("transformer", {}))),
            "text_encoder": set(_flatten(components["text_encoder"])),
            "vae": set(_flatten(components["vae"])),
        }
        for component, mapping in self._mappings.items():
            missing = [target.to_pattern for target in _required(mapping)
                       if target.to_pattern not in normalized[component]]
            if missing:
                raise ValueError(
                    f"Qwen Image 2.1 Turbo 4-bit checkpoint normalization lost {component} tensors: {missing}"
                )
        return weights

    def generate_image(self, **kwargs):
        steps = kwargs.setdefault("num_inference_steps", 8)
        guidance = kwargs.setdefault("guidance", 1.0)
        if steps != 8:
            raise ValueError("Qwen Image 2.1 Turbo 4-bit requires exactly 8 inference steps")
        if guidance != 1.0:
            raise ValueError("Qwen Image 2.1 Turbo 4-bit requires guidance=1.0 with CFG off")
        if kwargs.get("negative_prompt"):
            raise ValueError("Qwen Image 2.1 Turbo 4-bit does not accept a negative prompt because CFG is off")

        from mflux.models.common.schedulers import SCHEDULER_REGISTRY
        from mflux.models.common.schedulers.base_scheduler import BaseScheduler

        scheduler = type(
            "PinnedQwenTurboScheduler",
            (QwenTurboScheduler, BaseScheduler),
            {"sample_sigmas": self._sample_sigmas},
        )
        previous = SCHEDULER_REGISTRY.get(SCHEDULER_NAME, _MISSING)
        SCHEDULER_REGISTRY[SCHEDULER_NAME] = scheduler
        kwargs["scheduler"] = SCHEDULER_NAME
        try:
            return super().generate_image(**kwargs)
        finally:
            if previous is _MISSING:
                SCHEDULER_REGISTRY.pop(SCHEDULER_NAME, None)
            else:
                SCHEDULER_REGISTRY[SCHEDULER_NAME] = previous
