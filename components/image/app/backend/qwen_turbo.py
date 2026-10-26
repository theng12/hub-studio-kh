"""Qwen Image 2.1 Turbo 4-bit adapters for MFLUX 0.23.0 — text-to-image and reference edit.

mflux 0.23.0 carries native Turbo support that the 0.20.0-era adapter had to supply itself
(`Qwen21TurboScheduler` in ``mflux/models/qwen21/model/qwen21_scheduler.py``: the checkpoint's own
eight `sample_sigmas`, no resolution shift, no terminal rescale, selected by ``ModelConfig``, with
``check_generate`` refusing guidance != 1). So the adapter now passes
``ModelConfig.qwen_image_21_turbo()`` and keeps only what the **row** guarantees:

* the pack is the Turbo recipe (its `model_index.json` states the Turbo sampling curve), because a
  checkpoint with a different curve under this name would silently render a different image;
* exactly 8 steps, guidance 1.0 and no negative prompt, which is what the row's `limits` publish
  and the condition CFG-off sampling requires;
* the reference lane's `strength` is fixed at 1.0 (the lane conditions on the reference tokens;
  the frozen contract publishes `reference_strength_mode: "fixed"` and no strengths).

The weight loading, the unshifted schedule and the eviction/tiling configuration are mflux's own
through :class:`~.qwen_lowram.QwenLowRam`; nothing here rewrites weights or schedules.
"""
from __future__ import annotations

import json
from pathlib import Path

from .qwen_lowram import QwenLowRam


STEPS = 8
GUIDANCE = 1.0


def _turbo_nodes() -> tuple:
    """The eight sigma nodes mflux 0.23.0's Turbo schedule samples. Read from mflux, never copied."""
    from mflux.models.qwen21.model.qwen21_scheduler import Qwen21TurboScheduler

    return tuple(Qwen21TurboScheduler.SIGMA_NODES)


def _check_recipe(model_path: Path) -> None:
    """Refuse a checkpoint whose `model_index.json` is not the Turbo sampling recipe."""
    try:
        document = json.loads((model_path / "model_index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"Qwen Image 2.1 Turbo 4-bit checkpoint has no readable model_index.json: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("Qwen Image 2.1 Turbo 4-bit checkpoint model_index.json is not an object")
    expected = _turbo_nodes()
    found = tuple(document.get("sample_sigmas") or ())
    if len(found) != len(expected) or any(abs(a - b) > 1e-6 for a, b in zip(found, expected)):
        raise ValueError(
            "Qwen Image 2.1 Turbo 4-bit checkpoint is not the Turbo sampling recipe: "
            f"model_index.json sample_sigmas is {found!r}, mflux 0.23.0's Turbo schedule is "
            f"{expected!r}")
    if not str(document.get("_class_name") or "").startswith("QwenImage21"):
        raise ValueError(
            "Qwen Image 2.1 Turbo 4-bit checkpoint is not a Qwen-Image-2.1 pipeline: "
            f"model_index.json _class_name is {document.get('_class_name')!r}")


class QwenTurbo(QwenLowRam):
    """Text-to-image on the Turbo pack, in the one configuration this row ships."""

    model_config_name = 'qwen_image_21_turbo'

    def __init__(self, *, model_path: str, quantize=None, lora_paths=None, lora_scales=None):
        _check_recipe(Path(model_path))
        super().__init__(model_path=model_path, quantize=quantize,
                         lora_paths=lora_paths, lora_scales=lora_scales)

    def generate_image(self, **kwargs):
        steps = kwargs.setdefault("num_inference_steps", STEPS)
        guidance = kwargs.setdefault("guidance", GUIDANCE)
        if steps != STEPS:
            raise ValueError(f"Qwen Image 2.1 Turbo 4-bit requires exactly {STEPS} inference steps")
        if guidance != GUIDANCE:
            raise ValueError(f"Qwen Image 2.1 Turbo 4-bit requires guidance={GUIDANCE} with CFG off")
        if kwargs.get("negative_prompt"):
            raise ValueError("Qwen Image 2.1 Turbo 4-bit does not accept a negative prompt because CFG is off")
        return super().generate_image(**kwargs)


class QwenTurboEdit(QwenLowRam):
    """Reference edit on the same Turbo pack.

    The memory configuration is the worker's plan, not a constant of this class
    (``plan_managed``): the row's edit lane has measured tiers, and ``apply_memory_plan`` installs
    the one this Mac selected on the mflux model behind :attr:`memory_target`.
    """

    model_class = 'mflux.models.qwen21.variants.edit.qwen_image_21_edit:QwenImage21Edit'
    model_config_name = 'qwen_image_21_turbo'
    plan_managed = True

    def __init__(self, *, model_path: str, quantize=None, lora_paths=None, lora_scales=None):
        _check_recipe(Path(model_path))
        super().__init__(model_path=model_path, quantize=quantize,
                         lora_paths=lora_paths, lora_scales=lora_scales)

    def generate_image(self, **kwargs):
        kwargs.setdefault("num_inference_steps", STEPS)
        kwargs.setdefault("guidance", GUIDANCE)
        if kwargs["num_inference_steps"] != STEPS:
            raise ValueError(f"Qwen Image 2.1 Turbo 4-bit requires exactly {STEPS} inference steps")
        if kwargs["guidance"] != GUIDANCE:
            raise ValueError(f"Qwen Image 2.1 Turbo 4-bit requires guidance={GUIDANCE} with CFG off")
        if kwargs.get("negative_prompt"):
            raise ValueError("Qwen Image 2.1 Turbo 4-bit does not accept a negative prompt because CFG is off")
        # The reference lane conditions on the reference tokens and publishes no strength control:
        # a strength below 1 would start the loop from a noised reference instead, which is a
        # different operation than the one the contract describes.
        kwargs["strength"] = 1.0
        for unsupported in ("mask_image", "auto_mask", "enhance_prompt", "verify"):
            if kwargs.get(unsupported):
                raise ValueError(
                    f"Qwen Image 2.1 Turbo 4-bit reference edit does not accept {unsupported}")
        return super().generate_image(**kwargs)
