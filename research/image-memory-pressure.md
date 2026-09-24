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

## Baseline: FLUX.2 Klein 4-bit (current fleet model) — 32 GB M1 Max

| Scene | Resolution | Peak MLX | Max RSS | Footprint | Process swaps |
| --- | --- | --- | --- | --- | --- |
| Sleep-story cover | 1280×720 | **11.15 GB** [M] | 2.37 GB [M] | 19.33 GB [M] | 0 [M] |
| Storybook cover | 1024×1024 | **12.37 GB** [M] | 3.28 GB [M] | 22.38 GB [M] | 0 [M] |

Note the earlier "2.37 GB peak" figure in `text-rendering-model-shortlist.md` was
**max RSS only** and understates pressure; the MLX and footprint figures above are
the honest ones.

## Z-Image-Turbo 4-bit (5.91 GB pack) — 32 GB M1 Max

| Scene | Resolution | Peak MLX | Max RSS | Footprint | Process swaps |
| --- | --- | --- | --- | --- | --- |
| Sleep-story cover | 1280×720 | **9.84 GB** [M] | 3.72 GB [M] | 17.70 GB [M] | 0 [M] |
| Storybook cover | 1024×1024 | **10.70 GB** [M] | 4.40 GB [M] | 20.38 GB [M] | 0 [M] |

Z-Image-Turbo (6B) is **slightly lighter than FLUX.2 Klein (4B)** in this engine and
at these settings — about 1.3–1.7 GB less peak MLX. The extra parameters do not cost
more memory here; the quantized pack and the text encoder dominate.

Both models show 0 swaps on 32 GB, which says nothing about 8 GB: the cache is
absorbing the slack. The 8 GB verdict still requires the constrained and on-device
runs in "Open work" below.

## Open work

1. Measure Z-Image-Turbo (4-bit, 5.91 GB pack) at 1024² on the 32 GB machine:
   default, then `--mlx-cache-limit-gb 2`, then `--low-ram`. Record all metrics.
2. The same runs on an **actual 8 GB M1 and M2**. Until that exists, any 8 GB
   statement is **[I]** inference, not measurement.
3. If 4-bit still swaps on 8 GB, test the smaller `mflux-community/z-image-turbo-mflux-q3`
   pack.
4. Record results as rows in `.build/text-tests/memory-results.tsv`.
