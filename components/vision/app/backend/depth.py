"""Depth Anything V2 Small in plain MLX: a picture in, a relative depth map out (larger = nearer).

DINOv2-S/14 backbone plus the DPT neck and head, written against the bundled `mlx`, `numpy` and
`Pillow` (all already in the shared Voice runtime), so the Vision worker adds no package. It reads
the publisher's own `model.safetensors` (`depth-anything/Depth-Anything-V2-Small-hf`) by its
Hugging Face names; nothing is converted. Pre- and post-processing follow the publisher's
`preprocessor_config.json` (DPT: keep aspect, short side 518, multiple of 14, bicubic, ImageNet
mean/std) and transformers' `post_process_depth_estimation` (bicubic back to the asked size).

Only the Small checkpoint is Apache-2.0; Base and Large are CC-BY-NC and must never be pinned here.
"""
from __future__ import annotations

import json
from pathlib import Path

import mlx.core as mx
import numpy as np
from PIL import Image

MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


def _cubic_matrix(size_in: int, size_out: int) -> np.ndarray:
    """PyTorch `interpolate(mode="bicubic", align_corners=False)` along one axis, as a matrix."""
    a, scale = -0.75, size_in / size_out
    out = np.zeros((size_out, size_in), np.float64)
    for i in range(size_out):
        src = (i + 0.5) * scale - 0.5
        base = int(np.floor(src))
        t = src - base
        weights = (((a * (t + 1) - 5 * a) * (t + 1) + 8 * a) * (t + 1) - 4 * a,
                   ((a + 2) * t - (a + 3)) * t * t + 1,
                   ((a + 2) * (1 - t) - (a + 3)) * (1 - t) * (1 - t) + 1,
                   ((a * (2 - t) - 5 * a) * (2 - t) + 8 * a) * (2 - t) - 4 * a)
        for k, w in enumerate(weights):
            out[i, min(max(base - 1 + k, 0), size_in - 1)] += w
    return out.astype(np.float32)


def _linear_matrix(size_in: int, size_out: int, align_corners: bool) -> np.ndarray:
    """PyTorch `interpolate(mode="bilinear")` along one axis, as a matrix."""
    out = np.zeros((size_out, size_in), np.float32)
    for i in range(size_out):
        if align_corners:
            src = i * (size_in - 1) / (size_out - 1) if size_out > 1 else 0.0
        else:
            src = max((i + 0.5) * size_in / size_out - 0.5, 0.0)
        low = min(int(np.floor(src)), size_in - 1)
        high = min(low + 1, size_in - 1)
        frac = src - low
        out[i, low] += 1 - frac
        out[i, high] += frac
    return out


def _resize(x: mx.array, height: int, width: int, align_corners: bool) -> mx.array:
    """Bilinear resize of an NHWC tensor (the exact PyTorch formula, no antialias)."""
    rows = mx.array(_linear_matrix(x.shape[1], height, align_corners))
    cols = mx.array(_linear_matrix(x.shape[2], width, align_corners))
    x = mx.einsum("ih,nhwc->niwc", rows, x)
    return mx.einsum("jw,niwc->nijc", cols, x)


def _conv(x: mx.array, w: dict, name: str, stride: int = 1, padding: int = 0) -> mx.array:
    out = mx.conv2d(x, w[name + ".weight"], stride=stride, padding=padding)
    bias = w.get(name + ".bias")
    return out if bias is None else out + bias


def _linear(x: mx.array, w: dict, name: str) -> mx.array:
    return x @ w[name + ".weight"] + w[name + ".bias"]


def _norm(x: mx.array, w: dict, name: str, eps: float) -> mx.array:
    return mx.fast.layer_norm(x, w[name + ".weight"], w[name + ".bias"], eps)


class DepthAnything:
    """One loaded checkpoint. `predict(image, long_side)` -> float32 array, nearer is larger."""

    def __init__(self, model_dir: Path, dtype=mx.float32) -> None:
        model_dir = Path(model_dir)
        config = json.loads((model_dir / "config.json").read_text())
        self.backbone = config["backbone_config"]
        self.patch = int(config["patch_size"])
        self.factors = [float(f) for f in config["reassemble_factors"]]
        self.stages = [int(i) for i in self.backbone["out_indices"]]
        self.heads = int(self.backbone["num_attention_heads"])
        self.eps = float(self.backbone.get("layer_norm_eps", 1e-6))
        self.short_side = 518
        raw = mx.load(str(model_dir / "model.safetensors"))
        self.weights = {}
        for name, value in raw.items():
            if value.ndim == 4 and ".resize." in name and "reassemble_stage" in name:
                factor = self.factors[int(name.split(".layers.")[1].split(".")[0])]
                # ConvTranspose2d (in, out, k, k) is kept as is for the matmul form below; the
                # stride-2 Conv2d (factor 0.5) takes the ordinary layout.
                if factor < 1:
                    value = value.transpose(0, 2, 3, 1)
            elif value.ndim == 4:
                value = value.transpose(0, 2, 3, 1)   # Conv2d (O, I, kh, kw) -> MLX (O, kh, kw, I)
            elif value.ndim == 2 and name.endswith(".weight"):
                value = value.T
            self.weights[name] = value.astype(dtype)
        self.dtype = dtype
        self._pos_cache: dict[tuple[int, int], mx.array] = {}
        mx.eval(self.weights)

    # ---- preprocessing (the publisher's DPT image processor) --------------------------------
    def input_size(self, width: int, height: int) -> tuple[int, int]:
        """(width, height) the model reads: keep aspect, the scale nearer 1 wins, multiple of 14."""
        scale_h, scale_w = self.short_side / height, self.short_side / width
        if abs(1 - scale_w) < abs(1 - scale_h):
            scale_h = scale_w
        else:
            scale_w = scale_h
        fit = lambda value: max(int(round(value / self.patch) * self.patch), self.patch)
        return fit(scale_w * width), fit(scale_h * height)

    def pixels(self, image: Image.Image) -> np.ndarray:
        width, height = self.input_size(*image.size)
        array = np.asarray(image.convert("RGB").resize((width, height), Image.BICUBIC), np.float32)
        return ((array / 255.0 - MEAN) / STD)[None]   # NHWC

    # ---- DINOv2 backbone ------------------------------------------------------------------
    def _positions(self, rows: int, cols: int) -> mx.array:
        key = (rows, cols)
        if key not in self._pos_cache:
            table = np.array(self.weights["backbone.embeddings.position_embeddings"].astype(mx.float32))[0]
            side = int(round((table.shape[0] - 1) ** 0.5))
            grid = table[1:].reshape(side, side, -1)
            grid = np.einsum("ih,hwc->iwc", _cubic_matrix(side, rows), grid)
            grid = np.einsum("jw,iwc->ijc", _cubic_matrix(side, cols), grid)
            self._pos_cache[key] = mx.array(
                np.concatenate([table[:1], grid.reshape(rows * cols, -1)], 0)[None]).astype(self.dtype)
        return self._pos_cache[key]

    def _layer(self, x: mx.array, index: int) -> mx.array:
        w, name = self.weights, f"backbone.encoder.layer.{index}"
        batch, length, hidden = x.shape
        h = _norm(x, w, name + ".norm1", self.eps)
        split = lambda t: t.reshape(batch, length, self.heads, -1).transpose(0, 2, 1, 3)
        q = split(_linear(h, w, name + ".attention.attention.query"))
        k = split(_linear(h, w, name + ".attention.attention.key"))
        v = split(_linear(h, w, name + ".attention.attention.value"))
        h = mx.fast.scaled_dot_product_attention(q, k, v, scale=(hidden // self.heads) ** -0.5)
        h = h.transpose(0, 2, 1, 3).reshape(batch, length, hidden)
        x = x + _linear(h, w, name + ".attention.output.dense") * w[name + ".layer_scale1.lambda1"]
        h = _norm(x, w, name + ".norm2", self.eps)
        h = _linear(_gelu(_linear(h, w, name + ".mlp.fc1")), w, name + ".mlp.fc2")
        return x + h * w[name + ".layer_scale2.lambda1"]

    def features(self, pixels: mx.array) -> tuple[list[mx.array], int, int]:
        w = self.weights
        rows, cols = pixels.shape[1] // self.patch, pixels.shape[2] // self.patch
        x = _conv(pixels, w, "backbone.embeddings.patch_embeddings.projection", stride=self.patch)
        x = x.reshape(x.shape[0], rows * cols, -1)
        cls = mx.broadcast_to(w["backbone.embeddings.cls_token"], (x.shape[0], 1, x.shape[-1]))
        x = mx.concatenate([cls, x], axis=1) + self._positions(rows, cols)
        maps = []
        for index in range(max(self.stages)):
            x = self._layer(x, index)
            if index + 1 in self.stages:
                maps.append(_norm(x, w, "backbone.layernorm", self.eps))
        return maps, rows, cols

    # ---- DPT neck and head ----------------------------------------------------------------
    def _reassemble(self, x: mx.array, index: int, rows: int, cols: int) -> mx.array:
        w, name = self.weights, f"neck.reassemble_stage.layers.{index}"
        x = x[:, 1:].reshape(x.shape[0], rows, cols, -1)
        x = _conv(x, w, name + ".projection")
        factor = self.factors[index]
        if factor > 1:
            # ConvTranspose2d with kernel == stride and no padding: every input pixel paints its own
            # k x k block, which is one matmul and a reshape.
            k, kernel = int(factor), w[name + ".resize.weight"]       # (in, out, k, k)
            batch, height, width, _ = x.shape
            out = (x @ kernel.reshape(kernel.shape[0], -1)).reshape(batch, height, width, -1, k, k)
            out = out.transpose(0, 1, 4, 2, 5, 3).reshape(batch, height * k, width * k, -1)
            x = out + w[name + ".resize.bias"]
        elif factor < 1:
            x = _conv(x, w, name + ".resize", stride=int(round(1 / factor)), padding=1)
        return x

    def _residual(self, x: mx.array, name: str) -> mx.array:
        w = self.weights
        h = _conv(mx.maximum(x, 0), w, name + ".convolution1", padding=1)
        h = _conv(mx.maximum(h, 0), w, name + ".convolution2", padding=1)
        return h + x

    def _fuse(self, x: mx.array, residual: mx.array | None, index: int, size) -> mx.array:
        name = f"neck.fusion_stage.layers.{index}"
        if residual is not None:
            if residual.shape != x.shape:
                residual = _resize(residual, x.shape[1], x.shape[2], align_corners=False)
            x = x + self._residual(residual, name + ".residual_layer1")
        x = self._residual(x, name + ".residual_layer2")
        height, width = size if size is not None else (x.shape[1] * 2, x.shape[2] * 2)
        x = _resize(x, height, width, align_corners=True)
        return _conv(x, self.weights, name + ".projection")

    def forward(self, pixels: mx.array) -> mx.array:
        """NHWC normalised pixels -> (N, H, W) predicted relative depth (the model's own output)."""
        w = self.weights
        maps, rows, cols = self.features(pixels.astype(self.dtype))
        feats = [_conv(self._reassemble(m, i, rows, cols), w, f"neck.convs.{i}", padding=1)
                 for i, m in enumerate(maps)]
        feats = feats[::-1]
        fused = None
        for index, feat in enumerate(feats):
            size = feats[index + 1].shape[1:3] if index != len(feats) - 1 else None
            fused = self._fuse(feat if fused is None else fused, None if fused is None else feat,
                               index, size)
        x = _conv(fused, w, "head.conv1", padding=1)
        x = _resize(x, rows * self.patch, cols * self.patch, align_corners=True)
        x = mx.maximum(_conv(x, w, "head.conv2", padding=1), 0)
        x = mx.maximum(_conv(x, w, "head.conv3"), 0)
        return x[..., 0].astype(mx.float32)

    def predict(self, image: Image.Image, width: int, height: int) -> np.ndarray:
        """Relative depth at (width, height): the model's map, bicubic-resized like transformers'
        `post_process_depth_estimation`. Larger values are nearer."""
        raw = self.forward(mx.array(self.pixels(image)))[0]
        mx.eval(raw)
        raw = np.array(raw)
        rows = _cubic_matrix(raw.shape[0], height)
        cols = _cubic_matrix(raw.shape[1], width)
        return (rows @ raw @ cols.T).astype(np.float32)


def _gelu(x: mx.array) -> mx.array:
    """Exact (erf) GELU, as PyTorch's default."""
    return x * (1 + mx.erf(x * 0.7071067811865476)) / 2


def to_png_values(depth: np.ndarray) -> tuple[np.ndarray, float]:
    """8-bit map (255 = nearest) and the usable range (p95 - p5 of the 0..1 map)."""
    low, high = float(depth.min()), float(depth.max())
    unit = (depth - low) / (high - low) if high > low else np.zeros_like(depth)
    spread = float(np.percentile(unit, 95) - np.percentile(unit, 5))
    return np.clip(np.round(unit * 255), 0, 255).astype(np.uint8), round(spread, 4)
