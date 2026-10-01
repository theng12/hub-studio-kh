"""Keep the MLX voice worker's memory near what the model needs, not what it can grab.

Measured on Qwen3-TTS 0.6B Base 8-bit (weights 2.1 GB resident): one section's peak was 6-7 GB active
and 14-17 GB of freed-but-kept MLX buffer cache on a 32 GB Mac, which is what pushed an 8 GB Mac into
swap and a never-finishing long script. Two causes, both fixed here without changing the audio:

1. MLX keeps every freed Metal buffer for reuse and its default ceiling scales with the machine, so the
   cache grows to whatever RAM is free. `bound_cache` caps it for every MLX engine.
2. mlx-audio's Qwen3 codec decoder (`chunked_decode`) builds the vocoder for a whole 325-token chunk as
   one lazy graph; the vocoder's 1920x upsampling makes that ~5 GB of transient buffers per section.
   Everything after the (cheap) transformer is a stack of causal convolutions with a receptive field of
   under 10 tokens, so running it in slabs that each carry `_SLAB_CONTEXT` tokens of left context yields
   the same samples (max difference ~2e-6, float rounding) in a fraction of the memory.
"""
from __future__ import annotations

import os

# 0 is ~1.6x slower (VoxCPM2's sampler re-allocates every step); 256 MB-2 GB measured the same speed.
CACHE_LIMIT_MB = 512
QWEN3_DECODE_SLAB = 24      # tokens decoded per vocoder pass
EVAL_STAGES = True
_SLAB_CONTEXT = 12          # >= the post-transformer receptive field (~10 tokens), verified to 2e-6


def bound_cache() -> None:
    """Cap MLX's buffer cache (STUDIOHUB_MLX_CACHE_LIMIT_MB overrides; <0 keeps MLX's default)."""
    try:
        import mlx.core as mx
        limit_mb = float(os.environ.get("STUDIOHUB_MLX_CACHE_LIMIT_MB", CACHE_LIMIT_MB))
        if limit_mb >= 0:
            mx.set_cache_limit(int(limit_mb * 1_000_000))
    except Exception:
        pass   # a missing or older MLX simply keeps its default; never fail a job over a memory knob


def _slabbed_forward(decoder, codes, slab: int):
    """`decoder(codes)` with the vocoder run `slab` tokens at a time. codes: [B, Q, T] -> [B, 1, S]."""
    import mlx.core as mx

    hidden = decoder.quantizer.decode(codes)
    hidden = decoder.pre_conv(mx.transpose(hidden, (0, 2, 1)))
    hidden = decoder.pre_transformer(hidden)   # [B, T, latent]: cheap, needs the whole chunk
    mx.eval(hidden)
    parts = []
    for start in range(0, hidden.shape[1], slab):
        context = min(_SLAB_CONTEXT, start)
        x = hidden[:, start - context:start + slab]
        for layer in (*(l for layers in decoder.upsample for l in layers), *decoder.decoder):
            x = layer(x)
            if EVAL_STAGES:
                mx.eval(x)   # sync here so Metal can recycle the previous stage's buffers
        x = mx.clip(mx.transpose(x, (0, 2, 1)), -1.0, 1.0)[..., context * decoder.total_upsample:]
        mx.eval(x)
        parts.append(x)
    return mx.concatenate(parts, axis=-1)


def bound_qwen3_decoder() -> bool:
    """Replace the Qwen3 codec decoder's `chunked_decode` with the slabbed, same-output version."""
    try:
        import mlx.core as mx
        from mlx_audio.tts.models.qwen3_tts.speech_tokenizer import Qwen3TTSSpeechTokenizerDecoder as Decoder
    except Exception:
        return False
    if getattr(Decoder, "_studiohub_bounded", False):
        return True
    required = ("quantizer", "pre_conv", "pre_transformer", "upsample", "decoder", "total_upsample")
    if not callable(getattr(Decoder, "chunked_decode", None)):
        return False
    original = Decoder.chunked_decode

    def chunked_decode(self, codes, chunk_size: int = 300, left_context_size: int = 25):
        if not all(hasattr(self, name) for name in required):
            return original(self, codes, chunk_size, left_context_size)
        wavs, start = [], 0
        while start < codes.shape[-1]:
            end = min(start + chunk_size, codes.shape[-1])
            context = left_context_size if start - left_context_size > 0 else start
            wav = _slabbed_forward(self, codes[..., start - context:end], QWEN3_DECODE_SLAB)
            wavs.append(wav[..., context * self.total_upsample:])
            start = end
        return mx.concatenate(wavs, axis=-1)

    Decoder.chunked_decode = chunked_decode
    Decoder._studiohub_bounded = True
    return True


class _StagedEncoder:
    """The Mimi reference encoder with a sync after every layer (same values, a fraction of the memory)."""

    def __init__(self, encoder):
        self._encoder = encoder

    def __getattr__(self, name):
        return getattr(self._encoder, name)

    def __call__(self, xs):
        import mlx.core as mx
        import mlx.nn as nn

        enc = self._encoder
        xs = enc.init_conv1d(xs)
        for layer in enc.layers:
            mx.eval(xs)
            xs = layer(xs)
        mx.eval(xs)
        return enc.final_conv1d(nn.elu(xs, alpha=1.0))


def bound_qwen3_encoder(model) -> bool:
    """Bound the memory of encoding the clone reference (it grew ~0.1 GB per second of reference)."""
    try:
        tokenizer = getattr(model, "speech_tokenizer", None) or model.audio_tokenizer   # Qwen3 / Breeze
        encoder = tokenizer.encoder_model.encoder
    except AttributeError:
        return False
    if isinstance(encoder, _StagedEncoder) or not all(
        hasattr(encoder, name) for name in ("init_conv1d", "layers", "final_conv1d")
    ):
        return False
    tokenizer.encoder_model.encoder = _StagedEncoder(encoder)
    return True


# ---- VoxCPM2: its audio VAE decodes a whole 25-30 s section (48 kHz) in one pass: 7-9 GB active ----

VOXCPM_DECODE_SLAB = 32     # latent frames (40 ms each) per decoder pass
_VOXCPM_CONTEXT = 24        # >= the decoder's receptive field (~19 frames), verified against the unslabbed decode


class _StagedLocalEncoder:
    """VoxCPM2's local patch encoder with a sync after every layer.

    Its weights are computed on in fp32 activations, so one unsynced call holds an fp32 copy of every
    layer's weights at once (+0.8 GB even for a single patch, on each of the ~170 steps per section).
    """

    def __init__(self, module):
        self._module = module

    def __getattr__(self, name):
        return getattr(self._module, name)

    def __call__(self, x):
        import mlx.core as mx

        module = self._module
        inner = module.encoder
        batch, steps, patch, _ = x.shape
        x = module.in_proj(x)
        special = mx.broadcast_to(module.special_token, (batch, steps, 1, module.config.hidden_size))
        h = mx.concatenate([special, x], axis=2).reshape(batch * steps, patch + 1, -1)
        cos = sin = None
        if inner.rope is not None:
            cos, sin = inner.rope(mx.arange(0, patch + 1).astype(mx.int32))
            cos, sin = cos[None, :, :], sin[None, :, :]
        for layer in inner.layers:
            h, _ = layer(h, cos, sin, mask=None, cache=None)
            mx.eval(h)
        return inner.norm(h)[:, 0, :].reshape(batch, steps, -1)


def _staged_unit(unit, x):
    """CausalResidualUnit with a sync after each op."""
    import mlx.core as mx

    y = x
    for op in (unit.snake1, unit.conv1, unit.snake2, unit.conv2):
        y = op(y)
        mx.eval(y)
    return x + y


class _StagedVAEEncoder:
    """VoxCPM2's reference encoder with a sync after every op (same values, a fraction of the memory)."""

    def __init__(self, encoder):
        self._encoder = encoder

    def __getattr__(self, name):
        return getattr(self._encoder, name)

    def __call__(self, x):
        import mlx.core as mx

        enc = self._encoder
        x = enc.conv_in(x)
        for block in enc.blocks.layers:
            for unit in (block.res1, block.res2, block.res3):
                x = _staged_unit(unit, x)
            x = block.snake(x)
            mx.eval(x)
            x = block.conv(x)
        mx.eval(x)
        return enc.fc_mu(x)


def _voxcpm_decode(vae, z, sr_cond=None):
    """`AudioVAE.decode` with the decoder run VOXCPM_DECODE_SLAB frames at a time, one block at a time."""
    import mlx.core as mx

    if sr_cond is None:
        sr_cond = mx.array([vae.out_sample_rate], dtype=mx.int32)
    decoder = vae.decoder
    conditioned = decoder._sr_boundaries is not None and sr_cond is not None
    sr_idx = decoder.get_sr_idx(sr_cond) if conditioned else None
    parts = []
    for start in range(0, z.shape[1], VOXCPM_DECODE_SLAB):
        context = min(_VOXCPM_CONTEXT, start)
        x = decoder.conv_in(z[:, start - context:start + VOXCPM_DECODE_SLAB])
        for index, block in enumerate(decoder.blocks.layers):
            mx.eval(x)
            if conditioned and decoder.sr_cond_layers[index] is not None:
                x = decoder.sr_cond_layers[index](x, sr_idx)
            x = block(x)
        x = mx.tanh(decoder.conv_out(decoder.snake_out(x))).squeeze(-1)
        # keep this slab's own frames; only the last slab keeps the few samples the transposed convs spill
        stop = None if start + VOXCPM_DECODE_SLAB >= z.shape[1] else (context + VOXCPM_DECODE_SLAB) * vae.decode_chunk_size
        x = x[:, context * vae.decode_chunk_size:stop]
        mx.eval(x)
        parts.append(x)
    return mx.concatenate(parts, axis=1)


def bound_voxcpm_vae(model) -> bool:
    """Bound the memory of VoxCPM2's reference encode and codec decode (instance-level, same audio)."""
    try:
        vae = model.audio_vae
        decoder, encoder = vae.decoder, vae.encoder
    except AttributeError:
        return False
    if getattr(vae, "_studiohub_bounded", False):
        return True
    if not all(hasattr(decoder, n) for n in ("conv_in", "blocks", "snake_out", "conv_out", "sr_cond_layers", "get_sr_idx", "_sr_boundaries")):
        return False
    if not all(hasattr(encoder, n) for n in ("conv_in", "blocks", "fc_mu")):
        return False
    vae.encoder = _StagedVAEEncoder(encoder)
    vae.decode = lambda z, sr_cond=None: _voxcpm_decode(vae, z, sr_cond)
    vae._studiohub_bounded = True

    # Every section of a long script re-encoded the same reference clip (twice: as reference and as
    # continuation prompt). Same file + padding gives the same features, so keep the last few.
    encode_wav, memo = model._encode_wav, {}

    def cached_encode_wav(audio_input, padding_mode: str = "right", trim_silence_vad: bool = False):
        if not isinstance(audio_input, str):
            return encode_wav(audio_input, padding_mode, trim_silence_vad)
        try:
            stat = os.stat(audio_input)
        except OSError:
            return encode_wav(audio_input, padding_mode, trim_silence_vad)
        key = (audio_input, stat.st_mtime_ns, stat.st_size, padding_mode, trim_silence_vad)
        if key not in memo:
            if len(memo) >= 4:
                memo.pop(next(iter(memo)))
            memo[key] = encode_wav(audio_input, padding_mode, trim_silence_vad)
        return memo[key]

    model._encode_wav = cached_encode_wav

    encoder_ok = all(hasattr(getattr(model, "feat_encoder", None), n) for n in ("in_proj", "special_token", "encoder"))
    if encoder_ok:
        model.feat_encoder = _StagedLocalEncoder(model.feat_encoder)
    return True


# ---- OmniVoice: its Higgs codec decodes a whole 10-25 s section in one pass (3-4 GB) and the reference is re-encoded per section ----

OMNIVOICE_DECODE_SLAB = 32   # codec frames (40 ms each) per decoder pass
_OMNIVOICE_CONTEXT = 20      # frames of real neighbour kept on each side; >= the decoder's receptive field


def _omnivoice_decode(tokenizer, tokens):
    """`HiggsAudioTokenizer.decode` with the acoustic decoder run in slabs, one block at a time."""
    import mlx.core as mx

    squeeze = tokens.ndim == 2
    if squeeze:
        tokens = tokens[None]
    z = tokenizer.fc2(tokenizer.quantizer.decode(tokens))
    mx.eval(z)
    decoder, frames = tokenizer.acoustic_decoder, z.shape[1]
    samples_per_frame = 1
    for stride in decoder._STRIDES:
        samples_per_frame *= stride
    parts = []
    for start in range(0, frames, OMNIVOICE_DECODE_SLAB):
        stop = min(start + OMNIVOICE_DECODE_SLAB, frames)
        low, high = max(0, start - _OMNIVOICE_CONTEXT), min(frames, stop + _OMNIVOICE_CONTEXT)
        x = decoder.conv1(z[:, low:high])
        for block in decoder.block:
            mx.eval(x)
            expected = x.shape[1] * block._stride
            x = block.conv_t1(block.snake1(x))
            if x.shape[1] > expected:
                x = x[:, :expected, :]
            for unit in (block.res_unit1, block.res_unit2, block.res_unit3):
                mx.eval(x)
                x = unit(x)
        x = decoder.conv2(decoder.snake1(x))
        x = x[:, (start - low) * samples_per_frame:(stop - low) * samples_per_frame]
        mx.eval(x)
        parts.append(x)
    wav = mx.concatenate(parts, axis=1)
    return wav[0, :, 0] if squeeze else wav


def bound_omnivoice(model) -> bool:
    """Bound OmniVoice's codec decode (instance-level, same audio) and reuse the encoded reference."""
    try:
        tokenizer = model.audio_tokenizer
        decoder = tokenizer.acoustic_decoder
    except AttributeError:
        return False
    if getattr(tokenizer, "_studiohub_bounded", False):
        return True
    if not all(hasattr(decoder, n) for n in ("conv1", "block", "snake1", "conv2", "_STRIDES")):
        return False
    tokenizer.decode = lambda tokens: _omnivoice_decode(tokenizer, tokens)
    tokenizer._studiohub_bounded = True

    # generate_audio hands over the reference as an array, so there is no path to key on: key on the
    # samples. Every section of a long script encodes the same reference (+1.5 GB and ~2 s each time).
    encode, memo = tokenizer.encode, {}

    def cached_encode(waveform, *args, **kwargs):
        import hashlib

        import mlx.core as mx
        import numpy as np

        data = np.ascontiguousarray(np.asarray(mx.array(waveform).astype(mx.float32)))
        key = (data.shape, hashlib.blake2b(data.tobytes(), digest_size=16).hexdigest(), repr(args), repr(kwargs))
        if key not in memo:
            if len(memo) >= 4:
                memo.pop(next(iter(memo)))
            memo[key] = encode(waveform, *args, **kwargs)
        return memo[key]

    tokenizer.encode = cached_encode
    return True


BREEZE_DECODE_SLAB = 8   # Breeze's weights are 3.1 GB, so its codec slab is smaller than Qwen's (24)


class _StagedBackbone:
    """Breeze's Qwen3 backbone with a sync after every layer while it prefills a prompt (+0.85 GB otherwise)."""

    def __init__(self, backbone):
        self._backbone = backbone

    def __getattr__(self, name):
        return getattr(self._backbone, name)

    def __call__(self, input_ids=None, *, input_embeddings=None, cache=None):
        import mlx.core as mx
        from mlx_audio.lm.models.base import create_attention_mask

        backbone = self._backbone
        if (input_ids is None) == (input_embeddings is None):
            raise ValueError("Pass exactly one of input_ids or input_embeddings.")
        hidden = input_embeddings if input_embeddings is not None else backbone.embed_tokens(input_ids)
        if cache is None:
            cache = [None] * len(backbone.layers)
        mask = create_attention_mask(hidden, cache[0])
        stage = hidden.shape[1] > 1          # prefill only: a one-token step is already tiny
        for layer, layer_cache in zip(backbone.layers, cache):
            hidden = layer(hidden, mask, layer_cache)
            if stage:
                mx.eval(hidden)
        return backbone.norm(hidden)


def bound_breeze(model) -> bool:
    """Breeze TTS 2 shares Qwen3's speech tokenizer: same decode and encoder bounds, plus a reference cache."""
    global QWEN3_DECODE_SLAB
    QWEN3_DECODE_SLAB = BREEZE_DECODE_SLAB
    ok = bound_qwen3_decoder()
    ok = bound_qwen3_encoder(model) and ok
    backbone = getattr(model, "backbone_model", None)
    if backbone is not None and hasattr(backbone, "layers") and not isinstance(backbone, _StagedBackbone):
        model.backbone_model = _StagedBackbone(backbone)
    encode_reference = getattr(model, "_encode_reference", None)
    if encode_reference is None or getattr(model, "_studiohub_ref_cached", False):
        return ok
    memo: dict = {}

    def cached_reference(ref_audio):
        if not isinstance(ref_audio, (str, os.PathLike)):
            return encode_reference(ref_audio)
        try:
            stat = os.stat(ref_audio)
        except OSError:
            return encode_reference(ref_audio)
        key = (str(ref_audio), stat.st_mtime_ns, stat.st_size)
        if key not in memo:
            if len(memo) >= 4:
                memo.pop(next(iter(memo)))
            memo[key] = encode_reference(ref_audio)
        return memo[key]

    model._encode_reference = cached_reference
    model._studiohub_ref_cached = True
    return ok


# ---- Registry: every MLX engine family must say how its memory is bounded (tests/test_mlx_memory_registry.py) ----

def _bound_qwen3(model) -> None:
    global QWEN3_DECODE_SLAB
    QWEN3_DECODE_SLAB = 24
    bound_qwen3_decoder()
    bound_qwen3_encoder(model)


# family -> (kind, how, bounder). kind "bounded": codec/VAE decode slabbed + reference encode staged/cached,
# measured peak in docs/mlx-memory-discipline.md. kind "cache-only": only the MLX cache cap applies; `how`
# must say why that is enough (or why nothing more is feasible) and what was measured.
PROFILES: dict[str, tuple[str, str, object]] = {
    "qwen3-tts": ("bounded", "speech-tokenizer decode in 24-token slabs, encoder synced per layer; S2 peak 7.3 -> 3.06 GB", _bound_qwen3),
    "voxcpm-mlx": ("bounded", "audio-VAE decode slabbed, encoders synced, reference features cached; S2 peak 9.2 -> 3.3 GB", lambda m: bound_voxcpm_vae(m)),
    "omnivoice": ("bounded", "Higgs codec decode slabbed, reference tokens cached; S2 peak 5.4 -> 2.7 GB (3.4 first section)", lambda m: bound_omnivoice(m)),
    "breeze-tts": ("bounded", "shares Qwen3's speech tokenizer: same decode/encoder bounds, reference tokens cached", lambda m: bound_breeze(m)),
    "chatterbox-mlx": ("cache-only", "not profiled on a codec decode; cache cap only (owner decision 2026-10-02)", None),
    "pocket-tts": ("cache-only", "tiny model (4-bit, streaming mimi decode); cache cap only (owner decision 2026-10-02)", None),
    "fish-audio-mlx": ("cache-only", "5B model, 6.7 GB of weights alone: it can never meet the 8 GB tier (catalog floor stays 24 GB), so its DAC codec decode is not slabbed; cache cap only. 13.3 GB peak measured on the fleet 2026-08-07", None),
}


def bound_model(family: str, model) -> str:
    """Apply the family's registered bounds to a freshly loaded model; returns its profile kind."""
    kind, _, bounder = PROFILES[family]
    if bounder is not None:
        bounder(model)
    return kind
