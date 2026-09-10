# Standalone Studio App Planning

## Goal
Prepare a reviewable architecture and feasibility-first implementation plan for a new standalone Apple Silicon Studio application. This turn creates planning artifacts only.

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
Not started. No app packaging, source adoption, existing-node migration or deployment authorized by this planning task.

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
1. Initialize isolated repository, adopt tracked Voice source, inventory runtime — in_progress.
2. Build isolated relocatable Voice runtime and external data paths — pending.
3. Verify local relocation/imports/API and perform independent review — pending.
4. Record qualification evidence and remaining hardware/model/integration work — pending.
5. Hub/Image integration, shell and distribution follow the feasibility outcome; not yet started.

## Preflight snapshot
Standalone AGENTS: preserve old nodes; read-only source baselines; external state; local checks; no hosted compute or deployment. Source Voice instructions apply as reference release discipline; no legacy launcher is being edited. New standalone project already isolates all work. Existing plan is the accepted scope; no competing plan is needed.
