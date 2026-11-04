# Standalone Studio App Planning

## Active amendment — remote rendering Phase 1, 2026-11-04

Developer approved the Story-owned parity design and requested Hub's encode fix first: publish only actual per-node probed encode tuples, remove the default-medium admission ceiling, preserve honest owner opt-in and immutable accepted-job authority. Coordinate tuple/probe provenance with Story/Gateway before integration. Base `5738076`, feature `feat/render-encode-probes-20261104`, worktree `_worktrees/studio-hub-kh/render-encode-probes`. Normal focused tests, full gate, packaging and paced rollout (online Shops included) remain required; this Mac stays paused. Phases 2/3 await Story's runner/Sketch Draw/font schema. Shared design/evidence: `_orchestra/remote-render-parity-20261104/`. This amendment is in progress, not a qualification or release result. Backend checkpoint: reversible grant storage `f900a3e`, exact-cap admission/publication and durable job/receipt probe bindings implemented; legacy v1 cap bypass blocked. Detached focused R3 exit 0 (206 tests/174 subtests), claim R4 exit 0 (37/9), broader backend/fleet R5 exit 0 (271/204); final boundary R6 exit 0 (86/56). Gateway-required v2 dispatch binds root encode_authority with separate legacy/probed resolvers; read-only probe mapping supplies exact measured or labelled owner-grant execution authority/digest/bounds/provenance. V2 R7 exit 0 (211/180); final route projection R8 exit 0 (48/13). Durable controller/lifecycle wiring, native grant display, combined final verification and Gateway seam agreement remain outstanding. See HANDOFF for current evidence; no Phase 1 gate or deployment yet.

## Ordered completion plan — owner authorized 2026-09-21

**Current owner correction:** GatewayKH replaces GenStudio as the external client.
R09 must pin and test GatewayKH's actual contract. Legacy protocol identifiers and
historical receipts retain their original names until an evidenced migration is
needed. Continue all safe work runnable/testable on this Mac; physical hardware,
live enrollment, installed-app replacement and distribution gates remain separate.
GatewayKH remote work uses the existing Fleet token; optional Hub/admin credentials
are reserved for shared-voice administration, as its current provider definition specifies.

**Goal:** Finish the remaining handoff in priority order, retaining a usable,
truthful standalone Hub/Image/Voice/Subtitle app and the accepted Music extension.
**Architecture:** Keep native client → Hub → private workers. Reuse existing
identity, job receipts, model manifests, runtime isolation and output policy.
**Spec:** `docs/standalone-plan.md`, `docs/implementation-plan.md`, current board,
and `qualification/new-computer-resume-2026-09-21.md`; historical checklists are
evidence inputs, not instructions to rebuild already-working functionality.
**Execution:** Subagent-driven implementation; coordinator owns planning, design,
independent review, verification, canonical records and all Git integration.
**Base:** `1267a51170503a7801786c021a16b6a6a3d3b8af`, clean local/origin integration.
**Worktree:** `/build/.codex/worktrees/handoff-completion/studio-hub-kh`,
branch `codex/handoff-completion`. Existing worktrees remain preserved.

### Global constraints and completion rule

- Local builds/tests only; no GitHub-hosted compute or paid calls. Check workflows
  before every push. Reviewed, verified, mergeable changes may use feature PRs and
  merge into `implementation/voice-runtime`, then fast-forward local integration.
- Never change installed apps, production nodes, Pinokio, credentials/live job
  stores or the protected Downloads model library. Deployment, real enrollment,
  releases/tags/notarization and disruptive hardware operations require separate authority.
- Use verified project-owned runtimes and temporary test state. Acquire only exact
  manifest-defined inputs needed for an authorized qualification task; no whole-cache migration.
- Preserve configurable 50 GB/30-day per-device defaults and enforced integrity,
  authentication, accepted-job fencing, active-output protection and verified delivery.
- Ponytail: reuse current code; no speculative frameworks. Graphify CLI remains
  unavailable; the saved 747-node/1669-link graph covers older Voice source and is
  insufficient for current Hub/native/Music work. Verify callers directly; do not
  claim graph coverage or commit generated graphs. Restore code-only graph tooling
  under R05 if feasible without paid extraction.
- Each implementation task: reproduce → smallest fix → focused tests → independent
  review → proportionate full/build/packaged gate → integration and evidence.
  No claim of DONE while required proof is missing. Unknown bugs remain investigations.
- Execute top to bottom by wave. R01 and R02 may run concurrently because files
  do not overlap; review/integrate R01 before R02. Never concurrently edit shared
  native, broker, staging or Voice registry files. Later tasks rebase their stated
  base on the newest reviewed integration, not a stale initial commit.
- Luna handles mechanical records/scans; Sol at high or below handles lifecycle,
  integrity and security; Terra handles bounded cross-file UI integration.
  Workers have explicit owned files and cannot commit, publish or recursively delegate.

### Review focus

1. Late/cancelled responses cannot revive a stopped/replaced process or stale connection (R01).
2. Malformed inputs/metadata never erase original reference bytes or weaken valid inputs (R02).
3. Lost/malformed acceptance never causes untracked duplicate generation or false completion (R03);
   stable-ID idempotent reposts and the existing unverified-output fallback are distinct contracts.
4. Remote dispatch must preserve local pause/storage/physical-machine safeguards (R04).
5. New UI and artifact export remain source-bound, authenticated, keyboard-usable and truthful
   about missing models, uncertain jobs and unsupported hardware (R06–R10).

### R01 — Native lifecycle/rejoin correctness [VERIFIED — PR #12]

**Owner:** Sol/high worker; root independent review/integration.
**Owned files:** `native/StudioHub.swift`, `native/StartupRecovery.swift`,
`native/StudioAPI.swift`, `tests/native/main.swift`, `tests/native/api_main.swift`;
one focused lifecycle test runner under `tests/` only if the real app lifecycle
cannot be exercised through existing fixtures (coordinator approves exact path first).
**Interface:** Existing `StartupRecovery`, `adoptStartupMembership`, `refresh`,
`rejoinControllerIfNeeded`, process ownership and authenticated health polling.

- [x] Reproduce N-01 with ready-but-unhealthy uptime ≥60 seconds (controlled clock,
  no real wait): crash budget must not reset; actual healthy uptime may reset it.
- [x] Reproduce N-03: hold a health response, terminate its owning process,
  then deliver it; stopped/error/new-process state must win over the old response.
- [x] Reproduce N-02: first identity request fails then succeeds (paired version
  path inspected, not separately fault-injected);
  membership recovers without manual join or restart, with no duplicate successful
  rejoin, no enrollment for minted/standalone identity, no retry after disconnect/leave.
- [x] Fix the existing lifecycle: successful-health interval, post-await ownership
  guards and bounded/idempotent retry through existing polling. No generic scheduler.
- [x] Run native boundary/API fixtures and `bash native/build.sh`; review delayed,
  disconnected, token-changed and explicit-stop cases before integrating.

### R02 — Reference boundary integrity [VERIFIED — PR #12]

**Owner:** coordinator inline fallback after second worker service rejection;
independent review by `music_recovery_read`. **Owned files:**
`components/hub/backend/execution_assets.py`, `tests/test_hub_voice_references.py`,
`tests/test_execution_asset_purge.py`; root owns canonical records.
**Interface:** Existing ExecutionAssetError and reference stage/read/purge contract.

- [x] Reproduce C-01 through the real temporary authenticated endpoint with
  `NaN`, `Infinity`, `-1`, reversed and zero-length intervals. Valid intervals
  have finite start ≥0, finite end >start; preserve existing segment ordering rules.
- [x] Require a controlled machine-readable 400 before creating/altering an asset;
  valid local/GenStudio roundtrips and model-specific preparation remain unchanged.
- [x] Reproduce C-02 by corrupting only stored expiry to `invalid-deadline` and
  restaging/reading it. Choose controlled refusal (not silent metadata repair),
  preserving original clip/metadata and unrelated assets; reject non-finite expiry too.
- [x] Fix shared parsing/validation, not each route separately; preserve purge's
  existing skip-invalid behavior and transaction/locking/symlink safeguards.
- [x] Run `PYTHONPATH=.build/test-deps .build/voice-runtime/bin/python3 -B -m pytest
  -q tests/test_hub_voice_references.py tests/test_execution_asset_purge.py
  tests/test_hub_standalone.py`, then independent fault-injection review.

### R03 — Voice acceptance/completion fault matrix [VERIFIED LOCAL — PR #13]

Clean candidate `936b3e5`; [PR #13](https://github.com/theng12/Studio-Hub-KH/pull/13)
is the integration record. Full gate and limitations:
`qualification/local-readiness-2026-09-21.md`. This does not qualify real models or GatewayKH.

**Owned scope:** `components/hub/backend/broker.py`,
`components/hub/backend/artifact_metadata.py`, its existing tests, and
`tests/hub_regression/cases/test_voice_durability.py`, reference existing
`test_image_uncertainty.py`/`test_music_durability.py`; Sol/high implementation.

- [x] Return missing ID, non-object receipt, unknown state, missing output URL,
  invalid bytes/hash/media and changed identity from a fake Voice worker.
- [x] Count generation POSTs **and worker-created jobs**; stable-ID identical-body
  reposts are permitted by the existing Voice worker idempotency contract. One
  worker attempt/result must remain; changed body with same ID remains a conflict.
- [x] Reproduce unknown live-poll state and done-without-output-URL in both live
  and reconcile paths. Keep these uncertain rather than publishing false completion.
- [x] Preserve valid terminal receipt plus failed artifact fetch/verification as
  the existing `done + artifact_metadata_error` compatibility path; do not rewrite
  billing semantics. No missing-URL bypass and no falsely verified metadata.
- [x] Add a truncated-WAV payload fixture. Preflight reproduced a 1,644-byte WAV
  truncated to 64 bytes still claiming 0.1s; require full declared PCM frame bytes
  before reporting verified audio metadata, using the existing standard-library parser.
- [x] Fix only reproduced gaps using existing identity/receipt validation and
  Voice's stable client-request recovery; preserve Image/Music behavior.
- [x] Run all `tests/hub_regression/cases`, Voice delivery and fleet fixtures,
  independent review and a model-free packaged Hub gate. If no failure reproduces,
  retain the negative tests and close the candidate with evidence, not a rewrite.

Preflight ruling: the original shorthand “one POST/no DONE without verified
receipt” was too broad for the inherited contract. Preserve deliberate stable-ID
reposts and generated-but-unverified results; fix false terminal states, absent
artifact references and falsely verified payloads. Cost if wrong: callers still
need to check metadata availability before treating a generated result as verified;
this task does not certify or change the external billing counterpart.

Independent-review ruling: stable-ID deduplication is **worker-local**. The Hub's
work-stealing queue can select a different worker after a malformed successful
acceptance, so a same-worker replay test does not prove one fleet-wide attempt.
Fence malformed successful acceptance with missing identity as uncertain/manual;
do not add a pinned-retry subsystem. Preserve the real worker's idempotent replay
contract separately. Cost: receipt-less accepted work needs operator reconciliation
instead of unsafe automatic failover. Malformed live/recovery JSON must retain the
same classification even when preceded by a genuine transport error.

### R04 — Peer admission negative qualification [LOCALLY VERIFIED — self-review; independent review pending]

Four current-source bypasses reproduced on `9a4570d` and repaired on the same
base; see `r04-report.md`, `r04-preflight.md` and
`qualification/evidence/local-readiness-2026-09-21/r04_admission_probe.py`.
Two-real-Hub stale-capability proof added for the Studio-disable case; the other
three gates are covered at the real peer HTTP route, not each at two Hubs.

**Owned scope:** `tests/test_standalone_connections.py`, `tests/test_studio_proxy.py`;
any fix in `components/hub/standalone.py`, `backend/broker.py`, worker admission
requires root's traced-boundary decision before expansion; Sol/high.

- [x] Use two disposable Hub processes, never real enrollment. Submit via intended
  peer route with node-local model paused, storage exhausted and a physical node busy.
  All four reproduced (200 + 1 worker request each); after repair 409/409/507/409
  with zero worker requests.
- [x] Trace controller and node decisions, distinguish stale capability reports
  from worker refusal, and require enforced node-owned safeguards without duplicate work.
- [x] Preserve intended controller→node worker forwarding and separate credentials.
  Implemented the smallest shared admission repair: gate only work-submitting POSTs,
  keep GET/DELETE/status/artifact/ACK reachable, never forward the caller credential.
- [x] Prove happy dispatch, negative admission, cancellation, artifact access and
  offline-node handling (proxy negatives plus existing controller/node tests).
  Accepted asynchronous peer work holds a machine lease until observed terminal,
  with a safety TTL; the POST's return does not free the machine.
- [ ] Independent review before closing C-04. No independent review performed;
  R04 is locally verified and self-reviewed only.

### R05 — Portable bootstrap and accurate handoff [PARTIAL — records reconciled; Image offline inputs and Graphify open]

**Owned scope:** `native/build.sh`, `native/render-page.sh`,
`packaging/image-runtime/build.sh`, related runtime tests and `docs/NEW-COMPUTER-HANDOFF.md`;
Terra for build scripts, Luna for root-approved mechanical documentation edits.

- [x] Preserve macOS14 deployment target while documenting/validating SDK selection;
  exercise valid explicit selection and unavailable SDK failures, not blind newest-SDK fallback.
- [ ] Establish declared CPython/wheel/cache inputs for Image; test offline rebuilding
  with an empty user cache and project inputs. Never silently use the network.
  Declared CPython archive + lock verified present; offline rebuild attempted with an
  empty user cache and failed **loudly** (`contourpy` and 20 other locked packages are
  absent from `.build/uv-cache`). No silent network use. Gate: an explicitly populated,
  declared Image wheel cache (a network fetch needing separate authorization).
  Evidence in `r05-report.md`.
- [x] Record separate test-dependency setup and safe explicit paths for old qualification
  harnesses; replace no live library and avoid ctime-changing hardlinks into verified packages.
  `.build/test-deps` is separate from `.build/voice-runtime`; exact versions recorded in
  `r05-report.md` and the handoff.
- [x] Reconcile ST-03/04/05/06/08/10/11 checkboxes against current source and exact receipts.
  Preserve history; label missing evidence separately from missing implementation.
  Delivered as [r05-report.md](r05-report.md); ST-04/05/06 status lines corrected. Individual
  checkboxes were deliberately not bulk-ticked without per-receipt proof.
- [ ] Check local code-only Graphify availability; restore/update an ignored code-only
  graph if possible without paid model calls or private data ingestion. Otherwise record limitation.
  Recorded unavailable in `r05-report.md`; callers inspected directly.

Also repaired here: `apple_chip_name()`/`apple_machine_type()` no longer depend on
`sysctl`/`system_profiler` being on PATH (commit `b68e7b2`).

### R06 — Native Music workflow [LOCALLY VERIFIED / self-reviewed; independent review pending]

**Owned scope:** `native/StudioAPI.swift`, `native/StudioViews.swift`,
`native/ShellCore.swift`, `tests/native/api_main.swift`, focused Music UI tests;
Terra implementation after root/Impeccable design review. Serialize native edits.

- [x] Reuse the Image/Audio form-result layout and existing durable Music Hub routes.
  Show supported exact model/controls, install/readiness block, submit/progress/cancel,
  recovery uncertainty, per-studio and unified history. No new job subsystem.
  Music models decode from the worker's `generation_defaults`/`generation_limits`;
  submit uses `POST /api/hub/jobs` with `modality: "music"`.
- [x] Wire authenticated artifact open/save with byte/hash verification and ACK only
  after verified save. Preserve output protection when export or acknowledgement fails.
  Corrupt artifacts are rejected with no ACK; a failed save never ACKs; a verified
  save ACKs exactly once; a rejected ACK leaves the file on disk.
- [x] Test request validation and stale connection/job responses with native API fixtures;
  render minimum/normal sizes, keyboard/focus/accessibility and long/error/empty states.
  Native fixtures cover the cases above; offscreen renders at 1040×680 and 1920×1053
  were inspected. Actual GUI save-panel/keyboard interaction is not exercised.
- [x] Stage model-free app; prove actual UI routing/readiness. Native build and the
  three native suites pass; the workspace renders a real workflow.
- [ ] Independent review. Real generation is R07 and is **not** claimed by R06.
  Evidence: [r06-report.md](r06-report.md).

### R07 — Current-artifact model matrix [QUEUED]

**Owned scope:** existing `qualification/evidence/` harnesses, model task cards and
exact manifest/runtime receipts; model adapters only after a failing reproduction.
Sol/high for integrity/adapters; root selects exact inputs and validates outputs.

- [ ] Inventory only authorized project model inputs; request no protected-library scan.
  Select manifest-defined inputs for the next missing check, not all model caches.
- [ ] Requalify FLUX, Qwen0.6/1.7 Base, OmniVoice and Whisper Turbo against current
  artifact; historical successes are starting evidence. Check reference origins, real
  export/ACK, cancellation, restart/history, errors and immutable package bytes.
- [ ] Obtain separate VoxCPM2 4-bit/8-bit/bf16 success/failure receipts with engines
  idle on available suitable hardware; do not infer root cause solely from memory wording.
- [ ] Exercise existing private NPZ→safetensors views for real Whisper Base/Small;
  do not reimplement conversion because old cards called them catalog-only.
- [ ] Run pinned ACE-Step Turbo/0.6B planner short and accepted long-duration Music
  workloads, cancellation, restart/reconciliation and WAV artifact checks.
- [ ] Record exact model/runtime/source/workload/host, output hash, duration and memory;
  preserve unsupported combinations and missing hardware as qualification gates.

### R08 — End-to-end native usability and export acceptance [QUEUED]

**Owned scope:** existing native views/API and UI fixtures, ST-03/ST-08 evidence;
root/Impeccable owns review, Terra only bounded fixes after reproduction.

- [ ] Drive real native Image/Voice/Subtitle/Music Save panels against an isolated
  current app; verify chosen output bytes, receipt ACK and history after restart.
- [ ] Check minimum/normal layouts, focus order, keyboard actions, errors, missing
  models and disconnected state. Reuse existing views; no aesthetic redesign.
- [ ] Resolve retained task-card review gaps with exact current evidence, not stale
  'missing export' claims where save/ACK code already exists.

### R09 — Fleet and GatewayKH completion [PARTIAL — Hub-side covered; counterpart edits and physical enrollment gated]

**Owned scope:** ST-10, existing Hub connection/settings/stats and native Connections;
root owns topology/contract decisions, Sol/high bounded lifecycle/data changes.

Hub-side coverage already present (no counterpart change needed):
`tests/test_standalone_connections.py` (33 passed) drives discovery, join/adopt,
dispatch, artifact, cancel, authentication and token rotation across two real Hub
processes; `tests/test_hub_delivery.py` proves the Image/Subtitle ACK contract
(worker ack, idempotency, failure preservation); `tests/test_hub_statistics.py`
and the native statistics path cover attribution. R04 added node-owned admission
and stale-capability refusal.

- [ ] Complete disposable counterpart fixtures: discovery, submit/poll/cancel,
  fetch/ACK, retries/timeouts, token rotation and reconnect with accepted jobs.
  Hub side mostly covered; the *GatewayKH* timeout/retry matrix is not.
- [ ] Prove selected-device settings with durable acknowledgement and no unselected
  fanout; node identity, freshness, attempts-versus-items and location deduplication.
  Hub-side selection is now proven: `set_mode`/`release` with `studio_ids` touch only
  the selected Studio and leave its sibling unchanged
  (`tests/test_memory_policy_contract.py`, 16 passed). Durable acknowledgement and
  two-machine deduplication remain unproven.
- [ ] Pin and verify the GatewayKH counterpart contract without changing billing,
  accounts or assets. Real counterpart changes/enrollment require separate authority.
  Read-only inventory at `gatewaykh-contract-inventory.md` @ `736d7b0`: counterpart
  gaps are **no delivery ACK**, **no Hub DELETE cancel**, omitted Music lane and
  partial voice-reference metadata. Fixing these needs explicit GatewayKH scope
  authorization, which this session does not have.
- [ ] Run second-physical-node checks only when hardware and enrollment authority are
  available; fixture success alone cannot close physical ST-10/ST-11 evidence.

### R10 — Final kit, hardware and deployment gates [QUEUED / authority-dependent]

**Owned scope:** `packaging/release_kit.py`, `qualification/focused-release.md`,
ST-11, manifests/licenses and per-machine receipts; root integration.

- [ ] Build a clean exact-source candidate; run complete relevant tests/native build,
  verify immutable runtime/tree identity, licenses/notices and selected model packages.
- [ ] Exercise configured download and offline copy/import using temporary clean state:
  interruption, missing companions, moved library and no redundant full model copy.
- [ ] On authorized clean target hardware without dev tools/network, prove each
  advertised eligible mode, missing SSD, repair/recheck, cleanup, crash/cancel and rollback.
- [ ] Explicit hardware gates: original M1/oldest supported Sonoma and newer OS;
  actual login/logout/reboot/startup approval and upgrade recovery. Never simulate
  these into a physical pass or promise generation through reboot.
- [ ] Independent release review; update supported-model/hardware claims only from
  evidence. Installed app replacement, distribution/releases/signing/notarization and
  real enrollment remain separate explicit approvals, not automatic plan completion.

### Preflight ownership/interface check

| Tasks | Shared boundary | Decision |
| --- | --- | --- |
| R01/R02 | none of their owned source/tests | Concurrent wave 1 permitted; integration ordered. |
| R01/R06/R08/R09 | native API/views/connection lifecycle | Serialize edits and carry reviewed connection guards forward. |
| R02/R03 | staged references consumed by broker | R02 keeps existing successful contract; R03 starts after review. |
| R03/R04 | broker receipts/admission | Sequential; preserve uncertainty and no-replay guarantees. |
| R05/R06/R07/R10 | build/staging/model runtime identity | Serialize; qualify rebuilt exact artifacts, not version labels. |
| R01–R10 individually | stated files, checks and deliverable | Each has explicit failure/success boundaries; physical gates are not code tasks. |

Planning uses this existing canonical directory and its `progress.md` ledger;
no additional Superpowers planning/workspace system is created. Current skills'
extra approval pauses do not supersede the owner's explicit plan-and-execute
authorization. Binding security/deployment boundaries still apply.

## Active takeover — new computer, 2026-09-20

[MEASURED] **Resumed 2026-09-21:** integration and GitHub match `9f25c03`
(reviewed Music contract PR #10); the runtime continuation is preserved on
`codex/music-package-runtime`. The current checkpoint is that reviewed runtime
segment. Root owns the board and integration. The bounded worker completed
normalized accepted-parameter recovery and rollback of terminal publication on
failed persistence. Final independent review found no remaining findings.
Product commit `c9eadca` and its clean artifact are verified; PR #11 records the
integration state. After integration, the single next task is native lifecycle
N-01/N-02/N-03 reproduction/repair, followed by reference-validation C-01/C-02.

[MEASURED] This resume verified native build and the selected SDK 26.5, the pinned
Voice runtime, and the matching Image runtime already available in the installed app.
The Image runtime was copied separately and reverified in the main `.build` tree;
no model library or installed app was changed. Affected tests (111 plus 122 subtests),
five original audit probes, two Image process tests and a model-free assembled Music
service proof pass. Final root gate: 631 tests OK (7 skips), nested Voice 722
passes/19 skips and Hub 85 passes. Native lifecycle and reference-validation work
precede native Music UI; model and physical qualification remain open. Exact
artifact evidence is in `qualification/new-computer-resume-2026-09-21.md`.

The following September 20 entries describe the prior checkpoint and are superseded
by this resume note and the current board where they differ.

[MEASURED] Codex `/root` owns the current continuation. The integration/default branch
is `implementation/voice-runtime`. Reviewed PRs #6 through #8 are integrated at `47e7e1c`,
`ea1e622`, and `a012c0a`; reviewed PR #9 carries the H-05 fix. Read `docs/tasks/BOARD.md`,
`docs/NEW-COMPUTER-HANDOFF.md`, and
`qualification/bug-audit-2026-09-20.md`; this section and the board's TAKE OVER HERE
block are the only current plan. Later sections are historical evidence.

[MEASURED] Intake established an arm64 Mac14,13 with 32 GiB RAM, macOS 27.0, Command
Line Tools, and no repo-local runtime. The pinned Voice runtime now builds locally.
Reviewed PR #3 fixed FFmpeg/FFprobe deployment metadata to macOS 14.0. Reviewed PR #4
closed H-01; reviewed PR #6 closed H-02 accepted-Voice receipt durability; reviewed
PR #7 closed H-03 cancellation truthfulness and lease-expiry fencing; reviewed PR #8
closed H-04 deny-by-default fleet worker routing; reviewed PR #9 carries the H-05
truthful Subtitle artifact-disposition fix.

[MEASURED] All five original takeover probes now pass together. H-05 passed 106 affected
Subtitle/Hub tests with seven real-model skips, 73 Hub regressions, and independent
fault-injection/restart review. The next bounded source task is the recovered Music
package/engine contract decision before worker, supervisor, peer-routing, and native UI
integration. Do not merge the WIP wholesale.

[MEASURED] The root gate is not globally green on this computer. The H-01 tests passed,
while a freshly built Voice runtime lacks the expected `espeak-ng` command and an old
staged 0.5.4 app conflicts with source 0.6.38. The protected 38 GiB legacy library in
`~/Downloads/model-library` remains untouched. No deployment, publication, signing,
notarization, model inference/download, or real fleet enrollment has occurred.

## Historical planning state

Updated: 2026-09-15. Canonical planning directory remains this directory.

## Historical goal recorded 2026-09-15

Execute the owner-authorized standalone Hub + Image + Voice + Subtitle plan.
`implementation/voice-runtime` @ `cd6ed5d` is the integration line. ST-01 foundation,
ST-02 bundled Hub, ST-07 output storage and ST-09 startup/recovery are DONE and
integrated. ST-03 Packaged FLUX is merged and a real packaged 1K generation is
evidenced, but export/receipt acknowledgement, retry counts and independent review
remain, so ST-04/05/06/08 correctly stay WAITING on it. The September 15 owner
instruction authorizes the remaining work and both missing Whisper downloads.

## Confirmed scope

- Image: current fleet FLUX.2 Klein 4B MLX 4-bit.
- Voice: Qwen3 TTS 0.6B/1.7B Base, VoxCPM2 4-bit/8-bit/full, OmniVoice full.
- Separate Subtitle backend, workspace and jobs; three Whisper choices.
- Bundled Hub controls local and optional connected work; protected controller token for GenStudio.
- All operating defaults editable through backend-owned settings.
- Per-device output defaults: 50 GB total across studios, 30 days after generation, either condition triggers cleanup.
- Editable automatic login startup; preserve fixed authentication/integrity/active-job safeguards.
- Tested runtimes bundled in the app; complete exact model packages external, optionally one offline SSD kit.
- Apple Silicon/macOS 14 target pending physical qualification. Existing Pinokio nodes untouched.
- Chat, Music and Video deferred. Prior six-function release order is superseded.

## Sources of truth

- Product design: docs/standalone-plan.md.
- Execution/files/dependencies: docs/implementation-plan.md.
- Task status/ownership: docs/tasks/BOARD.md; checklists and handoffs: ST-01 through ST-11.
- Source evidence: research/rescope-model-inventory-2026-09-15.md,
  research/rescope-hub-storage-2026-09-15.md and research/rescope-packaging-2026-09-15.md.
- Historical implementation/qualification: progress.md and qualification/native-redesign.md.

## This planning pass

- [x] Read applicable instructions, inspect status and preserve pre-existing work.
- [x] Inspect existing app, model, Hub/storage and packaging sources.
- [x] Confirm owner retention clarification: 50 GB and 30 days, editable per device.
- [x] Write narrowed design, build order, task cards and handoff protocol.
- [x] Complete independent review and documentation consistency checks.
- [x] Deliver the concrete plan and next task: ST-01 is READY.

## Implementation state

Current built artifact: 0.4.0, bundled Hub with a private Voice worker and real Qwen
CustomVoice / Whisper Turbo evidence; ST-02 added the authenticated Hub, editable device
policy and independent Subtitle registry, and ST-09 added bounded startup recovery.
Requested Qwen **Base** models are still not proven by CustomVoice evidence. Image is
merged in source but not bundled into the recorded artifact: staging it needs
`--image-runtime` and the pinned Image runtime currently exists only in
`.worktrees/st-03/.build/st03-image-runtime-worker`.

ST-00 planning and ST-01/02/07/09 are complete and integrated. ST-03 is the only open
foundation and gates ST-04/05/06/08. Historical 676 / 107 / 640 regressions are prior
runs; the current integrated counts are recorded on the board. Physical hardware and
distribution conditions remain explicit, not reasons to stall independent local
implementation.

## Preflight and exit snapshot

Applicable instructions: independent standalone scope; canonical plan; exact source
adoption; preserve old nodes and unrelated changes; smallest reusable implementation;
local verification; no hosted compute, deployment or live credentials.
No Pinokio launcher creation/editing occurs; launcher destination/example/capture checks
are not applicable. Shared plan documents are root-owned; agents own their reports.

Baseline: 29d0baa, no Git remote configured. Pre-existing progress additions, graph query
stamp, SSD research and Yume research are preserved. No model downloads, polling,
runtime installation, generation, app rebuild or production changes in this pass.

## Historical record

The September 10 feasibility plan, Voice proof, September 11 native 0.2/0.3 work and
September 14 personal stock/research remain in progress.md and qualification records.
Their original tests/limitations remain evidence for those artifacts only.

## Latest owner addition

Model download/copy-folder paths, location membership/telemetry and deployment-independent IP/token APIs are added. ST-01 and ST-02 are DONE. ST-10.TOPOLOGY is resolved: preserve GenStudio-to-location-controller-to-nodes routing. Future standalone GenStudio implementation remains deferred.

## Older-device addition

Preserve original M1/MLX support on the oldest qualified Sonoma release, without requiring the newest OS. ST-01/02/08/09/11 now contain OS compatibility and update-recovery checkpoints. Existing macOS14 target remains unqualified physically. Model and endpoint requirements from prior turns remain active; ST-10.TOPOLOGY is resolved; existing routing remains unchanged.

## ST-01 implementation evidence

- Baseline: 28 unittest cases ran successfully (one skipped) before changes.
- Runtime/model imports, ten exact manifests, separate Image hash lock and notices,
  file receipt validation and resumable download/copy implementation are complete.
- Real Turbo copy/import verified 1,618,590,987 bytes; moved library and offline
  tokenizer round trip passed. Real HTTPS smoke verified 89,500 companion bytes.
- Existing Voice regression: 640 passed, five warnings. Foundation checks: 84 passed
  across the full suite and subsequently resolved runtime-path skip. Local integration
  commit `072abf6`; 724 passing tests total. Details are recorded in qualification/st01.md and docs/tasks/ST-01.md.
- Independent review covers filesystem races/durability, explicit full Verify,
  source-adoption identity and model/runtime qualification boundaries.
- Original user progress/research/graph changes are preserved outside this worktree.
- Pinokio launcher creation, URL capture, destination and example rules are not
  applicable: no launcher is being edited. Existing fleet/source checkouts remain
  read-only. No hosted compute, publication, deployment or model-weight download.

## ST-01 exit

Complete: all ST-01 checkpoints, independent review and local integration are recorded.
ST-02 is READY; ST-03 and later phases remain WAITING. No remote exists for a PR.
The clean implementation worktree is retained with build-side runtime/model proof
artifacts; disposable input scaffolding and superseded exploratory runtime were removed.
All owned test processes exited. Applicable preflight/exit checks are satisfied; no
launcher, native app artifact, installed fleet machine or credential was changed.


## ST-02 active execution — 2026-09-15

Owner authorized remaining work and missing Whisper Base/Small downloads, with cheap
independent agents. Start c16833c; isolated .worktrees/st-02. Existing unrelated changes
remain in original checkout. Apply standalone/canonical-plan/read-only-source rules;
no Pinokio launcher edits, live fleet changes, hosted compute or publication.

Dispatch record: Luna/high whisper_stock starts pinned two-model download+hash proof
in original ignored model-library; prove with .build/whisper-download report. Terra/high
hub_backend owns adopted Hub backend/tests/provenance; prove focusedtests and source
receipt. Sol/high hub_contract_review owns read-only integrationmap and independent
review; root owns packaging/native and shared contracts/board. No overlapping writers.

Work sequence: baseline -> exact Hub adoption -> private process/auth lifecycle ->
native Hub API routing -> packaged fixture submit/status/cancel/result/recovery ->
local suites/build/smoke -> independent review/integration. Continue later READY tasks
under owner's remaining-work authorization; retain physical qualification limitations.

Download result: both Whisper packages passed full rehash; 628,667,821 bytes fetched
and nine existing file placements reused. Receipt: qualification/whisper-stock-2026-09-15.md.
Luna whisper_stock now owns tests/test_hub_process.py for process/crash boundaries.
Sol completed native/StudioAPI.swift and its API fixtures, then independently reviewed
process/auth/artifact boundaries. Root's full native build passed. Terra is resolving
artifact, download lifetime and admission/replay findings with focused job tests.
Luna/high hub_regression starts at c16833c in the same isolated ST-02 worktree, owning
only tests/hub_regression/** and qualification/st02-regression.md; completion proof
is preserved-core upstream tests run locally with .build/st02-hub-regression.log.

Terra's continuation hit model capacity; Sol/high hub_backend_finish took over its
exact owned backend/test paths and completed the remaining fixes. Luna completed
32 isolated retained-Hub regressions and is independently reviewing final admission,
identity and routing changes. Root added the existing delivery ACK route; Luna
reviewed it, then supplied statistics and two pause/resume routes. No overlapping
writers remain. Full project suite: 107 passed; existing Voice suite: 640 passed;
native build passed. ST-02 is REVIEW until the final artifact, review disposition
and local integration are recorded. Hub-owned Voice's old independent cleanup
timer is disabled; ST-07 will enforce the shared protected-output policy.


ST-02 final review repairs: potentially accepted synchronous Subtitle work remains
uncertain on lost response, cancellation and restart. Its saved physical machine
continues to participate in existing admission, preventing overlap while other nodes
remain usable. Exact GenStudio replays precede new model readiness and retain conflict
validation. Native surfaces uncertainty without resubmitting. Packaged Subtitle's
legacy cleanup timer is off pending the shared ST-07 policy.
Proof: root 107 passed (52.71 s); final admission/config + retained Hub wrapper
11 passed (13.40 s, including 7 real-worker cases and 32 retained upstream cases);
Voice 640 passed (34.59 s); native boundary/API/full build passed. Final assembled
artifact and review disposition remain required. Whisper Base/Small are fully
file-verified and stock receipts are retained; no further model download is needed
for the current scoped catalog inventory.


## ST-02 exit

DONE on 2026-09-15: implementation 4a5124c is independently reviewed and locally
integrated. Final native 0.4.0 app is in the original dist/ directory; eight bundled
job-contract cases, real Hub/Voice startup/settings/auth/pause/statistics and relocated
startup all pass, with no child processes or readiness files left. Payload/runtime/tree
hashes match after execution. See qualification/st02.md for exact evidence.
The 0.3.0 preview and original unrelated files are preserved. No remote, hosted compute,
publication, deployment, old-node changes or new model inference. ST-03, ST-07, ST-09 READY.


## Parallel batch after ST-02

Owner authorized continuing remaining implementation with cheap-model fanout.
Dispatch baseline 5358a52; isolated worktrees:
- st03_image, Sol/high, .worktrees/st-03: Image source/runtime/private worker; proof
  offline relocatable runtime, private API/lifecycle and actual 1K FLUX receipt.
  Root owns shared native Image UI/API, Hub wrapper and staging integration there.
- st07_storage, Terra/high, .worktrees/st-07: Hub shared output index/settings,
  admission/protected cleanup and Voice private receipt; proof temporary-file policy
  tests plus affected retained regressions. Never delete real generated outputs.
- st09_startup, Luna/high, .worktrees/st-09: native StudioHub/ShellCore lifecycle,
  login settings and fixtures; proof native build, lifecycle cases and physical-test
  procedure. No live login registration. Root leaves these native files to this owner.
Shared board/version/changelog are root-owned. API/View/native Image edits remain
separate from startup-owned files. Agents supply contracts before integration; whole
phase completion still requires independent review, exact tests and local integration.
No new Git remote, deployment, signing, fleet enrollment or physical qualification.


## ST-07 exit

Reviewed storage implementation `8978aa0` is locally integrated by `0e97d98`.
Root reran all 12 focused output/settings/delivery/jobs tests after integration: passed
in 17.473 seconds. ST-07 is DONE; native receipt/export wiring stays in ST-08 and
real Image registration will additionally be exercised by ST-03. No user outputs
were deleted. All ten scoped model packages now have file-verification receipts;
the remaining seven packages were imported from local stock with no network download.
