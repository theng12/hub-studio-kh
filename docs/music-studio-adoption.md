# Music Studio: adopting a local generator into Studio Hub

> Contract update, 2026-09-21: the first package is
> `ace-step-1-5-turbo` (`music-mlx`). Its verified installer folder owns a
> single `model/` checkpoint root containing only the Turbo DiT, VAE, text
> encoder, and 0.6B planner. The worker requires an explicit pinned ACE-Step
> source root and an interpreter where `acestep` is installed; it never falls
> back to a developer checkout's checkpoints. XL, 1.7B, and 4B choices are not
> offered. ACE-Step startup uses read-only bounded checks, so it cannot download
> the excluded planner or rewrite the installed package. This is a source
> contract, not packaged inference qualification.

Written 2026-09-20, while the Voice/Image/Subtitle studios are at 0.6.37. This is the adoption plan for
the fourth studio the owner asked for ("i want you to help me check for music generator for the local
fleet… i think there's a new one that can generate instrumental for around 6m+ and can be run locally
too").

## The answer, in one paragraph

**ACE-Step 1.5** (`ACE-Step/Ace-Step1.5`) is the model to adopt: MIT-licensed weights *and* code
(ungated), a 10-minute ceiling per request (`DURATION_MAX = 600`), instrumental as a first-class
parameter, and a first-party MLX backend for Apple Silicon. Nothing else in the field fits the
requirement as cleanly — see "What was rejected" for the licence and duration reasons, which are the
two that cannot be engineered around.

## Why not the old Pinokio Music Studio

The old app (`~/pinokio/api/musicstudio-mac`) is useful as a *reference*, not as a source:

- Its catalogue is six MusicGen variants plus `stabilityai/stable-audio-open-1.0` and Suno Bark.
  MusicGen's weights are **CC-BY-NC-4.0**, and its native length is 30 s with extension only as an
  18 s sliding window inside a 30 s receptive field. Stable Audio Open is ~47 s and gated. Neither
  reaches a six-minute brief.
- **No music weights were ever downloaded on this Mac** (`musicstudio-mac/cache` = 0 B). There is
  nothing to reuse and nothing that has ever run locally.

What *is* worth copying from it (already reflected in this plan): a worker that pins `HF_HOME` so it
finds the same cache the fleet already has; `PYTORCH_ENABLE_MPS_FALLBACK=1` for diffusers pipelines
that hit ops with no Metal kernel; `_detect_device()` preferring MPS; the "verify the imports, then
announce success" install step; and its parameter conventions (`do_sample`, guidance, seed per chain,
crossfade between chained segments).

## The integration, in the shape the app already uses

The Hub was built modality-general, and the standalone reboot kept the table:

```python
# components/hub/backend/broker.py
MODALITY = {
    "image": ("/api/generate/txt2img",   "prompt", "image"),
    "music": ("/api/generate/txt2music", "prompt", "audio"),   # already here
    "voice": ("/api/generate/txt2speech","text",   "audio"),
    "video": ("/api/generate/txt2video", "prompt", "video"),   # already here
    "render":("/api/generate/render",    "label",  "video"),
}
```

So the Hub scheduler, priority classes (`music` already counts as an audio modality, which outranks
image), routing, memory gate, batch ledger, artifacts and job pages need **no new concept** — they
need a worker that answers `/api/generate/txt2music`. What is missing is:

| piece | what it is | where |
|---|---|---|
| worker | FastAPI app exposing `/api/generate/txt2music` + `/api/catalog` + `/api/cache` + job state | `components/music/app/backend/main.py` |
| catalogue | `CATALOG` of music models with `family`, `size_gb`, `min_unified_memory_gb`, `capabilities`, per-family `MusicParams` (duration ceiling, instrumental, guidance) | `components/music/app/backend/catalog.py` |
| generation | one job runner, sectioned/continued for long pieces, resource telemetry, cancellation | `components/music/app/backend/generation.py` |
| launcher | starts the worker as its own process and state root, the way Subtitle does | `packaging/music_worker.py` |
| registration | the Hub's worker map gains one entry per place it names engines | `components/hub/standalone.py` (2 places), `model-manifests/*.json` engine `music-mlx` |
| staging | copy the component and manifests; reuse the shared runtime bytes | `packaging/stage_voice.py` |
| page | a Music page in the native app: prompt, duration, instrumental switch, model, history | `native/StudioViews.swift` (+ `StudioFunction.music`) |
| storage | `data/music` output root, joining the 50 GB / 30 day rule | `packaging/hub_worker.py` spec list |

Checked in the current source, so this is a fact rather than an expectation:

- `_eligible_studios()` selects on `s["modality"] == modality`, with no allowlist — a worker that
  registers `modality: "music"` is eligible for music batches the moment it is up.
- `capabilities.OPERATION_BY_MODALITY` already publishes `music.generation` (and `video.generation`,
  `chat.completion`), so the public capability contract needs no new operation either.

**Runtime reuse is the important simplification.** The Voice runtime already carries `mlx`,
`mlx_audio`, `torch`, `diffusers`, `transformers`, `soundfile` and `numpy` — everything the old Music
Studio installed and everything an MLX or diffusers music model needs. Subtitle already proves the
pattern of a second worker reusing the shared runtime bytes with its own state root, so Music should
do the same rather than add another ~1.5 GB runtime to the bundle. Only genuinely missing packages are
added to the existing runtime at build time.

## Long-form for music is not the TTS problem

Voice sections a script at sentence boundaries and rejoins the audio. Music has no sentences and the
model itself supports long single passes, so the shape is different:

- ACE-Step takes `duration` in seconds up to 600 in one request; a six-minute piece is **one call**,
  not a chain.
- Beyond 600 s, the documented mechanism is **repaint**: generate in 3–90 s segments that each see the
  previous segment's context and concatenate. If the owner wants longer than 10 minutes, that is the
  path, and it belongs in the model's own `long_form_strategy` (the field the Voice catalogue already
  publishes), not in the TTS section policy.
- The published contract should therefore be a **duration ceiling per model**, not a character budget:
  `max_duration_seconds`, and `long_form_strategy: "single_pass" | "repaint"`.

## What has to be settled by measurement, not by reading

The research produced two contradictions that only a real run can settle, and one that is a business
call:

1. **Does the MLX backend work at 16 GB?** The project's own macOS instructions say yes and list a
   12–16 GB tier; an independent 16 GB M4 report says MLX "fights PyTorch for memory and crashes" at
   that size. Prototype in progress; the result decides whether the fleet's 8/16 GB Macs get MLX or
   the MPS/PyTorch fallback.
2. **Wall-clock for six minutes.** One third-party measurement puts a 70 s instrumental at ~300 s on a
   16 GB M4 with the 2B turbo tier, scaling linearly — i.e. roughly 26 minutes for six minutes of
   music. That would make the 24/32 GB Macs the practical music machines and this Mac a testbed. Must
   be measured here before promising anything.
3. **Licence fit** is the owner's call: ACE-Step is MIT (no threshold, no attribution clause), which
   is why it is the recommendation. Stable Audio 3 Medium (380 s single pass, official pure-MLX) is
   the quality alternative but is gated under the Stability Community Licence ($1M revenue
   threshold). MiniMax Music 3 is a 24 GB-class model with a mandatory UI attribution clause.

## Order of work

1. Prototype ACE-Step 1.5 here: short instrumental, then one six-minute instrumental, recording
   wall-clock and peak memory. **Done or abandoned on evidence, not on optimism.**
2. Add the manifests and the worker for the tier that fits, then register it with the Hub.
3. Native Music page.
4. Only then decide whether the fleet needs the repaint path (>10 minutes) or the 24 GB machines need
   a bigger tier.

## What was rejected, and why

| model | reason |
|---|---|
| YuE2 | weights are CC BY-NC 4.0 despite the Apache-2.0 tag; needs 24 GB VRAM + CUDA/FlashAttention |
| YuE v1 | Apache-2.0 and genuinely 5-minute, but lyrics-first, 8B total, no MLX or MPS port |
| DiffRhythm 2 | Apache-2.0 and small, but its README leaves "song extension" **and "instrumental music generation"** unchecked |
| DiffRhythm v1 | 4 m 45 s maximum, local deployment still a TODO |
| MusicGen family | CC-BY-NC weights; 30 s native |
| Stable Audio Open 1.0 / Small | ~47 s / ~11 s; gated |
| MAGNeT, AudioLDM 2, Tango 2, MelodyFlow | all 30 s class, most CC-BY-NC |
| MiniMax Music 3 | 6:00 at the boundary, 9.2 GB of 4-bit weights, 24 GB-class, attribution clause |
| SongGeneration v2 / "LeVo 2" | benchmark-leading but the canonical repo 401s and the mirrors declare `license: unknown` |
| Magenta RealTime 2 | a live streaming instrument (40 ms frames), not an offline renderer |
| HeartMuLa-oss-3B | 4-minute default, lyrics-first |
