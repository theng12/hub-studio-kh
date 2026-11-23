# R05 local slice — explicit SDK and routine lifecycle gate

Base `b5506178cd0543a4734911c4fdb3c91d21ad271b`; worktree
`~/.codex/worktrees/handoff-completion/studio-hub-kh`, branch
`codex/local-readiness-gatewaykh`. This is only the native-build portion of R05;
do not mark the whole R05 portable/bootstrap task complete.

## Goal / owned files

Own `native/build.sh`, `native/render-page.sh`, a shared `native/sdk.sh` helper
only if needed to prevent divergent selection, and new
`tests/test_native_build_scripts.py`. Report only in
`.planning/2026-09-10-standalone-app-feasibility/r05-native-build-report.md`.
Do not edit native Swift, Python product source, canonical board or docs.

## Steps / deliverable / proving checks

1. Read the scripts and R01 lifecycle harness. Preserve macOS14 arm64 target.
   Default remains explicitly known-good macosx26.5 (27.0 lacks the required
   State macro plugin on this host); do not silently select newest SDK.
2. Add one explicit environment override `STUDIOHUB_MACOS_SDK`, accepting an
   xcrun SDK selector (for example macosx26.5). Resolve through xcrun. Fail early
   with clear requested-SDK / override guidance if unavailable; never fallback
   after an explicit invalid request. Both scripts must use identical selection.
3. Wire existing tests/native/lifecycle_main.swift into ordinary native/build.sh
   alongside current boundary/API gates, using -D LIFECYCLE_TESTING and
   -parse-as-library. No test-only behavior in the shipped app; build optimized
   product as before. Consult r01-report.md for exact compile inputs.
4. TDD: behavioral shell tests with disposable fake xcrun prove default selection,
   explicit selection, missing SDK, no compile on failed resolution and lifecycle
   execution/failure propagation. Do not assert only source strings. Keep portable
   tests quick; don't launch real apps/services or read production defaults.
5. Run focused tests, shell syntax checks, actual explicit macosx26.5 native build
   including all gates, and real invalid-selector rejection. Root owns final
   combined regression/build review. Keep all artifacts local under ignored .build.

## Don't / if incomplete

Use Ponytail/debug/TDD; root already authorized bounded design/execution. Graphify
CLI unavailable, direct inspection. No new dependencies, model scans, live data,
installed-app changes, commits, pushes, deployment, hosted compute or subagents.
Never signal processes by broad name; fixtures own their own children. Other
workers own broker/parser and GatewayKH inventory; do not overlap edits.
Use apply_patch. Report RED/GREEN commands/output, exact changed files, warnings
and limitations. If blocked, preserve work and state exactly what is missing.
