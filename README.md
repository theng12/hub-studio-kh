# Studio Hub Standalone

Planning project for a standalone macOS application installed on **new Apple Silicon fleet nodes**. Existing Pinokio installations retain their current installation, runtime, and update flow.

Status: design and feasibility plan only. No application has been built or qualified.

- [Design and staged implementation plan](docs/standalone-plan.md)
- [Active planning record](.planning/2026-09-10-standalone-app-feasibility/task_plan.md)
- [Research findings](.planning/2026-09-10-standalone-app-feasibility/findings.md)
- [Source checkout identities](research/source-checkouts.json)

The working name is Studio Hub Standalone. This folder is independent of `/build/pinokio`; source repositories are reference material, not live runtime dependencies. Open this folder as its own development project when implementation starts.

## Planning tools

Use planning-with-files with `PWF_PLAN_ROOT=/build/Projects/studiohub-standalone` and `PLAN_ID=2026-09-10-standalone-app-feasibility`. Graphify research is stored under `research/voice-source-graph`; its copied graph covers Voice only and has stale source locations. Confirm findings against source. Apply Ponytail: reuse existing behavior and prove the smallest complete packaging path before expanding scope.
