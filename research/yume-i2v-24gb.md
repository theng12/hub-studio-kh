# Yume 1.5 MLX 4-bit — 24 GB Mac research

Date: 2026-09-14. Research only: no model download, install, conversion or generation.

## Verdict

Supports ordinary image+prompt-to-video through mlx-video, but 24 GB is an unqualified candidate, not a verified safe fleet requirement. The current text-encoder loader makes memory substantially larger than the 4-bit title suggests. No exact-model 24 GB Mac speed or peak-memory benchmark has been established in this research.

## Exact assets

Model: https://huggingface.co/ckurasek/Yume-1.5-5B-720P-MLX-4bit
Revision: e2afbc9460e69320ea942e369a30b1a328441d7f.

Hugging Face API file metadata:

| File | Bytes | Decimal GB |
|---|---:|---:|
| model.safetensors | 2,945,950,389 | 2.95 |
| t5_encoder.safetensors | 11,361,845,505 | 11.36 |
| vae.safetensors | 2,818,778,952 | 2.82 |

Total weights: 17,126,574,846 bytes (17.13 GB / 15.95 GiB). Configuration and documentation add a few KB; google/umt5-xxl tokenizer assets and runtime dependencies are additional. Only transformer attention/FFN layers are 4-bit; T5 remains BF16 on disk and the VAE float32. File size is not peak memory.

## Runtime evidence and memory concern

Inspected mlx-video commit 87db56a51758fefb748a359b90a5283bb8ba4837:
https://github.com/Blaizzy/mlx-video/blob/87db56a51758fefb748a359b90a5283bb8ba4837/mlx_video/models/wan_2/utils.py

`load_t5_encoder` explicitly casts every loaded weight to `mx.float32`, then materializes encoder parameters. The BF16 encoder therefore requires approximately 22.72 GB / 21.16 GiB of parameter storage, before activations, load/conversion temporaries and the OS. This is a calculation from source and asset metadata, not measured runtime usage. Memory pressure or swapping is a serious concern on 24 GB; a 480p output does not shrink these text-encoder weights.

The generation path releases T5 before loading the video transformer, so summing all three component sizes as simultaneous memory is also misleading. It implements image conditioning and VAE spatial/temporal tiling. Later video stages must still be measured; advertised 4-bit size alone cannot establish fit.
https://github.com/Blaizzy/mlx-video/blob/87db56a51758fefb748a359b90a5283bb8ba4837/mlx_video/models/wan_2/generate.py

## Capabilities and practical limits

The model card describes a Yume fine-tune of Wan2.2 TI2V-5B with both T2V and I2V. Native 1280×704 at 24 fps; card example 832×480, 81 frames, 30 steps. Configuration defaults are 40 steps, guidance 5, 81 frames. At 24 fps, 49/81/121 frames produce about 2.04/3.38/5.04 seconds of encoded playback. Frame count must be 4n+1; dimensions align to multiples of 32. These are examples, not measured memory limits or an unlimited-duration promise.

The runtime guide prefers native resolution for quality, whereas the conversion card demonstrates lower resolution. A low-resolution memory check should not be treated as a native-resolution quality verdict. Four-bit quantization is not four-step distillation: a Wan 14B Lightning LoRA example is not evidence of compatibility with this 5B Yume model.

The original YUME project describes interactive/long-video features and an RTX4090 Laptop 16 GB result. That CUDA result does not establish equivalent Apple unified-memory fit, speed, or full feature parity in the generic MLX clip pipeline.
https://github.com/stdstu12/YUME
https://github.com/Blaizzy/mlx-video/blob/87db56a51758fefb748a359b90a5283bb8ba4837/mlx_video/models/wan_2/README.md

## Published speed anchor

The converter author publishes these results for the separate BF16 Yume MLX checkpoint on an M5 Pro with 48 GB unified memory:

| Output | Steps | Reported total |
|---|---:|---:|
| 640×352, 17 frames | 10 | ~40 seconds |
| 832×480, 81 frames | 30 | ~6 minutes (4.5 minutes denoising, 1.5 minutes decoding) |

At the stated 24 fps the longer output is about 3.38 seconds. These are author-reported BF16 results, not measurements of the requested 4-bit checkpoint, a 24 GB Mac, or our fleet. No peak-memory figure or exact input-image configuration is provided. Do not promise a 4-bit speedup or transfer these numbers to M2/M4 machines. Independent bounded research found no exact 4-bit 24 GB benchmark.
https://huggingface.co/ckurasek/Yume-1.5-5B-720P-MLX/blob/main/README.md

## Later qualification direction, not executed

Investigate a validated lower-memory T5 path (BF16 with numerically stable operations, or a separately validated quantized encoder). Do not blindly replace float32 and assume quality parity. Precomputing text embeddings elsewhere would require an explicit supported input path; the inspected CLI does not expose one. After solving that bottleneck, try one idle 24 GB Mac at 832×480/49 frames, then 81 frames, then native 1280×704. Measure text encoding, denoising, VAE decoding, total time, MLX/process peak, system pressure/swap and output quality separately. Avoid concurrent generation. No qualification or runtime change is authorized by the research request.

Runtime declares Python>=3.11, MLX>=0.22.0 and other dependencies; this does not certify the assembled environment on every older macOS version. The model card declares Apache-2.0.
https://github.com/Blaizzy/mlx-video/blob/87db56a51758fefb748a359b90a5283bb8ba4837/pyproject.toml
