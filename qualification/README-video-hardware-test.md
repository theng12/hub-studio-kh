# Video tests — LTX-2.3 22B RAM-12GB (mixed 2-8 bit)

Two double-clickable tests in this folder. They measure the same model and answer different
questions, and the difference decides which one you want.

| File | Drives | Answers |
| --- | --- | --- |
| `RUN-LTX-VIDEO-WORKER.command` | the shipped **Video worker** — the same app the Hub talks to, through its own routes | Does the **lane we ship** run on this Mac, and what does the lane cost? Includes the refusal when the Mac is below the tier. |
| `RUN-16GB-LTX-VIDEO.command` | the standalone qualification runner, bypassing the lane's memory tier | Can this model produce a video on this Mac **at all**, and what does it cost? |

Both print the same headline numbers: wall-clock seconds, peak MLX Metal allocation, peak
process footprint (the statistic that counts), and the MP4's real duration and track counts.
Each writes a results folder beside itself and changes nothing else — nothing is installed,
moved or deleted.

**Run them one at a time, and quit Studio Hub first.** Both load ~19 GB of weights; a worker
doing something else at the same time splits the memory and the number then describes the
machine's afternoon rather than this run.

---

## What to expect, by Mac

The manifest publishes a **24 GB** minimum for this model, measured here on a 32 GB M2 Max
(peak process footprint 16.38 GB [M]) — the vendor's own card claims 16 GB [V], and the
measured run is why the lane does not offer 16 GB. **The canvas the owner accepts (1024×576 true
16:9, 2 s with audio) was measured through the worker on 2026-09-28: 16.92 GB peak footprint against
10.05 GB on the MLX allocator — see the section near the end.** Read the footprint: the allocator
alone would suggest a 16 GB Mac fits, and it does not.

| This Mac | `RUN-LTX-VIDEO-WORKER.command` | `RUN-16GB-LTX-VIDEO.command` |
| --- | --- | --- |
| 32 GB | should complete | should complete |
| 24 GB | should complete — **this is the machine nobody has measured yet** | should complete |
| 16 GB | **refuses**, by design, before loading anything — now confirmed by the accepted canvas's own 16.92 GB footprint | still the open question: the standalone script runs the model outside the lane's tier |
| 8 GB | refuses | expected to fail or swap hard; informative only |

A refusal from the worker test is that test passing, not failing: it is the lane declining to
start a job it would have to swap through. The refusal is recorded with its reason in
`measurement.json`, so it is evidence either way.

---

## Requirements

**`RUN-LTX-VIDEO-WORKER.command`** needs, on the Mac being measured:

1. **Studio Hub installed.** The test runs the app's own components
   (`/Applications/Studio Hub.app/Contents/Resources/components`) with the app's own Python 3.11
   video runtime (`…/Contents/Resources/runtimes/video`). Nothing else is needed — no checkout,
   no clone.
2. **The model in `~/StudioHubModels`**, the way the app installs it
   (`~/StudioHubModels/ltx-2-3-22b-ram-12gb-mlx/<revision>/`) together with its required
   companion **gemma-3-12b-it-4bit** (`~/StudioHubModels/gemma-3-12b-it-4bit/`). Install both
   from the app's model list. The first run hashes them once and records a receipt, which takes
   a few minutes; later runs skip it.

**`RUN-16GB-LTX-VIDEO.command`** needs the weights, and finds them on the SSD by itself:

```
<drive>/Video Generation Kit/Studio Hub KH/model-library/ltx-2-3-22b-ram-12gb-mlx/<revision>/model
```

That is the kit's **own** library — the one `verify-kit.sh` checks and the one that reports 30 of 30
on every drive. It is tried first, and a copy is used only if it is **complete**.

> **Corrected 2026-09-28.** This file named `/Volumes/KH-STUDIO-*/model-library/video-models/ltx-2.3-22b-ram-12gb-mlx`,
> and the script looked there — a **stale duplicate that nothing verifies**. Measured the same day,
> its package count was 43/35/35/34 across the four drives while the kit's library was **31 every
> time**, and on ATLAS and ORICO its `transformer-distilled.safetensors` was **1.86 GB where the pack
> ships 9.97 GB**. Because the glob expands alphabetically the script chose **ATLAS** and died 24 s
> in with *"invalid data offsets … Perhaps an incomplete download or corrupt file?"* — which reads as
> a corrupt model when the truth is a truncated copy on a drive. The script warned about the size and
> ran anyway; it now treats an incomplete copy as **ineligible** and refuses rather than producing
> that error. Found by running the harness end to end, which had never been done.

It also needs a Python 3.11 runtime that can import `mlx.core` and `ltx_pipelines_mlx`. On a Mac
with the app installed that is already true — the app's video runtime is exactly that
environment, and the script now uses it. If it reports the runtime missing, it prints the recipe.

---

## Running them from Terminal instead

Double-clicking runs the defaults. Everything below is optional.

```sh
# through the shipped worker, text-to-video, the lane's own defaults
sh "/Volumes/KH-STUDIO-NOVA/Video Generation Kit/Studio Hub KH/Technical/RUN-LTX-VIDEO-WORKER.command"

# through the shipped worker, image-to-video (the heavier door; scene-first)
STUDIOHUB_LTX_IMAGE=~/Desktop/scene.png \
  sh "/Volumes/KH-STUDIO-NOVA/Video Generation Kit/Studio Hub KH/Technical/RUN-LTX-VIDEO-WORKER.command"

# a different configuration of the same run
STUDIOHUB_ASPECT=16:9 STUDIOHUB_RESOLUTION=288p STUDIOHUB_DURATION=2 \
  sh "/Volumes/KH-STUDIO-NOVA/Video Generation Kit/Studio Hub KH/Technical/RUN-LTX-VIDEO-WORKER.command"
```

The knobs that are read: `STUDIOHUB_LTX_IMAGE`, `STUDIOHUB_PROMPT`, `STUDIOHUB_ASPECT`,
`STUDIOHUB_RESOLUTION`, `STUDIOHUB_DURATION`, `STUDIOHUB_MODELS_DIR`, `STUDIOHUB_HUB_ROOT`,
`STUDIOHUB_VIDEO_RUNTIME` (worker test); `STUDIOHUB_LTX_MODEL`, `STUDIOHUB_LTX_RUNTIME`,
`STUDIOHUB_LTX_IMAGE`, `LTX_FRAMES`, `LTX_WIDTH`, `LTX_HEIGHT` (standalone test).

---

## What to send back

The results folder each run prints, or just the `measurement.json` inside it. For a refusal,
that file is the whole answer; for a completed run it carries the numbers above plus the
artifact's checksum.

---

## Lance — the owner's verdict, 2026-09-28: not pursued, and no run needed

**Update 0.6.120 (2026-09-30): Lance is retired** - the runner, its runtime, both manifests and the
whole Lance path were removed from the Hub; the two package ids are in `components/retired_models.py`.
The measurements below stay as history.

**Decision: Lance is not pursued.** The owner tried the quality himself and it does not hold up
(*"lance sucks. can't generate good enough quality"*), so **the 16 GB Lance run this file used to ask
for is cancelled** — do not spend a drive session on it. **Nothing is removed:** the runner stays
adopted, pinned and staged, the two packages stay in the catalog, and the measurements below stay
published, because a model kept as a known spare is a different thing from a model being recommended.

The measurements as they stand, for the record — **Lance 3B Video bf16**
(`mlx-community/Lance-3B-Video-bf16`), whose own card claims 8–16 GB, adopted in Hub 0.6.82
(`packaging/lance-runtime`, pinned at `f8ecc5a8…`, staged as `runtimes/video-lance`), measured on
**this** 32 GB Mac: **8.11 GB** peak footprint in `relay` mode at 256×256 × 13 frames, 184.9 s, and
**20.0 GB** in `parallel` at 25 frames, 329.5 s — both by sampled `footprint phys_footprint_peak` in
the process doing the work. Below ~18 GiB the runner resolves to `relay`, so 8.11 GB is what a 16 GB
Mac would have seen. The weights are on the SSD shelf
(`model-library/video-models/lance-3b-video-bf16`).

---

## The canvas the owner accepts: 1024×576 true 16:9, and what it costs

**Verdict, 2026-09-28:** `576p` `16:9` (1024×576) with audio is *"close to acceptable … i think it's
ok"*. That makes it the configuration the fleet plans around, and it is the one to quote.

Measured through the shipped worker's own job path on this 32 GB M2 Max — image-to-video, 49 frames
at 24 fps, 2.04 s of output **with an audio track**, job `e9caa8c4ce2a4074a29d2349a99193ce`,
receipt `qualification/ltx-video-worker-results-20260928-004554/`:

| Statistic | Value |
| --- | --- |
| **Peak process footprint** (`ri_phys_footprint`, 1 Hz) | **16.92 GB** (16,919,438,528 B) |
| Peak MLX Metal allocation | 10.05 GB (10,046,030,772 B) |
| Peak process RSS | 9.25 GB |
| Wall clock | 258.0 s |
| Artifact | 2,055,736 B, one video + one audio track |

**Read the footprint, not the allocator.** The MLX number is about 10 GB, which would *look* like a
16 GB Mac fits; the process footprint at the same moment is **16.9 GB**, because the 17.81 GB of
mixed 2–8 bit weights load outside what that counter tracks. The published tier therefore stays
**24 GB minimum / 32 GB recommended**, and **16 GB is not offered this model**. For comparison, the
shorter 704×448 4 s canvas measured 13.19 GB MLX / 15.71 GB footprint in 304.0 s: the footprint barely
moves between canvases because the weights dominate, while the allocator moves with frames per pass.

**One environment note from the run.** The first attempt at this canvas died at 15 % with
`[METAL] Command buffer execution failed: Impacting Interactivity` after 13 s — macOS's GPU watchdog
firing on a command buffer that held the GPU too long while the display wanted it. The identical
command succeeded on retry (258.0 s, `done`). If a video job fails that way, **retry once before
believing it**; it is contention with whatever else is drawing, not the lane.

