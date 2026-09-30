# Studio Hub — project status (owner-facing checklist and resumption index)

Last updated: 2026-09-22 13:05 +07
Owner: DeepSeek (autonomous takeover)
Canonical plan: `.planning/2026-09-10-standalone-app-feasibility/` (export
`PWF_PLAN_ROOT=/build/Developer/studio-hub-kh`,
`PLAN_ID=2026-09-10-standalone-app-feasibility`). This file is an index, not a
second planning system.

## Repository / worktree state

- Local repo: `/build/Developer/studio-hub-kh`
- Branch: `implementation/voice-runtime`
- Base commit (takeover): `9a4570da57cb9109cd7da1d06d2197f923a71139` (PR #13 merged)
- Current HEAD: `ffe3315` (main too); installed app build `ffe3315`
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

Timestamp: 2026-09-22 12:05 +07
Repository / worktree: `/build/Developer/studio-hub-kh` (main checkout)
Branch: `implementation/voice-runtime`
Current HEAD: `539b7fa`; installed app build `b4db781` (`development_build:false`)
Uncommitted changes: none.
Current packet: **all four SSDs carry the full library and pass their kit verifier.**
- KH-STUDIO-NOVA / UGREEN / ATLAS / ORICO: app 0.6.38 (b4db781), fixed installer,
  35 of 35 catalogue packages `files_verified` (ATLAS and ORICO also carry f5-tts,
  which the catalogue does not list), kit verifier PASS, 0 quarantined files.
- Nothing deleted: Pinokio/TerraNash kits moved to `_trash/` on UGREEN, ATLAS and
  ORICO. `My Stuff`/`Documents` untouched; NOVA's 153 GB plain archive untouched.
- `.build/prepare-kit.sh <volume>` prepares any future drive in one step.
Also complete this session: two music engines (ACE-Step + Stable Audio 3; ACE-Step was retired in 0.6.116, music is now YuE2 + Stable Audio 3), the
UI/UX pass, P0 stabilisation (SA3 progress/cancel, log rotation, auto-discard,
no-transcript clones), the progress-stage polish, the R08 owner pass, and the
owner-facing R08 checklist (`docs/R08-click-through-checklist.md`).
Remaining optional work: the 18 advertised models are all present now, so nothing
is missing; Image wheel cache (R05) still needs a network pass if the image runtime
is ever rebuilt offline; GatewayKH parked by owner decision.
First command to run:
`git status --short && git log --oneline -3`
Important do-not-touch: `~/Downloads/model-library`, `~/StudioHubAppBackups/`,
other worktrees, `codex/recovered-music-draft`, GatewayKH checkout, and every
`_trash/` folder on the SSDs until the owner empties them.
