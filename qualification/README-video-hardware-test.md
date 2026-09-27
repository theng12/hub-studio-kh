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
measured run is why the lane does not offer 16 GB.

| This Mac | `RUN-LTX-VIDEO-WORKER.command` | `RUN-16GB-LTX-VIDEO.command` |
| --- | --- | --- |
| 32 GB | should complete | should complete |
| 24 GB | should complete — **this is the machine nobody has measured yet** | should complete |
| 16 GB | **refuses**, by design, before loading anything | the open question — this is the run worth having |
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
/Volumes/KH-STUDIO-*/model-library/video-models/ltx-2.3-22b-ram-12gb-mlx
```

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

## Lance on 16 GB — measured here, and the 16 GB verdict is still yours

The 16 GB target is **Lance 3B Video bf16** (`mlx-community/Lance-3B-Video-bf16`), whose own card
claims 8–16 GB. Its runner **is adopted** (Hub 0.6.82: `packaging/lance-runtime`, pinned at
`f8ecc5a8…`, staged as `runtimes/video-lance`) and the lane is measured on **this** 32 GB Mac:
**8.11 GB** peak footprint in `relay` mode at 256×256 × 13 frames, 184.9 s, and **20.0 GB** in
`parallel` at 25 frames, 329.5 s — both by sampled `footprint phys_footprint_peak` in the process
doing the work. Below ~18 GiB the runner resolves to `relay`, so 8.11 GB is what a 16 GB Mac sees.

**What is still not measured is a 16 GB machine**, and that is the run only you can do: the figure
above is a floor from a host under no memory pressure, and the card's own claim stays `[V]`. The
weights are on the SSD shelf (`model-library/video-models/lance-3b-video-bf16`) and a hard-linked
installed layout exists at `model-library/_installed` for qualification runs (`--models-dir`).
