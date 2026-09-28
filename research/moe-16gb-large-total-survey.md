# Large-total MoE survey for 16 GB Apple Silicon — measured sizes & speeds

Target: Mac mini, 16 GB unified memory (M1 / M2 / M4). Weight budget ≈ 11.5 GB.
Evidence labels: **[M]** measured (I read a primary artifact — HF blob API, benchmark page, issue);
**[V]** vendor/publisher claim (model card); **[I]** inference (mine, including fit calls and bandwidth math).
Every number carries a source URL in SOURCES.

## Correction to three premises in the brief (read first)

1. **`gpt-oss-20b` native MXFP4 does not fit 11.5 GB.** The publisher checkpoint is **13.761 GB**
   of safetensors [M]; the official GGUF is **12.110 GB** [M]. Only a *re-quant* (MLX MXFP4-Q4,
   11.179 GB) or a 2-bit GGUF (11.468 GB) fits, and both are marginal.
2. **`NVIDIA-Nemotron-Nano-v2` is NOT MoE.** Its `config.json` has no expert fields; it is a dense
   hybrid Mamba-Transformer (`nemotron_h`, 9B/12B) — **all** parameters are active, so the
   "~0.6–1 B active" premise is wrong [M from config]. The real small-active Nemotron MoE is
   **`Nemotron-3-Nano-30B-A3B`** (3.5 B active) or the 2026 **`Nemotron-Labs-3-Elastic-12B-A2B`** (2.0 B active).
3. **`tencent/Hunyuan-A13B` is 80 B total / 13 B active**, not ~13 B total. Its 4-bit MLX is
   **45.222 GB** [M]; the *smallest* GGUF (IQ1_M) is **17.755 GB** [M] → does not fit. `gemma-3-27b`
   is likewise the dense 27B (4-bit MLX **16.834 GB** [M]) → out.

## Fitting candidates (on-disk ≤ ~11.5 GB) — comparison table

| Exact model id | Quant quoted | Total / **active** params | On-disk | ≤11.5 GB? | MLX repo | GGUF repo | Measured tok/s (chip, ctx, who) | Thinking? |
|---|---|---|---|---|---|---|---|---|
| `nightmedia/NVIDIA-Nemotron-Labs-3-Elastic-12B-A2B-mxfp4-mlx` | **mxfp4 MLX** | 12 B / **2.0 B** [V] | **6.611 GB** [M] | ✅ comfortable | yes (same) | `mradermacher/…-12B-A2B-GGUF` Q4_K_M 9.642 GB | **87.5 tok/s** @1k, 83.5 @4k — **M4 (10c) 16 GB**, oMLX contributor [M] | reasoning; Elastic/Nemotron card | 
| `mlx-community/gpt-oss-20b-MXFP4-Q4` | **MXFP4 4-bit MLX** | 21 B / **3.6 B** [V] | **11.179 GB** [M] | ⚠️ marginal | yes (same) | `ggml-org/gpt-oss-20b-GGUF` | 41.9 @1k, 41.4 @4k — M4 (10c) **32 GB**, oMLX [M] | yes; low/med/high [V] |
| `unsloth/gemma-4-26B-A4B-it-GGUF` | **UD-IQ3_S GGUF** | 25.2 B / **3.8 B** [V] | 11.290 GB [M] | ✅ (just) | `mlx-community/gemma-4-26B-A4B-it-heretic-msq-2.6bit` 10.145 GB | yes (same) | unsloth **3-bit** on **M4 16 GB: 42.1 @1k / 40.2 @4k** [M] | yes, `enable_thinking=False` [V] |
| `unsloth/Qwen3-30B-A3B-GGUF` | **UD-IQ2_M GGUF** | 30.5 B / **3.3 B** [V] | 10.866 GB [M] | ✅ | ❌ no MLX ≤11.5 (3-bit = 13.358 GB) | yes (same) | 4-bit only: 43.3 @1k — M3 Pro 18c 32 GB [M] | yes, `enable_thinking` [V] |
| `unsloth/ERNIE-4.5-21B-A3B-PT-GGUF` | **Q3_K_M GGUF** | 21 B / **3 B** [V] | 10.525 GB [M] | ✅ | ❌ MLX 4-bit = 12.277 GB [M] | yes (same) | 4-bit: 66.6 @1k / 57.4 @4k — M2 Pro 19c 32 GB [M] | Thinking variant is thinking-only [V] |
| `JANGQ-AI/Nemotron-Cascade-2-30B-A3B-JANG_2L` | **JANG mixed ~2-bit MLX** | 30 B / **3 B** [V] | 11.035 GB [M] | ✅ | JANG format, **not** stock mlx-lm | `bartowski/…-GGUF` (sizes suspect) | 4-bit JANG-less: 63.9 @1k — M1 Max 24c 64 GB [M] | thinking / instruct via `<think></think>` [V] |
| `unsloth/Qwen3.6-35B-A3B-GGUF` | **UD-IQ2_XXS GGUF** | 35 B / **3 B** [V] | 10.757 GB [M] | ✅ | ❌ MLX 4-bit = 20.402 GB [M] | yes (same) | not published on M1/M2/M4 base | yes, disableable [V] |
| `unsloth/Qwen3.5-35B-A3B-GGUF` | **UD-IQ2_M GGUF** | 35 B / **3 B** [V] | 11.392 GB [M] | ⚠️ marginal | ❌ MLX 4-bit = 20.392 GB [M] | yes (same) | 4-bit: 95.8 @1k / 92.0 @4k — M4 Pro 20c 48 GB [M] | yes, thinking default [V] |
| `bartowski/nvidia_NVIDIA-Nemotron-Nano-12B-v2-GGUF` | **Q4_K_M GGUF** | 12 B / **12 B (dense)** [M config] | 7.494 GB [M] | ✅ | n/a | yes (same) | not published on M1/M2/M4 base | `/no_think`, thinking budget [V] |

**Fit calls are [I]**, based on the measured blob sizes above vs the ~11.5 GB budget.

### Models in the brief that do NOT fit at any sensible quant
- `openai/gpt-oss-20b` native MXFP4 — **13.761 GB** [M]; GGUF MXFP4 12.110 GB [M].
- `Qwen/Qwen3-30B-A3B` **MLX** — 4-bit 17.175 GB, 3-bit 13.358 GB; **no MLX 2-bit exists** [M].
- `google/gemma-4-26B-A4B-it` **4-bit** — MLX QAT **15.609 GB**, QAT `q4_0` GGUF **14.439 GB** [M]. (Only ≤3-bit fits.)
- `google/gemma-3-27b` (dense) — 4-bit MLX **16.834 GB** [M], and slow.
- `tencent/Hunyuan-A13B` — 80 B/13 B; MLX 4-bit **45.222 GB**, min GGUF 17.755 GB [M].
- `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` — MLX 4-bit **17.775 GB**; lmstudio GGUF Q3_K_L **20.749 GB** [M]. Not found ≤11.5 GB.
- `nvidia/NVIDIA-Nemotron-Nano-12B-v2` — exists and Q4_K_M fits (7.494 GB) but it is **dense**, not MoE.

### Other 2026 MoEs in scope (15–40 B total, <5 B active)
- `NVIDIA-Nemotron-Labs-3-Elastic-30B-A3B` mxfp4 MLX = **16.789 GB** ✗; 23B-A2.8B Q4_K_S = 14.825 GB ✗ [M].
- `ornith-ai/Ornith-1.5-35B-A3B` — smallest build Q4_K_M **21.713 GB** ✗ [M].
- Pruned 4-bit MLX: `gemma-4-26B-A4B-it-OptiQ-4bit-REAP-14B` **11.205 GB** ✅ (includes 1.15 GB vision tower) [M];
  `Qwen3.5-35B-OptiQ-4bit-REAP-19B` 13.737 GB and `Qwen3.6-35B-OptiQ-4bit-REAP-19B` 14.865 GB ✗ [M].
- `barozp/Qwen3.8-Whittle-MoE-27B-MLX-4bit` = 15.156 GB ✗ [M].
- Not verified as existing / not found: no `google/gemma-4-31B-A…` MoE ≤11.5 GB; no small-active Hunyuan build; no
  `gemma-4-26B` ≤2-bit MLX at a maintainer repo (only the 2.6-bit heretic fork).

## Notes

- **Bandwidth math [I]**: read bytes/token ≈ active params × bits/weight. Elastic-12B mxfp4 (2.0 B × ~4.25 b)
  ≈ 1.1 GB; Qwen3-30B-A3B Q2_K (3.3 B × ~2.6 b) ≈ 1.1 GB; ERNIE Q3_K_M (3 B × ~3.4 b) ≈ 1.3 GB;
  gpt-oss MXFP4 (3.6 B × ~4.25 b) ≈ 1.9 GB. The **measured** 87.5 vs ~42 tok/s on the *same M4 16 GB*
  is consistent with 2.0 B active beating 3.8 B active, and with low-bit MoE experts being pushed to CPU.
- **gpt-oss on 16 GB is a CPU-offload trick, not a clean fit.** The one measured 16 GB run
  (`ggml-org/gpt-oss-20b-GGUF`, `--n-cpu-moe 12 -fa on -c 32768 --no-mmap`) reached **~26 tok/s** with
  13.05/16 GB RAM and 0.04 GB swap [M, user "mingodad", llama.cpp #15396]. The MLX MXFP4-Q8 (12.076 GB)
  **OOM'd** on the same class of machine [M, mlx-lm #644].
- **No M4-base-16 GB measurement exists** for Qwen3-30B-A3B at any quant; its published Mac numbers are
  4-bit on larger machines (M3 Pro 43.3, M5 Max 113.4, M4 Max 123.9 tok/s) [M]. A 2-bit fit is therefore
  a fit on paper, speed unmeasured on target.
- **ERNIE MLX 4-bit (12.277 GB) is just over budget**; the only ≤11.5 GB route is GGUF at ≤3-bit,
  which llama.cpp runs, and whose M2 Pro 4-bit speed (66.6 tok/s) suggests a fast 3 B-active model.
- **Thinking control**: gpt-oss (`Reasoning: low/medium/high`), Qwen3 (`enable_thinking`, with
  Instruct-2507 = non-thinking-only and Thinking-2507 = thinking-only), Gemma-4 (`enable_thinking=False`),
  Nemotron-3/Elastic (`enable_thinking=False`), Nemotron-Nano (`/no_think`), Cascade-2 (`<think></think>`).
- **Third-party estimates, not measurements** (labelled [I]): llmrun.dev predicts ERNIE Q4_K_M ≈ 36 tok/s
  on a Mac mini M4 16 GB from a bandwidth formula — no artifact measurement behind it. willitrun.ai
  likewise only models feasibility. Do not quote these as measured.
- **Format caveat**: `JANGQ-AI/…-JANG_2L` and the `-CRACK` mirror are MLX-family JANG quants that
  **lmstudio, Ollama and oMLX do not load**; they need MLX Studio or `jang[mlx]` [V, repo card].
- **Size-reporting anomaly**: several `bartowski/nvidia_Nemotron-3-…` and `…Nemotron-Cascade-2-…` GGUF
  files report ≈18 GB for *every* quant including IQ2_XXS, which is inconsistent with the model size;
  I relied on `mlx-community` and `JANGQ-AI` sizes for those models instead [M, flagged].

## Bottom line for a 16 GB M4 fleet

1. **Fastest verified fit: `NVIDIA-Nemotron-Labs-3-Elastic-12B-A2B` mxfp4 MLX (6.61 GB) — 87.5 tok/s on M4 10c 16 GB.** [M]
2. **`gemma-4-26B-A4B-it` 3-bit GGUF/2.6-bit MLX (~10–11.3 GB) — 42.1 tok/s on M4 16 GB**, better quality headroom than 2-bit. [M]
3. **`gpt-oss-20b` MXFP4-Q4 MLX (11.18 GB)** is the only sub-11.5 GB gpt-oss, but 16 GB behaviour is unmeasured and the publisher/GUF builds need CPU offload (~26 tok/s). [M]
4. `Qwen3-30B-A3B` and `ERNIE-4.5-21B-A3B` fit **only as ≤3-bit GGUF** (~10.5–10.9 GB); no target-chip measurement exists; treat speed as unknown.

## SOURCES

- HF blob API (sizes): https://huggingface.co/api/models/openai/gpt-oss-20b?blobs=true
- https://huggingface.co/api/models/mlx-community/gpt-oss-20b-MXFP4-Q4?blobs=true
- https://huggingface.co/ggml-org/gpt-oss-20b-GGUF (12.110 GB MXFP4)
- https://huggingface.co/api/models/bartowski/Qwen_Qwen3-30B-A3B-GGUF?blobs=true
- https://huggingface.co/unsloth/Qwen3-30B-A3B-GGUF
- https://huggingface.co/api/models/google/gemma-4-26B-A4B-it?blobs=true
- https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF
- https://huggingface.co/google/gemma-4-26B-A4B-it-qat-q4_0-gguf
- https://huggingface.co/unsloth/ERNIE-4.5-21B-A3B-PT-GGUF
- https://huggingface.co/bartowski/baidu_ERNIE-4.5-21B-A3B-PT-GGUF
- https://huggingface.co/tencent/Hunyuan-A13B-Instruct-GGUF
- https://huggingface.co/mlx-community/Hunyuan-A13B-Instruct-4bit
- https://huggingface.co/api/models/nvidia/NVIDIA-Nemotron-Nano-12B-v2?blobs=true
- https://huggingface.co/bartowski/nvidia_NVIDIA-Nemotron-Nano-12B-v2-GGUF
- https://huggingface.co/api/models/nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16?blobs=true
- https://huggingface.co/lmstudio-community/NVIDIA-Nemotron-3-Nano-30B-A3B-GGUF
- https://huggingface.co/nvidia/NVIDIA-Nemotron-Labs-3-Elastic-30B-A3B-BF16 (nested 30B/23B/12B table)
- https://huggingface.co/nightmedia/NVIDIA-Nemotron-Labs-3-Elastic-12B-A2B-mxfp4-mlx
- https://huggingface.co/mradermacher/NVIDIA-Nemotron-Labs-3-Elastic-12B-A2B-GGUF
- https://huggingface.co/nvidia/Nemotron-Cascade-2-30B-A3B
- https://huggingface.co/JANGQ-AI/Nemotron-Cascade-2-30B-A3B-JANG_2L
- https://huggingface.co/mlx-community/Nemotron-Cascade-2-30B-A3B-4bit
- https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF
- https://huggingface.co/unsloth/Qwen3.5-35B-A3B-GGUF
- https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B-GGUF
- https://huggingface.co/mlx-community/gemma-4-26B-A4B-it-OptiQ-4bit-REAP-14B
- Model cards (params/reasoning): https://huggingface.co/openai/gpt-oss-20b · https://huggingface.co/Qwen/Qwen3-30B-A3B · https://huggingface.co/google/gemma-4-26B-A4B-it · https://huggingface.co/baidu/ERNIE-4.5-21B-A3B-PT · https://huggingface.co/nvidia/NVIDIA-Nemotron-Nano-12B-v2
- Measured speed, M4 16 GB: oMLX community benchmark pages
  https://omlx.ai/benchmarks/performance/9egz23cu (Elastic-12B mxfp4, M4 16 GB, 1k, 87.5)
  https://omlx.ai/benchmarks/performance/9aftoe1j (Elastic-12B mxfp4, M4 16 GB, 4k, 83.5)
  https://omlx.ai/benchmarks/performance/6wppewb1 (gemma-4 unslothoQ3, M4 16 GB, 1k, 42.1)
  https://omlx.ai/benchmarks/performance/3et5t380 (gemma-4 unslothoQ3, M4 16 GB, 4k, 40.2)
- Measured speed, other chips: https://omlx.ai/benchmarks/performance/0h4kthyk (gpt-oss MXFP4-Q4, M4 32 GB, 41.9) ·
  https://omlx.ai/benchmarks/performance/9ln82dp0 (gemma-4 oQ3, M4 32 GB, 3-bit, 37.7) ·
  https://omlx.ai/benchmarks/performance/gdavibfg (Qwen3-30B-A3B 4-bit, M3 Pro, 43.3) ·
  https://omlx.ai/benchmarks/performance/8gxfx0ca (ERNIE 4-bit, M2 Pro, 66.6) ·
  https://omlx.ai/benchmarks/performance/dcuubm6v (Cascade-2 4-bit, M1 Max, 63.9)
- gpt-oss 16 GB measurement: https://github.com/ggml-org/llama.cpp/discussions/15396 and https://github.com/ml-explore/mlx-lm/issues/644
- llama.cpp MXFP4 on M2 Ultra (tg32 129.97): https://huggingface.co/OpenTransformer/llama.cpp-prismml/blob/570ff7712cc4a00224dce4d27af7249e019fa28a/benches/mac-m2-ultra/mac-m2-ultra.md
- vllm-mlx M4 Max (Qwen3-30B-A3B-4bit 123.9 tok/s, 16.05 GB): https://github.com/Thump604/vllm-mlx/blob/main/docs/benchmarks/llm.md
- SiliconBench (M4 Pro Mac mini, Qwen3.5-35B-A3B 8-bit, mlx_lm 49.3 TPS): https://arxiv.org/html/2609.19169v1 · https://huggingface.co/papers/2609.19169
- Third-party estimates (NOT measured): https://llmrun.dev/model/baidu-ernie-4-5-21b-a3b-pt · https://willitrunai.com/can-run/qwen-3-30b-a3b-on-m1-16gb
