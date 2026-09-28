# Small MoE models (<~10 GB at 4-bit) for 16 GB Apple Silicon — evidence report

Date: **2026-09-28**. Scope: small MoE for writing (long-form prose + JSON) on **16 GB M1/M2/M4 Mac minis**.
Evidence labels: **[M]** measured (I read the primary artifact / summed the HF API), **[V]** vendor or publisher claim,
**[I]** inference. Where no number was found it says **not published** — nothing is estimated silently.
Every model is named with its quantization. On-disk sizes were computed by summing actual HF file sizes via
`https://huggingface.co/api/models/<repo>?blobs=true` on 2026-09-28.

## Answer in one line

The only models in your list that are genuinely **small-total / tiny-active and fit 8–16 GB at 4-bit** are
**LFM2.5-8B-A1B 4-bit (4.85 GB MLX / 5.16 GB Q4_K_M GGUF)** and **Granite 4.0 H-Tiny 4-bit (3.92 GB MLX /
4.23 GB Q4_K_M GGUF)**. The genuinely new 2026 in-scope entrant I found is
**NVIDIA Nemotron Labs 3 Elastic 12B-A2B (12B total / 2.0B active, 6.63 GB MXFP4 MLX / 9.64 GB Q4_K_M GGUF)**.
Everything else on your list is either too big (LFM2-24B-A2B, Granite H-Small, Qwen3-30B-A3B, Phi-3.5-MoE)
or dense (Qwen3-4B, Granite 4.2). **No base-M1/M2/M4 throughput measurement exists for any candidate** —
all published Apple Silicon numbers are Max/Ultra-class.

## Table 1 — parameters, on-disk size, tier, builds

| Model (exact quantization) | Total / active | On-disk | 8 GB | 16 GB | MLX repo id | GGUF repo id |
|---|---|---:|---|---|---|---|
| `mlx-community/granite-4.0-h-tiny-3bit-MLX` | 6.94B / 1B [V] | **3.05 GB** [M] | yes | yes | same | `ibm-granite/granite-4.0-h-tiny-GGUF` Q3_K_L 3.60 GB |
| `lmstudio-community/granite-4.2-3b-MLX-4bit` (dense) | 3.66B dense [M] | **2.07 GB** [M] | yes | yes | same | `ibm-granite/granite-4.2-3b-GGUF` |
| `mlx-community/Qwen3-4B-Instruct-2507-4bit` (dense) | 4.02B dense [M] | **2.28 GB** [M] | yes | yes | same | `unsloth/Qwen3-4B-Instruct-2507-GGUF` |
| `mlx-community/granite-4.0-h-tiny-4bit` | 6.94B / 1B [V] | **3.92 GB** [M] | yes | yes | same | `ibm-granite/granite-4.0-h-tiny-GGUF` Q4_K_M 4.23 GB |
| `Edge0/Edge0-8B-A1B-preview` **4-bit MLX** | 7.92B / ~1B [V] | **4.51 GB** [M] | tight | yes | same (library_name: mlx) | community only (`Proairos/…-GGUF`) |
| `bartowski/OLMoE-1B-7B-0924-Instruct-GGUF` **Q4_K_M** | 6.92B / 1B [V] | **4.21 GB** [M] | yes | yes | **none found** | same |
| `LiquidAI/LFM2.5-8B-A1B-MLX-4bit` | 8.47B / 1.5B [V] | **4.85 GB** [M] | tight | yes | same; `mlx-community/LFM2.5-8B-A1B-MLX-4bit` 4.78 GB | `LiquidAI/LFM2.5-8B-A1B-GGUF` Q4_K_M 5.16 GB |
| `LiquidAI/LFM2-8B-A1B` **4-bit MLX** | 8.34B / 1.5B [V] | **5.22 GB** [M] | no | yes | `mlx-community/LFM2-8B-A1B-4bit` | `LiquidAI/LFM2-8B-A1B-GGUF` Q4_K_M 5.04 GB |
| `unsloth/LFM2.5-8B-A1B-GGUF` **MXFP4_MOE** | 8.47B / 1.5B [V] | 5.35 GB [M] | no | yes | — | same |
| `nightmedia/NVIDIA-Nemotron-Labs-3-Elastic-12B-A2B-mxfp4-mlx` | 12.43B / 2.0B [V] | **6.63 GB** [M] | no | yes | same (also `-mxfp8-mlx`) | `mradermacher/…-GGUF` Q4_K_M 9.64 / Q4_K_S 8.67 GB |
| `lmstudio-community/granite-4.2-8b-MLX-4bit` (dense) | 8.79B dense [M] | **4.95 GB** [M] | no | yes | same | `ibm-granite/granite-4.2-8b-GGUF` |
| `LiquidAI/LFM2-24B-A2B-MLX-4bit` | 23.84B / 2.3B [V] | 13.42 GB [M] | no | **no** (24/32 GB) | same; `lmstudio-community/…` | `LiquidAI/LFM2-24B-A2B-GGUF` Q4_K_M 14.42 GB |
| `mlx-community/Qwen3-30B-A3B-4bit` | 30.53B / 3.3B [V] | 17.19 GB [M] | no | no | same | `Qwen/Qwen3-30B-A3B-GGUF` Q4_K_M 18.56 GB |
| `mlx-community/granite-4.0-h-small-4bit` | 32.21B / 9B [V] | 18.14 GB [M] | no | no | same | `ibm-granite/granite-4.0-h-small-GGUF` Q4_K_M 19.48 GB |
| `mlx-community/Phi-3.5-MoE-instruct-4bit` | 41.87B / 6.6B [V] | 23.56 GB [M] | no | no | same | `bartowski/Phi-3.5-MoE-instruct-GGUF` Q4_K_M 25.35 GB |

"8 GB yes" means weights leave room for macOS + KV at short context; that is a fit judgement, not a measured run.
`LFM2-24B-A2B` at 13.42 GB cannot share 16 GB with macOS + KV comfortably, and Liquid positions it for 32 GB [V].

## Table 2 — quality, thinking trace, measured Apple Silicon throughput

| Model | Long-form prose / creative | Structured JSON & instruction following | Thinking trace | Measured Apple Silicon tok/s (whose) |
|---|---|---|---|---|
| LFM2.5-8B-A1B 4-bit | **not published** (no CW score on the LFM2.5 card) | IFEval **91.84**, IFBench **56.47**, Multi-IF **79.93**, BFCLv3 **64.79**, BFCLv4 49.73 [V]; **not on SOB** | **Yes** — explicit `<think>`; shipped `chat_template.jinja` has **no** disable flag (only `preserve_thinking` for history) [M template, V card] | M1 Max 32c **bf16**: 69.5 @1k → 66.9 @8k → 59.2 @32k → 37.6 @128k [M, oMLX]. M4 Max **FP16 GGUF**, llama.cpp+Metal: 87–93 baseline, 90–129 with DSpark (+18%) [M, Liquid AI]. **Base M1/M2/M4: not published** |
| LFM2-8B-A1B 4-bit | Creative Writing v3 **44.22** [V] | IFEval 77.58, IFBench 25.85, Multi-IF 58.19 [V] | Card describes instruct/fine-tune role; no thinking trace documented [V/I] | not published |
| LFM2-24B-A2B 4-bit | not published | Card: "general-purpose instruct model (without reasoning traces)" [V] | **No** [V] | not published |
| Granite 4.0 H-Tiny 4-bit | Creative Writing v3 **32.60** (weak) [V] | IFEval **82.23**, IFBench 21.28, Multi-IF 59.00, BFCLv3 56.89 [V]; **SOB: `IBM-Granite-4.0` overall 83.2%, JSON Pass 98.3%, Perfect 38.1%** [M] | **No** — Granite 4.0 is non-reasoning; 4.2 introduced native thinking [V/I] | M5 Max 40c **8-bit**: 169.6 @1k → 144.4 @8k → 88.2 @128k [M, oMLX]. 4-bit, mlx-lm pp512/tg128: M1 Ultra 117.2, M5 Max 235.4 [M, mlxcel sweep]. **Base M1/M2/M4: not published** |
| Granite 4.0 H-Small | — | — | No | not published (too big) |
| Qwen3-30B-A3B 4-bit | CW v3 68.1 (Qwen's own card, non-thinking) [V] | **SOB: Qwen3-30B overall 84.2%, JSON Pass 98.3%** [M] | base Qwen3 has hybrid thinking; `-Instruct-2507` is non-thinking [V] | not published (too big) |
| Qwen3-4B-Instruct-2507 4-bit (dense) | **conflicting**: CW v3 **51.71** [V, Liquid table] vs **83.5** [V, Qwen card]; WritingBench **83.4** [V, Qwen card] | IFEval 85.62 [V Liquid] / 83.4 [V Qwen], IFBench 30.28, Multi-IF 75.54 [V] | **No** — "supports only non-thinking mode and does not generate `<think>` blocks" [V] | not published |
| OLMoE-1B-7B-0924 / Instruct | **none found** | MMLU ~54; **no JSON benchmark found** | **No** | not published |
| Phi-3.5-MoE-instruct 4-bit | **none found** | MMLU 78.9, GSM8K 88.7, long-context Qasper 40.0 [V]; no JSON benchmark found | **No** | not published (too big) |
| Nemotron Labs 3 Elastic 12B-A2B | **none found** for the 12B | 12B: **none found**; parent `Nemotron-3-Nano-30B` **SOB overall 84.1%, JSON Pass 98.7%** [M] | **Yes** — reasoning LLM; `enable_thinking=False` supported [V] | not published (only H100 vLLM: 12B 224 tok/s, 2.4× the 30B) [V] |
| Granite 4.2 3B/8B dense 4-bit | no creative-writing score; 8B IFBench **79.33**, Arena-Hard-V2 65.19, RULER 128K 71.41 [V] | `enable_thinking=False` / low-effort modes [V] | **Yes** — native `<think>` [V] | not published |
| Edge0-8B-A1B-preview 4-bit | none found | none found | not published | card claims "1 GiB active memory · 25 tok/s · 4-bit" [V, phone-class target] |

## Notes on each item in your list

- **LFM2.5-8B-A1B** — exists, and its 4-bit build is the best-documented small MoE. **8.47B total, 1.5B active** [V].
  Dense-equivalent read per token at 4-bit is ~0.9 GB vs ~5–6 GB for a 9B dense [I] — the speed thesis is sound.
  **Prose is the open risk**: no CW/long-form score is published for 2.5 (the 44.22 figure is the *older* LFM2-8B-A1B).
  **More important for your JSON concern: it is a thinking model and the shipped template exposes no disable switch**
  [M, template read]. That is a real problem for `JSON.parse` call sites.
- **Other LFM2.5 MoE variants** — I found **none**. LFM2.5-8B-A1B is the only MoE in the LFM2.5 family
  (`LFM2.5-230M/350M/1.2B/2.6B/VL-3B` are dense). Confirmed by the full LiquidAI LFM2.5 repo listing [M].
- **LFM2-8B-A1B** — exists (8.34B / 1.5B). MLX 4-bit at `mlx-community/LFM2-8B-A1B-4bit` 5.22 GB; GGUF Q4_K_M 5.04 GB.
  Its only prose data point is a weak CW v3 44.22 [V]. Use only if you need the non-2.5 generation.
- **LFM2-24B-A2B** — exists (23.84B / 2.3B per card table; the card also says "2B active"). MLX 4-bit **13.42 GB**,
  GGUF Q4_K_M 14.42 GB. **Does not fit 16 GB with OS+KV** — treat as 24/32 GB. No thinking trace [V].
- **IBM Granite 4.0 H-Tiny** — exists (6.94B / 1B). Best small-MoE **JSON** pedigree (SOB JSON Pass 98.3% for
  the Granite-4.0 label) but **weak prose** (CW v3 32.60) and non-reasoning, so no thinking-trace problem.
- **IBM Granite 4.0 H-Small** — exists but is **32.21B total / 9B active**; 4-bit MLX 18.14 GB, Q4_K_M 19.48 GB.
  **Too big for 16 GB.** (Also note `granite-4.0-h-micro` is 3.19B *dense hybrid*, not MoE.)
- **Qwen3-30B-A3B smaller siblings** — **none exist**. 30B-A3B is the smallest Qwen3-family MoE; the newer
  Qwen3.5/3.6 MoE tiers are all **35B-A3B** (larger), and Qwen3's other MoE is 235B-A22B. [M, HF search]
- **Qwen3-4B** (dense, for comparison) — `Qwen3-4B-Instruct-2507` is **non-thinking only** ("does not generate
  `<think>` blocks") [V], 4.02B dense, 4-bit MLX **2.28 GB** — the best JSON-safe small option and a surprisingly
  strong writing signal (Qwen claims CW v3 83.5 / WritingBench 83.4; Liquid's table says CW v3 51.71 — the two
  disagree, so treat prose as unverified). Dense 4B reads all ~2.3 GB/token, so it will be slower than a 1–1.5B-active MoE.
- **AllenAI OLMoE-1B-7B** — exists (6.92B / 1B, Sept 2024). **No 4-bit MLX build found** (the community repo
  returned HTTP 401/does not exist); GGUF Q4_K_M exists (4.21 GB). **4,096-token context** [M config] rules it out
  for long-form writing. No creative/JSON benchmark found.
- **Microsoft Phi-3.5-MoE-instruct** — exists but **41.87B total / 6.6B active**; 4-bit MLX 23.56 GB, Q4_K_M 25.35 GB.
  **Far too big for 16 GB.** Listed only to close the item.
- **Other 2026 MoE ≤~15B total / ≤~2B active with MLX or GGUF**:
  - **NVIDIA Nemotron Labs 3 Elastic 12B-A2B** — the standout. The official `nvidia/NVIDIA-Nemotron-Labs-3-Elastic-30B-A3B`
    is a **3-in-1 elastic checkpoint** (30B / 23B / 12B nested); the **12B variant is 12B total / 2.0B active** and is
    extracted zero-shot with NVIDIA's slicing script [V, official card, ICML 2026 paper]. Community MLX (`nightmedia`
    mxfp4 6.63 GB) and GGUF (Q4_K_M 9.64 GB) exist, but both derive from `DavidAU/NVIDIA-Nemotron-Labs-3-Elastic-12B-A2B`
    rather than a standalone official 12B repo [M, base_model tag]. It is a **reasoning model with `enable_thinking=False`**,
    so it is JSON-viable *if* thinking is actually disabled. No Apple Silicon throughput published.
  - `Edge0/Edge0-8B-A1B-preview` 4-bit MLX (7.92B total, ~1B active claim, 4.51 GB, 2026-09-17, base `inclusionAI/Ling-3.0-tiny-base`).
    A **preview** with a marketing-style card; its `25 tok/s` is a phone-class vendor claim [V]. Treat as unverified.
  - **Community upcycles, not official releases** (do not cite as vendor models): `blascotobasco/LFM2-16B-A2B`
    (16.09B total, top-k raised to 8 → ~2B active, built from LFM2-8B-A1B) and `mradermacher`/`DavidAU` **LFM2-12B-A1B**
    and **Qwen3-15B-A2B-Base** (base_model `TroyDoesAI/Qwen3-15B-A2B-Base`). None is an official Liquid/Qwen release.
  - `moondream/moondream3.1-9B-A2B` (9.27B / 2B) — a **vision-language** model; **no MLX or GGUF build found**
    (only 2 repos on HF), so it does not meet the "has MLX or GGUF" test for this workload.
  - **Granite 4.2 (3B/8B/30B, 2026-09-04)** is **dense** (`granite` / `GraniteForCausalLM`), **not MoE** [M config],
    so it is out of the MoE scope but relevant as a 2026 dense alternative with controllable thinking.

## Practical implications for the 16 GB writing box

1. **Speed (MoE) and JSON safety pull apart.** The fastest small MoE (LFM2.5-8B-A1B) is a thinking model with no
   documented off-switch; the JSON-proven small model (Granite 4.0 H-Tiny) is weak at prose. Grammar-constrained
   decoding (GGUF + JSON-schema/GBNF in llama.cpp) is the only mechanism shown to make JSON reliable independent of
   the model — the plain MLX path has no grammar. Both candidates have real GGUFs [M].
2. **Measured Apple Silicon data is all Max/Ultra-class.** The only base-adjacent numbers I could find are none;
   every published figure (oMLX M1 Max / M5 Max, Liquid M4 Max) is a higher-bandwidth part. **Do not transfer them
   to base M1/M2/M4.** A base-M4 4-bit run must be measured locally.
3. **The 2026 newcomer worth a look is Nemotron Labs 3 Elastic 12B-A2B** (12B/2.0B, thinking can be disabled,
   6.63 GB MXFP4 MLX) — it is the only in-scope 2026 MoE I verified with both MLX and GGUF, but its MLX/GGUF are
   community quants of an extracted elastic submodel, and it has no published writing/JSON quality.
4. **Qwen3-4B-Instruct-2507 4-bit (2.28 GB, non-thinking, dense)** is the safest "small and JSON-clean" control to
   benchmark against the MoEs; its prose quality is disputed between two vendor tables.

## Sources (full URLs)

**Model cards / HF repos (sizes via `?blobs=true`, 2026-09-28):**
- https://huggingface.co/LiquidAI/LFM2.5-8B-A1B
- https://huggingface.co/LiquidAI/LFM2.5-8B-A1B-MLX-4bit
- https://huggingface.co/LiquidAI/LFM2.5-8B-A1B-GGUF
- https://huggingface.co/unsloth/LFM2.5-8B-A1B-GGUF
- https://huggingface.co/mlx-community/LFM2.5-8B-A1B-MLX-4bit
- https://huggingface.co/LiquidAI/LFM2-8B-A1B
- https://huggingface.co/mlx-community/LFM2-8B-A1B-4bit
- https://huggingface.co/LiquidAI/LFM2-8B-A1B-GGUF
- https://huggingface.co/LiquidAI/LFM2-24B-A2B
- https://huggingface.co/LiquidAI/LFM2-24B-A2B-MLX-4bit
- https://huggingface.co/LiquidAI/LFM2-24B-A2B-GGUF
- https://huggingface.co/ibm-granite/granite-4.0-h-tiny
- https://huggingface.co/ibm-granite/granite-4.0-h-small
- https://huggingface.co/mlx-community/granite-4.0-h-tiny-4bit
- https://huggingface.co/mlx-community/granite-4.0-h-tiny-3bit-MLX
- https://huggingface.co/ibm-granite/granite-4.0-h-tiny-GGUF
- https://huggingface.co/mlx-community/granite-4.0-h-small-4bit
- https://huggingface.co/ibm-granite/granite-4.0-h-small-GGUF
- https://huggingface.co/Qwen/Qwen3-30B-A3B
- https://huggingface.co/Qwen/Qwen3-30B-A3B-GGUF
- https://huggingface.co/mlx-community/Qwen3-30B-A3B-4bit
- https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507
- https://huggingface.co/mlx-community/Qwen3-4B-Instruct-2507-4bit
- https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF
- https://huggingface.co/allenai/OLMoE-1B-7B-0924
- https://huggingface.co/allenai/OLMoE-1B-7B-0924-Instruct
- https://huggingface.co/bartowski/OLMoE-1B-7B-0924-Instruct-GGUF
- https://huggingface.co/microsoft/Phi-3.5-MoE-instruct
- https://huggingface.co/mlx-community/Phi-3.5-MoE-instruct-4bit
- https://huggingface.co/bartowski/Phi-3.5-MoE-instruct-GGUF
- https://huggingface.co/nvidia/NVIDIA-Nemotron-Labs-3-Elastic-30B-A3B-BF16
- https://huggingface.co/nightmedia/NVIDIA-Nemotron-Labs-3-Elastic-12B-A2B-mxfp4-mlx
- https://huggingface.co/mradermacher/NVIDIA-Nemotron-Labs-3-Elastic-12B-A2B-GGUF
- https://huggingface.co/ibm-granite/granite-4.2-8b
- https://huggingface.co/lmstudio-community/granite-4.2-8b-MLX-4bit
- https://huggingface.co/ibm-granite/granite-4.2-3b
- https://huggingface.co/lmstudio-community/granite-4.2-3b-MLX-4bit
- https://huggingface.co/Edge0/Edge0-8B-A1B-preview
- https://huggingface.co/moondream/moondream3.1-9B-A2B
- https://huggingface.co/blascotobasco/LFM2-16B-A2B

**Measured throughput:**
- oMLX, LFM2.5-8B-A1B bf16 on M1 Max 32c/64 GB — https://omlx.ai/benchmarks/performance/utijdwgn
- oMLX, granite-4.0-h-tiny 8-bit on M5 Max 40c/128 GB — https://omlx.ai/benchmarks/performance/qmm3nxcx
- Liquid AI DSpark blog (LFM2.5-8B-A1B + DSpark on M4 Max, llama.cpp Metal, FP16) — https://www.liquid.ai/blog/lfm2.5-dspark
- mlxcel issue #1685 (granite-4.0-h-tiny-4bit mlx-lm on M1 Ultra / M5 Max) — https://github.com/lablup/mlxcel/issues/1685

**Quality / JSON:**
- Interfaze Structured Output Benchmark leaderboard — https://interfaze.ai/leaderboards/structured-output-benchmark
- SOB paper — https://arxiv.org/abs/2604.25359
- Liquid docs, chat template — https://docs.liquid.ai/lfm/key-concepts/chat-template (template file itself:
  https://huggingface.co/LiquidAI/LFM2.5-8B-A1B/raw/main/chat_template.jinja)
- NVIDIA Star Elastic paper reference (ICML 2026) — via the official card above

**Engine support (inspected 2026-09-28):**
- mlx-lm model registry (contains `lfm2_moe.py`, `granitemoehybrid.py`, `olmoe.py`, `phimoe.py`, `qwen3_moe.py`,
  `nemotron_h.py`, `qwen3.py`) — https://github.com/ml-explore/mlx-lm/tree/main/mlx_lm/models

## Verification status

**[M]** existence, parameter totals and all on-disk sizes (HF API sum, shas recorded in the API responses);
[M] mlx-lm architecture support; [M] LFM2.5 chat template lacks a thinking-disable flag; [M] oMLX and Liquid
throughput figures and the mlxcel sweep; [M] SOB scores.
**[V]** vendor parameter counts, active-parameter counts, benchmark scores and thinking-mode claims.
**[I]** the "reads ~0.9 GB/token" active-bytes reasoning; the fit judgements in Table 1.
**Not verified / not found:** any base-M1/M2/M4 throughput for any candidate; a creative-writing score for
LFM2.5-8B-A1B; MLX 4-bit for OLMoE; any MLX/GGUF for `moondream3.1-9B-A2B`; an official standalone HF repo for
the Nemotron Elastic 12B submodel.
