# R02 — Reference boundary repair brief

Base: `0ec3b3b09c9642b670ec0a092f4223ea4ffe0e24` (plan-only child of `1267a51`).
Work only in `/build/.codex/worktrees/handoff-completion/studio-hub-kh`.
Read AGENTS and canonical task_plan.md Global constraints, Review focus and R02.

GOAL: reproduce and fix C-01/C-02 at the shared execution-asset boundary.
INPUT: execution_assets.py and its stage/read/purge callers, reference route tests.
Baseline reference/purge/standalone run: 18 passed, two dependency deprecations.

OWNED FILES: components/hub/backend/execution_assets.py,
tests/test_hub_voice_references.py, tests/test_execution_asset_purge.py,
and this task's r02-report.md. No broker/native/route edits unless root approves.

STEPS:
1. Read current consumers/locking and write real temporary endpoint/store regressions
   reproducing non-finite, negative, reversed and zero-length timing acceptance.
2. Require finite start >=0 and end >start; preserve existing order/type/size
   contracts and valid local/GenStudio segment roundtrips. Controlled 400 must
   precede new asset creation or existing asset mutation. Avoid model-specific checks.
3. Corrupt only an existing fixture's expires_at to invalid text/non-finite values.
   Restage and read/status must refuse with ExecutionAssetError through existing
   route error mapping, not HTTP500. Preserve clip, metadata and unrelated assets.
   Choose refusal, not silent metadata repair; purge keeps skip-invalid behavior.
4. Make the minimal shared parsing change, preserving transactional locking,
   digest, authentication, permissions, symlink and expiry protections.
5. Record expected RED then GREEN. Run reference/purge/standalone tests using
   PYTHONPATH=.build/test-deps .build/voice-runtime/bin/python3 -B -m pytest -q.
   Root owns full suite and packaging. Self-review and write r02-report.md.

OUTPUT: uncommitted bounded diff and report with exact proving checks and limitations.
DON'T: edit unrelated files, commit/push, touch live app/state, read the protected
model library, alter user clips, run hosted compute/paid calls or recursively delegate.
IF INCOMPLETE: report precise blocker and preserve partial work. Ask root before
expanding behavior, dependency surface or file ownership.
