# R03 — Voice receipt and payload integrity

Base: `b5506178cd0543a4734911c4fdb3c91d21ad271b`.
Worktree: `/build/.codex/worktrees/handoff-completion/studio-hub-kh`.
Branch: `codex/local-readiness-gatewaykh`.
Owner: assigned Sol/high worker; coordinator owns review, records and integration.

## Goal and owned files

Repair the reproduced R03 gaps in `components/hub/backend/broker.py` and
`components/hub/backend/artifact_metadata.py`. Own tests in
`tests/hub_regression/cases/test_voice_durability.py` and the existing artifact
metadata test file (locate and report its exact path before editing). Own only
this task's `r03-report.md` report under this planning directory. Ask before
expanding file scope. Do not edit the board, plan or other workers' files.

Coordinator-approved exact test scope: `tests/test_hub_standalone.py` for WAV
validation; fixture-contract fidelity only in
`tests/hub_regression/cases/test_execution_assets.py`,
`tests/hub_regression/cases/test_terminal_progress.py` and
`tests/test_standalone_connections.py`. Preserve their existing assertions.
Review follow-up also permits adding a changed-body conflict assertion to the
existing queued-job idempotency test in
`components/voice/app/tests/test_native_executor.py`; no Voice worker source edits.

## Requirements and steps

1. Read `r03-preflight.md`, trace live acceptance/poll/completion and explicit
   reconciliation callers against current source. Reproduce with permanent RED
   tests before repair. Baseline Hub suite: 85 passed, two upstream warnings.
2. Reject missing/non-object acceptance IDs/receipts, unknown terminal states,
   absent/invalid output references and mismatched job/request identities without
   false success or untracked duplicate inference. Prove live and reconcile
   behavior, persisted ledger state and recovery after malformed responses.
3. Count actual worker-created jobs as well as HTTP POSTs. Preserve stable request
   ID/exact-body idempotent reposts and conflicts for changed bodies.
4. Preserve intentional valid-terminal-result + artifact fetch/parse/hash/count
   failure behavior (`done` with `artifact_metadata_error`); missing artifact
   references must not bypass validation. Do not invent billing changes.
5. Reject truncated PCM WAV payloads: a 1,644-byte file truncated to 64 bytes
   currently reports a valid duration. Use the standard-library parser and prove
   all declared frame bytes exist before verified metadata is emitted.
6. Reuse current identity/receipt/uncertainty helpers; preserve Image/Music paths,
   cancellation fencing, reference privacy and deliberate remote regeneration policy.
7. Run focused RED/GREEN, all `tests/hub_regression/cases`, and relevant existing
   Voice delivery/reference/fleet tests. Coordinator handles full suite, packaged
   gate and independent review. Record exact commands, failures, warnings and limits.

## Boundaries and report

Use Ponytail, systematic debugging and TDD. Graphify CLI unavailable; saved graph
is stale Voice-only, so verify directly. User authorized execution without repeated
approval pauses. GatewayKH now replaces GenStudio as current external client, but
do not rename legacy wire fields during this integrity repair. Do not access
Downloads/model-library, live data, credentials, installed apps or Pinokio. No
paid calls, hosted compute, deployment, enrollment, commits, pushes or subagents.
Use apply_patch. Write full evidence in `r03-report.md`; return a short status,
changed-file list, test summary and explicit limitations. If incomplete, state
the exact blocker and preserve all partial work.
