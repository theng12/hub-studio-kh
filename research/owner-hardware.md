# Owner hardware fleet — canonical memory for model research

**Purpose.** Every model-fit question (LLM, image, voice, video) is answered against
*specific machines the owner owns*. This file is the one place to read that list, so he
never has to restate it. It also carries the standing research brief for the text/LLM
work, so a new research note starts from the real call sites instead of generic
"best local model" advice.

Owner-stated, 2026-09-24. If reality and this file disagree, the owner's next statement
wins and this file gets fixed — the same rule the handoff uses.

## The fleet

**Corrected by the owner on 2026-09-26, from his own statement:** *"i think i have about 15 units of
16gb machines, 2 unit of 24gb machines and the current 1 unit of 32gb machines. and about 35 units
of 8gb machines."* That is **~53 machines, not 7**, and it changes which tier is the target: the
fleet is dominated by **8 GB (35 units)** and **16 GB (15 units)** — **50 of ~53 machines are 16 GB
or below**, so 16 GB is the tier to qualify against. The 7-machine table this replaces is kept
below as history, because **every measurement recorded in this repo was taken on that subset.**

| Unified memory | Chip(s) | Machines | Notes |
| --- | --- | ---: | --- |
| 8 GB | M1, M2 | 35 | Largest group by count. Owner's own reading: *"i doubt it can [run video] since just regular text to image, it's already hot and struggling."* |
| 16 GB | M1, M2, M4 | 15 | **The tier to qualify against.** The repo's packaged-app receipts are from a Mac mini M4 16 GiB, macOS 26.6.2 (ST-04/08/10/11). |
| 24 GB | M4 | 2 | The fallback tier if 16 GB fails. |
| 32 GB | M2 Max | 1 | The ceiling. A **Mac Studio `Mac14,13`**. Still not a 64/128 GB machine. |

Total: **53 machines (35 + 15 + 2 + 1)**, owner-stated 2026-09-26. **The counts are the owner's own
approximations** — he said "about 15", "about 35" — and they are recorded here as plain integers
because `tests/test_model_record.py` parses this table and compares it to
`qualification/model_record.py::FLEET`; a decorated cell reads as a missing row. The uncertainty
lives in this sentence rather than in the cell.

**Owner's earlier product rule, stated 2026-09-26 and relaxed the same day:** *"i don't need
different models for every machine. one model that can run across the fleet is the best options."*
The qualification ladder from that rule still holds — **16 GB first, and only if that fails, 24 GB**
— but he then relaxed "one model for everything" to **one model per tier** (see the tier plan
below). He also requires that a downloaded package be **kept on each SSD as a backup even if it
fails qualification** — nothing is deleted for failing.

## Legacy fleet — superseded 2026-09-26, kept because the measurements reference it

**Recorded as prose, not as a table, on purpose.** `tests/test_model_record.py` parses the fleet
table out of this file and checks `qualification/model_record.py` against it, so a second markdown
table here reads as a second fleet and fails the guard. The history still matters — every
measurement in this repo was taken on these seven machines — but it must not look like canon.

The superseded count was **7 machines**: 2 × 8 GB (M1, M2), 3 × 16 GB (M1, M2, M4), 1 × 24 GB (M4),
and 1 × 32 GB — the **M2 Max Mac Studio `Mac14,13`**, owner-confirmed 2026-09-24, where this file
had previously said M1 Max in error. Anything in this repo that says "the fleet" and was written
before 2026-09-26 is talking about these seven.

## Chosen common model — owner decision 2026-09-24 (superseded 2026-09-26 by the tier plan below)

**Kept for history.** The tier plan below replaces the single-model preference; the Qwen3.5-9B
4-bit build named here becomes the script/prose model rather than the only text model.

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

## Tier model plan — owner direction 2026-09-26 (to qualify, not adopted)

The owner relaxed the single-model preference: *"it doesn't need to be one model for
everything. a preference doesn't mean you have to match it exactly."* Direction:

| Tier | Model (quantization) | On disk |
| --- | --- | ---: |
| 8 GB | **Granite 4.0 H-Tiny 4-bit MLX** (`mlx-community/granite-4.0-h-tiny-4bit`) | 3.92 GB |
| 16 GB | **LFM2.5-8B-A1B 4-bit MLX** (`LiquidAI/LFM2.5-8B-A1B-MLX-4bit`) | 4.85 GB |
| script/prose | **Qwen3.5-9B 4-bit MLX** (`mlx-community/Qwen3.5-9B-4bit`) — keep until a candidate passes a long-form test | 5.98 GB |

Serve JSON sites as **GGUF + JSON-schema grammar**; MLX has no grammar. Both candidates are
supported by `mlx-lm 0.31.3`. This supersedes the single-model preference for the text model
above, and is **pending qualification**. Qualification list, sizes and sources:
`research/moe-fleet-qualification-handoff.md` and `research/moe-16gb-2026-09.md`.

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
  `research/moe-16gb-2026-09.md` (MoE vs dense for 16 GB; LFM2.5-8B-A1B 4-bit, Granite 4.0
  H-Tiny 4-bit, Qwen3-4B-Instruct-2507 4-bit),
  `research/local-llm-24-32gb-2026-09.md` (24/32 GB deep dive: KV cost, measured M1 Max
  throughput, creative/JSON benchmarks), `research/moe-24-32gb-fit-2026-09.md` (per-model
  MoE fit/quant/speed companion), `research/text-rendering-model-shortlist.md`
  (image text rendering), `research/qwen-image-2-1-adoption.md` (image fit by tier).
- Admission/memory policy in the app: `components/hub/memory_admission.py`.
- Model-use scope (do not raise licensing): `AGENTS.md`.
