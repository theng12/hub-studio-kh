"""A memory-bounded decode for the FLUX.2 VAE: the same maths, a fraction of the peak.

Why it exists.  At 1280x720 mflux 0.20.0's FLUX.2 decoder peaks at 9.0 GB of MLX memory
(measured, M2 Max) on a model whose weights are 0.17 GB.  Nothing in it is a leak.  The decoder
runs a 3x3 convolution and a float32 GroupNorm over a 256-channel 720x1280 map, and each
of those materialises several full-size temporaries at once: the convolution unfolds its input
(about 9x), and mlx's ``pytorch_compatible`` GroupNorm transposes, normalises and transposes
back a float32 copy (about 4x the 0.9 GB float32 map).  That one spike is bigger than the whole
rest of the pipeline (text encoder 2.3 GB, transformer 2.2 GB, activations ~1 GB), and it is what
pushed the 8 GB tier to 9.8 GB MLX and about 4 GB of swap per image.

The obvious fix, VAE tiling, is deliberately off for this decoder - mflux marks it
``supports_implicit_tiling = False`` because each tile carries its own GroupNorm statistics and
the colour drifts between tiles.  This module keeps the statistics global and bands only the
work that is local:

* a 3x3 convolution is local (one row of halo), so it runs on horizontal bands of the map;
* GroupNorm is not local, so its mean and variance are accumulated over the whole map in two
  passes over bands (mean first, then squared deviation - the same two-pass form mlx uses) and
  the normalisation is then applied band by band.

Everything else (SiLU, the residual add, the 1x1 shortcut, the nearest-neighbour upsample and the
attention in the mid block, which runs at 90x160) is unchanged.  The maths is identical to
mflux's; what is not guaranteed is bit-for-bit equality, because a band reduces in a different
order than the whole map.  The result is bfloat16 either way, so the difference is bf16 rounding
noise: ``tests/test_image_vae_bounded.py`` pins it, and the before/after images the qualification
writes are the same seeds through both decoders.

The module touches mflux only through the VAE object it is handed (its ``decoder`` attributes),
and :func:`supports` says whether that object has the shape this code was written against, so a
future mflux that reorganises the decoder falls back to mflux's own decode instead of failing.
"""
from __future__ import annotations

import mlx.core as mx
from mlx import nn

# One band's float32 working set. 16 MB keeps every temporary a band creates small next to the
# 0.5-1 GB full-size maps (peak 2.5 GB at 1280x720 against 3.4 GB at 64 MB bands and 12.1 GB for
# mflux's own decode, measured), at no cost in time.
BAND_BYTES = 16 * 1000 ** 2
_PRECISION = mx.bfloat16  # mflux's ModelConfig.precision; the decoder casts to it after every norm.


def supports(vae: object) -> bool:
    """True when ``vae`` has the decoder layout this code was written against."""
    decoder = getattr(vae, 'decoder', None)
    try:
        blocks = decoder.up_blocks
        # By name as well as by shape: the layer names mflux's other decoders use overlap this one's,
        # and an NCHW decoder run through NHWC code would not fail, it would be wrong.
        return (type(vae).__name__ == 'Flux2VAE' and callable(getattr(vae, 'unpack_packed_latents', None))
                and callable(decoder.conv_in) and callable(decoder.mid_block)
                and callable(decoder.conv_out) and callable(decoder.conv_norm_out)
                and len(blocks) > 0
                and all(hasattr(resnet, name) for block in blocks for resnet in block.resnets
                        for name in ('norm1', 'conv1', 'norm2', 'conv2', 'conv_shortcut'))
                and all(hasattr(up, 'conv') for block in blocks for up in block.upsamplers))
    except AttributeError:
        return False


def _rows(height: int, row_bytes: int, band_bytes: int) -> int:
    return max(1, min(height, band_bytes // max(1, row_bytes)))


def _group_norm_silu(norm: nn.GroupNorm, x: mx.array, band_bytes: int, *, silu: bool) -> mx.array:
    """``silu(GroupNorm(x.float32).astype(bf16))`` on an NHWC map, statistics over the whole map."""
    batch, height, width, channels = x.shape
    groups = norm.num_groups
    size = channels // groups
    rows = _rows(height, batch * width * channels * 4, band_bytes)
    count = height * width * size
    total = mx.zeros((batch, groups), mx.float32)
    for top in range(0, height, rows):
        band = x[:, top:top + rows].astype(mx.float32).reshape(batch, -1, groups, size)
        total = total + band.sum(axis=(1, 3))
        mx.eval(total)
    mean = (total / count)[:, None, :, None]
    spread = mx.zeros((batch, groups), mx.float32)
    for top in range(0, height, rows):
        band = x[:, top:top + rows].astype(mx.float32).reshape(batch, -1, groups, size) - mean
        spread = spread + (band * band).sum(axis=(1, 3))
        mx.eval(spread)
    inverse = mx.rsqrt(spread / count + norm.eps)[:, None, :, None]
    pieces = []
    for top in range(0, height, rows):
        part = x[:, top:top + rows]
        band = (part.astype(mx.float32).reshape(batch, -1, groups, size) - mean) * inverse
        band = band.reshape(part.shape)
        band = (norm.weight * band + norm.bias).astype(_PRECISION)
        pieces.append(nn.silu(band) if silu else band)
        mx.eval(pieces[-1])
    return pieces[0] if len(pieces) == 1 else mx.concatenate(pieces, axis=1)


def _conv3x3(conv: nn.Conv2d, x: mx.array, band_bytes: int) -> mx.array:
    """A stride-1, padding-1 3x3 convolution of an NHWC map, in horizontal bands with a 1-row halo."""
    batch, height, width, channels = x.shape
    rows = _rows(height, batch * width * channels * x.dtype.size, band_bytes)
    if rows >= height:
        # Not ``conv(x)``: the decoder's own ``conv_out`` subclass takes NCHW, this takes NHWC.
        whole = mx.conv2d(x, conv.weight, (1, 1), (1, 1), (1, 1), 1)
        return whole + conv.bias if 'bias' in conv else whole
    pieces = []
    for top in range(0, height, rows):
        bottom = min(top + rows, height)
        part = x[:, max(top - 1, 0):min(bottom + 1, height)]
        if top == 0 or bottom == height:
            part = mx.pad(part, ((0, 0), (1 if top == 0 else 0, 1 if bottom == height else 0),
                                 (0, 0), (0, 0)))
        out = mx.conv2d(part, conv.weight, (1, 1), (0, 1), (1, 1), 1)
        if 'bias' in conv:
            out = out + conv.bias
        mx.eval(out)
        pieces.append(out)
    return mx.concatenate(pieces, axis=1)


def _resnet(block, x: mx.array, band_bytes: int) -> mx.array:
    h = _group_norm_silu(block.norm1, x, band_bytes, silu=True)
    h = _conv3x3(block.conv1, h, band_bytes)
    h = _group_norm_silu(block.norm2, h, band_bytes, silu=True)
    h = _conv3x3(block.conv2, h, band_bytes)
    residual = block.conv_shortcut(x) if block.conv_shortcut is not None else x
    out = h + residual
    mx.eval(out)
    return out


def _upsample(up, x: mx.array, band_bytes: int) -> mx.array:
    x = mx.repeat(mx.repeat(x, 2, axis=1), 2, axis=2)
    return _conv3x3(up.conv, x, band_bytes)


def decode(vae, latent: mx.array, band_bytes: int = BAND_BYTES) -> mx.array:
    """What ``vae.decode(latent)`` returns - a (B, 3, H, W) bfloat16 image in [-1, 1] - at a bounded peak.

    ``latent`` is the plain (B, 32, H/8, W/8) latent that ``Flux2VAE.unpack_packed_latents``
    produces.  The steps before the up-blocks (``post_quant_conv``, ``conv_in``, the mid block) run
    at 1/8 resolution and are mflux's own modules; the up-blocks and the output norm and
    convolution, where the memory is, run on the NHWC map the whole way, which also drops the
    NCHW<->NHWC transposes mflux does around every layer.
    """
    decoder = vae.decoder
    z = (latent / vae.scaling_factor) + vae.shift_factor
    z = mx.transpose(z, (0, 2, 3, 1))
    z = vae.post_quant_conv(z)
    z = mx.transpose(z, (0, 3, 1, 2))
    h = decoder.mid_block(decoder.conv_in(z))
    h = mx.transpose(h, (0, 2, 3, 1))
    mx.eval(h)
    for block in decoder.up_blocks:
        for resnet in block.resnets:
            h = _resnet(resnet, h, band_bytes)
        for up in block.upsamplers:
            h = _upsample(up, h, band_bytes)
    h = _group_norm_silu(decoder.conv_norm_out, h, band_bytes, silu=True)
    h = _conv3x3(decoder.conv_out, h, band_bytes)
    return mx.transpose(h, (0, 3, 1, 2))
