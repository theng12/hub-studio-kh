# Image generation memory pressure — method and baselines

Date: 2026-09-24. Purpose: answer **can the 8 GB M1/M2 machines generate images?**
This note records the method, the raw numbers, and the limits of what has been
measured. Evidence class per figure: **[M]** measured, **[V]** vendor claim,
**[I]** inference.

## Why a 32 GB measurement cannot answer the 8 GB question

mflux caps its MLX buffer cache only when told to. By default the cache grows to use
whatever RAM is free, so a peak measured on a big machine is **not** the model's
demand — it is the machine's spare memory being used as cache. The relevant flags
(`mflux-generate-* --help`):

- `--mlx-cache-limit-gb <n>` — cap MLX cache usage (e.g. `2.5`), without full low-RAM mode.
- `--low-ram` — reduce memory use at a performance cost (implies a cache limit and
  defers VAE decode work).

This is consistent with the apparent contradiction that the fleet runs FLUX.2 Klein
on 8 GB while the same model measures 19–22 GB footprint on a 32 GB machine: that
footprint is largely reclaimable cache, and `time -l` counts it.

## Method

Run every generation through `.build/text-tests/measure-image-memory.sh`, which records:

| Metric | Source | Why it matters |
| --- | --- | --- |
| Peak MLX memory | mflux's own "Peak MLX memory" line | The model's MLX-side high-water |
| Max RSS | `/usr/bin/time -l` | CPU-side resident set |
| Peak memory footprint | `/usr/bin/time -l` | Includes GPU/wired; comparable to Activity Monitor |
| Process swaps | `/usr/bin/time -l` | **Nonzero = the machine ran out** |
| System swap used | `sysctl vm.swapusage` before/after | Machine-level pressure |
| Compressor delta | `vm_stat` before/after | Compression pressure |

The decisive 8 GB signal is **swaps**, not peak GB. A model that fits shows 0 swaps;
one that does not will swap and slow to a crawl or be killed.

## Baseline: FLUX.2 Klein 4-bit (current fleet model) — 32 GB M2 Max (Mac14,13)

| Scene | Resolution | Peak MLX | Max RSS | Footprint | Process swaps |
| --- | --- | --- | --- | --- | --- |
| Sleep-story cover | 1280×720 | **11.15 GB** [M] | 2.37 GB [M] | 19.33 GB [M] | 0 [M] |
| Storybook cover | 1024×1024 | **12.37 GB** [M] | 3.28 GB [M] | 22.38 GB [M] | 0 [M] |

Note the earlier "2.37 GB peak" figure in `text-rendering-model-shortlist.md` was
**max RSS only** and understates pressure; the MLX and footprint figures above are
the honest ones.

## Z-Image-Turbo 4-bit (5.91 GB pack) — 32 GB M2 Max (Mac14,13)

| Scene | Resolution | Config | Peak MLX | Max RSS | Footprint | Swaps | Wall |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Sleep-story cover | 1280×720 | default | **9.84 GB** [M] | 3.72 GB [M] | 17.70 GB [M] | 0 [M] | 166 s [M] |
| Sleep-story cover | 1280×720 | `--low-ram` | **6.26 GB** [M] | 3.91 GB [M] | **7.12 GB** [M] | 0 [M] | 236 s [M] |
| Storybook cover | 1024×1024 | default | **10.70 GB** [M] | 4.40 GB [M] | 20.38 GB [M] | 0 [M] | 176 s [M] |
| Storybook cover | 1024×1024 | `--low-ram` | **6.26 GB** [M] | 3.91 GB [M] | **7.24 GB** [M] | 0 [M] | 196 s [M] |
| Storybook cover | 1024×1024 | `--mlx-cache-limit-gb 2` | **10.70 GB** [M] | 3.97 GB [M] | 11.53 GB [M] | 0 [M] | 184 s [M] |

All runs 9 steps, seed 42, fresh process, so the wall times **include model load**.

Z-Image-Turbo (6B) is **slightly lighter than FLUX.2 Klein (4B)** at default settings — about
1.3–1.7 GB less peak MLX — and **`--low-ram` more than halves it**: 10.70 → 6.26 GB peak MLX and
20.38 → 7.24 GB footprint, for roughly +11 % wall time at 1024². That is the flag the 16 GB tier
should use: the default footprint (20.4 GB) does not fit 16 GB, the `--low-ram` footprint
(7.2 GB) does.

0 swaps everywhere on 32 GB, which still says nothing about 8 GB: the cache absorbs the slack.
The 8 GB verdict needs an actual 8 GB M1/M2 [I]. Full table with generation times:
`qualification/z-image-turbo-2026-09-24.md`.

## Ideogram 4 q4 (14.9 GB pack) — 32 GB M2 Max (Mac14,13)

| Scene | Resolution | Peak MLX | Max RSS | Footprint | Compressor delta | Result |
| --- | --- | --- | --- | --- | --- | --- |
| Sleep-story cover, attempt 1 | 1280×720 | **17.70 GB** [M] | 5.40 GB [M] | 19.43 GB [M] | −0.05 GB | **FAILED** after 673 s |
| Sleep-story cover, `--low-ram` retry | 1280×720 | **21.05 GB** [M] | 5.50 GB [M] | 18.82 GB [M] | −0.12 GB | OK, 1466 s [M] |
| Storybook cover | 1024×1024 | **22.49 GB** [M] | 11.04 GB [M] | 24.06 GB [M] | **+9.33 GB** | OK, 1440 s [M] |

Attempt 1 died with a Metal command-buffer abort —
`RuntimeError: [METAL] Command buffer execution failed: Impacting Interactivity
(kIOGPUCommandBufferCallbackErrorImpactingInteractivity)` — macOS refusing the work under
memory pressure (a large model download and other work were running on the machine), not
a code bug. The `--low-ram` retry on a quiet machine completed; the 1024² run drove
**9.3 GB of memory compression** and pushed system swap from 2.3 GB to 3.7 GB.

**Ideogram 4 is not a fleet model.** Even the successful runs peak ~21–22.5 GB MLX /
19–24 GB footprint, so the 16 GB and 24 GB tiers cannot run it at all, and the 32 GB
machine only survives under heavy compression. Its typography was the best of the four
models, at roughly 24 minutes per image.

## Qwen-Image-2.1 4-bit via mlx-serve (10.7 GB pack) — 32 GB M2 Max (Mac14,13)

| Scene | Resolution | Resident | Result |
| --- | --- | --- | --- |
| Sleep-story cover | 1280×720 | 13.94 GB | OK, 826 s [M]; text correct but the title ran off the right edge |
| Storybook cover | 1024×1024 | 13.94 GB | **FAILED** after 793 s [M] |

Same Metal abort as Ideogram: `[mlx] [METAL] Command buffer execution failed:
Impacting Interactivity`. mlx-serve reported the pack at **13.94 GB resident** with a
wired limit of 25,559 MB, and the 1024² run exceeded the budget.

**Qwen-Image-2.1 is a 32 GB-only capability at 4-bit under mlx-serve** — 1280×720
fits, 1024² does not. It is not credible for the 16 GB tier despite the pack card
being documented "for 16 GB Macs". [M]

Two setup notes, both [M]: `mlx-serve pull` fetched only the two manifest files
(0 MB) for this pack — the weights had to be downloaded directly into
`~/.mlx-serve/models/`; and the documented Homebrew tap install fails
(`ddalcu/homebrew-mlx-serve` does not exist), so the release tarball was used.

## Open work

1. Measure Z-Image-Turbo (4-bit, 5.91 GB pack) with `--mlx-cache-limit-gb 2` and
   `--low-ram` on the 32 GB machine, to bound its real floor.
2. The same runs on an **actual 8 GB M1 and M2**. Until that exists, any 8 GB
   statement is **[I]** inference, not measurement.
3. If 4-bit still swaps on 8 GB, test the smaller `mflux-community/z-image-turbo-mflux-q3`
   pack.
4. Record results as rows in `.build/text-tests/memory-results.tsv`.
