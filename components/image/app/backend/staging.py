"""Make mflux's generate loop actually run in stages: encode, free the encoder, denoise, free, decode.

mflux 0.20.0 loads every weight file lazily (``mx.load`` returns arrays that read their file the
first time something evaluates them), builds the text encoder, the transformer and the VAE up
front, and then runs the whole generation.  Two things in that flow keep more memory resident
than the stages need, and both were measured on the 8 GB tier's failures:

1. **The prompt is encoded lazily, so the encoder is never really finished before the transformer
   loads.**  ``generate_image`` hands the still-unevaluated prompt embeddings to the denoise loop;
   the encoder's compute (and so its 2.3 GB of weights) happens inside the first denoise step,
   next to the transformer's 2.2-3.5 GB.  mflux's ``MemorySaver`` deletes ``model.text_encoder``
   before that step, but the unevaluated embeddings still reference the encoder's weight arrays,
   so nothing is freed until the step is over.  Measured: Z-Image-Turbo 4-bit low-RAM peaks at
   5.1-5.7 GB of MLX memory = encoder + transformer + activations.
2. **The transformer cannot be freed before the VAE decode.**  ``generate_image`` keeps a local
   ``predict`` closure over the transformer until the function returns, so ``MemorySaver``'s
   ``model.transformer = None`` after the loop releases nothing and the decode runs with the
   transformer still resident.

The hooks below fix exactly those two things and change no arithmetic: the prompt embeddings are
evaluated, and only then is the encoder dropped; the loop's transformer reference goes through one
holder that is emptied when the loop ends.  They are installed on the instance (not on mflux), they
are all-or-nothing per model shape, and a model that does not look like what they were written
against is left alone and reported.  They are meant for a model object that serves one generation:
the encoder is gone afterwards, so :func:`apply` is never used on a model the worker keeps
resident.
"""
from __future__ import annotations

import gc

import inspect
import sys

# Methods mflux's two registered models encode their prompt in, by model class.  The name is the
# whole difference: both return the embeddings the denoise loop needs.
_ENCODE_METHODS = ('_encode_prompt_pair', '_encode_prompts')


def _mx():
    """The ``mlx.core`` mflux already imported (a model instance exists, so it has been)."""
    return sys.modules['mlx.core']


def _release() -> None:
    gc.collect()
    _mx().clear_cache()


class _Holder:
    """The one reference the denoise loop keeps to the transformer; emptying it frees the weights.

    It holds two things, because both keep the weights alive: the module, and the ``predict``
    function mflux builds around it.  On Apple Silicon other than the M1/M2 line mflux wraps that
    function in ``mx.compile``, whose trace cache holds every weight it saw (measured: the 2.2 GB
    FLUX.2 transformer stayed resident until the function object died, with the module itself long
    gone), so the compiled function must be droppable too.
    """

    def __init__(self, module) -> None:
        self.module = module
        self.predict = None

    def __call__(self, *args, **kwargs):
        return self.module(*args, **kwargs)

    def clear(self) -> None:
        self.module = None
        self.predict = None


class _EndOfLoop:
    """An mflux ``AfterLoopCallback``: the loop is over, the transformer is not needed again."""

    def __init__(self, instance, holder, on_stage) -> None:
        self.instance, self.holder, self.on_stage = instance, holder, on_stage

    def call_after_loop(self, seed, prompt, latents, config) -> None:
        # The loop ends with `mx.eval(latents)`, so the weights have done their last work.
        self.holder.clear()
        self.instance.transformer = None
        _release()
        self.on_stage('denoised')


def _uncompiled(factory, holder):
    """``factory(holder)`` with mflux's non-compiled denoise function (what it uses on M1/M2).

    A streamed model evaluates inside its blocks, which ``mx.compile`` forbids. The switch is mflux's own
    (``AppleSiliconUtil.is_m1_or_m2``), flipped only for the one call that builds the function.
    """
    try:
        from mflux.utils.apple_silicon import AppleSiliconUtil
    except ImportError:
        return factory(holder)
    raw = inspect.getattr_static(AppleSiliconUtil, 'is_m1_or_m2')
    AppleSiliconUtil.is_m1_or_m2 = staticmethod(lambda: True)
    try:
        return factory(holder)
    finally:
        AppleSiliconUtil.is_m1_or_m2 = raw


def supports(instance: object) -> bool:
    """Whether ``instance`` has the pieces :func:`apply` hooks."""
    return (any(callable(getattr(instance, name, None)) for name in _ENCODE_METHODS)
            and callable(getattr(instance, '_predict', None))
            and getattr(instance, 'transformer', None) is not None
            and getattr(instance, 'text_encoder', None) is not None
            and hasattr(getattr(instance, 'callbacks', None), 'register'))


def apply(instance, *, stages: bool = True, bounded_decode: bool = False, uncompiled: bool = False,
          on_stage=None) -> list[str]:
    """Install the stage hooks on ``instance``; returns the names of the ones installed.

    ``on_stage(name)`` is called as each stage ends (``encoded``, ``denoised``), so a measurement
    can read MLX's peak between stages without the worker knowing about the measurement.
    ``stages=False`` leaves the encoder and transformer alone, so the model can serve another job.
    ``uncompiled`` builds the denoise function without ``mx.compile`` (required when blocks are streamed).
    ``bounded_decode`` swaps the FLUX.2 VAE's decode for :mod:`vae_bounded`'s.
    """
    note = on_stage or (lambda _name: None)
    installed = []

    if stages and supports(instance):
        name = next(name for name in _ENCODE_METHODS if callable(getattr(instance, name, None)))
        original_encode = getattr(instance, name)

        def encode(*args, **kwargs):
            mx = _mx()
            result = original_encode(*args, **kwargs)
            mx.eval(*[value for value in (result if isinstance(result, tuple) else (result,))
                      if isinstance(value, mx.array)])
            instance.text_encoder = None
            _release()
            note('encoded')
            return result

        setattr(instance, name, encode)
        installed.append('encode-then-free')

        holder = _Holder(instance.transformer)
        original_predict = instance._predict

        def predict_through_holder(_transformer):
            holder.predict = (_uncompiled(original_predict, holder) if uncompiled
                              else original_predict(holder))

            def predict(*args, **kwargs):
                return holder.predict(*args, **kwargs)

            return predict

        instance._predict = predict_through_holder
        instance.callbacks.register(_EndOfLoop(instance, holder, note))
        installed.append('free-transformer-after-loop')

    vae = getattr(instance, 'vae', None)
    if bounded_decode and getattr(vae, 'decoder', None) is not None:
        from . import vae_bounded
        if not vae_bounded.supports(vae):
            return installed

        def decode_packed_latents(packed_latents, tiling_config=None):
            return vae_bounded.decode(vae, vae.unpack_packed_latents(packed_latents))

        vae.decode_packed_latents = decode_packed_latents
        installed.append('bounded-vae-decode')
    return installed
