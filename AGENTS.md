# Standalone project scope

- This is an independent application project for new Apple Silicon nodes. Do not modify, migrate, reinstall, stop, or change updates on existing Pinokio machines as part of this project.
- The owner authorized implementation of the accepted plan on 2026-09-10, including sub-agents. Local source adoption, builds, tests and reversible packaging work are authorized. Signing submissions, deployment and real fleet enrollment require their separate authority.
- Read `docs/standalone-plan.md` and the active planning record before work. The canonical plan is `.planning/2026-09-10-standalone-app-feasibility/`; set `PWF_PLAN_ROOT` to this project and `PLAN_ID` to that directory name when using planning scripts. Do not create a parallel planning system.
- Existing source checkouts in `research/source-checkouts.json` are read-only. Adopt tracked source at an exact commit; never copy live environments, credentials, job data, service registration, or whole model caches. Import selected model assets separately with exact revisions and file checksums.
- Use the smallest implementation that proves the requirement. Reuse existing APIs, UI, worker isolation and safeguards. Do not add a generic plugin system, cross-platform layer, or custom update framework without a demonstrated need.
- Query the canonical Graphify graph for navigation (`graphify query "<question>" --budget 1500 --graph /build/Developer/_graphs/hub/graphify-out/graph.json`), then verify every answer against current source. Graphs refresh automatically; never run `graphify update` by hand. Graphify is an owner mandate on every task. The graph can be stale; do not infer absence or safe deletion from it.
- Keep planning state current. Record actual test evidence and distinguish a compatibility target from a passing qualification.
- Run development checks locally. Do not use GitHub-hosted compute. Do not publish, deploy, tag, enroll nodes, or submit notarization without explicit authority.

## Focused release handoffs — 2026-09-15

- **START HERE:** read `docs/tasks/HANDOFF.md` first — its top entry names the current version, build and open items. `docs/tasks/BOARD.md` is **historical** as of 2026-10-21 (0.6.194); its `▶ TAKE OVER HERE` block is not current.
- The release scope is `HUB_RELEASE_ARTIFACTS` (`packaging/stage_voice.py`): Hub, Image, Voice, Subtitle, Chat, Music and Vision ship; Video is retired/deferred. The constant is authoritative, not this sentence.
- One coordinator owns the board and progress; workers own assigned cards/reports. Serialize edits to the same shared files (native, staging, Voice registry); independent files run concurrently. Mark DONE only after required evidence, independent review and integration. Preserve partial checkpoints and explicit blockers. If no card is READY, the top of HANDOFF names the single task to resume.
- **Release flow:** prepare on a `release/<version>` branch from `main`; merge features with `--no-ff`, no squash; write the plain-language `CHANGELOG.md` entry (packaging generates the in-app notes); bump `VERSION` last; stage and build the DMG; integrate through `land/<version>-into-main`; publish the updater feed; install; verify live. Record the start commit/worktree, owned files and completion proof. History: [`docs/agent-rules-reference.md`](docs/agent-rules-reference.md#release-flow-history).
- **Per-device generated-output defaults are editable:** **20 GB live output, kept 7 days, 5 GB minimum free disk** (owner 2026-10-19); integrity, authentication and active-job safeguards remain enforced. Cleanup never deletes: acknowledged output is **moved** to the Hub's **Trash Folder** and later to macOS Trash restorable; `max_bytes` counts live output only. Defaults: [`docs/agent-rules-reference.md`](docs/agent-rules-reference.md#output-defaults-detail).
- **One disk governor keeps a Mac from filling (0.6.127; `components/hub/disk_governor.py`, owner 2026-10-02).** Runs at start-up and every 5 minutes; tiers green >= 12%, amber < 12%, red < 6%, stop < 5 GB. It reclaims temp/partials, downloads, orphan app copies, logs, stale `Updates/` and acked outputs oldest-first, but **never touches** installed models, unacked or leased outputs, or any `_trash`. The Trash Folder is capped at `min(15 GB, 6% of disk)` in physical bytes and never emptied by the governor; a download is refused (507) if it plus 5 GB does not fit. Detail: [`docs/agent-rules-reference.md`](docs/agent-rules-reference.md#disk-governor-detail).
- **Model catalog rules (owner 2026-10-27/28):** RAM is the only gate — no model is refused, hidden or locked by name. Every shipped package is installable on every Mac; `qualification.minimum_unified_memory_gb` is the only floor, and no published floor means it fits **every** Mac (new models publish one, `PROVISIONAL, NOT MEASURED` when derived from weight bytes). `RETIRED_MODELS` is the one list that moves an installed PRODUCT out; a retired model is refused by name (`MODEL_RETIRED`). **Shelving is deleted — do not restore it:** `shelved_models.py`, `SHELVED_MODELS`, `keep_all_models`, the kit skip and `NOT_STOCKED` are gone; the seven former packages are ordinary entries, RAM-gated, and an installed copy stays. Detail: [`docs/agent-rules-reference.md`](docs/agent-rules-reference.md#retired-and-former-model-detail).
- **Only the Hub that owns a model library may move packages out of it (data-loss fix, 2026-10-02):** `components/library_ownership.py`; the owner is recorded in `<library>/.studiohub/owner.json`. A non-owner Hub, or one whose data dir is under `/tmp` / `/var/folders` / `$TMPDIR`, logs why and never shelves, retires, trashes or prunes it; weights are never moved from a real library into a temp trash. **Every test/script that starts a Hub uses a temp or copy-on-write library (`qualification/library_guard.py`)**; `tests/test_no_real_library_launch.py` enforces it. Incident: [`docs/agent-rules-reference.md`](docs/agent-rules-reference.md#library-ownership-incident).
- **Workstation mode - shown as "Lite Mode" (owner 2026-10-03/04; `components/hub/workstation.py`).** Default off; set in Settings > Lite Mode (`POST /api/hub/workstation`) or from the Gateway (`POST /api/hub/fleet/nodes/{id}/workstation`), published as `workstation` on `/api/hub/resources` and every fleet row. When on: background-priority workers, only `components/workstation_models.py` (runtime allowlist, not an install set), optional idle/step-aside/quiet hours. A job starts on the model's free-before-start floor or `start_free_gb`; **a started job always finishes** except under macOS critical memory pressure for 20 s, when it is cancelled and handed back (`WORKSTATION_YIELDED`, no try spent). A standing-by Mac is busy for routing, never offline or failed. Kit: set up like any Mac, take **Essentials 8 GB**, tick Lite Mode; there is **no** Lite-only copy step and no file sets `STUDIOHUB_WORKSTATION`. Detail: [`docs/agent-rules-reference.md`](docs/agent-rules-reference.md#lite-mode-detail).

## Working rules

- **Every worker commits its owned files before reporting; no uncommitted owned work remains.** Every shipped release fast-forwards `main` as part of closeout.
- **A fleet rollout fans out (owner 2026-10-26).** `scripts/hub_rollout.py` publishes to every site in parallel with its own bounded gate: a busy site is re-polled, an unreachable one is *deferred: unreachable* and retried inside its window, and an offline or silent member is a receipt warning — none halts another site. Nothing is published into a site with work running or non-terminal Gateway jobs routed to it. A rerun never republishes.
- **Every removal goes to this project's `_trash/` — never `rm`**, including clutter. Move it in (create the folder, keep sub-structure) and say where it went. Never delete or tidy anything *inside* `_trash/`. For a Git branch or remote ref, record its name and SHA in a note under `_trash/` before deleting it; never rewrite published history. Detail: [`docs/agent-rules-reference.md`](docs/agent-rules-reference.md#fleet-rollout-and-removal-detail).
- **One narrow carve-out: `.build/` scratch** may be reclaimed only through `packaging/prune-build-scratch.sh`, after it has made its own verified copy on a kit drive. Moving it to `_trash/` frees nothing (same volume). Nothing else is exempt.
- **Every release is committed, pushed, built into a DMG, published to the updater feed, installed and live-verified — and the previous DMG moves to `_trash/dist/` automatically.** Write the plain-language `CHANGELOG.md` entry (packaging generates the in-app notes), bump `VERSION` **after** it, stage, install, build the DMG, publish the feed, integrate into `main` without rewriting published history or moving an unrelated primary checkout branch. **A release is not finished while any of those is missing.** Docs-only work joins the next release; it does not force one. Use the tracked DMG builder and `scripts/hub_offline_gate.py` as the per-release gate; a coordinator performs integration and deployment.
- **Change-aware testing (owner 2026-11-08).** Test the changed work, its callers and its regression checks, plus cheap global checks (lint, types, build) and a packaged launch/update smoke. Reuse earlier passing results when source, tests, dependencies and toolchain are unchanged; record which result was reused and from which commit. Run the full regression only when reach cannot be bounded (toolchain, shared core, dependency bump, big refactor) or the last full run is gone. A skipped suite reads "skipped (unchanged since <commit>)", never "passed". Adopt the shared qualified proof design; until tooling records reuse honestly, run the checks current tooling requires; no bypass flag.
- **Every release updates `docs/tasks/HANDOFF.md`.** Read it first in a new session; keep it current with the version and build, newly learned, newly forbidden and open items. If it disagrees with the code, the code wins and the file gets fixed.

## Model use scope — 2026-09-22

- **The owner runs these models locally, on his own Macs, for himself. Do not raise commercial or non-commercial licensing.** That question is settled and out of scope: do not ask it, list it as a blocker, or repeat it in plans, reviews or notes. Where a license must be recorded, state its name once and move on.
- The single exception is a task that changes shape: shipping weights inside a distributed product, or running it as a paid service for others. Raise it then, not now.

## Always name the quantization — 2026-09-24

- **Every mention of a model names its quantization.** Not "FLUX.2 Klein" but "FLUX.2 Klein **4-bit**"; not "Z-Image-Turbo" but "Z-Image-Turbo **4-bit**". This covers plans, findings, commits, model lists, recommendations and replies.
- The owner validated specific quants on his 8 GB machines, so a model without its quantization cannot be checked against what he has run.
- The shipped labels already carry it (`FLUX.2 Klein 4B MLX 4-bit`, `Z-Image-Turbo 6B MLX 4-bit`), and the Image picker keeps the bits/weight marker visible; do not trim it from UI copy. Where more than one quant exists, say which ships.

## New models must not need a code change — 2026-09-24

**Owner rule: nothing about models is hardcoded. Registries are data; consumers read the data.** Add a model to the catalog and manifests; every screen, router and worker picks it up. If adding one means editing a second list, that list is a bug waiting for the next model.

- **A family, engine or model list in a consumer is a defect unless it is a genuine contract.** Prefer what the producer publishes (`capabilities`, `musicConfiguration`, `reference_audio.declared`, the engine name, the manifest) over a local enumeration.
- **Where a list is unavoidable, it must fail loudly and name the missing entry**, as `_worker_for_engine` raises a `ValueError` naming the engine and both files.
- **Test the invariant, not the membership.** "Every shipped family is selectable" catches the next model; "the picker equals this list of two" catches nothing.
- When you add a model, search every place that enumerates models (`research/hardcoded-model-lists-audit.md`). The three incidents behind this rule are in [`docs/agent-rules-reference.md`](docs/agent-rules-reference.md#hardcoded-model-list-incidents).

## Owner hardware fleet — 2026-09-24

**Do not ask the owner to restate his machines.** Live list, tiers and text/LLM brief: [`research/owner-hardware.md`](research/owner-hardware.md); read it before any model-fit, memory-tier or hardware question. September 2026 fleet table (history): [`docs/agent-rules-reference.md`](docs/agent-rules-reference.md#september-2026-fleet-history).

**Current direction:** 16 GB is the tier to qualify against (50 of ~53 are 16 GB or below); qualify at 16 GB first, fall back to 24 GB only if that fails. One model per tier (not per machine): **Granite 4.0 H-Tiny 4-bit MLX** for 8 GB, **LFM2.5-8B-A1B 4-bit MLX** for 16 GB, and **Qwen3.5-9B 4-bit MLX** as the script/prose model until a candidate passes a long-form test. Serve `JSON.parse` call sites as **GGUF + JSON-schema grammar**. This is a qualification plan, not an adopted decision; sizes and evidence are in `research/moe-fleet-qualification-handoff.md` and `research/moe-16gb-2026-09.md`; do not re-open the survey unless asked. Size to the smallest machine, label each memory claim measured/vendor/inference, and keep failed packages on the SSDs.

## Never publish a number the model did not state — 2026-09-25

A published fact carries **where it came from**, or it is not published.

- **Measure the limit from the artefact that states it** — the checkpoint's `config.json`, the manifest, the stored probe file — and name that source beside the value (`{"value": 262144, "source": "model/config.json text_config.max_position_embeddings"}`).
- **Where nothing states it, publish nothing** (`null` plus the reason). A worker's own fallback is a different fact from the model's limit: `max_output_tokens: 4096` is `llm_engine.FALLBACK_MAX_OUTPUT_TOKENS`, not a number the model states.
- **A dead pointer is worse than no pointer**: a manifest must not name a source that cannot be read.
- **Evidence labels**: `[M]` measured, `[V]` vendor claim, `[I]` inference. A runtime a scheduler sets a deadline from must trace to a stored measurement, or be absent. The Gateway Studio story is in [`docs/agent-rules-reference.md`](docs/agent-rules-reference.md#evidence-and-build-claim-incidents).

## A version field must carry a version — 2026-09-25

`GET /api/health`'s `app_version` is republished as `workers[].studio_version`, the field operators read to answer *"which build is that Mac running?"*.

- A worker reports the **release it was staged from** (`components/app_version.py`), or a value that cannot be mistaken for a version (`unknown`), or a labelled development build. Never a literal word like `"standalone"`.
- `monitor.reported_app_version` refuses to republish a value that cannot be a version, so a new worker cannot reintroduce the defect by omission; `tests/test_worker_version_reporting.py` pins both halves. Incident: [`docs/agent-rules-reference.md`](docs/agent-rules-reference.md#evidence-and-build-claim-incidents).

## A document names the build that has the behaviour — 2026-09-25

- **"It is implemented" and "it is in the build you are running" are different claims.** The second is the one an operator acts on. A document describing behaviour not in the current release says so **at the top**, with the version that will have it and the commit that adds it.
- Check the **installed bundle** when the question is "does this Mac have it", not the checkout (`strings`/`grep` on `/Applications/Studio Hub.app`); `git log` does not. **If a document disagrees with the code, the code wins and the document gets fixed.** Incident: [`docs/agent-rules-reference.md`](docs/agent-rules-reference.md#evidence-and-build-claim-incidents).

## One lane, one published contract — 2026-09-25

A scheduler must ask a lane for work without reading its source or a release on its side (`docs/lane-execution-contract.md`; work in `docs/tasks/ST-20.md`).

- Every model publishes a **`lane_contract`**: canonical `operation`, accepted parameters with enforced bounds, artifact form, terminal vocabulary with `uncertain` marked *not terminal*, and what a cancel does not promise.
- Build it **from the model's own limits**, beside the validator that enforces them; a second copy of a bound can disagree with the code that rejects the request.
- **Do not offer what the lane discards.** Stable Audio 3 forces instrumental output, so it must not advertise `instrumental`, `lyrics`, `vocal_language` or `shift`.
- `deadline_seconds` belongs to the **Hub** (its proxy timeout); `runtime_seconds` belongs to the **worker** and only where measured.

## MLX memory discipline — 2026-10-02

Read `docs/mlx-memory-discipline.md` before adding or changing any MLX model.

- Cap the MLX cache (`mlx_memory.bound_cache`), slab section decodes with `mx.eval` per layer, encode a reference once per script, clear the cache between stages.
- A new voice engine must be in `mlx_memory.PROFILES` (bounded, or `cache-only` with the reason); `components/voice/app/tests/test_mlx_memory_registry.py` fails otherwise.
- `min_free_memory_gb` is **free memory before a job starts**, NOT peak use. Set it from a clean 8 GB run ("Started with N GB free", ~0 swap), never from peak + margin; carry a `min_free_memory_note`, never a formula. Target a worker peak near 4.5 GB or less on an 8 GB Mac.
- A new image model needs a `MemoryPolicy` (or documented `single_mode_reason`) in `components/image/app/backend/generation.py`; `tests/test_image_memory_profile_guard.py` fails otherwise. Image floors follow the same rule; Klein's floor lives on its catalog row only, with the Models-screen override as the only other number; image jobs run on one inference thread. Incident and floor history: [`docs/agent-rules-reference.md`](docs/agent-rules-reference.md#mlx-memory-incident).

## Remote final rendering without a measurement — owner opt-in, 2026-10-23

The owner allows a Mac to offer final-render capacity before formal qualification ("accept that it works and fix it if it breaks"). It is a **weaker, labelled grant, never a fabricated one**.

- **Off by default; then byte-identical to 0.6.195.** Setting: `<data root>/render-authority/opt-in/unqualified.json` (owner-only, strict reader, absent/loosened/malformed = off), written **only** by `POST /api/hub/render-authority`, reached by the controller through `POST /api/hub/fleet/nodes/{id}/render-authority`. It is in `FLEET_ALLOWED`, not `NODE_ALLOWED`. `requested_by` is required and recorded with the time.
- **On** it offers a production-shaped authority labelled `accepted_unqualified` (provenance `owner-opt-in-unqualified`, who, when) for **one** encode, with the same checks as a measured authority. A valid `enabled/` record always wins. **Never a `measured` block:** the first real render writes the first real receipt (never clamped), and a run whose receipt cannot be written is not published; copying that receipt into `enabled/` is the designed promotion.
- **Mixed-fleet contract:** `accepted_unqualified` is added **only while on**; a 0.6.195 controller drops an opted-in row, every other combination keeps working. Do not add keys to the always-published row. Turning it off refuses unclaimed jobs; accepted jobs keep their authority.
- Detail: [`docs/agent-rules-reference.md`](docs/agent-rules-reference.md#remote-render-detail).
