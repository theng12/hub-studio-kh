# Story Studio text-LLM shortlist (Apple Silicon, 8–32 GB)

Date: 2026-09-24. Status: **research only — nothing here is adopted, wired into
gateway-kh, downloaded or qualified.** Every memory figure is labelled by its source:
**measured** (a published run), **vendor** (the model card's own claim) or **inferred**
(parameter count + quant arithmetic). Fleet list and the standing research brief live in
[`research/owner-hardware.md`](owner-hardware.md).

Goal: one local text model (or a small set) good enough to generate **every text artifact
in the Story Studio pipeline** — idea sets, outlines, narration scripts, thumbnail
prompts, per-scene image prompts, contextual cues and video metadata — on the owner's
8/16/24/32 GB Macs, without the cloud LLM.

## 1. What the model actually has to do

Sourced from the read-only sibling checkout's
`docs/handoffs-retired/LLM-CALL-SITE-SURVEY.md` and `electron/server/lib/chunked-narration.ts`:

- **Prose** — multi-chapter narration (chapter word target 50–600, default 350; narrative
  expanded 3 chapters per call, listicle 8), metadata description/titles/tags, audio
  expression markup, single-scene visual/motion prompt rewrites.
- **Strict JSON** — idea arrays; outline arrays; the scene planner's
  `{"scenes":[{start_sec,end_sec,visual_prompt,…}]}`; Prompt Director rewrites; thumbnail
  prompt sets; scene prompt-pack regeneration; contextual cues; research envelopes;
  metadata-template extraction; format extraction. The app `JSON.parse`s these, so a
  valid-JSON rate below ~100% is a functional failure.
- **Vision (one call site)** — describe a reference thumbnail's composition as JSON.
- **Context** — a batch carries system prompt + style sheet + narration/research, and
  single-pass scene planning re-sends the full narration plus a growing "story so far".
  Output budget floor is **8,192 tokens per batch**; a batch of three 600-word chapters is
  ≈2.3k output tokens, so throughput (tok/s) matters more than a huge output ceiling.
  Story Studio sizes its windows from the model's published `context_window_tokens` /
  `max_output_tokens`, and falls back to conservative defaults when a local server
  publishes neither.

Practical implication: the model must be good at **both** creative prose and obedient
schema-following. A model that is lovely at prose but drops JSON keys is worse here than a
slightly duller model that never misses a schema. Story Studio already has a mitigation for
the worst case — `planning_mode: 'two-pass'` (deterministic scene split + per-scene prose
prompts, **no JSON**) is offered for `lmstudio:*` models (see §7) — but the ideas, outline,
thumbnail-prompt and metadata call sites still require real JSON.

## 2. Tier summary — the short answer

| Mac | One model that does the whole pipeline | Runner-up / specialist |
| --- | --- | --- |
| **8 GB** (M1, M2) | **Gemma 4 E2B** — text+image+audio, 128K, ~1.5–3 GB depending on quant. Small, so scripts are outline-and-draft grade. | **Qwen3.5-4B** (4.7B, 256K, text+image) for stronger instruction-following; **1-bit Bonsai-8B** only if you accept a forked MLX. |
| **16 GB** (M1, M2, M4) | **Qwen3.5-9B** (OptiQ 4-bit, 6.6 GB) — the fleet workhorse for outline + chapter-by-chapter script + prompts + metadata. Text-only. | **Gemma 4 E2B/E4B** for the vision call; **Qwen3 14B Q4** if 9B prose feels thin. |
| **24 GB** (M4) | **Gemma 4 26B-A4B** (~15 GB 4-bit, 4B active, 256K) — best fit-per-GB MoE at this tier. | **Qwen3.8-27B** 4-bit (16.05 GB, best open ≈30B prose signal) or **Muse Glimmer 30B** 4-bit (19.41 GB, strongest instruction-follower) — tighter. See §10. |
| **32 GB** (M1 Max) | **Qwen3.6-35B-A3B 4-bit MLX** (~20.4 GB, 3B active, 262K, **multimodal**) — the 2026 Mac default; covers prose, JSON *and* the vision call in one model. | **Qwen3.8-27B** 4/6-bit (16.05/22.78 GB) or **Muse Glimmer 30B** 4-bit (19.41 GB) — the newer mid-size alternatives in §10. |

Rule of thumb used throughout: keep weights to **≤60–70% of unified memory**, then budget
the rest for macOS, the app, the KV cache and context. On 8 GB that leaves ~5–6 GB; on
16 GB, ~12–13 GB.

## 3. 8 GB tier — Gemma 4 E2B, then Qwen3.5-4B

| Model | Params | Quant on disk | Fits 8 GB? | Context | Prose | JSON | Vision | Engines |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **Gemma 4 E2B** (`AtomicChat/gemma-4-E2B-it-MLX-8bit`, `google/gemma-4-E2B-it`) | 2.3B effective (5.1B w/ embeddings) | ~2.5–3 GB (8-bit card; ~1.5 GB at 4-bit/Q4) | **Yes, comfortably** | 128K | Fair (small) | Fair | **Yes** (text/image/audio) | MLX, GGUF, LM Studio, Ollama |
| **Qwen3.5-4B** (`AtomicChat/Qwen3.5-4B-MLX-4bit`) | 4.7B dense | ~2.5–3 GB | **Yes** | 262K | Good for size | **Good** | **Yes** (text/image) | MLX, GGUF |
| **1-bit Bonsai 8B** (`prism-ml/Bonsai-8B-mlx-1bit`) | 8.19B dense (Qwen3-8B arch) | **1.28 GB** | **Yes** | 65K | Fair | **Good** (IFEval 79.8) | No | **PrismML MLX fork only** — integration risk |
| LFM 2.5 / Ministral 3B | ~2–3B | ~2 GB | Yes | — | Fair | Fair | Some | MLX, GGUF |

Evidence: the independent 8 GB M2 Mac mini benchmark (30 prompts, rubric-scored, LM Studio)
puts **Gemma 4 E2B first at 82.2%** weighted, then Bonsai 8B (79.4%), LFM 2.5 (78.9%),
Qwen 4B (74.4%), Ministral 3B (68.3%). Gemma 4 E2B's published scores: MMLU-Pro 60.0%,
IFEval-style instruction strength is its selling point at this size. Its long-context
retrieval is weak — MRCR v2 8-needle at 128k scores **19.1%** — so treat the 128K window as
a ceiling, not a usable length; keep the 8 GB context short.

**Verdict:** Gemma 4 E2B is the only 8 GB pick that is multimodal and mainstream. It is a
*drafting* model — good enough for ideas, outlines, short metadata and scene prompts, not
for a polished 20-minute narration in one pass. Use the app's two-pass scene planning and
expect to hand-edit prose. A 1-bit Bonsai 8B is technically the highest-capability tiny
footprint, but it needs the PrismML forks of MLX/llama.cpp (upstream PR pending), so it is
a project, not a download.

## 4. 16 GB tier — Qwen3.5-9B

| Model | Params | Quant on disk | Peak / fit | Context | Prose | JSON | Vision | Engines |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **Qwen3.5-9B OptiQ 4-bit** (`mlx-community/Qwen3.5-9B-OptiQ-4bit`) | 9B dense | **6.6 GB** | ~7–8 GB resident; comfortable on 16 GB | 128K+ | **Very good** | **Good** (IFEval 71.2, BFCL-V3 91.0) | No | MLX, GGUF, LM Studio, Ollama |
| Qwen3.5-9B uniform 4-bit | 9B dense | ~5.5 GB | comfortable | 128K+ | Very good | Good | No | MLX, GGUF |
| **Gemma 4 E4B** | ~4B effective | ~4.5 GB | comfortable | 128K | Good | Good | **Yes** | MLX, GGUF, Ollama |
| Qwen3 14B Q4 | 14B dense | ~9 GB | tight with context | 32K+ | Very good | Good | No | MLX, GGUF |
| Mistral Nemo 12B Q4 | 12B dense | ~7.5 GB | comfortable | **128K** | Good | Fair | No | MLX, GGUF |

Evidence: Qwen3.5-9B is the consensus 16 GB pick across community sources; the OptiQ card
publishes measured benchmarks (MMLU 69.1%, GSM8K 81.7%, IFEval 71.2%) and a 6.6 GB disk
size. Caveat published on that same card: **HashHop long-context retrieval is only 25%** —
so although the window is large, retrieval at depth is poor. Keep story-planning context
conservative and prefer the app's windowed calls.

> **Correction, 2026-09-24 (verified against the pinned files).** Both Qwen3.5-9B MLX repos
> are the same vision-language architecture (`Qwen3_5ForConditionalGeneration`, a
> `vision_tower` in the weights). The **OptiQ** build's weight index references
> `optiq/optiq_vision.safetensors` as a **required shard**, so its complete snapshot is
> **8.22 GB**, not 6.6 GB — the 6.6 GB figure is the two main shards only. The plain
> **`mlx-community/Qwen3.5-9B-4bit`** @ `8b2b98c0…` is 5.98 GB with no sidecars and a
> byte-identical chat template, which is the better 16 GB fit; OptiQ is the higher-quality
> 24/32 GB option. `mlx-lm 0.31.3` ships the `qwen3_5` implementation for both.

**Verdict:** Qwen3.5-9B is the **minimum for a full script** (outline → chapter expansion →
thumbnail prompts → metadata) and the lightest full-pipeline option for the three 16 GB
Macs. §10's 16 GB addendum ranks two heavier alternatives above it: **Gemma 4 12B**
(6.74 GB, vision+audio, 256K) and **gpt-oss-20b** (11.18 GB, 21B MoE/3.6B active,
text-only). Take Gemma 4 12B when the extra capability is worth 4–5 GB less context
headroom, and if the vision call matters it covers that in one model (or add the tiny
**Gemma 4 E4B**, ~4.5 GB, beside Qwen3.5-9B). **Skip gpt-oss-20b for this pipeline** — the
24/32 GB deep-dive measures it last of 21 on strict JSON and near-bottom on creative
writing; it is only a fast grammar-constrained fallback. For schema-heavy extraction where
prose does not matter, the deep-dive's `Schematron-8B` (SOB 0.987) also fits 16 GB.

## 5. 24 GB tier — Gemma 4 26B-A4B (fit) vs Qwen3.8-27B (quality)

> **Updated by §10:** Qwen3.8-27B (2026-08-14) supersedes Qwen3.6-27B as the current Qwen
> dense mid-size, and **Qwen3.6-27B is itself multimodal** (card: "Causal Language Model
> with Vision Encoder"), not text-only. §10 carries the ranked 12B–32B addendum.

| Model | Params (active) | Quant on disk | Fit 24 GB | Context | Prose | JSON | Vision | Engines |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **Gemma 4 26B-A4B** (MoE) | 26B total / ~4B active | **~15 GB** | **Comfortable; 128K fits** (≈1.45 GiB KV) | 256K | Very good | Very good | **Yes** (Gemma 4 family) | MLX, GGUF, Ollama |
| **Qwen3.8-27B dense** (supersedes Qwen3.6-27B) | 27B dense | **~16.05 GB** (4-bit) | **Tight** (~5 GB left; short context) | 262K → ~32K here | **Excellent** | **Excellent** | **Yes** (image+video) | MLX, GGUF, LM Studio |
| Qwen3 14B Q4 | 14B dense | ~9 GB | Comfortable | 32K+ | Very good | Good | No | MLX, GGUF |
| DeepSeek-R1-Distill-14B Q4 | 14B dense | ~8.5 GB | Comfortable | — | Good (reasoning-heavy) | Fair | No | MLX, GGUF |

Evidence: the July 2026 community guide puts Qwen3.6-27B Q4_K_M at ~16.8 GB / 18–28 tok/s
and calls 24 GB "the knife's edge"; Gemma 4 26B-A4B at ~15 GB with 4B active is the more
comfortable MoE fit. Both figures are community-reported, not measured on this fleet.

**Verdict:** on the single 24 GB M4, **Gemma 4 26B-A4B** is the better daily driver because
it leaves room for context — and with its corrected ≈10 KiB/token KV it is the strongest
24 GB **long-context** pick of the ≥25B group (128K fits comfortably, 256K on 32 GB).
**Qwen3.8-27B dense** is worth it when the task is quality-critical and the context can
stay short. **Muse Glimmer 30B** (19.41 GB) is the other strong option — best
instruction-follower and best local prose, tiny KV — but its reasoning cannot be disabled
and context overflow silently returns no answer, both JSON risks (§10). Do not put
Qwen3.6-35B-A3B here — at ~20.4 GB it is a 32 GB model.

**Pruned ("REAP") variants improve the 24 GB picture.** REAP-pruned MoEs shave the
inactive experts and land several 35B-class models in 24 GB with 64K+ context:
`Qwen3.5-35B-A3B-OptiQ-4bit-REAP-19B` (13.74 GB), its Qwen3.6 equivalent (14.86 GB) and
`GLM-4.7-Flash-REAP-23B-A3B` Q4_K_M (14.11 GB). Their **quality is not independently
benchmarked** — treat them as a fit win, not a quality claim, and qualify before trusting
one with a script.

## 6. 32 GB tier — Qwen3.6-35B-A3B (the one-model answer)

| Model | Params (active) | Quant on disk | Fit 32 GB | Context | Prose | JSON | Vision | Engines |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **Qwen3.6-35B-A3B 4-bit MLX** (`mlx-community/Qwen3.6-35B-A3B-4bit`) | 35B total / **~3B active** | **~20.4 GB** | **Comfortable** | 262K (vendor) | **Excellent** | **Excellent** (SWE-bench 73.4, AIME26 strong) | **Yes** — the MLX card is `image-text-to-text` | MLX, GGUF, Ollama, LM Studio |
| Qwen3.8-27B dense Q4/Q6 (§10) | 27B dense | 16.05 / 22.78 GB | Comfortable | 262K-class | Excellent | Excellent | **Yes** (image+video) | MLX, GGUF |
| Gemma 4 26B-A4B Q4 | 26B / 4B active | ~15 GB | Very comfortable | 256K | Very good | Very good | Yes | MLX, GGUF |
| Qwen3 32B Q4 / DeepSeek-R1-Distill-32B | 32B dense | ~20 GB | OK but 12–22 tok/s | — | Good (previous gen) | Good | No | MLX, GGUF |

Evidence: MoE with ~3B active per token is why this runs at usable speed while the 20.4 GB
file sits resident — the June 2026 community roundup reports ~61 tok/s on an M1 Max for the
MLX 4-bit build, versus ~16.7 tok/s for dense 27B 4-bit. The `mlx-community` card is
converted with `mlx-vlm`, so the same model can serve the reference-thumbnail vision call.
Context is 262K per the model's own family material; quality at that depth is not
independently verified.

**Verdict:** the 32 GB M1 Max should run **Qwen3.6-35B-A3B 4-bit** as the single model for
the entire pipeline. It is the only pick here that is strong at prose, strict JSON and
vision at once, and it is fast enough for interactive use. Keep context to 32–64K with a
quantized KV cache unless the workload proves it needs more. §10 adds **Qwen3.8-27B**
(4-bit for 128K, or 6-bit) and **Muse Glimmer 30B** as the newer mid-size alternatives on
this tier.

**KV, not weights, is what actually binds on 32 GB.** Only ~21–26 GB is usable, so a 4-bit
27–35B model fits while its fp16 KV at 128K may not. The 2026 hybrids are cheap —
Qwen3.6-35B-A3B ≈20 KiB/token (2.5 GiB @128K), Muse Glimmer ≈13, Gemma 4 26B-A4B ≈10
(1.45 GiB @128K, the cheapest ≥25B long-context profile) — while dense GQA models explode:
Qwen3-32B is ≈256 KiB/token, i.e. 32 GiB of KV at 128K, the whole machine. **Measured on the actual 32 GB M1 Max**: Qwen3.6-35B-A3B 3-bit ≈47.7
tok/s at 32K (peak 19.0 GB) and 36.9 tok/s at 64K (peak 22.4 GB). Full per-model KV table,
long-context caveats and the current 24/32 GB shortlist are in
[`research/local-llm-24-32gb-2026-09.md`](local-llm-24-32gb-2026-09.md).

**Explicitly out of fleet range:** Llama 4 Scout (109B/17B, ~58 GB Q4) and DeepSeek
V4-Flash (284B/13B, ~110 GB+ even at 2-bit) are not 32 GB models. Do not plan around them.

## 7. Running it and wiring it to Story Studio

**Engines (Apple Silicon, Sept 2026) — the structured-output picture is what matters here:**

- **llama.cpp / `llama-server` (GGUF)** — the most reliable guaranteed-parseable JSON.
  Accepts `response_format`/`json_schema` and `grammar`; run `-ngl 99 -c 32768
  --cache-type-k q8_0 --cache-type-v q4_0 --jinja`. GBNF **silently skips unsupported
  schema keywords** (nested `$ref`, `prefixItems`, unanchored `pattern`), so either stay in
  its subset or build with `-DLLAMA_LLGUIDANCE=ON`. Multimodal via a separate `mmproj`.
  Known Metal regressions to check per build: llama.cpp #21655 (M4 16 GB decode drop),
  #29134 (Gemma-4-26B-A4B ≈6% slower), #28805 (M1 Max long-context decode emits one token
  then EOS), and #29006 (chat-template control tokens can break `response_format` on
  `/v1/chat/completions` with Qwen3-style templates — raw `/completion` + GBNF still works).
- **`mlx-lm` server** — fastest for MoE, but **its OpenAI server exposes no
  `response_format`/grammar at all** (verified in `SERVER.md`/`server.py`; the Outlines PR
  was closed unmerged), and its default `max_tokens=512` truncates long JSON. Do **not**
  point a `JSON.parse` site at plain `mlx_lm.server`.
- **`mlx-vlm` server** (Blaizzy/mlx-vlm) — the MLX-native server that *does* document
  OpenAI `json_schema` on `/v1/chat/completions`, **including multimodal image requests**.
  This is the right server for the Qwen3.5/Qwen3.6 MLX vision builds above.
- **LM Studio** — GUI + OpenAI API on `http://127.0.0.1:1234/v1`. GGUF path uses llama.cpp
  grammar and is solid; MLX path uses Outlines. Known MLX bug #1971: `qwen3.5-4b-mlx`
  returns valid JSON in `reasoning_content` while `content` is empty. This is the provider
  Story Studio's own code expects for local models (the `lmstudio:*` namespace).
- **Ollama** — easiest setup, native MLX runner since 0.19. But through mid-2026 its
  MLX/NVFP4 engine structured output was **broken** (issues #17013 ignores `format`,
  #17933 MLX 0/3 vs GGUF 3/3, #18441 thinking-on emits a stray `.`, #18567 never
  terminates). **Use GGUF tags, not `-mlx` tags, for any JSON call site.** Set
  `OLLAMA_KV_CACHE_TYPE=q8_0` and `OLLAMA_FLASH_ATTENTION=1`.
- Newer Mac-only runners (Rapid-MLX, oMLX, Lightning MLX) are unproven for schema
  decoding; Splash requires M3+/macOS 26.4/36 GB and is **out of fleet range**.

**Rule of thumb:** strict-JSON call sites → **llama.cpp/GGUF** (or LM Studio's GGUF path,
or Ollama GGUF tags). Multimodal JSON → **`mlx-vlm`'s `json_schema`**. Keep
`mlx_lm.server` for prose and for the app's two-pass planner, which needs no JSON.

### Getting a `JSON.parse`-safe answer

Validity is not correctness. Measured 2026 preprints find constrained decoding lifts schema
validity to ~100% but can *lower* answer accuracy — one sub-3B study went 19.7% → 11.0%
accuracy with "wrong-but-valid" rising 49.5% → 88.9%. So:

1. Constrain at decode time **and** include the schema plus "return JSON" in the prompt.
2. Let reasoning run free and apply the grammar only to the final answer channel
   ("reason free, constrain late").
3. `temperature=0` and a **generous `max_tokens`** — truncation is the top residual cause
   (beware `mlx_lm`'s 512-token default). **Treat `finish_reason`/`done_reason == length`
   as failure**, not as a parseable object. Native generation never reaches 100%: the best
   of 21 models on the Structured Output Benchmark scores 0.987, so the grammar is
   mandatory rather than a nicety.
4. Keep the schema inside the engine's supported subset (llama.cpp drops unknown keywords
   silently).
5. Read the right field — `content`, not `reasoning`/`reasoning_content`.
6. Safety net: validate → `json_repair` once → re-ask with the validation error (cap ~3) →
   deterministic fallback.

For creative-payload schemas (scene plans, image prompts) treat the grammar as a syntax
guard only and check semantics separately — a schema-valid scene plan can still be empty.

**Path into Story Studio:** Story Studio talks only to gateway-kh (`genstudio_url`,
default `http://127.0.0.1:8787`) — the direct endpoint pool was retired. gateway-kh has a
declarative provider registry, an `openai_compatible` adapter and a custom-provider path,
so a local server is added as a provider row pointing at it. Story Studio keys its
local-model behaviours (`planning_mode: 'two-pass'`, small scene windows) off the
**`lmstudio:*` id prefix**, and it publishes chat models from gateway-kh's catalog. What is
**not** verified here: whether gateway-kh currently advertises any local chat product, or
whether a custom provider's ids can be made to carry the `lmstudio:` prefix. That is the
first thing to check before promising an in-app local LLM.

**KV cache is the hidden memory cost.** Context eats memory faster than weights. Quantize
it (`q8_0` K / `q4_0` V, or Ollama's `OLLAMA_KV_CACHE_TYPE=q8_0`), and start at 32–64K, not
the model's maximum. On MLX, use `--kv-bits 4/8` and lower `--prefill-step-size` (peak can
*rise* during prefill otherwise, mlx-lm#1587); **`--kv-bits` crashes on Gemma-4-class
sliding-window models** (mlx-lm#1573). Do not mix K and V quant types — that can silently
disable Metal flash attention.

### The vision call site — small VLMs

The one image call needs a model that returns a JSON composition description. There is
**no head-to-head JSON-reliability benchmark** for VLMs, so get validity from the engine
(`mlx-vlm` `json_schema` or llama.cpp `response_format`), not from the model. 4-bit
on-disk sizes below are delegated HF-API measurements; the fits are inference.

| Model | Params | 4-bit on disk | Fits 8 GB | Structured-output evidence |
| --- | --- | ---: | --- | --- |
| `ibm-granite/granite-vision-4.1-4b` | 4B | ~3.0 GB | Yes | **Strongest documented:** template-level JSON KVP plus `<tables_json>`/`<chart2csv>` |
| `Qwen/Qwen2.5-VL-3B-Instruct` | 3.75B | ~2.9 GB | Yes | Card claims stable JSON coordinates + form/table extraction |
| `Qwen/Qwen3-VL-2B-Instruct` | 2.1B | ~1.7 GB | Yes | OCR/doc cookbook; no JSON benchmark |
| `openbmb/MiniCPM-V-4.6` | 1.3B | ~2.2 GB | Yes | Tool-call JSON + schema example in the card |
| `Qwen/Qwen3-VL-4B-Instruct` | 4.4B | ~2.9 GB | **Tight** | `mlx-vlm`'s official multimodal `json_schema` example uses this family |

Qwen3.5-2B/4B/9B are **unified** vision-language models (their official ids have no "VL"
suffix), and the 32 GB Qwen3.6-35B-A3B MLX build is itself `image-text-to-text` — so the
tier picks already cover vision on 8–32 GB. Add one of the small VLMs above only on a
machine whose text pick is text-only.

## 8. Recommended first downloads

> **Owner decision (2026-09-24, revised):** one common fleet model — **`Qwen3.5-9B`** on the
> 16 GB machines, **text-only accepted**, vision call stays on the cloud. Stocked build:
> **`mlx-community/Qwen3.5-9B-4bit`** @ `8b2b98c0…` (5.98 GB); OptiQ 4-bit (8.22 GB complete)
> is the optional 24/32 GB quality tier. The 24 GB row below is not a requirement.

1. **16 GB M4 (the packaged-app dev machine):** `mlx-community/Qwen3.5-9B-4bit` —
   the everyday writer; **serve it as GGUF** for the strict-JSON call sites and keep the
   MLX build for prose / the two-pass planner.
2. **32 GB M1 Max:** `mlx-community/Qwen3.6-35B-A3B-4bit` — the do-everything model,
   vision included; use `mlx-vlm`'s server for the multimodal JSON call.
3. **8 GB M1/M2:** `AtomicChat/gemma-4-E2B-it-MLX-8bit` (or 4-bit) — drafting only.
4. **24 GB M4:** **Gemma 4 26B-A4B** QAT 4-bit (measured 34.8 → 10.1 tok/s at 1K → 32K
   on an M4 32 GB), with **Qwen3.8-27B** 4-bit kept for quality runs. A REAP-pruned
   35B/GLM MoE is a lower-memory alternative — unbenchmarked quality.

## 9. What to verify locally before adopting anything

- [ ] Does gateway-kh expose a local chat product at all, and can its ids carry the
      `lmstudio:` prefix that unlocks two-pass planning?
- [ ] Does the chosen server publish `context_window_tokens` / `max_output_tokens`? If
      not, Story Studio uses conservative defaults and batch sizes stay small.
- [ ] Valid-JSON rate on the **real** outline, thumbnail-prompt and metadata prompts — not
      a synthetic JSON test. Measure at the app's temperature.
- [ ] That the chosen engine actually **enforces the schema on the exact request path** —
      Ollama's MLX/NVFP4 engine ignored or mangled `format` through mid-2026, and LM
      Studio's MLX path can put JSON in `reasoning_content` with an empty `content`.
- [ ] **Semantics, not just syntax.** Constrained decoding can raise wrong-but-valid output
      sharply (19.7% → 11.0% answer accuracy in one sub-3B study); spot-check that scene
      plans and prompts are actually filled in.
- [ ] Peak unified memory and tok/s on the actual tier, with the app and other workers
      resident (Image/Voice are usually holding memory on the same Mac).
- [ ] Long-context behaviour at 32/64K on a real episode, given the weak HashHop/MRCR
      numbers above.
- [ ] Whether the vision call can run on the same model (32 GB) or needs a small VLM.

## 10. Mid-size (12B–32B) addendum — ranked per tier

**Source status: live web sources were accessed for this section on 2026-09-23** — HF model
cards and the HF `api/models?blobs=true` size API, mlx-community / lmstudio-community /
unsloth / bartowski repos, official vendor cards, EQ-Bench, llm-stats and lechmazur. **No
local measurement.** Tags: `[card]` vendor claim, `[bench]` published benchmark,
`[inference]` parameter + KV arithmetic. Sizes are weights-only on-disk GB (10⁹); MLX and
GGUF quants of the same model can differ ~5–10%. Two corrections to §2/§5:
**Qwen3.6-27B is multimodal** (card: "Causal Language Model with Vision Encoder",
`pipeline_tag: image-text-to-text`), and **Qwen3.8-27B (2026-08-14) now supersedes
Qwen3.6-27B** as the current Qwen dense mid-size (Muse Glimmer's card and the lechmazur
leaderboard both benchmark the "Qwen3.6/3.8-27B" class).

### 16 GB tier (M1/M2/M4)

| # | Model — base + example quant | Params / arch | Weights 4/6/8-bit GB | Est. peak @16 GB `[inference]` | Ctx → practical | Vision |
|---|---|---|---|---|---|---|
| 1 | **Gemma 4 12B** — `google/gemma-4-12B-it`; `mlx-community/gemma-4-12B-it-4bit` | dense 11.95B | 6.74 / 9.76 / 12.75 | ~11 GB @32K | 256K → ~32–64K | **Yes** (+audio) |
| 2 | **gpt-oss-20b** *(see warning below — not for prose or schema-critical calls)* — `openai/gpt-oss-20b`; `mlx-community/gpt-oss-20b-MXFP4-Q4` | MoE 21B / **3.6B active** | 11.18 (MXFP4) / — / — | ~14 GB @32K | 128K | No |
| 3 | **Ministral 3 14B** — `mistralai/Ministral-3-14B-Instruct-2512`; `mlx-community/...-4bit` | dense 14B (+0.4B vision) | 8.42 / 11.81 / 15.20 | ~14.5 GB @24K (tight) | 256K → ~16–24K | Yes |
| 4 | **Qwen3 14B** — `Qwen/Qwen3-14B`; `mlx-community/Qwen3-14B-4bit` | dense 14.8B | 8.31 / 12.12 / 15.70 | ~14.3 GB @24K (tight) | 32K → 32K | No |
| 5 | **Phi-4-reasoning** — `microsoft/Phi-4-reasoning`; `mlx-community/Phi-4-reasoning-4bit` | dense 14B | 8.25 / — / — | ~13.6 GB @32K | 32K → 32K | No |
| 6 | **Phi-4** — `microsoft/phi-4`; `mlx-community/phi-4-4bit` | dense 14B | 8.25 / — / — | ~11.6 GB @16K | **16K → 16K** | No |

- **Gemma 4 12B** = best fit + only vision/audio model under 10 GB. CW v3 Elo 1286.3 /
  rubric 15.71 `[bench]`; EQ-Bench3 1361.2 / 65.0 `[bench]`; **MRCR v2 @128K 43.4%**
  `[bench]`. JSON hazard: Ollama [#15416](https://github.com/ollama/ollama/issues/15416)
  returns a ```json fence with thinking on; thinking-off still emits an empty
  `<|channel>thought` block `[card/bug]`.
- **gpt-oss-20b** is the strongest *stated* structured-output pick: OpenAI card says it
  "runs within 16 GB", supports Structured Outputs, and it is a 3.6B-active MoE `[card]`.
  Text-only, so it does not cover the vision call site. Its MXFP4 quant is 11.18 GB.
  **Warning — the 24/32 GB deep-dive contradicts this pick:** gpt-oss-20b is last of 21
  on the Structured Output Benchmark (0.845) and near-bottom on EQ-Bench creative writing
  (#130/133, long-form 21.2). Use it only as a fast, grammar-constrained, non-prose
  fallback — never for scripts or schema-critical calls.
- **Ministral 3 14B** has the best JSON *claim* ("native function calling and JSON
  outputting"; "comparable to Mistral Small 3.2 24B") `[card]`, vision, 256K — but a heavy
  full-attention KV (≈5.4 GB @32K `[inference]`). EQ-Bench Longform 31.9 / repetition 21.9
  `[bench]`; no CW v3 entry.
- **Qwen3 14B**: CW v3 **0.803** `[bench, self-reported]`; 32K ctx, no vision.
- **Phi-4-reasoning**: IFEval-strict **83.4** `[bench]`, 32K, emits `<think>` (strip it).
  **Phi-4 base is 16K** — too short for multi-chapter narration with prior chapters in
  context; not recommended.
- All of these are above the parent note's §4 pick (Qwen3.5-9B OptiQ, 6.6 GB); they add
  quality/multimodality but cost 2–5 GB more and, for the 14B dense models, context headroom.

### 24 GB tier (M4)

| # | Model — base + example quant | Params / arch | Weights 4/6/8-bit GB | Est. peak @24 GB `[inference]` | Ctx → practical | Vision |
|---|---|---|---|---|---|---|
| 1 | **Qwen3.8 27B** — `mlx-community/Qwen3.8-27B-4bit` | dense 27B | 16.05 / 22.78 / 29.50 | ~20.2 GB @32K | 262K → ~32K | **Yes** (image+video) |
| 2 | **Muse Glimmer 30B** — `meta-models/Muse-Glimmer-30B`; `mlx-community/Muse-Glimmer-30B-4bit` | dense ~29.6B (incl 1.8B ViT) | 19.41 / 26.40 / 33.38 | ~21.9 @32K; ~23.2 @128K | 131K+ → 128K | Yes |
| 3 | **Gemma 4 26B-A4B** — `mlx-community/gemma-4-26B-A4B-it-qat-4bit` | MoE 25.2B / **3.8B active** | 15.37 / 21.81 / 27.95 | ~16.5 @32K; ~17.5 @128K | 256K → 128K on 24 GB; 256K on 32 GB | Yes |
| 4 | **GLM-4.7-Flash** — `zai-org/GLM-4.7-Flash`; `mlx-community/GLM-4.7-Flash-4bit` | MoE 30B-A3B | 16.85 / — / 29.60 | ~19.5 @32K | ~198K | No |
| 5 | **Nemotron-3-Nano-30B-A3B** — `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16`; `mlx-community/...-4bit` | MoE 30B / 3.5B active | 17.78 / — / 31.30 | ~20.5 @32K | 1M | No |
| 6 | **Granite 4.2-30B** — `ibm-granite/granite-4.2-30b` | dense 30B | ~16.5 (Q4_K_M) | tight | 128K (RULER-128K 81.4) | No |
| 7 | **Granite 4.0 H-Small** — `mlx-community/granite-4.0-h-small-4bit` | MoE 32B / **9B active** | 18.13 / — / — | ~21 GB | 128K | No |
| 8 | **Mistral Small 3.2 / Magistral Small 2509 / Devstral Small 2 24B** | dense 24B | 13.26 / 14.09 / 15.10 (4-bit) | ~20.6–22.5 @32K | 128K–256K | Yes |

- **Qwen3.8 27B** is the best open ≈30B prose signal here (lechmazur **rank 30, −0.5** vs
  Gemma 4 31B rank 38, −1.7 `[bench]`) and IFBench **79.5** `[card]`; `enable_thinking:false`
  is available; hybrid attention keeps KV small (~2.15 GB @32K). 16.05 GB fits 24 GB only at
  short context — long context belongs on the 32 GB machine.
- **Muse Glimmer 30B** is the strongest instruction/tool follower (IFBench 77.0, AA-LCR 80.0,
  MMMU-Pro 74 `[card]`), Meta explicitly targets 24 GB, tiny KV (~1.74 GB @128K) — but
  **reasoning cannot be disabled** and **context overflow silently returns no answer**
  (`[card]`), both JSON risks. The deep-dive's creative benchmark puts it **first among
  local models** (EQ-Bench CW v3 Elo 1789.7, #22/133), so prose is a strength, not a gap.
- **Gemma 4 26B-A4B** matches the parent note's §5 pick and is the comfortable MoE fit; same
  thinking-block JSON hazard. **Granite 4.0 H-Small** is a hybrid Mamba-2/attention MoE with
  IFEval 87.6 `[bench]`. **GLM-4.7-Flash** (MIT) and **Nemotron-3-Nano-30B-A3B** (Arena-Hard
  V2 Creative Writing 63.2, IFBench 71.5 `[bench]`) are the other 2026 MoE entrants.
- **Mistral 24B family — prose evidence is split.** Mistral Small 3.2 24B scores CW v3 Elo
  **1252.9 / rubric 14.31** `[bench]`; Small **3.1** is far worse (759.2 / 8.46) — prefer
  3.2. The only measured Apple-Silicon peak found for any candidate is **Small 3.2 4-bit on
  a 64 GB M1 Max: 13.1 GB @1K, 18.3 GB @32K, 23.3 GB @64K** `[secondary, measured]`, i.e.
  24 GB is comfortable at 32K and tight at 64K. Magistral and Devstral have **no CW v3 or
  BFCL entry** → unbenchmarked, not proven weak; Devstral is deliberately code/agentic-tuned.
- **Gemma 4 31B 4-bit (18.41 GB) and Qwen3-32B 4-bit (18.43 GB) do not leave enough room for
  context on 24 GB** (KV 5.37 / 8.59 GB @32K `[inference]`).

### 32 GB tier (M1 Max)

| # | Model — base + example quant | Params / arch | Weights 4/6/8-bit GB | Est. peak @32 GB `[inference]` | Ctx → practical | Vision |
|---|---|---|---|---|---|---|
| 1 | **Qwen3.8 27B** — `mlx-community/Qwen3.8-27B-4bit` / 6-bit | dense 27B | 16.05 / 22.78 / 29.50 | 4-bit ~26.6 @128K; 6-bit ~29 @64K | 262K → 128K | Yes |
| 2 | **Muse Glimmer 30B** — 4-bit / 6-bit | dense ~29.6B | 19.41 / 26.40 / 33.38 | 4-bit ~23.2 @128K | 131K+ → 128K | Yes |
| 3 | **Gemma 4 31B** — `mlx-community/gemma-4-31b-it-4bit` | dense 30.7B | 18.41 / 26.12 / 33.76 | ~25.8 @32K (128K needs ~40) | 256K → **~32K** | Yes |
| 4 | **Qwen3 32B** — `Qwen/Qwen3-32B`; `mlx-community/Qwen3-32B-4bit` | dense 32.8B | 18.43 / — / — | ~29 @32K (tight) | 32K → 32K | No |
| 5 | **Qwen3-30B-A3B-Instruct-2507** — `Qwen/Qwen3-30B-A3B-Instruct-2507` | MoE 30.5B / 3.3B active | quant **unverified** | — | 256K | No |

- **Gemma 4 31B** has the best Gemma prose (CW v3 Elo **1366.0** / 16.01; MRCR v2 @128K
  **66.4%** — the only Gemma with decent 128K recall `[bench]`) but its global-attention KV
  (~21.5 GB @128K `[inference]`) caps it near 32K on this tier.
- **Qwen3-30B-A3B-Instruct-2507** is the one to confirm: non-thinking (no `<think>` leakage),
  256K, **CW v3 86.0**, WritingBench 85.5, IFEval 84.7, RULER avg 86.8 `[card]` — verify its
  quant size before relying on it.
- This extends, not replaces, the parent note's §6 pick (`Qwen3.6-35B-A3B-4bit`, 20.4 GB,
  multimodal); **Qwen3.8-27B** and **Muse Glimmer 30B** are the newer alternatives.

### Excluded / does not fit

| Model | Why |
|---|---|
| **Llama 4 Scout 17B-16E** | 109B total; 4-bit MLX = **61.14 GB**; does not fit 32 GB even at ~1–2 bit with KV |
| **Mistral Small 4 119B**, **Mistral Medium 3.5 128B**, **Qwen3.8-Flash-Next** (125B/6B + 51B n-gram emb), **Command R+ 104B** | far beyond 32 GB at usable quant |
| **Gemma 4 31B / Qwen3-32B on 16 or 24 GB** | weights + KV exceed a workable budget `[inference]` |
| Any 12B+ on **8 GB** | no credible path (consistent with the parent note's §3) |

### Addendum sources (full URLs)

- Cards: https://huggingface.co/google/gemma-4-12B-it · https://huggingface.co/google/gemma-4-26B-A4B-it · https://huggingface.co/google/gemma-4-31B-it · https://huggingface.co/Qwen/Qwen3.8-27B · https://huggingface.co/Qwen/Qwen3.6-27B · https://huggingface.co/Qwen/Qwen3.5-27B · https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507 · https://huggingface.co/openai/gpt-oss-20b · https://huggingface.co/microsoft/Phi-4-reasoning · https://huggingface.co/meta-models/Muse-Glimmer-30B · https://research.meta.ai/blog/introducing-muse-glimmer-open-agentic-model · https://huggingface.co/mistralai/Ministral-3-14B-Instruct-2512 · https://huggingface.co/mistralai/Magistral-Small-2509 · https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512 · https://huggingface.co/ibm-granite/granite-4.2-30b · https://huggingface.co/ibm-granite/granite-4.0-h-small · https://huggingface.co/zai-org/GLM-4.7-Flash · https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16 · https://huggingface.co/meta-llama/Llama-4-Scout-17B-16E-Instruct
- Quants (sizes via HF API): https://huggingface.co/mlx-community/gemma-4-12B-it-4bit · https://huggingface.co/mlx-community/gemma-4-26B-A4B-it-qat-4bit · https://huggingface.co/mlx-community/gemma-4-31b-it-4bit · https://huggingface.co/mlx-community/Qwen3.8-27B-4bit · https://huggingface.co/lmstudio-community/Qwen3.8-27B-MLX-6bit · https://huggingface.co/mlx-community/gpt-oss-20b-MXFP4-Q4 · https://huggingface.co/mlx-community/Muse-Glimmer-30B-4bit · https://huggingface.co/mlx-community/Ministral-3-14B-Instruct-2512-4bit · https://huggingface.co/mlx-community/Phi-4-reasoning-4bit · https://huggingface.co/mlx-community/GLM-4.7-Flash-4bit · https://huggingface.co/mlx-community/NVIDIA-Nemotron-3-Nano-30B-A3B-4bit · https://huggingface.co/mlx-community/granite-4.0-h-small-4bit · https://huggingface.co/lmstudio-community/Magistral-Small-2509-MLX-4bit · https://huggingface.co/mlx-community/Devstral-Small-2-24B-Instruct-2512-4bit · https://huggingface.co/mlx-community/Qwen3-14B-4bit · https://huggingface.co/mlx-community/Qwen3-32B-4bit · https://huggingface.co/mlx-community/phi-4-4bit
- Benchmarks: https://github.com/lechmazur/writing · https://eqbench.com/creative_writing.html · https://eqbench.com/results/eqbench3_reports/google__gemma-4-12b-it.html · https://eqbench.com/results/eqbench3_reports/google__gemma-4-26b-a4b-it.html · https://llm-stats.com/benchmarks/creative-writing-v3 · https://artificialanalysis.ai/models/comparisons/qwen3-8-27b-vs-gemma-4-31b
- JSON tooling / bugs: https://docs.ollama.com/capabilities/structured-outputs · https://lmstudio.ai/docs/developer/openai-compat/structured-output · https://github.com/ml-explore/mlx-swift-lm/blob/main/Libraries/MLXGuidedGeneration/README.md · https://github.com/ollama/ollama/issues/15416

## Sources

- Story Studio call sites and output shapes (read-only sibling):
  `story-studio-kh/docs/handoffs-retired/LLM-CALL-SITE-SURVEY.md`;
  `story-studio-kh/electron/server/lib/chunked-narration.ts`;
  `story-studio-kh/electron/server/models/types.ts` (`planning_mode`, `window_size`).
- gateway-kh provider registry and OpenAI-compatible adapter:
  `gateway-kh/repository/gateway/providers/definitions.py`,
  `gateway-kh/repository/gateway/adapters/openai_compat.py`.
- [mlx-community/Qwen3.5-9B-OptiQ-4bit](https://huggingface.co/mlx-community/Qwen3.5-9B-OptiQ-4bit)
  — quant card with measured MMLU/GSM8K/IFEval/BFCL/HashHop.
- [mlx-community/Qwen3.6-35B-A3B-4bit](https://huggingface.co/mlx-community/Qwen3.6-35B-A3B-4bit)
  — MLX vision build (converted with `mlx-vlm`).
- [AtomicChat/gemma-4-E2B-it-MLX-8bit](https://huggingface.co/AtomicChat/gemma-4-E2B-it-MLX-8bit)
  — Google's published Gemma 4 E2B scores and 128K/2.3B-effective spec.
- [AtomicChat/Qwen3.5-4B-MLX-4bit](https://huggingface.co/AtomicChat/Qwen3.5-4B-MLX-4bit)
  — 4.7B, 262K, text+image, Apache 2.0.
- [prism-ml/Bonsai-8B-mlx-1bit](https://huggingface.co/prism-ml/Bonsai-8B-mlx-1bit)
  — 1.28 GB 8B, measured IFEval/BFCL; requires PrismML MLX fork.
- [Sid Local LLM Benchmark v3](https://github.com/blackdeerbits/sid-local-llm-bench)
  — independent 8 GB M2 Mac mini comparison used for §3.
- [Mac + MLX Megathread (June 2026)](https://github.com/AtlasOmnia/hermes-megathreads/blob/main/megathreads/mac-mlx-apple-silicon-2026-06.md)
  — community MoE tok/s, engine bugs, KV-cache guidance.
- [InsiderLLM — Best Local LLMs for Mac 2026](https://insiderllm.com/guides/best-local-llms-mac-2026/)
  — tier picks and the ≤60–70% memory rule (community-reported figures).
- Engine structured-output facts: [llama.cpp grammars README](https://raw.githubusercontent.com/ggml-org/llama.cpp/master/grammars/README.md),
  [llama.cpp llguidance docs](https://raw.githubusercontent.com/ggml-org/llama.cpp/master/docs/llguidance.md),
  [mlx-lm SERVER.md](https://raw.githubusercontent.com/ml-explore/mlx-lm/main/mlx_lm/SERVER.md),
  [mlx-vlm README](https://raw.githubusercontent.com/Blaizzy/mlx-vlm/main/README.md),
  [LM Studio structured output](https://lmstudio.ai/docs/developer/openai-compat/structured-output),
  [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs).
- Engine bugs verified via GitHub: Ollama [#17933](https://github.com/ollama/ollama/issues/17933),
  [#18441](https://github.com/ollama/ollama/issues/18441), [#18567](https://github.com/ollama/ollama/issues/18567),
  [#17013](https://github.com/ollama/ollama/issues/17013); LM Studio
  [#1971](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1971); llama.cpp
  [#21655](https://github.com/ggml-org/llama.cpp/issues/21655), [#29134](https://github.com/ggml-org/llama.cpp/issues/29134),
  [#28805](https://github.com/ggml-org/llama.cpp/issues/28805).
- Constrained-decoding trade-off ("Constraint Tax"):
  [arXiv 2605.26128](https://arxiv.org/abs/2605.26128); schema-validity/compile
  comparison: [arXiv 2609.23742](https://arxiv.org/abs/2609.23742).
- Vision candidates: [granite-vision-4.1-4b](https://huggingface.co/ibm-granite/granite-vision-4.1-4b),
  [Qwen3-VL-4B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct),
  [MiniCPM-V-4.6](https://huggingface.co/openbmb/MiniCPM-V-4.6),
  [Qwen2.5-VL-3B-Instruct](https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct).
- Companion deep dive (24/32 GB): [`research/local-llm-24-32gb-2026-09.md`](local-llm-24-32gb-2026-09.md)
  — KV-cost table, measured M1 Max 32 GB throughput, EQ-Bench/SOB creative and JSON
  numbers, and the models that do not fit. Per-model MoE quant/speed companion:
  [`research/moe-24-32gb-fit-2026-09.md`](moe-24-32gb-fit-2026-09.md).
