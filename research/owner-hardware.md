# Owner hardware fleet — canonical memory for model research

**Purpose.** Every model-fit question (LLM, image, voice, video) is answered against
*specific machines the owner owns*. This file is the one place to read that list, so he
never has to restate it. It also carries the standing research brief for the text/LLM
work, so a new research note starts from the real call sites instead of generic
"best local model" advice.

Owner-stated, 2026-09-24. If reality and this file disagree, the owner's next statement
wins and this file gets fixed — the same rule the handoff uses.

## The fleet

| Unified memory | Chip(s) | Machines | Notes |
| --- | --- | ---: | --- |
| 8 GB | M1, M2 | 2 | The hard constraint. Anything recommended "for the fleet" must have a credible 8 GB path or be explicitly excluded from it. |
| 16 GB | M1, M2, M4 | 3 | The practical working tier. The repo's packaged-app receipts are all from a **Mac mini M4 16 GiB, macOS 26.6.2** (ST-04/08/10/11). |
| 24 GB | M4 | 1 | First comfortable tier for larger models and long-context work. |
| 32 GB | M2 Max | 1 | The ceiling. A **Mac Studio `Mac14,13`** (owner-confirmed 2026-09-24; this file previously said M1 Max in error). Still not a 64/128 GB machine — large-MoE and big-KV-cache plans must be checked, not assumed. |

Total: 7 machines (2 + 3 + 1 + 1).

## Chosen common model — owner decision 2026-09-24

- **One model across the fleet: `mlx-community/Qwen3.5-9B-4bit`** at
  `8b2b98c00a6b4d291155e4890773ca8f769aee53` (uniform 4-bit MLX, 5.98 GB). It fits all three
  **16 GB** Macs (M1/M2/M4) with context headroom and covers outline, chapter-by-chapter
  script, thumbnail and scene/image prompts, contextual cues and metadata. Serve it as **GGUF
  for the `JSON.parse` call sites** and as MLX for prose; keep context at 32–64K. Rationale and
  alternatives: `research/story-text-llm-shortlist.md`.
  *(Revised 2026-09-24: the OptiQ build's index requires its 0.91 GB vision shard, making the
  complete snapshot 8.22 GB; the plain build is the same architecture and chat template at
  5.98 GB. OptiQ stays the optional 24/32 GB quality tier.)*
- **Text-only is accepted.** The single reference-thumbnail vision call stays on the cloud;
  the fallback is to add the tiny `Gemma 4 E4B` (~4.5 GB) beside it if that ever changes.
- **Optional 24 GB quality tier:** `Gemma 4 26B-A4B` QAT 4-bit (15.6 GB) — better prose and
  cheap long context, measured on an M4; `Qwen3.8-27B` 4-bit if prose quality matters more
  than context. Neither is required for the fleet to work.

## Standing brief for text/LLM research

The target workload is the Story Studio KH generation pipeline (see the read-only
sibling checkout's `docs/handoffs-retired/LLM-CALL-SITE-SURVEY.md`). A local model
replacing the cloud LLM must cover, at minimum:

- **Prose**: multi-chapter narration scripts (long-form; previous chapters and a style
  sheet are in context), metadata descriptions/titles/tags, audio expression markup.
- **Strict JSON**: idea arrays; outline (array of chapter slots); the scene planner
  (`{ "scenes": [{ start_sec, end_sec, visual_prompt, ... }] }`); Prompt Director
  scene rewrites; thumbnail prompt sets; scene prompt-pack regeneration; contextual
  cues; research envelopes (summary + claims + citations + outline); metadata-template
  placeholder extraction; format extraction from sample scripts. The app calls
  `JSON.parse` on these, so **a valid-JSON rate below ~100% is a hard functional
  failure, not a style issue.**
- **Vision (one call site)**: reference-thumbnail composition analysis returning a JSON
  object. A text-only pick still needs a separate small VLM for this, or the call stays
  on the cloud.
- **Fit**: the smallest machine that must run a tier decides the recommendation. Report
  each model against 8 / 16 / 24 / 32 GB separately, with on-disk quant size and a
  realistic peak-memory estimate.

### What a research note should state per model

Exact repo id (e.g. `mlx-community/...` or a GGUF repo), total **and active** parameters,
architecture (dense vs MoE), quant on-disk size, peak unified memory per tier, usable
context length, structured-output/JSON reliability, creative-prose reputation, vision or
not, engine support (MLX/`mlx-lm`, llama.cpp/GGUF, Ollama, LM Studio), and the evidence
class for each claim: **measured / vendor claim / inference**. License is recorded once
as a name and not relitigated (see `AGENTS.md`, "Model use scope").

## Known conflicts to reconcile

These older notes disagree with the owner's 2026-09-24 statement; treat the table above
as authoritative and fix the older line the next time it is touched:

- `docs/tasks/ST-05.md:52` says "M4-24 and M2 Max-32" machines — **now consistent** with the
  corrected 32 GB row.
- `research/three-model-rollout-plan.md:28` calls the dev machine "32 GB".
- `research/text-rendering-model-shortlist.md` *previously* listed "M1 16 GB, M2 16 GB
  mini, M4 16 GB mini, M4 24 GB, M1 Max Mac Studio" — it omitted the two 8 GB machines and
  did not state the 32 GB machine's chip decisively. Its line now points here instead.

**Resolved 2026-09-24:** the 32 GB machine is an **M2 Max Mac Studio** (`Mac14,13`), confirmed
from the machine itself during the video shortlist work; the earlier "M1 Max" statement was
wrong. There is no separate M2 Max-32 eighth machine to add — the fleet stands at seven.

## Related

- Model-research notes: `research/story-text-llm-shortlist.md` (text/LLM),
  `research/local-llm-24-32gb-2026-09.md` (24/32 GB deep dive: KV cost, measured M1 Max
  throughput, creative/JSON benchmarks), `research/moe-24-32gb-fit-2026-09.md` (per-model
  MoE fit/quant/speed companion), `research/text-rendering-model-shortlist.md`
  (image text rendering), `research/qwen-image-2-1-adoption.md` (image fit by tier).
- Admission/memory policy in the app: `components/hub/memory_admission.py`.
- Model-use scope (do not raise licensing): `AGENTS.md`.
