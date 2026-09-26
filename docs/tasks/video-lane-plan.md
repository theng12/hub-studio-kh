# The video lane — scoped plan

Written 2026-09-26, after the owner's decisions. Status: **scoped, not started.** This is the last
gate before distribution, alongside "all known bugs fixed".

## The owner's decisions, recorded

1. **Ship video to the 24/32 GB machines now; chase 16 GB afterwards.** The lane is built with a
   per-model **memory tier**, so the gateway offers video only to locations whose machines fit. A
   16 GB pass is an open qualification, not a blocker.
2. **The 16 GB question is settled on real hardware**, on one of his 16 GB Macs — not by inference
   from the 32 GB measurement.

## Why this is a build, not a finish

**There is no video lane.** `components/` is `chat, hub, image, music, subtitle, voice`; the gateway
publishes **9 cloud video rows and 0 local** (`/v1/videos/models`, measured 2026-09-26). What exists:

- `research/video-fleet-shortlist-2026-09-24.md` — the model survey, with every memory claim labelled.
- `qualification/ltx-video-measure.py` + `sixteen-gb-ltx-video-run.sh` — a standalone runner, and
  **one successful run** (`ltx-video-results-20260926-32gb-first-success`).
- The model on disk: `ltx-2.3-22b-ram-12gb-mlx`, 17.81 GiB, `baa-ai/LTX-2.3-22B-RAM-12GB-MLX` rev
  `d8a9d2f6081c4136130983a0b612962418ddf203`.
- `components/hub/standalone.py:70` — **`MODALITY_DEADLINE_FLOOR_S["video"] = 7200.0`**, justified by a
  measured LTX run (44:38 for q8 HQ on an M2 Pro 32 GB). The scheduler already expects a lane that
  does not exist.

## The model, and what it costs

| | |
| --- | --- |
| Model | `baa-ai/LTX-2.3-22B-RAM-12GB-MLX`, I2V + T2V, with audio |
| Measured here | **298.5 s** wall, 704×448, 97 frames, 4.0 s **with audio** |
| Peak footprint | **16.38 GB process**, peak MLX Metal 13.19 GB, RSS 9.91 GB |
| Runs on | **24 GB and 32 GB**, per that measurement. **16 GB is a vendor claim we contradict** |
| Not on | 8 GB, and 16 GB until a physical test says otherwise |

## The shape to copy

`components/image/` is the smallest complete lane — `__init__`, `main`, `generation`,
`memory_policy`, `loading_mode` — and the closest fit: one model, a file artifact, a per-job release.

**Three things the qualification runner already learned, and the lane must not relearn:**
1. **Use the pack's own loader** (from its `generate.py`), not stock `apply_quantization` — that
   applies ONE bit width to a mixed 2–8-bit pack.
2. **`--low-ram-streaming` does not work on this pack** — `ValueError: Received 2 parameters not in
   model: video_to_audio_attn.to_gate_logits.{biases,scales}`. It is opt-in for that reason.
3. **Search the drives before `~/StudioHubModels`** — a 10 GB partial copy on the internal disk beat
   the complete 17.81 GiB copy on NOVA, and the failure reads as a corrupt model.

## The work, file by file

| # | File | What |
| --- | --- | --- |
| 1 | `components/video/app/backend/{__init__,main,generation,memory_policy}.py` | The worker: `/api/health`, a generate route, the pack loader, per-job release |
| 2 | `model-manifests/ltx-2-3-22b-ram-12gb-mlx.json` | Exact revision, files, sha256, **`memory_tier`**, the measured peaks above |
| 3 | `packaging/hub_worker.py` | `_video_spec` + the worker map entry |
| 4 | `packaging/stage_voice.py` | Stage the component; add a `smoke_imports` entry |
| 5 | `components/hub/standalone.py` | Register worker → `video` modality (the deadline floor is already there) |
| 6 | `gateway/adapters/studio_hub.py` + `catalog.py` | Route the `video` modality; publish local rows on `/v1/videos/models`; a video `lane_contract` (artifact form, terminal vocabulary, what a cancel does not promise) |
| 7 | tests | **Six files assert video's absence and must flip:** `test_hub_modality_vocabulary.py`, `test_hub_model_packages.py`, `test_jobs_tabs.py`, `test_fleet_nodes.py`, `test_hub_job_deadline.py`, `test_chat_packaging.py` |

**The memory tier is the load-bearing part.** Without it the gateway offers video to an 8 GB Mac and
the failure is a node-side load error instead of a routing decision. `fleet.route` already reads
per-model supply, so the tier is data the manifest publishes and the router honours — the same shape
as the existing per-model capacity weighting.

## Verification, in order

1. **Worker alone** — one I2V generation on this Mac through the worker, peak footprint recorded, to
   prove the lane reproduces the qualification runner's numbers.
2. **Through the chain** — Story's I2V surface → gateway → Hub → this Mac, artifact fetched, the
   whole path the owner will use.
3. **Tier gating** — assert the gateway does **not** offer video to an 8/16 GB location, and does to
   a 24/32 GB one.
4. **The 16 GB test the owner approved** — on one of his 16 GB Macs. Candidates that were reachable
   on 2026-09-26: `studio-mac-1201-m2-16-256-hub` (M2, 16 GB), `studio-mac-1204-m1-16-512-hub`
   (M1, 16 GB). This needs either shell access to that Mac or the lane dispatching a real job to it —
   which is what step 2 makes possible.

## What this does not do

- It does not make video work on 8 GB machines: **no released I2V model fits 8 GB** (Lance 3B is the
  only 8 GB option and it is T2V-only, and I2V is the owner's hard requirement).
- It does not settle 16 GB — that is step 4, on hardware.
- It does not touch the drives' reserved empty `video-models/` shelf; a kit refresh is a whole-kit
  action, as the handoff records.
