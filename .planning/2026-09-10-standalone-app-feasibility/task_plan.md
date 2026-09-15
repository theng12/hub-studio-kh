# Standalone Studio App Planning

Updated: 2026-09-15. Canonical planning directory remains this directory.

## Current goal

Execute the owner-authorized standalone Hub + Image + Voice + Subtitle plan.
ST-01 is integrated. The September 15 owner instruction authorizes the remaining
work and both missing Whisper downloads. Continue eligible phases in board order,
using bounded parallel assignments and reviewed integration.

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

Current built preview: 0.3.0, Voice-only packaging with real Qwen CustomVoice and Whisper
Turbo evidence. Native shell, immutable runtime and relocation proof are reusable.
Requested Qwen Base is not proven by CustomVoice. Hub/Image are not bundled.
Historical 676 regressions and 28 packaging tests are not new test runs.

ST-00 planning is complete. ST-01 foundation is DONE and locally integrated; ST-02 through ST-11
remain pending and their board controls readiness. Start runtime/model contracts, then bundled local Hub, then packaged FLUX.
Physical hardware and distribution conditions remain explicit, not reasons to stall
independent local implementation.

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

Model download/copy-folder paths, location membership/telemetry and deployment-independent IP/token APIs are added. ST-01 is DONE; ST-02 is READY. ST-10.TOPOLOGY is resolved: preserve GenStudio-to-location-controller-to-nodes routing. Future standalone GenStudio implementation remains deferred.

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
