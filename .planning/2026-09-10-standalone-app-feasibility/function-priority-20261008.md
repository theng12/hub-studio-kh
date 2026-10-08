# Per-Mac ranked function priority implementation plan

> For agentic workers: use subagent-driven-development, systematic-debugging, test-driven-development and verification-before-completion. Owner approved design and implementation in this chat; no additional approval pause. Workers do not commit, publish, install or delegate. Coordinator integrates and delivers.

**Goal:** Every Mac can accept chosen functions and independently rank them, or give all enabled functions equal priority. Configure locally in Hub and centrally in Gateway.
**Architecture:** Extend existing work-policy storage and shared dispatch/lease decisions. The Hub is authoritative for each Mac; Gateway relays policy and uses reported preferences in assignment. Existing admission, model qualification, memory, pauses, uncertain-attempt fences and render priority remain enforced.
**Tech stack:** Python Hub/Gateway and Swift native apps; existing HTTP/auth/registry/queue contracts only.
**Spec:** Owner approved in-chat design: current work finishes; highest-ranked eligible queued function goes next; oldest eligible work at the same rank; other enabled work borrows idle capacity; no preference means equal function rank. Strict ranking may starve lower-ranked functions while preferred demand continues. No hardware-based automatic assignments.

## Bases and ownership

Hub base 30c6f65bca2a100b99a981045e0870a23ecd3a01, Gateway base e75ce2cd4ed2aad5561627fb782b1f75df1c2001. Isolated worktrees: Developer/_worktrees/{studio-hub-kh,gateway-kh}/function-priority-20261008.

- Hub backend worker owns components/hub Python backend/control/protocol and Python tests.
- Hub UI worker owns native/StudioAPI.swift, native/StudioViews.swift, tests/native/{api_main,render_main}.swift and docs/runtime-contract.md.
- Gateway worker owns Gateway backend, native central control, tests and feature docs, excluding version/release handoff files reserved to coordinator.
- Coordinator owns this canonical plan, BOARD/HANDOFF, versions/changelogs, independent review, serialized proving checks, merge/push/build/DMG/install, graph refresh and delivery receipt.

## Global constraints

- Qwen Image 2.1 qualification worktree and its contents remain untouched. Existing models and qualification flags remain unchanged.
- Preserve every manual pause and enabled function; no automatic fleet or SSD rollout. Local installation is authorized; coordinate active work and preserve existing state.
- Never print secrets. Never delete files; preserve removals in project _trash and never tidy existing trash.
- Hub tests use isolated synthetic data and an isolated synthetic model library, never temporary data against the real library. Gateway tests sandbox all production state before imports. No paid generation.
- One test/build/UI execution lane at a time, coordinator assigned. Source edits may run concurrently only in the owned files above.
- Existing Ponytail and code-only Graphify navigation required; verify graph claims against source. Impeccable for native UI. No model hardcoding or new dependency.
- Releases must be committed/pushed through main and have verified DMGs in primary dist. Hub primary unrelated branch remains untouched; Gateway unrelated .DS_Store preserved.

## Frozen wire/storage contract

GET /api/hub/work-policy returns existing machine_enabled and studios plus priority_order and priority_functions. studios retain id/title/modality/enabled. priority_functions is [{id: canonical_modality, title: first_local_studio_title}], deduplicated from registered local functions recognized by the existing vocabulary. Canonical Subtitle modality is transcription; it is not the subtitle worker id.

POST /api/hub/work-policy accepts exactly {"priority_order": [unique canonical modality strings]}. Empty list means no preference. Listed entries rank first in list order; omitted functions tie below them. Disabled/offline registered functions may keep saved ranks; enabled switches and machine flags are not changed. Reject duplicates, nonstrings, empty tokens, unknown/non-local functions or mixed/extra fields with HTTP 400 and do not partially mutate.

Existing machine rows lacking priority_order read chat_priority:true as ["chat"], false/missing as []. Preserve all sibling settings. Retain an exact legacy bool-only write adapter (true -> ["chat"], false -> []), and a computed chat_priority compatibility response whose value is true when Chat is first. New schedulers use priority_order; missing remote arrays fall back to legacy bool, while explicitly malformed remote arrays are neutral.

Resources, peer caches and fleet machine rows transport priority_order. Hub controller proxies GET/POST /api/hub/fleet/nodes/{id}/work-policy to the authenticated target Mac, including the controller-local identity, following existing workstation relay boundaries. Gateway adds its corresponding machine control and native editor. Unsupported older Hubs show a clear upgrade/restart requirement and must not simulate successful edits.

The existing bounded synchronous Chat waiting mechanism is opted into only by X-StudioHub-Queue-Wait-Seconds and works independently of preference, including [] and unranked Chat. Ordinary calls keep streaming/default-worker-model compatibility and immediate busy refusal. Queue allowance remains bounded and model-specific; changing rank cannot grant eligibility or extra physical execution. Uncertain running leases remain held until exact completion evidence.

## Decisions

- Legacy LLM-first migration leaves other functions tied, rather than inventing a full order.
- A user choosing Custom from empty deliberately seeds the displayed rankable function order. Reading partial order never assigns missing ranks. An explicit reorder may convert displayed enabled functions to a total order while retaining disabled saved entries.
- Scheduling compares only dispatchable candidates with key (rank, created_at, stable identity). Preserve existing render precedence. Scarcity and model-affinity heuristics cannot override explicit rank or oldest eligible work in the same rank; use them only where compatible with machine placement.
- Local installation is this release's delivery scope. Fleet/SSD rollout and Qwen/model qualification remain separate.

## Task 1 — Hub policy, arbitration and relay

Files: backend/registry.py, broker.py, chat_priority.py, chat_jobs.py, transcription_jobs.py, peers.py; operator_controls.py; standalone.py; capability/fleet/auth relay modules as current callers require; tests/test_chat_priority.py, test_hub_operator_controls.py and focused new work-priority/relay cases.

- [x] Add failing cases for legacy migration, empty/partial/full order, invalid bodies and pause/flags preservation.
- [x] Implement canonical policy read/write using existing persistence; expose producer-owned rankable functions and compatibility fields.
- [x] Add failing dispatch cases: busy TTS finishes, Image-first beats older Chat, Voice-first beats Image, consecutive ranked work, no-preference FIFO, partial-order tie FIFO, priority changed while queued, disabled/unqualified/low-memory preferred work does not block lower work.
- [x] Extend existing actual dispatchability probes per function and compare per Mac immediately before the existing atomic lease. Direct Chat receives timestamp evidence and participates in the same ordering; recheck after awaits. Preserve direct-Chat duplicate/cancel/disconnect/unknown leases and render exception.
- [x] Add local and remote authenticated control tests, unsupported-old-peer tests and publication/cache tests. No arbitrary target URL or scope expansion.
- [x] Run focused isolated red/green proving checks in assigned lane and report exact failures/pass counts. No live model inference.

Representative acceptance order: priority_order=["image","voice","chat"], eligible queue Voice(t1), Chat(t2), Image(t3) -> Image then Voice then Chat. With [] -> Voice then Chat then Image. If Image fails current qualification/memory/enablement, Voice proceeds. An already executing Chat finishes before any selection.

## Task 2 — Hub native controls

Files: native/StudioAPI.swift, native/StudioViews.swift, tests/native/api_main.swift, tests/native/render_main.swift, docs/runtime-contract.md.

- [x] Decode producer-owned functions/order, preserving unknown enable checkboxes without inventing rankability. Reuse authoritative save response, stale-poll revision and visible error paths.
- [x] Replace Prefer LLM jobs with Job priority: No preference / Custom order. Accessible up/down controls, rank labels, and unranked tied section; show disabled functions honestly and retain saved positions. No drag-only interaction.
- [x] No preference sends []; explicit Custom/reorder sends canonical modality strings. Changes never resume a pause or alter enabled switches. Missing new capability on an old Hub gives a clear unsupported state.
- [x] Native tests prove exact JSON, response ownership, refused save rollback, partial order, unknown-function behavior, and stale refresh protection. Render controller/node, light/dark, wide/narrow actual views; coordinator inspects one batch and confirms any repairs.

## Task 3 — Gateway centralized policy and assignment

Files: gateway/startup_control.py, admin.py, fleet.py, worker.py/pool.py as real dispatch callers require; menubar API/Fleet editor; isolated Python/native tests.

- [x] Mirror Hub contract through existing authenticated controller/target identities. Validate shape, block unsupported old target clearly, retain manual pauses and local/remote trust boundaries.
- [x] Expose policy in fleet rows and an accessible per-Mac editor using reported function metadata. Do not guess capabilities or assign priorities from RAM.
- [x] Use per-Mac reported preferences when selecting eligible capacity and ordering waiting work. Prefer an available suitable Mac; never wait for a busy preferred Mac when eligible idle capacity can run work. Keep paid lanes, admission ceilings, unknown accepted-work holds and existing bounded pending Chat contract.
- [x] Prove Image-first/Voice-first/Chat-first/no-preference behavior with multiple Macs and queued functions, including older Hub neutral fallback, disabled preferred function, no eligible preferred job, and no duplicate submission.

## Review focus

1. A stale UI response must not replace a newer saved rank or clear a pause.
2. Disabled or unqualified high-rank work cannot strand an otherwise usable Mac.
3. Remote/controller-local identifiers must affect exactly one target Mac with existing authentication.
4. Pending direct Chat and durable batches must share the same physical lease, including disconnect/unknown outcomes.
5. Migration and No preference must be persistent and honest across new/older peers without granting missing capability.

## Coordinator integration and release

- [x] Independent review of contract, scheduler execution paths and native saves; resolve material issues.
- [x] Focused proving checks and project-required gates, one lane at a time; retain logs and honest skipped-test scope. No repeated full suites without a new reason.
- [ ] Release notes/handoffs then patch versions Hub0.6.157/Gateway0.2.145 (recheck origin before final numbers). Commit and push main without resetting unrelated primary branches.
- [ ] Build native/staged apps with existing qualified runtimes; build/verify DMGs in project dist; preserve previous apps/installers. Install locally and check running source identities and migrated policy while preserving pauses/function flags.
- [ ] Refresh code-only graphs after structural changes, retain old graphs recoverably. Record final screenshots, artifact hashes, tests and live limits in the delivery receipt.

## Execution ledger

2026-10-08: owner authorized implementation. Fresh main-based worktrees created; existing graph commits match bases. Three disjoint implementation owners assigned. No product edits/tests/live changes at planning checkpoint.

2026-10-08 source verification checkpoint: Hub integration267 passed; backend regression374 passed after one synthetic Voice status correction. Native boundary/API/secret/lifecycle/optimized compilation passed, with24 actual-view captures and six inspected. Material independent findings resolved. Gateway final integration and post-commit delivery steps remain receipt-owned: `/build/Developer/_orchestra/function-priority-20261008/DELIVERY.md`. Do not infer installation from this source checkpoint.

2026-10-08 Gateway final gate:16 isolated files passed,366 tests plus579 end-to-end checks. Native form and all-menubar typecheck passed. Remaining release checklist records post-commit operations; DELIVERY.md is authoritative once written, avoiding a rebuild solely to self-stamp the release commit.
