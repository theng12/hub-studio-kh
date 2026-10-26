"""Low-memory Qwen Image 2.1 adapter for the pinned mflux q4 package."""
from __future__ import annotations


class DenoiseCancelled(RuntimeError):
    """The denoise callback observed the job's cancellation event."""


class PromptMaterializer:
    """Evaluate cached prompt arrays before the text encoder is evicted."""

    def __init__(self, model) -> None:
        self._model = model

    def call_before_loop(self, *, prompt: str, **kwargs) -> None:
        del prompt, kwargs
        cached = getattr(self._model, "prompt_cache", {}) or {}
        arrays = [array for pair in cached.values() if pair for array in pair]
        if arrays:
            import mlx.core as mx
            mx.eval(*arrays)


class _CancelReporter:
    def __init__(self, owner) -> None:
        self._owner = owner

    def call_in_loop(self, *args, **kwargs) -> None:
        event = self._owner._cancel_event
        if event is not None and event.is_set():
            raise DenoiseCancelled("Generation cancelled")
        callback = self._owner._step_callback
        if callback is None:
            return
        t = kwargs.get("t", args[0] if args else None)
        config = kwargs.get("config", args[4] if len(args) > 4 else None)
        total = getattr(config, "num_inference_steps", None)
        if isinstance(t, int) and isinstance(total, int) and total > 0:
            callback(t + 1, total)


class QwenLowRam:
    """Qwen Image 2.1 on the pinned q4 pack, with the memory discipline this row ships.

    ``model_class`` names the mflux class this row loads (text-to-image or edit) and
    ``model_config_name`` the ``ModelConfig`` factory that must be passed with it, so one class
    serves both lanes of the same pack without a second copy of the loading path.

    ``plan_managed`` leaves the eviction, tiling and cache cap to the worker's own
    `apply_memory_plan`, which is what a row with measured memory tiers needs (the edit lane,
    owner 2026-10-26: bounded on the Macs where the full configuration would swap, full above).
    The shipped text-to-image rows keep their configuration installed here: it is the single mode
    they were measured in, and their ``MemoryPolicy`` declares no tiers to choose from.
    """

    model_class = 'mflux.models.qwen21.variants.txt2img.qwen_image_21:QwenImage21'
    model_config_name: str | None = None
    plan_managed = False

    def __init__(self, *, model_path: str, quantize=None, lora_paths=None, lora_scales=None):
        if lora_paths or lora_scales:
            raise ValueError("Qwen Image 2.1 low-RAM does not support LoRA")
        if quantize is not None:
            raise ValueError("Qwen Image 2.1 low-RAM requires its packaged q4 weights")
        from mflux.models.qwen21.weights.qwen21_weight_definition import Qwen21WeightDefinition

        original_components = Qwen21WeightDefinition.get_components

        def full_q4_components():
            components = original_components()
            for component in components:
                if component.name == "text_encoder":
                    component.skip_quantization = False
            return components

        Qwen21WeightDefinition.get_components = staticmethod(full_q4_components)
        try:
            self._model = self._build(model_path)
        finally:
            Qwen21WeightDefinition.get_components = staticmethod(original_components)

        self._cancel_event = None
        self._step_callback = None
        # Order matters: the prompt arrays are evaluated *before* the saver's before-loop hook
        # evicts the text encoder, so registering the materializer first is what makes the
        # eviction free memory (see `staging`'s note on the same failure in mflux).
        self._model.callbacks.register(PromptMaterializer(self._model))
        self._model.callbacks.register(_CancelReporter(self))
        if not self.plan_managed:
            self._install_shipped_memory_configuration()

    def _build(self, model_path: str):
        import importlib

        module_name, _, class_name = self.model_class.partition(':')
        model_class = getattr(importlib.import_module(module_name), class_name)
        kwargs = {}
        if self.model_config_name:
            from mflux.models.common.config.model_config import ModelConfig
            kwargs['model_config'] = getattr(ModelConfig, self.model_config_name)()
        return model_class(quantize=None, model_path=model_path, **kwargs)

    def _install_shipped_memory_configuration(self) -> None:
        """The one bounded configuration this row is measured in: tiled decode, encoder and
        transformer evicted, MLX's cache left to `generate_image` (which zeroes it)."""
        from mflux.callbacks.instances.memory_saver import MemorySaver
        from mflux.models.common.vae.tiling_config import TilingConfig

        self._model.tiling_config = TilingConfig()
        self._model.callbacks.register(MemorySaver(
            model=self._model, keep_transformer=False, cache_limit_bytes=None, num_seeds=1,
        ))

    @property
    def memory_target(self):
        """The mflux model the worker's memory plan applies to (this wrapper is not it)."""
        return self._model

    @property
    def callbacks(self):
        return self._model.callbacks

    @callbacks.setter
    def callbacks(self, value) -> None:
        self._model.callbacks = value

    def set_cancel_event(self, event) -> None:
        self._cancel_event = event

    def set_step_callback(self, callback) -> None:
        self._step_callback = callback

    def generate_image(self, **kwargs):
        from .generation import _set_mlx_cache_limit
        import mlx.core as mx

        _set_mlx_cache_limit(0.0)
        try:
            mx.clear_cache()
            mx.reset_peak_memory()
            return self._model.generate_image(**kwargs)
        finally:
            _set_mlx_cache_limit(None)
