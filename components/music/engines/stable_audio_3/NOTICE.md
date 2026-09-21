# Stable Audio 3 — vendored MLX inference runtime

Source: <https://github.com/Stability-AI/stable-audio-3>
Path: `optimized/mlx/`
Pinned commit: `779434a908193105335fd8d833418603625b2859` (2026-09-01)
Vendored by Studio Hub on 2026-09-22.

## Licensing
- The upstream **code** in this directory is **MIT** (see `LICENSE`,
  Copyright (c) 2026 Stability AI).
- The **model weights** it runs are governed by the **Stability AI Community
  License** (<https://stability.ai/license>). They are not vendored here; they are
  imported as a pinned Studio Hub model package
  (`model-manifests/stable-audio-3-medium.json`) from
  `stabilityai/stable-audio-3-optimized`.

## Runtime
Pure MLX: `mlx`, `numpy`, `sentencepiece`, `huggingface_hub`, `soundfile`. No
PyTorch at inference (`torch` appears only in an optional checkpoint converter,
`models/defs/dit_mlx.py`, which Studio Hub does not call).

## What Studio Hub uses
`scripts/sa3_mlx.py` (text-to-audio, `--dit medium --decoder same-l`). Training,
LoRA, gradio, parity and benchmark scripts are vendored for completeness but are
not wired into the app.
