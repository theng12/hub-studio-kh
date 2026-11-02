# The 8 GB image run - double-click, walk away

Two image models on an 8 GB Mac, through Studio Hub: **Z-Image-Turbo 4-bit** (the thumbnail model) and **FLUX.2 Klein 4B 4-bit**
(the scene model), in that order. It answers two questions: does image generation on an 8 GB Mac have the same memory pressure and
swap as the 8 GB text-to-speech test showed, and **does using it all day wear the Mac's internal SSD** (swap is written to the SSD).
Labels: **[M]** measured, **[I]** inference.

**On the 8 GB Mac** (Studio Hub 0.6.125 or later installed, the two image models put there by the kit's copy step):

1. Close your other apps, plug the Mac in, and open Studio Hub (leave it open).
2. On the kit drive, open `1 Install Apps` > `Studio Hub` > `Technical` and double-click
   **`RUN-8GB-IMAGE.command`**.
3. Leave the Mac alone. When it says "Finished", read `summary.txt` in the new `Image 8GB Run <date>` folder beside the script
   (or send the whole folder back).

Keep `RUN-8GB-IMAGE.command`, `eight-gb-image-run.sh`, `eight_gb_image_run.py` and `ssd_wear.py` together (the kit's Technical
folder has all four). If Finder refuses the double-click ("unidentified developer"), right-click it > Open, once.

This is the image twin of `RUN-8GB-TTS.command` and measures the same way. It replaces the older `RUN-8GB-Z-IMAGE.command` (mflux run
directly, not through Studio Hub), which is retired from the kit; that run's result is the comparison line in the summary.

## What it runs

Each image is a Studio Hub job (`POST /api/hub/jobs`, modality `image`) - the route the Gateway and Story Studio use - so the Hub's
own scheduler and Image worker run it exactly as in production. **Three images per model, one at a time**, seeds 42, 43, 44, so you
can see whether swap piles up over consecutive jobs. The sizes and steps are the ones Story Studio gets (it sends none of its own;
the model's published defaults apply):

| Model | Used for | Size | Steps | Memory mode on this Mac |
| --- | --- | --- | --- | --- |
| Z-Image-Turbo 4-bit (`z-image-turbo-mflux-4bit`) | thumbnails | 1280x720 (16:9) | 9 | low-RAM (the Image worker uses it on every Mac below 24 GB) |
| FLUX.2 Klein 4B 4-bit (`flux2-klein-4b-mlx-4bit`) | scenes | 1280x720 (16:9) | 4 | bounded (staged + streamed; the Image worker uses it below 12 GB, a lean cache-capped one below 24 GB) |

The Image worker drops the model after every job, so every image loads it again; the first image of each model also finds the
file cache cold. The prompts are fixed (a title-text cover for the thumbnails, a village night scene for the scenes).

- **It does not wait for memory.** Before each model it tells the Hub to **bypass the free-memory guard** for that model
  (`PUT /api/hub/memory/admission`, Hub 0.6.125+; the requirement goes to 0 GB) and just runs it. It first asks the Hub to
  release the idle Studios' models (never a running job) and records the free memory and what holds the rest.
- **It never stops an image because the Mac is swapping.** It records the peak memory, swap and memory pressure.
- **Every image has a hard time limit: 15 minutes** (`IMG8_TIMEOUT`, seconds). One still going then is cancelled and written down as
  **timed out**; the script waits for the worker to finish the image it was on before moving on.
- **A crash is a result, not a stop.** A dead worker, out-of-memory or an error is written down with the error text; that model's
  other images are skipped and the **next model goes on**.
- **It always puts the setting back** after each model and on any exit (Ctrl-C, closing the window, an error). If the whole run was
  killed, the next run restores it first (kept in `image8-guard-restore.json` beside the Hub's `hub-ready.json`). The Hub's own
  default numbers are never edited.
- It does nothing while the Hub is busy with fleet work (it waits up to 30 minutes, then stops), installs nothing and deletes nothing.

**How long:** roughly 15 to 60 minutes **[I]**: the earlier standalone run took 336 s for one Z-Image-Turbo 4-bit image on an 8 GB M1
**[M]**, so six images plus loading is about half an hour to an hour. The worst case is 15 minutes per model (one timed-out image per
model, then it moves on) plus loading.

## What it measures, per image (`results.csv`, one row per image)

`model`, `quant`, `role` (thumbnail/scene), `image`, `seed`, `resolution`, `steps`, `low_ram`, `memory_mode` (what the Image worker says
it ran), `status`/`error`, `generation_seconds`, `peak_memory_gb` (the Image worker's footprint, macOS `footprint`, sampled every
second), `mlx_peak_active_gb` (MLX's own peak, from the worker's job record), `worker_lifetime_peak_gb`, `memory_pressure`,
`swap_before_gb`/`swap_after_gb`/`swap_peak_gb`/`swap_delta_gb`, `mem_free_pct_min`, `free_gb_before`, `memory_released`,
`memory_holders`, `guard_relaxed`, `hub_free_floor_gb`, `responsive`/`max_stall_s`, `worker_restarts`, `verdict` - the same columns
as the TTS results where they apply - and the SSD columns from `ssd_wear.py` (shared with the TTS runner):

- `ssd_written_gb`: bytes written to this Mac's **internal** SSD while the image ran (`ioreg`, no sudo; the external kit SSD and the
  SD reader are not counted). Decimal GB.
- `swapouts_gb`: `vm_stat` swapouts in that window.
- `ssd_written_baseline_gb_per_min`: what the idle Mac writes anyway, sampled for 30 s before the first image
  (`IMG8_BASELINE_S`; 0 skips it). `ssd_written_net_gb`: gross less baseline x the image's duration.
- `cumulative_ssd_written_gb`, `cumulative_ssd_written_net_gb`, `cumulative_swapouts_gb`: running totals over that model's images,
  so you can see them pile up (they start over for the next model).

Two cautions the summary repeats where they apply: **run it with nothing else going** (any other process's CPU, swap and disk writes
land in the same window; the summary flags images measured while the Mac was busy), and `mlx_peak_active_gb` is the Image worker's
**all-time** MLX high-water mark - the worker never resets it - so the second model's figure can carry the first model's peak; the
sampled `peak_memory_gb` and `footprint_before_gb` (what the worker already held when the image started) show what is leftover.

## The summary (`summary.txt`)

Per model, in plain words: seconds per image, peak memory, swap, memory pressure, a verdict ("fits", "completed but strained this
Mac", "only with the memory guard bypassed", "does not fit"), the swap and SSD writes image by image with the running total, then
**"≈ X GB written to this Mac's SSD per image"** and **"at 200 images a day that is ≈ Y TB a year"**. 200 images a day is an
**assumption for illustration** (`IMG8_PER_DAY=<n>` changes it) and Y assumes every image costs what these three did. It says how
much would be written, not when a drive wears out - this run does not know the SSD's rated endurance.

## Options

`bash eight-gb-image-run.sh --quick` is the script's self-test (1 image per model at 1024x576, 2 steps; labelled "not the test"),
`--models zimage,klein` picks models, `--images 5` sets images per model, `--allow-non-target` runs the full test on a Mac that is not
8 GB (the summary then says it is not 8 GB evidence; without it the script refuses). Environment: `IMG8_TIMEOUT` (900 s),
`IMG8_IMAGES` (3), `IMG8_PER_DAY` (200), `IMG8_RELAX_FREE_GB` (0), `IMG8_BASELINE_S` (30), `IMG8_SETTLE_S` (600), `IMG8_OUT` (results
folder), `IMG8_READY_FILE`, `STUDIOHUB_APP`.

## What it checks first (and says in plain words if it cannot go on)

Not an 8 GB Mac: it refuses (`--quick` and `--allow-non-target` still run). Studio Hub not running or older than 0.6.125: it says
so. A model missing or not verified: it names the kit step to use (`2 - Copy the models` / `Update this Mac`). A refusal writes nothing.
