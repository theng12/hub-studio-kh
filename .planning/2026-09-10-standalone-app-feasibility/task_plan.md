# Standalone Studio App Planning

## Goal
Implement the accepted feasibility-first plan for a new standalone Apple Silicon Studio application, beginning with the relocatable Voice worker proof.

## Owner direction
- Separate project outside existing Pinokio repositories.
- Existing installed Macs retain their Pinokio installation and update flow.
- Standalone app is for new nodes; adopt only if qualification succeeds.
- Apple Silicon only; target macOS 14 Sonoma or later, subject to actual engine qualification.
- Bundle tested runtimes; models imported/downloaded separately.
- Primary product: generate all six asset types locally. API exposure and accepting Studio Hub/GenStudio control are optional secondary capabilities; preserve their safeguards when integrated.
- Owner now confirms six functions: Audio, Transcription, Image, Music, Video, Chat. Hub is their management layer; Audio/Transcription share the Voice engine.
- Use planning-with-files, Graphify and Ponytail.
- Owner selected SwiftUI/AppKit; the subsequent redesign replaces WKWebView with native workspaces; no paid Apple Developer membership currently available. Local development proceeds; Developer ID/notarization remain later distribution work.

## Planning phases
1. Establish isolated project and source evidence — complete.
2. Trace reusable engines, APIs, installation and runtime assumptions — complete.
3. Write design, staged feasibility plan and decisions — complete.
4. Review consistency and deliver plan — complete.

## Implementation status
Local implementation authorized by the owner on 2026-09-10. Voice source adoption, external state paths, launcher, staging and API relocation checks are implemented; local runtime/model verification has passed; clean physical Sonoma and fleet qualification remain pending. Existing-node migration and deployment are outside scope.

## Decisions
Confirmed target scope: Audio, Transcription, Image, Music, Video, Chat. First usable subset: local Audio/Transcription, followed by Image and the remaining engines with independent packaging and model tests. Add shared local scheduling as needed; remote controller setup remains optional. Working folder/name studiohub-standalone is provisional. Architecture, packaging and qualification choices will be recorded in the design.

## Errors
- Two read-only searches referenced nonexistent files (native_execution.py and child CAPABILITY_CONTRACT.md). Resolved using discovered native_executor.py, Hub contract and actual child README paths.

## Final evidence
- Design: docs/standalone-plan.md.
- Independent read-only Hub inventory: research/hub-contract-inventory.md.
- Graphify traversals and corrected result saved only in the copied research graph.
- Documentation links, explicit plan-root resolution and unchanged source checkout identities/status verified.
- No application tests/builds were run: only planning documents were created. Compatibility and integration remain acceptance work, not claimed results.

## Implementation authorization — 2026-09-10
Owner approved starting implementation and using sub-agents. Prior planning-only restrictions are superseded for local implementation.

## Implementation phases
1. Initialize isolated repository, adopt tracked Voice source, inventory runtime — complete.
2. Build isolated relocatable Voice runtime and external data paths — complete (local proof).
3. Verify local relocation/imports/API and perform independent review — complete; 665 tests and final Qwen/Whisper smoke pass.
4. Record qualification evidence and remaining hardware/model/integration work — complete; qualification/voice-runtime.md records exact artifact and limitations.
5. Native app shell and six-function navigation — complete as local Audio/Transcription preview 0.2.0; 676 regression tests, native boundary/build checks and final relocation/model smoke passed. See qualification/native-preview.md.
6. Native redesign — implemented; final artifact verification in progress: owner requires separate Audio/Transcription workspaces, central Models/Jobs/Connections/Settings and node-operation Overview. Replace legacy embedded navigation and duplicated management.
7. Local Image, then Chat/Music/Video, with necessary local capacity coordination — pending.
8. Optional Studio Hub/GenStudio control — pending.
9. Full local contract tests, physical qualification and distribution — pending.

## Preflight snapshot
Standalone AGENTS: preserve old nodes; read-only source baselines; external state; local checks; no hosted compute or deployment. Source Voice instructions apply as reference release discipline; no legacy launcher is being edited. New standalone project already isolates all work. Existing plan is the accepted scope; no competing plan is needed.

## Next acceptance condition
Milestone 1 is locally proven, not fully qualified. Obtain a clean physical Sonoma Apple Silicon test machine and repeat the recorded artifact offline before claiming Sonoma/fleet readiness. Next implementation: local Image, then Chat/Music/Video and optional Studio Hub/GenStudio integration. Physical Sonoma, login/reboot and distribution qualification remain pending.
