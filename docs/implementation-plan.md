# Focused Studio Hub Implementation Plan

> For agentic workers: use executing-plans or subagent-driven-development for implementation, scoped to the assigned task. Extend this canonical plan; do not initialize another planning system.

**Goal:** One standalone Apple Silicon app containing Hub, Image Studio, Voice Studio and independent Subtitle Studio.
**Architecture:** Native client → bundled Hub → private studio workers. Qualified immutable runtimes, external model packages and one device output policy.
**Tech stack:** Existing SwiftUI/AppKit, Python, MLX, SQLite and macOS ServiceManagement.
**Spec:** [Focused design](standalone-plan.md).
**Status:** ST-01 foundation implemented from checkpoint 2fc8ee0; see the board for review/integration state and [phase evidence](../qualification/st01.md). Later app integration and model inference remain pending.

## Shared constraints

- Apple Silicon; macOS 14 target requires physical qualification.
- Include original M1 and qualify the oldest practical Sonoma point release per model/runtime. Preserve last-compatible app/runtime operation while OS upgrades are deferred; no newest-OS requirement by default and no generation-through-reboot promise.
- Only the ten choices in the design. Chat, Music and Video are deferred.
- Target devices need no Pinokio, Git, package manager, compiler or system Python.
- Editable operating defaults, including per-device 50,000,000,000 bytes of generated files and 30 days from generation.
- Integrity, authentication, active-job protection and verified delivery remain fixed safeguards.
- Existing Pinokio checkouts are read-only references; no old-node migration.
- Local checks only; no GitHub Actions, publication, deployment or live enrollment.
- Do not copy live environments, credentials, jobs or whole caches.
- Preserve personal stock, pre-existing changes and historical proof artifacts.

## Sequence and parallel work

~~~mermaid
flowchart TD
  A[ST-01 Runtime/model contracts] --> B[ST-02 Local Hub]
  B --> C[ST-03 Packaged FLUX]
  C --> D[ST-04 Qwen Base]
  C --> E[ST-05 Vox and Omni]
  C --> F[ST-06 Subtitle]
  B --> G[ST-07 Output cleanup]
  C --> H[ST-08 Native workflows and pilot]
  G --> H
  B --> I[ST-09 Startup/recovery]
  B --> J[ST-10 Fleet/GenStudio]
  I --> J
  J --> K[ST-11 Full qualification]
  D --> K
  E --> K
  F --> K
  H --> K
~~~

**Start ST-01, then ST-02 and ST-03.** A real FLUX image through the packaged Hub is the first new vertical slice. ST-04/05/06 can then progress independently. ST-07 and ST-09 can proceed after ST-02. Native ST-08 starts after FLUX/storage; connection ST-10 starts after Hub/software lifecycle, independently of physical test-machine availability.

The intermediate pilot is FLUX + Qwen 0.6B Base + Whisper Turbo, with usable native controls, Hub admission, cleanup and startup. It does not complete the full Voice/Subtitle scope. ST-08.PILOT waits for independently reviewed and integrated QWEN06/Turbo receipts, not every model card. All remaining real-model and physical startup checks are mandatory at ST-11.

## File ownership and interfaces

| Owner | Existing entry points / intended additions |
| --- | --- |
| Runtime/model | packaging/voice-runtime/, packaging/stage_voice.py, research/source-checkouts.json; new model-manifests/ and docs/runtime-contract.md |
| Hub | New components/hub/ adopted at an exact recorded commit; scheduling, settings, worker lifecycle |
| Image | New components/image/ adopted at an exact recorded commit; FLUX adapter and tests |
| Voice | components/voice/; selected model adapters, references and quality checks |
| Subtitle | New components/subtitle/; reuse existing decoding at one tracked source location |
| Storage | Hub output index/settings and existing worker receipt/deletion mechanisms |
| Native integration | native/StudioHub.swift, ShellCore.swift, StudioAPI.swift, StudioViews.swift; shared packaging/stage_voice.py |
| Qualification | tests/, qualification/, research/licenses.md; final hardware/artifact evidence |

These are responsibilities, not a mandate for a framework. ST-01/02 record exact module choices before dependent work. New paths in task cards are planned, not already implemented.

One integration owner serializes shared native/staging edits. ST-04 and ST-05 share Voice registry/runtime areas: use isolated worktrees and integrate sequentially, or one Voice owner. Workers can supply API fixtures for the native integrator instead of editing shared UI concurrently.

Before engine work, ST-01/02 publish a short docs/runtime-contract.md using existing Hub semantics:

- Stable device/job/attempt/output IDs; retries distinct from user-requested items.
- Exact model revision, files and runtime artifact identity.
- Equivalent configured GitHub/original-host downloads and offline copied-folder validation, including all companion assets.
- Readiness: absent/importing/invalid/ineligible/ready/model paused.
- Submit/status/cancel/result and verified receipt semantics.
- Common model, RAM and output-space admission for native and remote work.
- Persisted per-device settings and effective defaults.
- Stable location/site membership and physical node identity, generation attribution, statistics/freshness and deployment-independent address/IP + token APIs. Owner confirmed current GenStudio-to-controller-to-nodes routing; no topology change.
- Output-group registration plus delivery/read protection.
- Independent public Subtitle lifecycle; internal Voice Whisper quality checks stay under the parent job lease.
- Owned processes and truthful packaged release identity.

Do not invent a generic plugin API, extra global scheduler or custom updater.

## Execution and session handoff

The [board](tasks/BOARD.md) owns status/dependencies. Task cards own checklists/evidence. The existing .planning directory owns overall findings/progress.

1. Read AGENTS.md, design, board and assigned card. Inspect current Git status. Record actual start commit/worktree; 29d0baa is historical context, not a reset instruction.
2. Claim an unowned READY task. Record agent/session, owned paths and completion proof. Only the coordinator changes the shared board.
3. Establish focused baseline and meaningful failing cases for new behavior or reproduced defects.
4. Implement the smallest change; run focused checks. Preserve existing protections.
5. Test the packaged boundary for runtime/process/model changes; record commands, exit codes, exact model/runtime IDs and log paths.
6. Move to REVIEW. An independent reviewer inspects code and evidence; resolve findings and rerun affected checks.
7. Mark DONE only after reviewed integration and required proof. Missing required hardware checks remain incomplete.
8. Update card, board and canonical progress; name next eligible work. Stop owned test processes and retain review artifacts.

Session prompt: “Read AGENTS.md, docs/standalone-plan.md, docs/implementation-plan.md and docs/tasks/BOARD.md. Claim ST-XX if its prerequisites are DONE. Complete that task and record its handoff; preserve unrelated work.”

## Existing verification commands

References for later execution, not tests run during planning:

~~~sh
bash native/build.sh
python3 -m unittest discover -s tests -p 'test_*.py'
~~~

For the adopted Voice regression suite, run from components/voice with its pytest.ini:

~~~sh
../../.build/voice-runtime/bin/python3 -m pytest
~~~

Inspect current tests/runtime before execution. ST-02/03 record the assembled app build command; the existing stage_voice.py command currently bundles Voice only.

## Release completion

All ST-01 through ST-11 cards are DONE. A clean eligible Mac can import the offline kit, start the app, use each advertised model/mode, export results and recover after restart without fetching code. Fixture-qualified controller/worker and GenStudio contracts work. Every device applies its own editable policy.

Record unsupported hardware combinations without substituting another model. Physical and distribution conditions remain explicit in ST-11.
