# MoE LLM throughput on Apple Silicon — published-benchmark evidence (2026-09)

Scope: does Mixture-of-Experts actually beat dense on 16 GB M1 (2020), M2 (2023) and M4 (2024)
Mac minis, and what is the real tokens/second ceiling? This is an **evidence report**, not a
qualification. Every claim is labelled **[M]** measured (a published measurement that was read),
**[V]** vendor claim, or **[I]** inference / own calculation. Nothing computed here is presented as
a measurement.

Published bandwidth used for scaling checks: M1 ~68 GB/s, M2 ~100 GB/s, M4 ~120 GB/s
(=120 GB/s per [canitrun M4](https://canitrun.dev/gpus/m4/), which also prints 68 and 100 for
M1/M2). M3 base is 100 GB/s — the same as M2 — so M3 is not part of the target set.

**Read this before any table.** Two independent public benchmark sets disagree by ~2x on the *same
class of hardware* for the *same model class*:

- **oMLX community DB** ([listing](https://omlx.ai/benchmarks/performance)) — an MLX server with its
  own settings; internal cross-chip ratios match the bandwidth ratios almost exactly, and its dense
  Qwen3.5-9B 16 GB number is close to the owner's own baseline.
- **Manojb `macmini-16gb-bench-gguf-mlx`** ([SUMMARY](https://huggingface.co/Manojb/macmini-16gb-bench-gguf-mlx))
  — a controlled, reproducible harness on an **M4 Mac mini 16 GB**, `llama-server` + `mlx_lm.server`.
  Its LFM2-8B-A1B MLX 4-bit number (38.9 tok/s) is less than half oMLX's M4 number (86.9).

Neither is "wrong"; they are different engines, prompts, generation lengths and warm-up rules.
**Do not put them in one column.** Where both exist, both are shown.

---

## Task 1 — Published tokens/second for MoE models on Apple Silicon

### 1a. Base M1 / M2 / M4 Mac minis, 16 GB, 4-bit — the fleet-relevant numbers

Source for M1/M2/M4 base rows: **oMLX community benchmark DB**, filtered per chip via
`https://omlx.ai/benchmarks/performance?device=["M1"|"M2"|"M4",""]&model=<name>` [M]. Prompt is
oMLX's "Code (Python)" benchmark context; `ctx` is the context length at which PG (prefill) and TG
(decode) were measured. oMLX does **not** publish the generated-token count (a real gap).

| Model (quant) | Total / active | Chip (RAM) | Ctx | TG tok/s [M] | Prefill tok/s [M] |
| --- | --- | --- | ---: | ---: | ---: |
| LFM2-8B-A1B **4-bit** | 8.3B / 1.5B [V] | M1 8c (16 GB) | 1k | **48.2** | 425.9 |
| LFM2-8B-A1B **3-bit** | 8.3B / 1.5B | M1 8c (16 GB) | 1k | **53.3 / 53.1** | ~366 |
| LFM2.5-8B-A1B **4-bit** | 8.3B / 1.5B [V] | M1 8c (16 GB) | 1k | **51.1 / 50.4** | ~270 |
| LFM2.5-8B-A1B **MLX-4bit** | 8.3B / 1.5B | M1 8c (16 GB) | 1k | **46.5 / 44.8** | ~276 |
| Granite 4.0 H-Tiny **4-bit DWQ** | 7B / 1B [V] | M1 8c (16 GB) | 1k | **45.6** | 239.7 |
| Granite 4.0 H-Tiny **oQ4** | 7B / 1B | M1 8c (16 GB) | 1k | **35.2** | 318.7 |
| gpt-oss-20b **MXFP4-Q4** | 21B / 3.6B [V] | M1 8c (16 GB) | 1k | **23.3 / 22.8** | ~120 |
| gpt-oss-20b **MXFP4-Q4** | 21B / 3.6B | M1 7c (16 GB) | 1k | **22.0 / 21.9** | ~104 |
| Nemotron Labs 3 Elastic 12B-A2B **mxfp4** | 12B / 2B | M1 8c (16 GB) | 1k | **39.3** | 200.0 |
| Gemma 4 26B-A4B **heretic-msq-2.6bit** | 26B / ~4B | M1 8c (16 GB) | 1k | **15.4** | 108.0 |
| Gemma 4 26B-A4B **oQ2.5** | 26B / ~4B | M1 7c (16 GB) | 1k | **17.3** | 188.7 |
| Qwen3-30B-A3B **oQ2.5** | 30.5B / 3.3B | M1 7c (16 GB) | 1k | **20.5** | 210.2 |
| LFM2-8B-A1B **4-bit** | 8.3B / 1.5B | M2 10c (16 GB) | 1k / 4k | **73.8 / 65.7** | ~600 / ~509 |
| LFM2-8B-A1B **3-bit** | 8.3B / 1.5B | M2 10c (16 GB) | 1k / 4k | **77.1 / 69.0** | ~590 |
| LFM2.5-8B-A1B **4-bit** | 8.3B / 1.5B | M2 10c (16 GB) | 1k / 4k | **75.4 / 70.9** | ~362 / ~399 |
| LFM2.5-8B-A1B **4-bit** | 8.3B / 1.5B | M2 8c (16 GB) | 1k / 4k | **69.2 / 63.1** | ~296 / ~318 |
| Granite 4.0 H-Tiny **4-bit** | 7B / 1B | M2 10c (24 GB) | 1k / 4k | **62.3 / 61.1** | ~316 / ~359 |
| gpt-oss-20b **MXFP4-Q4** | 21B / 3.6B | M2 10c (16 GB) | 1k / 4k | **32.9 / 28.6–29.7** | ~158 / ~167 |
| gpt-oss-20b **MXFP4-Q4** | 21B / 3.6B | M2 8c (16 GB) | 1k | **28.5** | 131.2 |
| Nemotron Labs 3 Elastic 12B-A2B **mxfp4** | 12B / 2B | M2 10c (24 GB) | 1k | **56.2** | 276.1 |
| Gemma 4 26B-A4B **mxfp4** | 26B / ~4B | M2 10c (24 GB) | 1k | **28.5** | 176.2 |
| Qwen3-30B-A3B distill **mxfp4** | 30.5B / 3.3B | M2 10c (24 GB) | 1k | **32.2** | 176.9 |
| LFM2-8B-A1B **4-bit** | 8.3B / 1.5B | M4 10c (16 GB) | 1k | **85.1–89.0** | ~778–846 |
| LFM2-8B-A1B **3-bit** | 8.3B / 1.5B | M4 10c (16 GB) | 1k / 4k | **101.8–103.1 / 92.2–93.7** | ~696–818 |
| LFM2.5-8B-A1B **4-bit** | 8.3B / 1.5B | M4 10c (16 GB) | 1k / 4k | **90.5–91.1 / 90.6** | ~912–985 / ~973 |
| Nemotron Labs 3 Elastic 12B-A2B **mxfp4** | 12B / 2B | M4 10c (16 GB) | 1k / 4k | **80.6–87.5 / 78.9–83.5** | ~516–578 |
| Gemma 4 26B-A4B **3-bit** | 26B / ~4B | M4 10c (16 GB) | 1k | **41.8–42.1** | ~390–398 |
| Gemma 4 26B-A4B **2.6-bit mixed** | 26B / ~4B | M4 10c (16 GB) | 1k | **41.5–41.7** | ~366–373 |
| Gemma 4 26B-A4B **oQ4 + MTP** | 26B / ~4B | M4 10c (32 GB) | 1k / 4k | **44.7 / 42.7** | ~419 / ~403 |

Cross-check on the owner's dense baseline from the same oMLX DB [M]: **Qwen3.5-9B 4-bit dense** on
M2 16 GB = **19.6–19.7 tok/s** (and 38.7 on an M2 24 GB) — i.e. oMLX is roughly calibrated to the
owner's measured 14 tok/s M2 dense-9B, which is why its MoE rows are used as the primary M1/M2/M4
table despite the Manojb disagreement.

### 1b. The same models on an M4 Mac mini 16 GB, controlled harness (Manojb)

[M], from [Manojb `macmini-16gb-bench-gguf-mlx` SUMMARY](https://huggingface.co/Manojb/macmini-16gb-bench-gguf-mlx)
— Mac mini M4 16 GB, 10 GPU cores, macOS 15.x; GGUF via `llama-server` (waves 1–33 CPU-only,
wave 34+ Metal GPU-first), MLX via `mlx_lm.server`; p50 over repeated requests at 1k and 4k context,
concurrency 1 and 2.

| Model (quant) | Engine | Ctx | tok/s [M] |
| --- | --- | ---: | ---: |
| LFM2-8B-A1B **MLX 4-bit** (`mlx-community`) | MLX | 1k / 4k | **38.9 / 49.2** |
| LFM2-8B-A1B **MLX mxfp4** | MLX | 1k / 4k | **47.4 / 56.4** |
| LFM2-8B-A1B **MLX 6-bit** | MLX | 1k / 4k | **24.7 / 27.1** |
| LFM2-8B-A1B **MLX 8-bit** | MLX | 1k / 4k | **15.8 / 22.7** |
| LFM2-8B-A1B **GGUF Q5_K_M** | llama.cpp | 1k / 4k | **20.0 / 19.3** |
| LFM2-8B-A1B **GGUF Q8_0** | llama.cpp | 1k | **14.1** |
| LFM2-24B-A2B **IQ4_XS** | llama.cpp | 1k | **15.3** |
| Ling-mini-2.0 / Ling-Coder-lite family (**MoE**) | llama.cpp | 1k | **40–51** (best 50.3; 47 complete variants, mean 27.4) |
| GPT-OSS-Swallow-20B **Q4_0** | llama.cpp | 1k | **9.4** |
| gpt-oss-20b-glm-4.6-distill **Q8_0** | llama.cpp | 1k | **9.0** |
| Qwen3.5-35B-A3B **Q3_K_XL** | llama.cpp | 1k | **2.6** (does not fit; thrash) |
| Qwen3.5-9B dense **Q4_0** (control) | llama.cpp | 1k | **4.0** |
| Dense 8B class (their summary) | llama.cpp | 1k | **5–7** |

[Manojb] also reports the memory cliff explicitly [M]: *"If a model's weights + KV cache exceed
~14 GB, performance falls off a cliff … all 27B+ dense models and some poorly quantized 24B models."*
Concurrency scaling is poor (0.57x aggregate at concurrency 2) — **memory-bandwidth bound** [M].

### 1c. Llama.cpp on an M4 Mac mini 16 GB without MLX — gpt-oss with partial CPU offload

[M], [mlx-lm issue #644](https://github.com/ml-explore/mlx-lm/issues/644): a user on a **Mac mini M4
16 GB** ran `ggml-org/gpt-oss-20b-GGUF` with
`llama-cli -hf … --n-cpu-moe 12 -fa on -c 32768 --jinja --no-mmap` and measured **~26 tokens/s**
(no swap: "RAM 13.05 / 16 GB -- SWAP 0.04 / 1 GB"). The same user's MLX attempt with
`mlx-community/gpt-oss-20b-MXFP4-Q8` **failed with a Metal out-of-memory exception** at the default
`iogpu.wired_limit_mb` (11.5 GB required, 10.9 GB default cap); an mlx-lm maintainer confirmed it is
"quite close to the limit" and recommended raising the wired limit. [M]

### 1d. Non-target chips (for context only — do not transfer)

| Model (quant) | Chip (RAM) | Engine | Decode tok/s | Source |
| --- | --- | --- | ---: | --- |
| Qwen3-30B-A3B **4-bit** | **M1 Ultra 128 GB** | mlxcel, B=1 | **81.5** (aggregate 54.3; TTFT 799 ms @512p) | [mlxcel M1 Ultra][mlxcel-moe] [M] |
| LFM2-8B-A1B **4-bit** | **M1 Max 32c 64 GB** | oMLX, 1k | **152.8** (67.4 @64k; peak 6.3→7.7 GB) | [oMLX][omlx-lfm2-m1max] [M] |
| Qwen3-30B-A3B-Thinking **4-bit** | **M1 Max 32c 64 GB** | oMLX, 1k | **69.7** | [oMLX listing][omlx] [M] |
| Gemma 4 26B-A4B | **M1 Max 64 GB** | (community, 37K ctx) | **~40** | [ai-primer][ai-primer] [M] |
| LFM2.5-8B-A1B **8-bit** | **M2 Max 38c 32 GB** | oMLX, 1k / 64k | **129.1 / 76.6** | [oMLX][omlx-lfm25-m2max] [M] |
| gpt-oss-20b **MXFP4** | **M4 Max 128 GB** | MLX (512p/1024g) | **100.2** (prefill 1528) | [Core AI bench][coreai] [M] |
| gpt-oss-20b **MXFP4** | **M4 Max 128 GB** | Apple Core AI | **78.1** (prefill 1252) | [Core AI bench][coreai] [M] |
| Gemma 4 26B-A4B **4-bit** | **M4 Max 128 GB** | mlx-serve, fastest | **125** | [mlx-serve][mlxserve-bench] [M] |
| Qwen3.6 35B-A3B **4-bit + MTP** | **M4 Max 128 GB** | mlx-serve | **232** | [mlx-serve][mlxserve-bench] [M] |
| gpt-oss-20b **mxfp4-q8** | **M4 Pro 48 GB** | Rapid-MLX, 512p/128g | **73.2** (70.1 @2K/512) | [Rapid-MLX][rapidmlx] [M] |
| gemma-4-26b **4-bit** | **M4 Pro 48 GB** | Rapid-MLX, 512p/128g | **75.6** (71.3 @2K/512) | [Rapid-MLX][rapidmlx] [M] |
| Qwen3.6-35B-A3B **4-bit** | **M2 Pro 32 GB** | Rapid-MLX 8-stream aggregate | **82.9** vs Ollama **27.2** | [Rapid-MLX][rapidmlx] [M] |
| Qwen3-Coder-30B (MoE) 4-bit | **M4 Pro 64 GB** | LM Studio MLX vs Ollama | **102.2** vs **69.8** | [asiai][asiai-gap] [M] |
| Qwen3.5-35B-A3B (MoE) 4-bit | **M4 Pro 64 GB** | LM Studio MLX vs Ollama | **71.2** vs **30.3** | [asiai][asiai-gap] [M] |
| Qwen3-30B-A3B **Q6_K** | **M3 Pro 36 GB** | llama.cpp vanilla | **38.1** | [llama.cpp PR 23440][llamacpp-moe-offload] [M] |
| Qwen3-30B-A3B **Q6_K**, 48 slots | **M1 Pro 16 GB** | llama.cpp MoE disk offload | **13** | [llama.cpp PR 23440][llamacpp-moe-offload] [M] |
| Gemma 4 26B-A4B **4-bit** (SSD streaming) | **M2 8 GB MacBook Air** | TurboFieldfare | **5.1–6.3** | [TurboFieldfare][tff] [M] |
| Gemma 4 26B-A4B **4-bit** (SSD streaming) | **M5 Pro 24 GB** | TurboFieldfare | **31–35** | [TurboFieldfare][tff] [M] |

Not measured but published as a *calculator projection* [I/V]: [canitrun M2][canitrun-m2] projects
Qwen3 30B-A3B at Q2_K (~17.3 t/s on M2 24 GB), Gemma 4 26B MoE Q3_K_M (~12.4 t/s), GPT-OSS 20B
Q4_K_M (~10.6 t/s); [canitrun M4][canitrun-m4] projects Qwen3 30B-A3B Q4_K_M ~13.9 t/s and
Qwen3.5 35B-A3B Q3_K_M ~19.3 t/s on M4 32 GB. These are modeled, not measured; treat as directional only.

**Gaps found:** no clean, single-harness, published M1/M2/M4-16GB measurement exists for
gpt-oss-20b at 4-bit on **M4 base** (only the M4 24 GB `MXFP4-Q8` row at **64k context** = 9.3 tok/s,
which is a long-context, higher-bit point: [oMLX][omlx-gptoss-m4]), and none for
**Granite H-Tiny 4-bit on M4 base**. For task 5 those two are given as [I].

---

## Task 2 — How much speedup does MoE give over dense at similar on-disk size?

The direct answer from the target hardware and the owner's own baseline:

| Comparison (same chip, 16 GB) | Dense | MoE | Ratio | Label |
| --- | ---: | ---: | ---: | --- |
| M1: Qwen3.5-9B 4-bit vs LFM2-8B-A1B 4-bit | 12 (owner) | 48.2 | **4.0x** | [M] |
| M1: dense 9B vs Granite H-Tiny 4-bit (1B active) | 12 | 45.6 | **3.8x** | [M] |
| M2: Qwen3.5-9B 4-bit vs LFM2-8B-A1B 4-bit | 14 (owner) | 73.8 | **5.3x** | [M] |
| M4: Qwen3.5-9B 4-bit vs LFM2-8B-A1B 4-bit | 18 (owner) | 86.9 | **4.8x** | [M] |
| M1: dense 9B vs gpt-oss-20b MXFP4 (3.6B active) | 12 | 23.3 | **1.9x** | [M] |
| M2: dense 9B vs gpt-oss-20b MXFP4 | 14 | 32.9 | **2.4x** | [M] |

Head-to-heads where **total** size is close, on a common harness:

- [asiai MTP table][asiai-panel] [M], llama.cpp b9430, M4 Pro 64 GB: **35B-A3B MoE** 36.3 tok/s
  (44.6 with MTP) vs **27B dense** 10.4 tok/s → **~3.5–4.3x**, same engine, same session.
- [canitrun M4 32 GB][canitrun-m4] [I/modeled]: Qwen3 32B dense ~4.5 t/s vs Qwen3.5 35B-A3B
  ~19.3 t/s → ~4.3x. Labeled modeled; directionally consistent with asiai.
- [Manojb][manojb] [M, their interpretation]: LFM2-8B-A1B MLX 4-bit 38.9 vs "dense 8B models top
  out at 5–7 tok/s on the same hardware" → ~5–7x. Same harness, different model class.

**Verdict:** the theory in the brief is borne out in practice, *directionally*: an MoE with ~1–1.5B
active beats a dense ~9B by roughly **3–4x** on the target chips, and the MoE's advantage shrinks as
active params rise (3.6B active ≈ 1.9–2.4x the dense 9B, not 9x). The speedup tracks **active**
params, not total params. [M + I]

---

## Task 3 — Practical obstacles to MoE speed on Apple Silicon

### (a) Does decode really only read active expert weights?

Partly, and with a measurable overhead. Decode is **bandwidth-bound on the base chips**, but the
bytes actually moved per token are larger than the naive "active params × bytes/param".

- [I] Using published bandwidth and the measured tok/s, the *effective* read per token is nearly
  constant across M1/M2/M4 for the same model: LFM2-8B-A1B 4-bit → 68/48.2 = **1.41 GB**,
  100/73.8 = **1.36 GB**, 120/86.9 = **1.38 GB** (~1.4 GB/token). Naive active-weight size
  (1.5B × 4-bit ≈ 0.75 GB) is about half that, so attention/shared layers, embeddings, router, KV
  reads and quant metadata roughly double the active-weight bytes.
- Same for gpt-oss-20b MXFP4-Q4: 68/23.3 = **2.92 GB**, 100/32.9 = **3.04 GB** per token
  (3.6B active ≈ 1.9 GB naive). [I]
- Op-level measurement, [mlxcel M1 Ultra][mlxcel-moe] [M]: reading 1.02 GB of expert planes took
  3.55 ms = **290 GB/s** at n=1, and 8.1 GB in 20.7 ms = 390 GB/s at n=8 — *below* the part's
  streaming bandwidth, and the "all rows hit the same experts" arm equalled the "all disjoint" arm.
  The authors' conclusion: **expert-plane traffic is not the limiter at these batch sizes**; the
  limiter is per-token kernel dispatch and graph serialization.

So: yes, decode reads only the routed experts (plus the always-on shared/attention weights), but
"only active weights" is not the whole cost, and the base-chip numbers show the memory bus is
already the wall. [M + I]

### (b) Prompt processing / prefill when the whole model must be touched

Prefill is the hidden tax and it is much worse than decode for MoE:

- [rMLX PERF_BASELINE][rmlx] [M], M5 Max: Gemma 4 26B-A4B's **4k-token prefill takes 8–10 s (TTFT)**
  while 99 decode steps take ~1.4 s. Their headline: the much-quoted "MoE is 16–48x slow" was
  **almost entirely prefill contamination** — combined `tokens/(prefill+decode)` numbers collapsed
  once decode-only was reported (Gemma4-26b 3.47 → **72.19** decode tok/s; Qwen3.6 35B-A3B
  4.46 → **94.96**). Always ask whether a published "tok/s" includes prefill.
- [mlxcel M1 Ultra][mlxcel-moe] [M]: a **cold 440-token MoE prefill (~560 ms)** is ~7x the cost of a
  single decode tick.
- [Core AI M4 Max][coreai] [M]: gpt-oss-20b prefill **1252 tok/s** vs decode **78.1 tok/s** (~16x).
- [Rapid-MLX][rapidmlx] [M]: Qwen3.8 Flash-Next (180B/6B active) on M3 Ultra prefills at
  **868 tok/s** but decodes at only **23 tok/s**.
- [famstack][famstack] [M]: on an M1 Max 64 GB, Qwen3.5-35B-A3B-4bit prefill across four engines
  ranged from **1.1 s to 96.9 s** depending on engine and cache state — prefill, not decode, decided
  total wall time.
- Mitigations seen: chunked prefill (TurboFieldfare caps at 128 tokens [M]); prompt/prefix caching
  (oMLX's SSD KV cache turned a 55K-token prefill from ~115 s into ~3.5 s on a cache hit
  [asiai-panel] [M]); Rapid-MLX radix prefix cache (131 ms prefix-test TTFT vs LM Studio's 5965 ms
  [M]).

### (c) Do Metal/MLX kernels handle MoE expert dispatch efficiently?

Mostly yes for **single-token decode**, with real slow paths for batching and cross-engine gaps:

- MLX's MoE path is `QuantizedSwitchLinear` → `mx.gather_qmm` with `sorted_indices`; mlx-lm sorts
  expert tokens when `indices.size >= 64` and keeps the top-k gather **unsorted for single-token
  decode** ([switch_layers.py][mlxlm-switch] [M]). On Metal the sorted path runs a segmented kernel
  per expert; on CUDA (as of the mlxcel GB10 report) `GatherQMM` ignored the sorted hint and ran
  B independent 1-row GEMMs, costing up to 40x prefill — a CUDA-only bug, but it shows the dispatch
  path is engine-specific ([mlxcel GB10][mlxcel-gb10] [M]).
- mlx-lm keeps the MoE **router/gate at 8-bit** in `quant_predicate` for qwen3_moe and
  granitemoehybrid [mlxlm-qwen3moe][mlxlm-granite] [M].
- [mlxcel M1 Ultra][mlxcel-moe] [M]: MoE families had **no `forward_batched` override**, so batched
  decode ran *four serialized single-token graphs* (B=4 tick 33.6 ms vs 12.3 ms at B=1, only ~30%
  GPU overlap). A fused MoE kernel was worth only **13%** of the B=1 tick; the fix was a real
  batched forward, gaining 10.3% aggregate at B=4. **MoE batching is the weak spot.**
- [Core AI vs MLX M4 Max][coreai] [M]: Core AI matched/beat MLX on every dense model (+4–12%) but
  **MLX beat Core AI by 28% on gpt-oss-20b (MoE)** — the gap is attributed to the expert-dispatch
  path, not the core engine.

### (d) KV cache at 16 GB

[I] Computed from each checkpoint's own `config.json` (attention-layer count × KV heads × head_dim ×
2 × bytes), 16-bit KV, ignoring the small conv/SSM state of hybrid models:

| Model | Attn layers | KV heads | head_dim | KV bytes/token | 8k | 32k |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| LFM2.5-8B-A1B (hybrid, 6 full-attn) | 6 | 8 | 64 | ~12 KiB | 96 MiB | 384 MiB |
| Granite 4.0 H-Tiny (4 attn + 36 Mamba) | 4 | 4 | 128 | ~8 KiB + state | 64 MiB | 256 MiB |
| gpt-oss-20b (12 full + 12 sliding=128) | 12 full | 8 | 64 | ~24 KiB | 192 MiB | 768 MiB |
| Gemma 4 26B-A4B (5 full + 25 sliding=1024) | 5 full | 8 | 256 | ~40 KiB | 320 MiB | 1.25 GiB |
| Qwen3-30B-A3B (all 48 attn) | 48 | 4 | 128 | ~96 KiB | 768 MiB | 3.0 GiB |

Takeaways: for the **small hybrid MoEs that fit 16 GB**, KV is a few hundred MB at 32k and is *not*
the binding constraint — the weights are. For the all-attention 30B-A3B, KV reaches ~3 GiB at 32k,
which is a second reason it does not belong on a 16 GB machine. Ollama's habit of pre-allocating KV
for the full context window inflated process RSS from 21.4 GB to **41.6 GB** in [asiai][asiai-gap]
[M]. KV quantisation buys 2–4x (oMLX `--kv-quant`, mlx-lm `--kv-bits`, Rapid-MLX/TurboQuant);
mlx-serve measured ~−10% decode at 2–4k context for KV quant [mlxserve-perf] [M].

### (e) If the model does not fit: SSD streaming

Three independent projects show what streaming does to speed:

- [TurboFieldfare][tff] [M]: Gemma 4 26B-A4B 4-bit (~14.3 GB of weights) with only the 1.35 GB shared
  core + KV resident, routed experts streamed by bounded parallel `pread` from SSD: **5.1–6.3 tok/s
  on an 8 GB M2 MacBook Air**, 31–35 tok/s on an M5 Pro. Chunked prefill (128 tokens) and an LFU
  expert cache (16 slots) are the mechanism.
- [llama.cpp PR #23440][llamacpp-moe-offload] [M]: Metal MoE disk offload (LRU expert slots). On
  **M3 Pro 36 GB**, Qwen3-30B-A3B-Q6_K: vanilla 38.1 tok/s → **30** at 128 slots, **22.1** at 32,
  **12** at 8; on **M1 Pro 16 GB** with 48 slots, **13 tok/s**. Cache hit rate >90% per expert after
  warm-up but only ~43% per token (0.90^8).
- [Routide][routide] [M]: iPhone/Apple-silicon flash-backed MoE paper — 512 MiB LRU gave **0.00%**
  demand hits on fixed routes; 576 MiB gave 38.58% — evidence that a too-small expert cache can be
  nearly useless and is workload-sensitive.
- [mlx-flash][mlxflash] [M, vendor-adjacent self-report]: Qwen3-30B-A3B 4-bit at ~18 GB on an
  M3 Max 36 GB under memory pressure: **3 tok/s with naïve swap-thrashing vs 82 tok/s with
  memory-aware expert caching**; Qwen1.5-MoE 14B 95 → 122 tok/s.

**Practical rule:** on 16 GB, if the 4-bit weights exceed ~12 GB you are either re-quantising
(3-bit/2-bit) or streaming from SSD, and streaming puts you in the **~5–35 tok/s** band depending
on chip, cache size and route locality.

---

## Task 4 — Runners that support MoE on Apple Silicon (2026) and known issues

| Runner | MoE support | Notes / measured evidence |
| --- | --- | --- |
| **mlx-lm** (Apple) | Yes — `QuantizedSwitchLinear`/`gather_qmm`, `switch_layers.py`, per-family files (`qwen3_moe.py`, `lfm2_moe.py`, `granitemoehybrid.py`, `gpt_oss.py`). Router/gate kept 8-bit for qwen3_moe + granitemoehybrid. [M] | Single-token decode uses unsorted `gather_qmm`; sorts at ≥64 expert tokens. Server is single-slot with prompt cache + `--kv-bits`; no grammar. Main risk: **16 GB OOM at default `iogpu.wired_limit_mb`** for an ~11.5 GB model (issue #644, M4 mini) [M]. Long-context large models warn and need a raised wired limit (README "Large Models") [V]. |
| **oMLX** | Yes (MLX server, own engine). Fastest published small-MoE numbers here: M4 16 GB LFM2.5 4-bit ~91 tok/s; tiered SSD KV cache. [M] | Known MoE-specific bug: **admission-time prefill guard scales with `max_tokens` (~80 MB/token, ~1000x real KV), rejecting normal agent requests as HTTP 500 on an M4 32 GB running Qwen3-30B-A3B / Qwen3.6-35B-A3B** ([issue #1885][omlx1885]) [M]. Also reported: tool calls lost after a cache hit ([#825 via asiai][asiai-panel]) [M]. Numbers ~2x above the controlled M4-mini harness — treat as an engine-specific best case. |
| **Rapid-MLX** | Yes — MLX-native, continuous batching, radix prefix cache with RNN-state snapshots, quantized KV; explicit MoE aliases (Qwen3.5/3.6-35B-A3B). [M] | Measured 3.0x Ollama aggregate on Qwen3.6-35B-A3B at 8 streams on M2 Pro (82.9 vs 27.2 tok/s); 73.2 tok/s gpt-oss-20b and 75.6 Gemma-4-26B on M4 Pro 48 GB. Its own 16 GB tier recommends `qwen3.5-4b` — **no MoE is recommended at 16 GB**. LFM2.5 tool-calling reported broken by a user; engine was single-slot in 0.6.66. [M] |
| **mlx-serve** | Yes — MoE benchmarks published (Gemma 4 26B-A4B 125 tok/s, Qwen3.6 35B-A3B + MTP 232 tok/s on M4 Max); MTP for MoE trunks. [M] | Benchmarks are **explicitly M4 Max 128 GB only**; the repo says don't mix other hardware into its table. Useful engine evidence, not a 16 GB fleet number. [M] |
| **LM Studio** | Yes — both a llama.cpp/GGUF backend and an MLX engine (`mlx-engine`). [M] | Slower than MLX-native engines on MoE: [asiai][asiai-gap] M4 Pro 64 GB, Qwen3-Coder-30B MoE 102.2 (MLX) vs 69.8 (Ollama) and Qwen3.5-35B-A3B 71.2 vs 30.3; [famstack][famstack] M1 Max: LM Studio was slowest of four MLX engines (e.g. doc-summary 56.9 tok/s vs 60.7 Rapid-MLX; prefill 31.3 s vs 10.6–11.4 s). GGUF rows route through the llama.cpp-derived backend, not `mlx-engine`. [M] |
| **Ollama** | Yes (GGUF/llama.cpp; MLX engine for safetensors). [M] | Works but generally the slowest for MoE on this evidence (asiai: 30.3 vs 71.2 tok/s; 69.8 vs 102.2; Rapid-MLX 3x at 8 streams) and pre-allocates full-context KV (RSS 41.6 vs 21.4 GB). [M] |
| **llama.cpp** | Yes — `MUL_MAT_ID` on Metal; grammar/GBNF (the JSON argument for choosing it); **open PR #23440** adds Metal MoE disk offloading with measured numbers. [M] | Best when you need grammar-constrained JSON, CPU/GPU split (`--n-cpu-moe`) or disk offload. Prefill on hybrid (Mamba/DeltaNet) archs disables cross-slot prefix cache. Plain GGUF cannot use an MTP head without a special `-MTP-GGUF`. [M] |
| **mlx-flash** | Yes (MLX wrapper; MoE expert cache + SSD streaming, speculative decode). [M/self-report] | Self-reported M3 Max 36 GB numbers (82.6 tok/s Qwen3-30B-A3B under pressure; 122 Qwen1.5-MoE); "when the model fits, the difference is small." Useful for the streaming question; treat its performance claims as vendor-adjacent. [V] |
| **TurboFieldfare** | Gemma 4 26B-A4B only (model-specific Swift/Metal). | The clearest SSD-streaming datapoint: 5.1–6.3 tok/s on 8 GB M2 Air, in ~2 GB RAM. [M] |

**Specifically on mlx-lm / MLX quantized MoE efficiency:** yes, MLX supports quantized MoE expert
layers directly and efficiently for **single-token decode** — the weights are stored as
`(num_experts, output, input)` quantized tensors and dispatched through `gather_qmm`
([switch_layers.py][mlxlm-switch] [M]). The known slow paths are (i) **batched decode** for MoE
families that lack a `forward_batched` override (mlxcel measured 4 serialized graphs at B=4
[mlxcel-moe] [M]), (ii) **sorted/grouped prefill on non-Metal backends**, and (iii) engine-level
memory guards (oMLX #1885) or wired-limit OOMs (mlx-lm #644).

---

## Task 5 — Realistic tok/s range for a 16 GB M1/M2/M4 Mac mini at 4-bit

All ranges combine published measurements (oMLX base-chip DB, Manojb M4 mini, mlxcel, llama.cpp,
Rapid-MLX) and are labelled. The **bandwidth check** is [I]: the measured M1:M2:M4 ratios track
68:100:120 closely (e.g. LFM2-8B-A1B 4-bit 48.2 : 73.8 : 86.9 = 1 : 1.53 : 1.80 vs bandwidth
1 : 1.47 : 1.76).

### ~1B–1.5B active, 4-bit (LFM2-8B-A1B, LFM2.5-8B-A1B, Granite 4.0 H-Tiny) — fits 16 GB

| Chip | Measured range [M] | Source | Bandwidth prediction [I] |
| --- | ---: | --- | ---: |
| **M1 16 GB** | **45–55 tok/s** | oMLX: LFM2 48.2, LFM2.5 46.5–51.1, Granite 45.6 | ~49 (68/1.4) |
| **M2 16 GB** | **65–78 tok/s** | oMLX: LFM2 ~74, LFM2.5 69–75; Granite 62 (24 GB) | ~71 (100/1.4) |
| **M4 16 GB** | **85–103 tok/s** | oMLX: LFM2 85–89, LFM2.5 90.5–91.1, 3-bit ~102 | ~86 (120/1.4) |

Counter-evidence to hold beside that: the controlled **M4 Mac mini 16 GB** harness measured the same
class of model (LFM2-8B-A1B MLX 4-bit) at **38.9 tok/s**, and llama.cpp GGUF at **20 tok/s** [M].
A realistic *planning* band for a 16 GB fleet is therefore **~45–55 (M1) / ~65–78 (M2) / ~85–100 (M4)
on a tuned MLX engine, and roughly 40–60% of that on a generic/llama.cpp stack**.

### ~3B–4B active, 4-bit — the fit problem decides everything

| Model | On-disk 4-bit | Fits 16 GB? | M1 | M2 | M4 |
| --- | ---: | --- | ---: | ---: | ---: |
| **gpt-oss-20b** MXFP4 (3.6B active) | 11.21 GB [M, HF API] | Yes, tight (12–13 GB RSS) | **22–24** [M] | **30–33** [M] | **~38–43** [I] (M2 × 120/100) |
| **Gemma 4 26B-A4B** (~4B active) | 15.64 GB [M, HF API] | No at 4-bit; **yes at 3-bit** | 15.4 (2.6-bit) [M] | 28.5 (24 GB) [M] | **~42 at 3-bit** [M] |
| **Qwen3-30B-A3B** (3.3B active) | 17.19 GB [M, HF API] | **No** | — (24 GB: ~20 at 2-bit [M]) | 32.2 (24 GB) [M] | 36–45 on M4 Pro (not base) [M] |
| **Nemotron Labs 3 Elastic 12B-A2B** (~2B active) | ~7 GB | Yes | 39.3 [M] | 56.2 (24 GB) [M] | 80.6–87.5 [M] |

Notes: 4-bit gpt-oss-20b also needed **partial CPU offload** (`--n-cpu-moe 12`) on an M4 mini to
reach 26 tok/s under llama.cpp, and OOMed under mlx-lm at the default wired limit [#644][mlxlm644].
The 30B-A3B class is a **24 GB+** answer; on a 16 GB machine it either does not load or is degraded
to 2-bit. The M4 16 GB sweet spot for a big MoE is **Gemma 4 26B-A4B at 3-bit (~42 tok/s)** or
**Nemotron 3 Elastic 12B-A2B at 4-bit (~80–88 tok/s)**.

### Ceiling, stated plainly

- Best observed small-MoE decode on a **base** M1/M2/M4: **~48 / ~77 / ~103 tok/s** (oMLX, 1k ctx)
  [M]. These are close to the memory-bus ceiling for their effective ~1.4 GB/token read [I].
- Best observed 3B-active decode that actually fits 16 GB: **~24 / ~33 / ~42 tok/s** [M/I].
- Anything much above these on a base chip is either a bigger engine's private kernel/streaming
  gain, a different (shorter) protocol, or a non-base chip (Max/Ultra). Do not transfer Max/Ultra
  numbers to a Mac mini: on an M1 Ultra, 128 GB/token-saturating decode was only ~82 tok/s for
  Qwen3-30B-A3B, i.e. the high-bandwidth chips are **latency/dispatch-bound, not bandwidth-bound**,
  and scale far below their bandwidth ratio [M, mlxcel].

---

## Caveats and open gaps

1. **oMLX vs controlled harness disagree ~2x.** Both are cited. Every number says which.
2. **Generation length is usually unpublished** (oMLX, Manojb). This matters because thinking models
   and long generations change effective throughput; the oMLX prompt is a "Code (Python)" context.
3. **Context length matters**: warm decode at 1k is the headline; at 64k+ the same models lose
   20–50% (LFM2-8B-A1B 152.8 → 67.4 on M1 Max; Granite H-Tiny 169.6 → 88.2 on M5 Max) [M].
4. **Concurrency is poor** on all of these (Manojb 0.57x at c=2; mlxcel MoE B=4 fell to ~1/3 per-request
   rate) — a bandwidth-bound machine serves one request best [M].
5. No published measurement was found for **Granite 4.0 H-Tiny 4-bit on M4 base** or
   **gpt-oss-20b 4-bit on M4 base**; those cells are [I].
6. [I] calculations assume the published bandwidth figures; real achievable bandwidth is lower, so
   the predictions are upper bounds, and the measured numbers already sit near them.

---

## SOURCES

- oMLX community benchmark listing (per-chip filter; M1/M2/M4 base rows used above):
  https://omlx.ai/benchmarks/performance
- oMLX individual pages cited:
  - LFM2-8B-A1B on M1 Max 32c: https://omlx.ai/benchmarks/performance/7vcq6q4s
  - LFM2.5-8B-A1B-oQ8 on M2 Max 38c: https://omlx.ai/benchmarks/performance/3a7rg64e
  - gpt-oss-20b-MXFP4-Q8 on M4 10c 24 GB (64k): https://omlx.ai/benchmarks/performance/hoca7fd6
  - granite-4.0-h-tiny on M5 Max (40c): https://omlx.ai/benchmarks/performance/qmm3nxcx
- Manojb, `macmini-16gb-bench-gguf-mlx` — 331 models on an M4 Mac mini 16 GB (SUMMARY.md):
  https://huggingface.co/Manojb/macmini-16gb-bench-gguf-mlx
- mlx-lm issue #644 — gpt-oss-20b on an M4 mini 16 GB (26 tok/s llama.cpp; MLX OOM):
  https://github.com/ml-explore/mlx-lm/issues/644
- mlxcel, "MoE batched decode attribution on M1 Ultra":
  https://github.com/lablup/mlxcel/blob/main/docs/benchmark_results/moe-batched-decode-m1ultra-2026-09-04.md
- mlxcel, "MoE prefill: sorted GatherQMM via dequant + CUTLASS grouped GEMM (GB10)":
  https://github.com/lablup/mlxcel/blob/main/docs/benchmark_results/moe-prefill-grouped-gemm-gb10-2026-07-10.md
- john-rocky/coreai-model-zoo, "Apple coreai-models: measured benchmarks" (Core AI vs MLX, M4 Max):
  https://github.com/john-rocky/coreai-model-zoo/blob/main/knowledge/apple-models-bench.md
- asiai, "Ollama vs LM Studio: Apple Silicon Benchmark" (M4 Pro 64 GB, MoE):
  https://asiai.dev/ollama-vs-lmstudio/
- asiai, "Apple Silicon Agentic Inference Panel" (M5 Max / M4 Pro, MTP, caches):
  https://asiai.dev/research/comparison-panel/2026-05-apple-silicon-agentic-inference-panel/
- famstack-dev/local-llm-bench, four MLX engines on Qwen3.5-35B-A3B-4bit (M1 Max 64 GB):
  https://github.com/famstack-dev/local-llm-bench/blob/main/results/mlx-engines-comparison-qwen3.5-35b-a3b.md
- Rapid-MLX README (M4 Pro / M3 Ultra / M2 Pro benchmarks; runner matrix):
  https://github.com/raullenchai/Rapid-MLX
- Rapid-MLX benchmark post referenced by the README:
  https://rapidmlx.com/blog/rapid-mlx-vs-ollama-benchmark
- NousResearch/hermes-agent issue #7800 (Rapid-MLX local-backend tip / tests):
  https://github.com/NousResearch/hermes-agent/issues/7800
- ddalcu/mlx-serve benchmarks (M4 Max) and performance/tuning guide:
  https://github.com/ddalcu/mlx-serve/blob/main/benchmarks.md
  https://github.com/ddalcu/mlx-serve/blob/v26.8.8/docs/performance.md
- drumih/turbo-fieldfare — Gemma 4 26B-A4B SSD streaming on M-series (M2 8 GB 5.1–6.3 tok/s):
  https://github.com/drumih/turbo-fieldfare
- MusaShams/Routide — flash-backed MoE inference on Apple devices (cache-hit evidence):
  https://github.com/MusaShams/Routide
- szibis/mlx-flash — MoE expert caching / SSD streaming (M3 Max self-reported):
  https://github.com/szibis/mlx-flash
- Pushkinist/rMLX PERF_BASELINE (decode-only re-baseline; prefill contamination):
  https://github.com/Pushkinist/rMLX/blob/dbbee7b33c1d5be5d30368d68551eb2dac481357/docs/PERF_BASELINE.md
- ggml-org/llama.cpp PR #23440 — MoE disk offloading for Metal (M3 Pro / M1 Pro numbers):
  https://github.com/ggml-org/llama.cpp/pull/23440
- jundot/omlx issue #1885 — MoE prefill-guard HTTP 500 on M4 32 GB (Qwen3-A3B):
  https://github.com/jundot/omlx/issues/1885
- mlx-lm repository README (large-model wired-limit guidance):
  https://github.com/ml-explore/mlx-lm
- mlx-lm `switch_layers.py`, `qwen3_moe.py`, `granitemoehybrid.py` (quantized MoE + gather_qmm):
  https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/models/switch_layers.py
  https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/models/qwen3_moe.py
  https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/models/granitemoehybrid.py
- canitrun.dev hardware calculators (modeled, not measured) — M2 and M4:
  https://canitrun.dev/gpus/m2/ · https://canitrun.dev/gpus/m4/
- ai-primer, "Gemma 4 26B-A4B runs at 30K context on 16 GB VRAM in community configs"
  (links the M1 Max ~40 tok/s community report and the 16 GB recipes):
  https://www.ai-primer.com/engineer/stories/gemma-4-local-16gb-configs
- Hugging Face model configs used for the KV-cache computation:
  https://huggingface.co/LiquidAI/LFM2.5-8B-A1B/blob/main/config.json
  https://huggingface.co/ibm-granite/granite-4.0-h-tiny/blob/main/config.json
  https://huggingface.co/openai/gpt-oss-20b/blob/main/config.json
  https://huggingface.co/Qwen/Qwen3-30B-A3B/blob/main/config.json
  https://huggingface.co/google/gemma-4-26B-A4B-it/blob/main/config.json
- Hugging Face API `?blobs=true` on-disk sizes (2026-09): `LiquidAI/LFM2.5-8B-A1B-MLX-4bit` 4.85 GB,
  `mlx-community/granite-4.0-h-tiny-4bit` 3.92 GB, `mlx-community/Qwen3-30B-A3B-4bit` 17.19 GB,
  `mlx-community/gemma-4-26B-A4B-it-qat-4bit` 15.64 GB, `mlx-community/gpt-oss-20b-MXFP4-Q4` 11.21 GB
- Ollama release v0.32.4 (runner context):
  https://github.com/ollama/ollama/releases/tag/v0.32.4

<!-- link aliases -->
[omlx]: https://omlx.ai/benchmarks/performance
[omlx-lfm2-m1max]: https://omlx.ai/benchmarks/performance/7vcq6q4s
[omlx-lfm25-m2max]: https://omlx.ai/benchmarks/performance/3a7rg64e
[omlx-gptoss-m4]: https://omlx.ai/benchmarks/performance/hoca7fd6
[omlx1885]: https://github.com/jundot/omlx/issues/1885
[manojb]: https://huggingface.co/Manojb/macmini-16gb-bench-gguf-mlx
[mlxlm644]: https://github.com/ml-explore/mlx-lm/issues/644
[mlxlm-switch]: https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/models/switch_layers.py
[mlxlm-qwen3moe]: https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/models/qwen3_moe.py
[mlxlm-granite]: https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/models/granitemoehybrid.py
[mlxcel-moe]: https://github.com/lablup/mlxcel/blob/main/docs/benchmark_results/moe-batched-decode-m1ultra-2026-09-04.md
[mlxcel-gb10]: https://github.com/lablup/mlxcel/blob/main/docs/benchmark_results/moe-prefill-grouped-gemm-gb10-2026-07-10.md
[coreai]: https://github.com/john-rocky/coreai-model-zoo/blob/main/knowledge/apple-models-bench.md
[asiai-gap]: https://asiai.dev/ollama-vs-lmstudio/
[asiai-panel]: https://asiai.dev/research/comparison-panel/2026-05-apple-silicon-agentic-inference-panel/
[famstack]: https://github.com/famstack-dev/local-llm-bench/blob/main/results/mlx-engines-comparison-qwen3.5-35b-a3b.md
[rapidmlx]: https://github.com/raullenchai/Rapid-MLX
[mlxserve-bench]: https://github.com/ddalcu/mlx-serve/blob/main/benchmarks.md
[mlxserve-perf]: https://github.com/ddalcu/mlx-serve/blob/v26.8.8/docs/performance.md
[tff]: https://github.com/drumih/turbo-fieldfare
[routide]: https://github.com/MusaShams/Routide
[mlxflash]: https://github.com/szibis/mlx-flash
[rmlx]: https://github.com/Pushkinist/rMLX/blob/dbbee7b33c1d5be5d30368d68551eb2dac481357/docs/PERF_BASELINE.md
[llamacpp-moe-offload]: https://github.com/ggml-org/llama.cpp/pull/23440
[canitrun-m2]: https://canitrun.dev/gpus/m2/
[canitrun-m4]: https://canitrun.dev/gpus/m4/
[ai-primer]: https://www.ai-primer.com/engineer/stories/gemma-4-local-16gb-configs
