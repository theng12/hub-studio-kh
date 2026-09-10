# Standalone project scope

- This is an independent application project for new Apple Silicon nodes. Do not modify, migrate, reinstall, stop, or change updates on existing Pinokio machines as part of this project.
- The owner authorized implementation of the accepted plan on 2026-09-10, including sub-agents. Local source adoption, builds, tests and reversible packaging work are authorized. Signing submissions, deployment and real fleet enrollment require their separate authority.
- Read `docs/standalone-plan.md` and the active planning record before work. The canonical plan is `.planning/2026-09-10-standalone-app-feasibility/`; explicitly set `PWF_PLAN_ROOT` to this project and `PLAN_ID` to that directory name when using planning scripts. Do not create a parallel planning system.
- Existing source checkouts listed in `research/source-checkouts.json` are read-only references. Adopt tracked source at an exact commit into this project during implementation; never copy their live environments, credentials, model cache, job data, or service registration.
- Use the smallest implementation that proves the requirement. Reuse existing APIs, UI, worker isolation and safeguards. Do not add a generic plugin system, cross-platform layer, or custom update framework without a demonstrated need.
- Prefer the existing Graphify snapshot for navigation, then verify against current source. It is incomplete and stale; do not infer absence or safe deletion from it.
- Keep planning state current. Record actual test evidence and distinguish a compatibility target from a passing qualification.
- Run development checks locally. Do not use GitHub-hosted compute. Do not publish, deploy, tag, enroll nodes, or submit notarization without the appropriate explicit authority.
