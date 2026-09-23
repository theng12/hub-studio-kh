# Standalone project scope

- This is an independent application project for new Apple Silicon nodes. Do not modify, migrate, reinstall, stop, or change updates on existing Pinokio machines as part of this project.
- The owner authorized implementation of the accepted plan on 2026-09-10, including sub-agents. Local source adoption, builds, tests and reversible packaging work are authorized. Signing submissions, deployment and real fleet enrollment require their separate authority.
- Read `docs/standalone-plan.md` and the active planning record before work. The canonical plan is `.planning/2026-09-10-standalone-app-feasibility/`; explicitly set `PWF_PLAN_ROOT` to this project and `PLAN_ID` to that directory name when using planning scripts. Do not create a parallel planning system.
- Existing source checkouts listed in `research/source-checkouts.json` are read-only references. Adopt tracked source at an exact commit into this project during implementation; never copy live environments, credentials, job data, service registration, or whole model caches as part of source adoption. Import selected model assets separately with exact revisions and file checksums, as specified by the accepted qualification plan.
- Use the smallest implementation that proves the requirement. Reuse existing APIs, UI, worker isolation and safeguards. Do not add a generic plugin system, cross-platform layer, or custom update framework without a demonstrated need.
- Prefer the existing Graphify snapshot for navigation, then verify against current source. It is incomplete and stale; do not infer absence or safe deletion from it.
- Keep planning state current. Record actual test evidence and distinguish a compatibility target from a passing qualification.
- Run development checks locally. Do not use GitHub-hosted compute. Do not publish, deploy, tag, enroll nodes, or submit notarization without the appropriate explicit authority.

## Focused release handoffs — 2026-09-15

- **START HERE:** the `▶ TAKE OVER HERE` block at the top of `docs/tasks/BOARD.md` names the current holder, the integration commit, what is DONE, the single task in progress, and what unlocks next. Read it before anything else.
- The current design supersedes the six-function release scope: Hub, Image, Voice and independent Subtitle only; Chat/Music/Video are deferred.
- Before implementation, read docs/implementation-plan.md and docs/tasks/BOARD.md. Claim a READY ST task and record start commit/worktree, owned files and completion proof. If no card is READY, the `▶ TAKE OVER HERE` block names the single task in progress to resume instead of starting new work.
- One coordinator owns the board and canonical progress; workers own assigned task cards/reports. Serialize shared native, staging and Voice registry edits.
- Mark DONE only after required evidence, independent review and integration. Preserve partial model/hardware checkpoints and explicit blockers.
- Per-device generated-output defaults are editable 50 GB total and 30 days; operating defaults are configurable, while integrity/authentication/active-job safeguards remain enforced.

## Working rules

- **Every removal goes to this project's `_trash/` — never `rm`.** This covers *all*
  of it, not only what the owner asks for: a file he names, and equally a file you
  decide is clutter while cleaning up. Move it into `_trash/` (create the folder if
  absent), keep any sub-structure, and say where it went. `_trash/` itself is his to
  sort out: never delete or tidy anything *inside* it. The one thing that is not a
  file is a Git branch or remote ref — record its name and SHA in a note under
  `_trash/` before deleting it, and never rewrite published history.
- **Every release updates `docs/tasks/HANDOFF.md`.** Read it first in a new session, and
  keep it current: the version and build, anything newly learned, anything newly
  forbidden, and what is still open. A stale handoff is worse than none, because it is
  trusted — if it disagrees with the code, the code wins and the file gets fixed.
