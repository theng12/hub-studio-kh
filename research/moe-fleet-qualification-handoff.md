# Fleet text-model tier plan — owner direction 2026-09-26 (to qualify)

Owner-stated: *"it doesn't need to be one model for everything. a preference doesn't mean you
have to match it exactly."* So the earlier "one model across the fleet" preference is relaxed to
**one model per tier**. This is a **qualification plan, not a shipped decision** — nothing is
adopted until the receipts exist.

## The plan

| Tier | Model (quantization) | Repo | On disk | Why |
| --- | --- | --- | ---: | --- |
| **8 GB** (35 machines) | **Granite 4.0 H-Tiny 4-bit MLX** | `mlx-community/granite-4.0-h-tiny-4bit` | **3.92 GB** | 7B total / **1B active**, hybrid Mamba-2 + MoE, 128K context; the smallest credible MoE with a real 8 GB path. 3-bit (`mlx-community/granite-4.0-h-tiny-3bit-MLX`, 3.05 GB) if 4-bit is tight. |
| **16 GB** (15 machines) | **LFM2.5-8B-A1B 4-bit MLX** | `LiquidAI/LFM2.5-8B-A1B-MLX-4bit` | **4.85 GB** | 8.3B total / **1.5B active**, 128K context; best instruction/structured-output numbers in the small-MoE class (IFEval 91.84, BFCLv3 64.79) [V]. 8-bit (`…-MLX-8bit`, 9.02 GB) if fidelity needs it. |
| **Scripts / prose** | **Qwen3.5-9B 4-bit MLX** (keep) | `mlx-community/Qwen3.5-9B-4bit` | 5.98 GB | Neither small MoE is prose-proven; POLARIS ([arXiv 2606.04095](https://arxiv.org/abs/2606.04095)) shows small models degrade on long stories. Granite H-Tiny's creative-writing score is weak (CW v3 32.60 [V]). |
| **24 GB fallback** | `gemma-4-26B-A4B-it-qat-4bit` or `Qwen3-30B-A3B-4bit` | — | 15.64 / 17.19 GB | 24 GB machines only. |

Both MoE models are supported by `mlx-lm 0.31.3` (`lfm2_moe.py`, `granitemoehybrid.py` —
inspected 2026-09-26 [M]).

## Serving split — this is the important part

- **JSON sites** (outline array, structured image prompts, metadata extraction, format
  extraction): serve the model as **GGUF through llama.cpp with a JSON-schema/GBNF grammar**.
  Grammar-constrained decoding drives validity to ~100% and is **model-agnostic**; native
  unconstrained generation never reaches it (best measured Structured Output Benchmark pass
  0.987 [M]). Ready GGUF repos exist: `ibm-granite/granite-4.0-h-tiny-GGUF`,
  `LiquidAI/LFM2.5-8B-A1B-GGUF`, `unsloth/LFM2.5-8B-A1B-GGUF` (has `MXFP4_MOE`).
- **Prose** (script chapters, metadata descriptions): serve as **MLX** for speed.
- **This is not "GGUF is a better model."** The same checkpoint in MLX and GGUF is the same
  weights and the same writing quality (± the quant recipe). GGUF is picked **only** for the JSON
  lane because **llama.cpp can constrain decoding to a grammar** and the plain `mlx-lm` server
  cannot. For prose, MLX is at least as good and usually faster.
- Why two lanes: the app's MLX path has no grammar, so the `JSON.parse` call sites are only safe
  on the GGUF/grammar lane. The alternative is to add constrained decoding to the MLX path
  (mlx-lm exposes a `logits_processors` hook; Outlines' `mlxlm` wrapper can adapt it), but that is
  more work and less battle-tested than llama.cpp's GBNF today.

## Qualifications to run (per model, per tier)

1. Pin the exact HF revision and record every file's SHA-256 in a manifest, exactly like the
   existing `model-manifests/` entries. Name the quantization in the manifest and the UI.
2. On a **16 GB M4 mini** for LFM2.5-8B-A1B 4-bit, and a **16 GB** machine limited to 8 GB
   headroom (or an 8 GB M1/M2) for Granite 4.0 H-Tiny 4-bit, measure:
   - decode tok/s and peak unified memory at **1K, 8K and 32K** context;
   - a **full long-form chapter** (prose) with length-adherence judgement;
   - **JSON validity** on the real outline / thumbnail-prompt / metadata prompts, through the
     grammar lane;
   - JSON validity on the *same* prompts without grammar, to show the gap.
3. Compare against **`Qwen3.5-9B 4-bit`** on the same chapter prompt; if LFM2.5-8B-A1B 4-bit
   degrades at 8–12k characters, it is a drafting/prompts model, not the script model.
4. Record an evidence class per number (`[M]` measured, `[V]` vendor, `[I]` inference), and keep
   any package that fails qualification on the SSDs as a backup — nothing is deleted for failing.

## Caveats the qualification must not skip

- **Granite 4.0 H-Tiny is a structured-extraction model, not a writer.** Do not put the script on
  it. It is the 8 GB answer for prompts, outlines and metadata.
- **LFM2.5-8B-A1B is a reasoning model** — it emits a chain of thought. The worker must suppress
  or budget that (the same class of bug already hit Qwen3.5-9B's `Thinking Process`: the trace
  consumed the token budget and JSON came back invalid).
- **8 GB is tight, not comfortable.** macOS takes ~2.5–3 GB; a 3.92 GB model leaves little for KV.
  Test at short context and expect one Studio at a time.
- **Speed**: MoE wins because only the active experts are read per token; community oMLX numbers
  (Max-class chips, not the fleet's base M1/M2/M4) are ~60–70 tok/s for LFM2.5-8B-A1B bf16 and
  ~144 tok/s for Granite H-Tiny 8-bit [M]. Fleet-tier numbers must be measured, not transferred.
- **JSON is an engine property, prose is a model property.** Do not pick the script model on JSON
  benchmarks, and do not fix JSON by upsizing the model.

## Reference

Full analysis, on-disk sizes, quality tables and sources: `research/moe-16gb-2026-09.md`.
Fleet and ladder: `research/owner-hardware.md`.

---

## Paste this to the Studio Hub session

> Owner direction 2026-09-26 (qualification plan, not yet adopted): use **two tier models**
> instead of one.
> - **8 GB machines:** qualify **Granite 4.0 H-Tiny 4-bit MLX** (`mlx-community/granite-4.0-h-tiny-4bit`,
>   3.92 GB; 3-bit 3.05 GB fallback) for image prompts, outlines and metadata.
> - **16 GB machines:** qualify **LFM2.5-8B-A1B 4-bit MLX** (`LiquidAI/LFM2.5-8B-A1B-MLX-4bit`,
>   4.85 GB; 8-bit 9.02 GB optional) for the same artifacts.
> - **Keep `Qwen3.5-9B 4-bit` as the script/prose model** until a candidate passes a full
>   long-form chapter test — POLARIS shows small models degrade on long stories, and Granite
>   H-Tiny's creative-writing score is weak (CW v3 32.60).
> - **Serve JSON sites as GGUF + JSON-schema grammar** (llama.cpp); MLX has no grammar. Ready
>   GGUFs: `ibm-granite/granite-4.0-h-tiny-GGUF`, `LiquidAI/LFM2.5-8B-A1B-GGUF`.
> - Qualify with pinned revisions + SHA-256 manifests, measured tok/s and peak memory at
>   1K/8K/32K, real chapter prose, and JSON validity on the real prompts (with and without
>   grammar). Record evidence class per number; keep failed packages on the SSDs as backups.
> - Note: LFM2.5 is a reasoning model (suppress the thinking trace), and Granite H-Tiny is a
>   structured-extraction model, not a writer.
> Full write-up: `research/moe-16gb-2026-09.md`. This follows the owner's fleet rule
> (one model per tier, not per machine) and his 16 GB-first qualification ladder.
