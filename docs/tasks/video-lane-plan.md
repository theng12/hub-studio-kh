# The video lane — scoped plan

Written 2026-09-26, after the owner's decisions. Status: **scoped, not started.** This is the last
gate before distribution, alongside "all known bugs fixed".

## The owner's decisions, recorded

1. **VIDEO-1 is revised: image-to-video is no longer required — text-to-video is accepted.** That is
   what makes the small tier reachable, because the only 8 GB-class video model is T2V-only. This
   supersedes the earlier "I2V is a hard requirement" answer.
2. **Two models, two tiers.** Lance 3B (T2V) for the small machines; LTX-2.3 22B (I2V + T2V, with
   audio) for the 24/32 GB ones. The lane is built with a per-model **memory tier** so the gateway
   offers each model only where it fits — the tier is the load-bearing part, not a nicety.
3. **Both tiers are tested on real hardware**: an 8 GB Mac and a 16 GB Mac, not by inference from
   the 32 GB measurement.

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

## Model A — Lance 3B Video (the small tier)

`mlx-community/Lance-3B-Video-bf16`, Apache-2.0, **alpha**. Port of ByteDance's `Lance_3B_Video`
video specialist. Everything below is the **model card's own measurement** `[V]` — none of it has
been reproduced here yet.

| | |
| --- | --- |
| Weights | `model.safetensors` 12.87 GB + `vit` 1.34 GB + `vae` 1.41 GB ≈ **15.6 GB** |
| Task | **T2V only** (video_edit and x2t_video are "implemented, not validated") |
| Runner | the **`lance-mlx` source repo** (`github.com/xocialize/lance-mlx`), not a package |
| Small-Mac path | `memory_mode=relay` (auto below ~18 GiB) — sheds the understanding tower after prefill and frees the generation tower before VAE decode, so peak ≈ the heaviest single phase. **Frame-MD5-verified byte-identical to the resident `parallel` mode** |
| Lossless decode | streaming VAE decode is **flat in frame count**: 256²×49f **7.99 GB**, 256²×121f **8.05 GB**, 512²×61f **12.64 GB** |
| Quality ceiling | production through **768²×25f**; **painterly by design, not photorealistic**; at n_lat ≥ ~30k (768²×49f) the VAE produces mesh artifacts and it is unusable |
| Speed | M5 Max 128 GB: 256²×16f ~33 s, 512²×16f ~60 s, 768²×13f ~145 s — **measured on a machine far above this fleet; do not transfer** |

**The caveat to settle before believing the 8 GB half.** The card's envelope table says *"8–16 GB,
relay"*, and its worked example is *"256²×61f in ~8.7 GB"*. On a literal 8 GB Mac that peak is
**larger than the machine's whole RAM**, and the smaller 7.99 GB configuration leaves nothing for
macOS. Its figures come from `ri_phys` — true OS-committed footprint, *"supersedes the older
`mx.get_peak_memory()` quotes which under-report ~2×"* — which is the same discipline this project
insists on, and it is why 8.7 GB should be read as 8.7 GB rather than as marketing.

**So the honest read is: Lance is credible on 16 GB, doubtful on 8 GB.** That is precisely why both
machines get a real test, and it is the first thing the test should answer.

## Model B — LTX-2.3 22B (the large tier)

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
| 2 | `model-manifests/ltx-2-3-22b-ram-12gb-mlx.json` + `lance-3b-video-bf16.json` | Exact revision, files, sha256, **`memory_tier`** (Lance small, LTX large), the measured peaks |
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
4. **The hardware tests the owner approved — both tiers.** One **8 GB** Mac and one **16 GB** Mac:
   * 16 GB → LTX (I2V + T2V) and Lance (T2V): does the vendor claim hold?
   * 8 GB → Lance only, at 256² first: **does it actually fit, or does it swap?** This is the single
     most valuable number in the whole plan, because ~35 of the owner's ~50 machines are 8 GB.
   Candidates reachable on 2026-09-26: `studio-mac-1201-m2-16-256-hub` (M2, 16 GB),
   `studio-mac-1204-m1-16-512-hub` (M1, 16 GB). Needs either shell access to those Macs or the lane
   dispatching a real job to them — which is what step 2 makes possible.

## What this does not do

- It does not promise 8 GB. Lance's own envelope groups 8–16 GB together while its measured peaks
  (7.99–8.7 GB) exceed an 8 GB machine's RAM — the test decides, not the table.
- It does not promise photorealism on the small tier: Lance is **painterly by design**, and its
  production envelope ends at 768²×25f.
- It does not settle 16 GB — that is step 4, on hardware.
- It does not touch the drives' reserved empty `video-models/` shelf; a kit refresh is a whole-kit
  action, as the handoff records.
