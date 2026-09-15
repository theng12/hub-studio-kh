# ST-03 real packaged inference — completed, previously unrecorded

Status: evidence of a **completed real FLUX generation** through the assembled
Hub + Image worker + native UI. This happened after
[`st03-image.md`](st03-image.md) was written, and was not recorded on the card,
the receipt or the board. It was found in the `st-03` worktree's gitignored
`.build/` during the 2026-09-15 record reconciliation and copied here so it
survives `.build/` cleanup.

## What was proven

One real 1K text-to-image generation through the packaged application, with the
native client driving the bundled Hub and the separate Image worker.

| Item | Value |
| --- | --- |
| Job ID | `af4913b2b52b41b0931f919112daf969` |
| Model | `AITRADER/FLUX2-klein-4B-mlx-4bit` |
| Model revision | `7fd24828501390b67a92c8b66d2fc5a707d0ba1a` |
| Image runtime tree SHA-256 | `c9cd56a6d1cde6b72dee8ba22cf45fcbd640538207038087261cae91ec67f67e` |
| Assembled app implementation commit | `4377237e350d6adc7636aaf23dddc454af74469d` |
| Mode / resolution | `txt2img`, 1K preset, 1280 × 720 |
| Steps / guidance / seed | 4 / 1.0 / 42 (resolved) |
| Prompt | "A quiet riverside garden at sunrise, soft mist over the water, green trees, natural light, photographic detail." |
| Duration | 105.943 s |
| Output bytes | 1,696,066 |
| Output SHA-256 | `290d2fb8cf0cd927d7d0633412bb157a9ee8675684ed2da7e2e1267c6ce919bd` |
| PNG dimensions / signature | 1280 × 720, `89504e470d0a1a0a` |
| Peak process RSS | 1,105,281,024 bytes (~1.03 GiB) |
| Worker state | `done`, `progress: 1.0`, `tries: 1` |
| Hub output group | `local:image:af4913b2b52b41b0931f919112daf969:1`, `state: finalized`, `attempt: 1` |
| Receipt acknowledgement | `receipt_acked_at: null` — **not acknowledged** |

The worker emitted a complete final receipt (`bytes`, `sha256`, `width`,
`height`, `media_type`, `output_path`, `runtime_revision`, `resolved_seed`), and
the Hub registered it as one finalized output group with the matching SHA-256.

## Evidence in this repository

- [`evidence/st03-image-real-inference.jsonl`](evidence/st03-image-real-inference.jsonl)
  — the collector run (126 records: 122 RSS samples, job seen, worker terminal,
  Hub item plus finalized output group).
- [`evidence/st03-flux2-klein-1280x720-seed42.png`](evidence/st03-flux2-klein-1280x720-seed42.png)
  — the generated output (SHA-256 verified above).
- [`evidence/st03-image-studio-completed.jpg`](evidence/st03-image-studio-completed.jpg)
  — the native Image Studio at 1405 × 768 showing Image size `16:9 - 1280 × 720`,
  Steps 4, Guidance 1, Seed 42, Model `FLUX.2 Klein · Installed`, the rendered
  preview, a `Recent Images` entry marked `FLUX.2 Klein · Completed`, and
  `Local services ready`.
- [`evidence/st03-image-studio-minimum-1040x712.jpg`](evidence/st03-image-studio-minimum-1040x712.jpg)
  — the same workspace at the 1040 × 680 minimum layout.

Captured on Apple M4, 16 GiB RAM, macOS 26.6.2 — the same host class recorded for
the rest of the phase, and within the 16 GB / 1K / 4-step workload the historical
Image audit approves. Peak RSS ~1.03 GiB means the workload fits the 16 GB host
with large headroom.

## What this does and does not close

Advances:

- ST-03.1 (FLUX.2 Klein 4B MLX 4-bit adoption) — exercised for real.
- ST-03.RELOCATE — the relocated runtime produced a real image.
- ST-03.2 — readiness and dispatch exercised with a real model.
- ST-03.5 — real packaged generation observed; **export/receipt acknowledgement
  is not proven** (`receipt_acked_at` is null) and restart-history checks are not
  evidenced in this run.

Still open:

- Bounded retry counts distinguishing attempts from requested jobs (ST-03.6).
- Native export with verified destination acknowledgement.
- Independent review of the final integrated Image changes.
- Physical original-M1 / Sonoma checks (ST-11).
- The assembled `ST03 Image Integration.app` was built from `4377237` in the
  `st-03` worktree and predates the `cd6ed5d` integration merge. Re-stage from the
  merged tree before citing artifact hashes: staging Image requires
  `--image-runtime <built runtime>` and the pinned Image runtime is **not** present
  in the main checkout's `.build/` (it remains in `.worktrees/st-03/.build/st03-image-runtime-worker`).
