# Studio Hub — project status (owner-facing checklist and resumption index)

Last updated: 2026-09-21 12:19 +07
Owner: DeepSeek (autonomous takeover)
Canonical plan: `.planning/2026-09-10-standalone-app-feasibility/` (export
`PWF_PLAN_ROOT=/build/Developer/studio-hub-kh`,
`PLAN_ID=2026-09-10-standalone-app-feasibility`). This file is an index, not a
second planning system.

## Repository / worktree state

- Local repo: `/build/Developer/studio-hub-kh`
- Branch: `implementation/voice-runtime`
- Base commit (takeover): `9a4570da57cb9109cd7da1d06d2197f923a71139` (PR #13 merged)
- Current HEAD: `9a4570da57cb9109cd7da1d06d2197f923a71139` (R04 changes uncommitted
  at the time of writing; see RESUME)
- Remote: `origin` = https://github.com/theng12/Studio-Hub-KH (private). No open
  issues/PRs and zero Actions workflows at takeover; recheck before any authorized
  remote write.
- Dirty changes: R04 code/tests/records owned by this session, plus this file.
- VERSION: `0.6.38`. 0.6.38 does not uniquely identify source; use commits + hashes.
- Preserved worktrees (do not delete/reset): `codex/local-readiness-gatewaykh`
  @ `16eba0d`, `codex/music-package-runtime` @ `5cfc171`,
  `codex/recovered-music-draft` @ `0eebc42` (do not merge wholesale), the five
  H-0x worktrees, and the Music recovery audit detached worktree.
- Local inputs (ignored, preserved): `.build/voice-runtime`,
  `.build/test-deps`, `.build/st03-image-runtime-worker`,
  `.build/music-engine-source`, and
  `/build/.codex/worktrees/music-package-runtime/studio-hub-kh/.build/music-runtime`.
- Protected, untouched: `/build/Downloads/model-library`,
  `/Applications/Studio Hub.app`, installed state, Pinokio machines.

## Product scope and non-negotiable constraints

- Core: Hub, Image, Voice, independent Subtitle. Music is an accepted extension.
  Chat/Video remain deferred. GatewayKH replaces GenStudio as current external client.
- Native client → bundled Hub → private Hub-owned workers. Local operation works
  with no GatewayKH and no network.
- Preserve credential separation, rotation, deny-by-default fleet routes; a fleet
  credential is never forwarded to workers. Hub token ≠ admin authority.
- Persist dispatch fencing before polling; stable identity + body fingerprint
  replays the same accepted work, changed body conflicts; worker-local idempotency
  does not make cross-worker regeneration safe.
- One physical heavy-work/memory admission boundary across studios. Node-owned
  pause/disable/storage/physical reservation must be enforced, not bypassed.
- Editable defaults 50,000,000,000 bytes/device and 30 days. Protect active,
  transferring, unacknowledged outputs and reference audio.
- A valid terminal completion whose artifact cannot be verified stays
  `artifact_metadata_error` (completed-but-unverified); do not change billing.
- Never forward credentials to arbitrary URLs; never expose secrets in logs.

## Completed baseline (evidence pointers)

- H-01–H-05 audit repairs: PRs #4, #6–#9.
- Music contract/runtime/supervision: PRs #10, #11.
- R01 native lifecycle N-01/N-02/N-03 + R02 reference integrity C-01/C-02: PR #12.
- R03 malformed Voice receipt/status/recovery + WAV/PCM integrity: PR #13.
- R05 partial (SDK selection/no silent fallback/native lifecycle gate): PR #13.
- R04 node-owned peer admission: **locally verified/self-reviewed** on `9a4570d`;
  independent review pending. Evidence:
  `.planning/2026-09-10-standalone-app-feasibility/r04-report.md`.
- Latest published evidence: `qualification/local-readiness-2026-09-21.md` —
  root 641 tests OK/7 skips; Voice 722 passed/19 skipped; Hub regressions 108
  passed; native build/boundary/API/lifecycle OK; build scripts 8 passed; release
  checks 21 OK; original probes 5 OK; packaged model-free startup/auth/shutdown OK.
  Product commit `fb36c89`, tested candidate `936b3e5`, evidence descendant `16eba0d`.
  Proof app `.build/StudioHubLocalReadinessV2.app`, tree SHA-256
  `06e881146e69e88097df71291569ffa873acf541381dc3fb8010df1ec34024e7`.
- GatewayKH read-only contract inventory @ `736d7b0` (separate repo, no edits).

## Packet checklist (R04–R10)

- **R04 node-owned admission — LOCALLY VERIFIED / self-reviewed.** Independent
  review pending. Four bypasses closed; lease lifetime + TTL added.
- **Bounded follow-up: host-identity robustness — IN PROGRESS NEXT.**
  `resources.apple_chip_name()` runs `sysctl` from PATH; with a PATH lacking
  `/usr/sbin` it returns no chip and `test_the_overview_shows_every_machine_in_one_row`
  fails. Passes with `/usr/sbin` on PATH; failed before any R04 edit.
- R05 portable bootstrap + accurate records: partial; remaining Image inputs/
  offline rebuild, harness paths, ST-status reconciliation, Graphify limitation.
- R06 native Music workflow: missing native UI (falls through to "Engine not installed").
- R07 exact-artifact model qualification: qualification gaps; needs models/hardware.
- R08 native usability + real export acceptance: needs real GUI/export exercise.
- R09 GatewayKH/fleet contract qualification: needs disposable fixtures; no ACK in
  GatewayKH, no upstream cancel, Music omitted from adapter.
- R10 release preparation and external qualification: exact-source candidate/kit;
  physical gates gated.

## Confirmed defects

- **R04 (repaired locally this session):** peer generation proxy bypassed
  node-owned model pause, studio/machine disable, output-storage refusal and
  physical-machine reservation. Baseline probe on `9a4570d`: each case HTTP 200 +
  1 worker request (`.build/deepseek-r04/r04-probe-baseline.log`); after repair
  409/409/507/409 + 0 worker requests (`.build/deepseek-r04/r04-probe-after.log`).
- **Host identity (confirmed, not yet repaired):** `apple_chip_name()` in
  `components/hub/backend/resources.py` calls `sysctl` by name; a launch PATH
  without `/usr/sbin` yields no chip. `sysctl` absolute path works on this host
  (`Apple M2 Max`, `Mac14,13`). One existing connections test fails only for this
  reason. Small, bounded fix planned next.

## Unverified concerns (do not treat as fixed/default)

- One earlier shared-voice peer reachability failure did not recur; cause unproven.
- Dependency deprecations + fixture socket/process ResourceWarnings remain.
- R09 GatewayKH reference-upload metadata/ttl and uncertain→PROVIDER_FAILED
  recovery semantics are only partly inspected.
- R04's four gates are proven at the peer HTTP boundary; only Studio-disable is
  also proven across two real Hubs. A two-Hub proof of each of the four is not claimed.
- Node-owned peer leases are in-process; a Hub restart forgets an in-flight lease.

## Qualification gaps (need assets/hardware/authority)

- Real model inference for FLUX, Qwen Base 0.6B/1.7B, OmniVoice, Whisper
  Base/Small/Turbo, VoxCPM2 4/8/bf16, ACE-Step Turbo/0.6B planner.
- Physical clean-Mac, original M1/oldest Sonoma, second node, login/reboot,
  offline kit, signing/notarization, publication.

## External gates awaiting separate authorization

- Remote push/PR/merge, tags/releases, Developer ID signing, notarization,
  deployment, installed-app replacement, real fleet enrollment, live generation,
  GatewayKH edits, paid/cloud compute, GitHub Actions, rebooting/logout.

## Current packet

- Objective: make `apple_chip_name()` independent of a minimal PATH (fall back to
  an absolute `/usr/sbin/sysctl`, then `platform`), so fleet overview/hardware
  identity is truthful for a packaged app launched without a login PATH.
- Owned files (proposed): `components/hub/backend/resources.py`, a focused test
  (existing `tests/test_standalone_connections.py` overview test is the end-to-end
  proof; add a direct unit-level fallback test if practical).
- State: queued (immediately next).
- Next smallest action: reproduce with a PATH that omits `/usr/sbin`, then fix and test.

## Test processes / temp paths / cleanup

- Logs: `.build/deepseek-r04/r04-probe-baseline.log`,
  `.build/deepseek-r04/r04-probe-after.log`,
  `.build/deepseek-r04/handoff-probes.log`.
- `tests/test_studio_proxy.py`: 14 passed, 2 warnings, 6 subtests (1.68s).
- `tests/hub_regression/cases`: 108 passed.
- `tests/test_hub_jobs.py` + `test_hub_dispatch_proof_gate.py` +
  `test_hub_standalone.py`: 18 passed.
- `tests/test_standalone_connections.py`: 32 passed, 1 failed (chip/PATH, env).
- Original five handoff probes: 5 OK, exit 0.
- No owned servers left running; probes used disposable temp state.

---

## RESUME STUDIO HUB

Timestamp: 2026-09-21 12:19 +07
Repository / worktree: `/build/Developer/studio-hub-kh` (main checkout)
Branch: `implementation/voice-runtime`
Base commit: `9a4570da57cb9109cd7da1d06d2197f923a71139`
Current HEAD: `9a4570da57cb9109cd7da1d06d2197f923a71139`
Uncommitted changes and owners: R04 product/tests/records and this status file
(DeepSeek). See `git status`.
Current packet and objective: bounded host-identity robustness fix for
`apple_chip_name()` (PATH-independent chip detection).
Completed changes: R04 node-owned admission repair, six proxy tests + one
two-Hub test, R04 report, plan/board/progress records.
Evidence: `.planning/2026-09-10-standalone-app-feasibility/r04-report.md`;
probe logs under `.build/deepseek-r04/`.
Self-review findings: first post-repair run exposed a `CancelledError` from the
new lease-sweep task during lifespan shutdown; fixed by swallowing cancellation
like `job_storage.budget_loop`. Re-verified 18/18.
Independent review status: not performed (self-review only).
Remaining confirmed defects: host-identity PATH fragility (next packet).
Unverified concerns: see above list.
External gates and authorization needed: see above list.
Owned processes / temporary data / cleanup: none outstanding.
Next smallest action: reproduce the chip failure with `PATH` lacking `/usr/sbin`,
then fix `apple_chip_name()` and run `test_standalone_connections.py` overview test.
First command to run:
`PATH=/usr/bin:/bin PYTHONPATH=.build/test-deps .build/voice-runtime/bin/python3 -B -m pytest -q "tests/test_standalone_connections.py::ControllerRegistryTests::test_the_overview_shows_every_machine_in_one_row"`
Acceptance criteria: chip is reported even when `/usr/sbin` is absent; the
overview test passes; no change to other hardware-identity consumers.
Important do-not-touch paths: `~/Downloads/model-library`,
`/Applications/Studio Hub.app`, other worktrees, `codex/recovered-music-draft`,
GatewayKH checkout.
