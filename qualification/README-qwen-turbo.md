# Qwen Image 2.1 Turbo Low-RAM MLX 4-bit qualification

Candidate source on `feature/qwen-image-turbo`, integrating released Hub 0.6.171. Not released or installed.
The pinned package is `mflux-community/qwen-image-2-1-turbo-mflux-q4` at
`bda8951ec6b8cc288a961432fe15c22433521a1d` (19 required files, 9,963,443,598 bytes).
Its manifest has `production_allowed: false`. There is no passing hardware result, measured
free-memory floor or chip timing. The ordinary Qwen packages keep their existing behavior.

## Before running

Read the shared iCloud `Story Studio Agent Automation/HANDOFF.md` for current coordinator and active jobs.
Hub 0.6.171 rollout and package repair are complete. Finish existing song work before using its Mac. Never change
pauses to make a test run. Finish all model work before qualification; the runner refuses foreign
model processes and uses private data plus a copy-on-write model library, not the real Hub state.
Use a separately staged candidate app with the new adapter, manifest and qualification launcher;
the ordinary installed app cannot run this disabled candidate.

## Physical runs

Run `RUN-QWEN-TURBO-16GB.command`, `RUN-QWEN-TURBO-24GB.command` or
`RUN-QWEN-TURBO-32GB.command` on the matching physical Mac, with `STUDIOHUB_APP` pointing to
the staged app if needed. These reuse `qwen-image-qualification.sh` and
`qwen_image_qualification.py --turbo --tier <tier>`. There is no 8 GB launcher.
The private worker receives only the explicit qualification package opt-in; ordinary startup
never enables the candidate. The runner must verify the opt-in, exact package and runtime identity.

Turbo cases use the official eight-step sigma grid, guidance 1 and no negative prompt:
cold/warm 1024x1024, then 1280x720 and 720x1280. Do not use the ordinary 40-step stress option.
Keep all outputs and the JSON/CSV/summary evidence: actual RAM/chip/macOS, package/runtime
hashes, worker-tree footprint peak, clean available memory at start/min/end, pressure, swap,
SSD writes and complete usable image artifacts. Review images for text/detail/prompt adherence.
For speed comparison run ordinary Qwen with the same prompt, dimensions and seeds in a separate
serial run. Eight rather than twenty steps is not proof of a wall-time speedup.

Measure the 16 GB tier first (M1/M2/M4 where available), then each supported larger tier. A
32 GB run cannot qualify 16 GB, and a missing receipt is NOT MEASURED. Set free-memory start
floors and `seconds_per_step` from the retained physical evidence only. The candidate is not
stocked or enabled until qualification and independent review pass, followed by a release on
latest main and the paced This Mac → site 1100 → other-sites rollout.
