# Standalone Studio App Planning

## Goal
Implement the accepted feasibility-first plan for a new standalone Apple Silicon Studio application, beginning with the relocatable Voice worker proof.

## Owner direction
- Separate project outside existing Pinokio repositories.
- Existing installed Macs retain their Pinokio installation and update flow.
- Standalone app is for new nodes; adopt only if qualification succeeds.
- Apple Silicon only; target macOS 14 Sonoma or later, subject to actual engine qualification.
- Bundle tested runtimes; models imported/downloaded separately.
- Preserve GenStudio connectivity and existing fleet safeguards.
- Use planning-with-files, Graphify and Ponytail.

## Planning phases
1. Establish isolated project and source evidence — complete.
2. Trace reusable engines, APIs, installation and runtime assumptions — complete.
3. Write design, staged feasibility plan and decisions — complete.
4. Review consistency and deliver plan — complete.

## Implementation status
Local implementation authorized by the owner on 2026-09-10. Voice source adoption, external state paths, launcher, staging and API relocation checks are implemented; local runtime/model verification has passed; clean physical Sonoma and fleet qualification remain pending. Existing-node migration and deployment are outside scope.

## Decisions
Proposed first scope: Hub + Image + Voice; working assumption; optional clarification had no reply during drafting. Working folder/name studiohub-standalone is provisional. Architecture, packaging and qualification choices will be recorded in the design.

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
5. Hub/Image integration, shell and distribution follow the feasibility outcome; not yet started.

## Preflight snapshot
Standalone AGENTS: preserve old nodes; read-only source baselines; external state; local checks; no hosted compute or deployment. Source Voice instructions apply as reference release discipline; no legacy launcher is being edited. New standalone project already isolates all work. Existing plan is the accepted scope; no competing plan is needed.

## Next acceptance condition
Milestone 1 is locally proven, not fully qualified. Obtain a clean physical Sonoma Apple Silicon test machine and repeat the recorded artifact offline before claiming Sonoma/fleet readiness. Hub/Image, native shell, GenStudio integration and distribution remain later milestones.
