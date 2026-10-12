# Qwen Image 2.1 Turbo: first normal-production comparison

Observed 2026-10-13 on installed Hub **0.6.173**, product source
`25e49b61c31d40742f3d04f9caf440ac6aace1a3`. The owner authorized normal
distribution and production on 16 GB+ Macs and waived a separate physical
memory-fit qualification. This report measures speed and reviews output; it does
not claim measured Turbo memory use or completed fleet deployment.

## Matched local pair

Both requests used normal Gateway routing to This Mac, an Apple M2 Max with
32 GiB unified memory. Same prompt, seed 42, 1024×1024 PNG, guidance 1; the
regular Low-RAM MLX 4-bit package used its 20-step default and Turbo MLX 4-bit
used its required 8 steps. Jobs ran serially, with model eviction after each job.

| Model | Gateway job | Hub-reported generation time |
| --- | --- | ---: |
| Qwen Image 2.1 Low-RAM 4-bit | `6686a8dd-a8de-4488-9e81-b21ba7fcb891` | 245.671334 s |
| Qwen Image 2.1 Turbo MLX 4-bit | `4781cd1a-017c-4579-aef7-1819dc4005f0` | 107.270224 s |

For this pair, Turbo was **2.2902× faster**, taking **56.3359% less generation
time**. These are ordinary production measurements, not a cold-filesystem or
repeated-run benchmark. Queue and delivery time are excluded from this figure.
Do not extrapolate the M2 Max timing to the 16 GB M1/M2/M4 fleet.

Prompt: a refined Phnom Penh sunrise travel poster, palace roof details, palms,
river, large “PHNOM PENH” and smaller “MORNING BY THE RIVER”, no extra lettering.
Both outputs contain the requested words correctly and follow the poster brief.
Turbo has bolder, cleaner typography; regular has a calmer centered composition
with thinner lettering. Neither image establishes accurate Royal Palace identity.
This one poster does not establish portrait, hand, landscape or overall quality.

Root and an independent agent viewed both images and checked the receipts.
Artifact SHA-256 values are in the retained receipts, with local artifact paths.
Evidence directory:
`~/Developer/_orchestra/codex-takeover-20261012/turbo-production-benchmark/`.
Receipts: `qwen21-4bit-production-20261013-local-poster-{regular,turbo}-01.json`.
Independent review: `../turbo-local-pair-independent-review.md`.

## Remaining rollout and comparisons

The version is pushed to main with a verified primary `dist/` DMG and installed
on This Mac. The normal paced fleet rollout is active; site 1100 is first after
This Mac. Live status and exact process identities are in the canonical handoff
and the orchestra checkpoint, not this immutable observation.

Next: distribute the pinned Turbo package to eligible 16 GB+ Macs through normal
Hub/Gateway model installation, one internet copy per network then LAN copies.
Do not use older catalogs with missing memory-tier metadata to select targets.
Preserve owner pauses. Offline Macs remain pending, and 8 GB Macs are excluded.
Run the already prepared normal site-1100 poster, portrait and landscape pairs,
recording each actual Mac; different machines are not a controlled speedup pair.
No changes to channel presets or paid image replacement are needed.
