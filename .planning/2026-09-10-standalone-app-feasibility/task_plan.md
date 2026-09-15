# Standalone Studio App Planning

Updated: 2026-09-15. Canonical planning directory remains this directory.

## Current goal

Execute the owner-authorized first phase, ST-01, of the standalone Hub + Image + Voice
+ Subtitle plan. The narrowed design is accepted. Complete reviewed runtime/model
foundations and hand off ST-02; do not silently begin later phases.

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

ST-00 planning is complete. ST-01 foundation is in final review; ST-02 through ST-11
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

Model download/copy-folder paths, location membership/telemetry and deployment-independent IP/token APIs are added. ST-01 is in final implementation review. ST-10.TOPOLOGY is resolved: preserve GenStudio-to-location-controller-to-nodes routing. Future standalone GenStudio implementation remains deferred.

## Older-device addition

Preserve original M1/MLX support on the oldest qualified Sonoma release, without requiring the newest OS. ST-01/02/08/09/11 now contain OS compatibility and update-recovery checkpoints. Existing macOS14 target remains unqualified physically. Model and endpoint requirements from prior turns remain active; ST-10.TOPOLOGY is resolved; existing routing remains unchanged.

## ST-01 implementation evidence

- Baseline: 28 unittest cases ran successfully (one skipped) before changes.
- Runtime/model imports, ten exact manifests, separate Image hash lock and notices,
  file receipt validation and resumable download/copy implementation are complete.
- Real Turbo copy/import verified 1,618,590,987 bytes; moved library and offline
  tokenizer round trip passed. Real HTTPS smoke verified 89,500 companion bytes.
- Existing Voice regression: 640 passed, five warnings. Final foundation checks and
  integration are recorded in qualification/st01.md and docs/tasks/ST-01.md.
- Independent review covers filesystem races/durability, explicit full Verify,
  source-adoption identity and model/runtime qualification boundaries.
- Original user progress/research/graph changes are preserved outside this worktree.
- Pinokio launcher creation, URL capture, destination and example rules are not
  applicable: no launcher is being edited. Existing fleet/source checkouts remain
  read-only. No hosted compute, publication, deployment or model-weight download.
