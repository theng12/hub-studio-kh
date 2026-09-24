# Z-Image-Turbo — generation time and memory requirements

Date: 2026-09-24. Host: **Apple M2 Max, Mac14,13, 32 GiB**. Status: **owner-approved for
quality; not yet integrated into the app.** Evidence class per figure: **[M]** measured here,
**[V]** vendor claim, **[I]** inference.

Model: `filipstrand/Z-Image-Turbo-mflux-4bit` @ `b3a8f31115a11f2f9e2fa0bfbc8d78dcc3e6568b`
(the pre-quantized 4-bit mflux pack, 5.91 GB on disk). Engine: `mflux 0.20.0` on MLX.
Sampling: **9 steps**, guidance default, seed 42. Each command is a fresh process, so every
wall time below **includes model load**.

## Generation time [M]

| Scene | Resolution | Config | Steps | Wall (incl. load) | Per step, approx. |
| --- | --- | --- | --- | --- | --- |
| Sleep-story cover | 1280×720 | default | 9 | **166 s** | ~18 s |
| Sleep-story cover | 1280×720 | `--low-ram` | 9 | **236 s** | ~26 s |
| Storybook cover | 1024×1024 | default | 9 | **176 s** | ~20 s |
| Storybook cover | 1024×1024 | `--low-ram` | 9 | **196 s** | ~22 s |
| Storybook cover | 1024×1024 | `--mlx-cache-limit-gb 2` | 9 | **184 s** | ~20 s |

`--low-ram` costs roughly **+11–42 %** wall time depending on resolution, and buys a large
memory reduction (below). The cache-limit flag is nearly free.

## Memory [M]

| Scene | Config | Peak MLX | Max RSS | Peak footprint | Swaps |
| --- | --- | --- | --- | --- | --- |
| 1280×720 | default | 9.84 GB | 3.72 GB | 17.70 GB | 0 |
| 1280×720 | **`--low-ram`** | **6.26 GB** | 3.91 GB | **7.12 GB** | 0 |
| 1024×1024 | default | 10.70 GB | 4.40 GB | 20.38 GB | 0 |
| 1024×1024 | **`--low-ram`** | **6.26 GB** | 3.91 GB | **7.24 GB** | 0 |
| 1024×1024 | `--mlx-cache-limit-gb 2` | 10.70 GB | 3.97 GB | 11.53 GB | 0 |

"Peak MLX" is the model's own reported high-water; "footprint" is macOS `phys_footprint`, which
is what Activity Monitor shows and what the OS actually accounts for. The default footprint is
inflated by MLX's buffer cache, which grows into free RAM — `--low-ram` is what removes it.

## Requirements by machine tier

| Tier | Verdict | Basis |
| --- | --- | --- |
| 32 GB | Runs comfortably | 0 swaps at every setting [M] |
| 24 GB | Runs | default footprint 17.7–20.4 GB [M] fits 24 GB with headroom |
| 16 GB | Runs — the intended fleet tier | default footprint 20.4 GB exceeds 16 GB, so **`--low-ram` is required** there: 7.1–7.2 GB footprint [M] |
| 8 GB | **Not established** | `--low-ram` footprint is 7.1–7.2 GB on an 8 GiB machine that also runs macOS — this is the one tier still needing a real run [I] |

Recommendation: on the 16 GB tier run Z-Image with `--low-ram`; on 24 GB and above the default
is fine and slightly faster. The 8 GB question needs an actual 8 GB M1/M2, because a 32 GB
machine's peak is cache-inflated and cannot answer it [I].

## Why this model was approved

Z-Image-Turbo rendered **both** test scenes' text correctly, including the subtitle
"A Bedtime Story" that the shipped FLUX.2 Klein misspelled as "A Bediime Story", and it peaks
*below* FLUX.2 Klein in the same engine. Full quality comparison:
`research/text-rendering-model-shortlist.md`.

## What integration still needs

Nothing here is in the app yet. Z-Image requires the **mflux 0.17.5 → 0.20.0 engine bump** plus
a per-model dispatch, exactly as `research/three-model-rollout-plan.md` sets out: the bump first,
then a real re-qualification of FLUX.2 Klein on the new engine, then the Z-Image adapter. The
weights now sit in `optional-models/` on each SSD kit for that work.

Raw logs and the results table: `.build/text-tests/memory-results.tsv` and
`.build/text-tests/logs/` (gitignored).
