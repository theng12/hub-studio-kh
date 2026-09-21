# Studio Hub — project status (owner-facing checklist and resumption index)

Last updated: 2026-09-22 04:20 +07
Owner: DeepSeek (autonomous takeover)
Canonical plan: `.planning/2026-09-10-standalone-app-feasibility/` (export
`PWF_PLAN_ROOT=/build/Developer/studio-hub-kh`,
`PLAN_ID=2026-09-10-standalone-app-feasibility`). This file is an index, not a
second planning system.

## Repository / worktree state

- Local repo: `/build/Developer/studio-hub-kh`
- Branch: `implementation/voice-runtime`
- Base commit (takeover): `9a4570da57cb9109cd7da1d06d2197f923a71139` (PR #13 merged)
- Current HEAD: `c6c2323`; installed app build `6eba1a5`
- Commit chain this session: `e1d167a` R04 → `b68e7b2` host identity →
  `295daf8` R05 records → `678630e` R04 multipart → `5cad55a` R09 note →
  `d31728f` status → `9f5c1a1` Image probe → `0004850` R09 selection →
  `0fdfbe3` flake probe → `6b7fd94` two-Hub storage → `e0205da` connections count →
  `1e61dea` render evidence → `6211fbf` R06.
- Dirty changes: none after the R06 commit (this status file update follows).
- Remote: `origin` = https://github.com/theng12/Studio-Hub-KH (private). Nothing
  was pushed this session. No open issues/PRs; recheck before any authorized write.
- VERSION: `0.6.38`. 0.6.38 does not uniquely identify source; use commits + hashes.
- Preserved worktrees (do not delete/reset): `codex/local-readiness-gatewaykh`
  @ `16eba0d`, `codex/music-package-runtime` @ `5cfc171`,
  `codex/recovered-music-draft` @ `0eebc42` (do not merge wholesale), the five
  H-0x worktrees, and the Music recovery audit detached worktree.
- Local inputs (ignored, preserved): `.build/voice-runtime`, `.build/test-deps`,
  `.build/st03-image-runtime-worker`, `.build/music-engine-source`, `.build/uv-cache`,
  `.build/downloads`, and the Music runtime worktree overlay.
- Protected, untouched: `/build/Downloads/model-library`,
  `/Applications/Studio Hub.app` and its running processes, installed state,
  Pinokio machines, GatewayKH checkout (read-only).

## Product scope and non-negotiable constraints

- Core: Hub, Image, Voice, independent Subtitle. Music is an accepted extension.
  Chat/Video remain deferred. GatewayKH replaces GenStudio as current external client.
- Native client → bundled Hub → private Hub-owned workers. Local operation works
  with no GatewayKH and no network.
- Preserve credential separation, rotation, deny-by-default fleet routes; a fleet
  credential is never forwarded to workers. Hub token ≠ admin authority.
- Persist dispatch fencing before polling; stable identity + body fingerprint
  replays the same accepted work, changed body conflicts.
- One physical heavy-work/memory admission boundary across studios. Node-owned
  pause/disable/storage/physical reservation must be enforced, not bypassed.
- Editable defaults 50,000,000,000 bytes/device and 30 days. Protect active,
  transferring, unacknowledged outputs and reference audio.
- A valid terminal completion whose artifact cannot be verified stays
  `artifact_metadata_error`; do not change billing semantics.
- Never forward credentials to arbitrary URLs; never expose secrets in logs.

## Completed this takeover (all locally verified / self-reviewed)

- **R04 node-owned peer admission** (`e1d167a`, `678630e`, `6b7fd94`). Work-submitting
  peer POSTs now pass machine/Studio disable, paused model, physical lease, output
  storage and packaged readiness and return `409/507/503`; status/cancel/artifact/ACK
  stay reachable; accepted async work holds a machine lease until terminal (TTL +
  sweep). Permanent negatives for all four gates, lease lifetime/expiry, multipart
  Subtitle, two-real-Hub Studio-disable and storage tests. `r04-report.md`.
- **Host-identity PATH fix** (`b68e7b2`). `apple_chip_name()`/`apple_machine_type()`
  prefer absolute `/usr/sbin` paths with a `system_profiler` fallback; the
  pre-existing chip test failure is closed (`test_standalone_connections.py` 34 passed).
- **R05 records reconciliation** (`295daf8`, `r05-report.md`). ST-03/04/05/06/08/10/11
  labeled implemented vs missing-evidence vs gated; ST-04/05/06 status lines corrected.
- **R05 Image offline rebuild probed** (`9f5c1a1`). Declared CPython archive + lock
  verified; offline rebuild failed loudly (no silent network) because 21 of 73 locked
  packages (incl. `mflux`, `matplotlib`, `opencv-python`, `contourpy`) are absent from
  `.build/uv-cache`. Gate: an explicitly populated Image wheel cache.
- **R09 Hub-side selected-device no-fanout** (`0004850`). `set_mode`/`release` with
  `studio_ids` touch only the selected Studio. `test_memory_policy_contract.py` 16 passed.
- **R06 native Music workflow** (`6211fbf`, `r06-report.md`). Full workflow with
  runtime-decoded controls, durable Hub submit, cancel/reconcile, persisted uncertain
  requests, and verified play/save with ACK only after a verified save. Native build
  exit 0; renders at 1040×680/1920×1053; root suite 655 OK/11 skipped.
- **Flake re-probe** (`0fdfbe3`). Shared-voice peer test passed 3/3 in isolation;
  still not fixed, cause unproven.
- **R06 packaged model-free smoke** (`eb3f0b1`). Staged `.build/deepseek-proof-r06/Studio Hub.app`
  (`implementation_commit a600ca6`, tree sha256 `712bb61d…`) with the bundled native
  shell hash matching the fresh build; probe `ok:true`, all four workers up, Music
  health/catalog 200, unauthenticated Music 401, owned processes stopped, bundle
  unchanged, no model inference.
- **R10 local candidate inventory** (`5c0e5f0`). `release_kit.py` records app/runtime
  identity, components and operator steps and exits 1 with 34 "advertised but not
  installed" problems because no model library exists here; model completeness and
  clean-Mac runs stay gated.

## Packet checklist (R04–R10)

- R04 — LOCALLY VERIFIED / self-reviewed. Independent review pending.
- R05 — records reconciled; SDK slice done. Open: Image offline wheel cache (gated),
  optional Graphify restore (CLI unavailable).
- R06 — LOCALLY VERIFIED / self-reviewed. Independent review pending.
- R07 — GATED: real model inference (FLUX, Qwen Base, VoxCPM2 4/8/bf16, OmniVoice,
  Whisper Base/Small/Turbo, ACE-Step Turbo/0.6B planner).
- R08 — GATED: real GUI save/ACK, keyboard/focus, restart history.
- R09 — PARTIAL: Hub side covered (discovery/dispatch/artifact/cancel/ACK/auth,
  selection/no-fanout). Counterpart GatewayKH ACK/cancel/Music edits need separate
  scope authority; physical second node gated.
- R10 — GATED: clean Mac without tools/network, original M1/Sonoma, reboot/login,
  signing/notarization, distribution.

## Confirmed defects

- None newly open. R04, host identity, and R06 defects are repaired locally.

## Unverified concerns (not fixed/default)

- Shared-voice peer reachability flake: re-probed 3× (3 passed), cause unproven.
- Dependency deprecations and fixture socket/process ResourceWarnings remain.
- GatewayKH lacks delivery ACK, upstream cancel and a Music lane; voice-reference
  metadata partial. Counterpart edits need authorization.
- R04's model-pause and physical-reservation gates are proven at the peer HTTP
  route only (Studio-disable and storage also at two real Hubs).
- Node-owned peer leases are in-process; a Hub restart forgets an in-flight lease.

## Qualification gaps (need assets/hardware/authority)

- Real inference for every qualified model; physical clean-Mac, original M1/oldest
  Sonoma, second node, login/reboot, offline kit, signing/notarization, publication.

## External gates awaiting separate authorization

- Remote push/PR/merge, tags/releases, signing/notarization, deployment,
  installed-app replacement, real enrollment, live generation, GatewayKH edits,
  paid/hosted compute, rebooting/logout, a network fetch to populate the Image cache.

## Current packet

- Objective: **all currently authorized locally executable work is complete.** R04,
  R05 (records + Image offline probe), R06 (native Music + packaged smoke) and R09
  Hub-side selection are locally verified and recorded; R10's local candidate and
  identity/licenses are inventoried.
- Remaining is gated, not blocked by effort: R07 needs real models (no
  `~/StudioHubModels` here), R08 needs real jobs/GUI interaction, R09's counterpart
  ACK/cancel/Music edits need GatewayKH scope authority, R10 needs a model library
  and clean/original-M1 hardware, R05's Image cache needs a network fetch, and
  independent review requires a separate reviewer.
- Next action on resumption: pick up whichever gate is opened first (install a
  qualified model package, obtain GatewayKH scope, or obtain clean hardware), then
  requalify against the then-current HEAD with exact commands.

## Test processes / temp paths / cleanup

- Logs: `.build/deepseek-r04/*`, `.build/deepseek-r05-image.log`,
  `.build/deepseek-r06-native-build.log`, `.build/deepseek-r06-root.log`.
- Renders: `.build/deepseek-music-render/` (2 PNGs, inspected).
- No owned servers/processes left; disposable temp homes/stages removed.
- `/Applications/Studio Hub.app` is now **this project's 0.6.38 build**
  (`ff4fd9b`), installed by this session with the owner's authorization. The old
  0.5.4 app and its data are backed up under `~/StudioHubAppBackups/`.

---

## RESUME STUDIO HUB

Timestamp: 2026-09-22 04:20 +07
Repository / worktree: `/build/Developer/studio-hub-kh` (main checkout)
Branch: `implementation/voice-runtime`
Base commit: `9a4570da57cb9109cd7da1d06d2197f923a71139`
Current c6c2323: `c6c2323`; installed app build `6eba1a5` (`development_build:false`)
Uncommitted changes and owners: none.
Current packet: **Stable Audio 3 is installed and qualified; the ACE-Step MLX
question is closed.**
Completed this session: R04 admission; host identity; R05 records; R06 native Music;
R09 selection; R10 inventory; app version + What's New footer; stale-listen-address
fix; VoxCPM2 fix + 8-bit exposure; ACE-Step Music (30s/60s/6min, vocal, cancel,
restart); Whisper Base/Small/Turbo; delivery ACK; the UI/UX pass (sidebar,
samples, aura, simpler model names, editable duration, discard, player, thumbnails);
**Stable Audio 3 Medium (MLX) as a second Music engine, verified end to end through
the installed app.**
Two music engines now: ACE-Step 1.5 (MIT; vocals; up to 600 s; DiT/VAE already run
on native MLX - verified from the engine log, only the checkpoint load is PyTorch)
and Stable Audio 3 Medium (Stability Community Licence; instrumental only; up to
380 s; pure MLX; ~9 s per 30 s clip, ~62 s for 380 s).
Model library: `~/StudioHubModels` now also holds `stable-audio-3-medium` (6.4 GiB).
Evidence: `.planning/2026-09-10-standalone-app-feasibility/{r04,r05,r06}-report.md`,
`r-music-mlx.md` and `progress.md`; `qualification/evidence/` for every real-model
receipt; listening copies in `~/Desktop/Studio Hub Test Outputs/`.
Self-review findings: SA3 startup defects fixed (manifest env override leaked into
the secondary cache; the runner's symlinked tree wrote weight links into the app
bundle). The exact-menu manifest test digest was updated deliberately for the new
package.
Independent review status: not performed for any packet.
Remaining known gaps: SA3 has no cancel-in-flight path beyond process termination;
Video/Chat have no engine; the R08 physical GUI click-through is the owner's;
GatewayKH scope and the Image wheel cache need authorization.
External gates: GatewayKH scope; Image wheel-cache network fetch; clean Mac /
original-M1 / reboot hardware; distribution authority.
Owned processes / temporary data / cleanup: none outstanding. `.build/` holds ignored
proof artifacts; keep. `.build/sa3-source` holds the upstream checkout + its venv.
Next smallest action: owner clicks through the two music models in the app (Music
picker should now show both), or authorize the Image wheel cache / GatewayKH work.
First command to run:
`git status --short && git log --oneline -3`
Important do-not-touch paths: `~/Downloads/model-library`, `~/StudioHubAppBackups/`,
other worktrees, `codex/recovered-music-draft`, GatewayKH checkout.
