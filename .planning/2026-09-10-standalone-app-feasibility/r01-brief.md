# R01 — Native lifecycle repair brief

Base: `0ec3b3b09c9642b670ec0a092f4223ea4ffe0e24` (plan-only child of `1267a51`).
Work only in `~/.codex/worktrees/handoff-completion/studio-hub-kh`.
Read AGENTS and the canonical task_plan.md Global constraints, Review focus and R01.

GOAL: reproduce and minimally fix N-01/N-02/N-03 in the existing native lifecycle.
INPUT: native/StudioHub.swift, StartupRecovery.swift, StudioAPI.swift and existing
tests/native/main.swift and api_main.swift. Baseline `bash native/build.sh` passed.

OWNED FILES: those five files, and this task's `r01-report.md`. If a behavioral
controller test cannot fit the existing binaries, propose the smallest harness
and exact additional files to root before adding it. Do not use source-string
assertions as the regression proof or build a second copy of lifecycle logic.

STEPS:
1. Read real callers and reproduce each flaw with deterministic behavior tests:
   unhealthy ready uptime cannot reset budget; late response cannot override
   stopped/replaced state; transient identity/version failure can recover rejoin.
2. Keep the existing policy. Track healthy time from valid authenticated health,
   clear it on failure, and require current-process/task ownership after awaits.
3. Retry pending identity/rejoin through existing lifecycle polling with bounded
   cadence and idempotent success. A stale connection/token, explicit leave or
   disconnect must invalidate pending attempts; standalone/minted identities never join.
4. Record RED output before product edits, then implement the smallest correct
   fix and run native boundary/API/full build. Root owns the full Python gate and
   final staging; do not duplicate those expensive checks.
5. Self-review, then write r01-report.md with exact files, RED/GREEN commands,
   results, unresolved limits and any public behavior changes. No commits.

OUTPUT: uncommitted bounded diff and report; brief response with status and tests.
DON'T: edit other files, manipulate Git state, touch installed app/preferences,
register login items, spawn production services, access model libraries, enroll
nodes, deploy, use hosted compute/paid calls or recursively delegate.
IF INCOMPLETE: report the specific missing test seam/context and propose the
smallest change; root owns architectural decisions. Preserve partial work.
