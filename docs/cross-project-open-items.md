# Cross-project open items — the Studio Hub side

**What this file is.** The Studio Hub rows for the master open-items tracker GatewayKH keeps
(`_handoffs/2026-09-25-MASTER-open-items-tracker.md`, `5183355`). The master is theirs; this is the
half we own and keep current, so it can be pasted across rather than reconstructed from memory at
each relay.

**Why it is a file here and not only a message.** The failure this project has actually suffered is
not losing an item — it is *keeping* one after it stopped being true. Three examples, all from the
last two releases:

- `docs/tasks/HANDOFF.md` carried *"chat is absent from `auth._FLEET_STUDIO`, so a remote fleet
  caller gets 403"* for a release after it was fixed.
- The ST-14 review recorded *"the app ignores the warning"* about chat's below-tier warning; the app
  has rendered it all along, and the sentence could be reached at one end and drawn at the other
  with nothing joining them.
- The image lane contract published `185.61 s` for a configuration whose only stored record says
  `166 s`, and nothing in the tree held the 185.61.

Each was a tracker row that had become a second source of truth and quietly lied. So the rules below
exist to make a row cheap to *disprove*.

## The rules these rows follow

1. **Every row names where the truth lives** — a file and line, a route, or a commit. A reader
   verifies rather than trusts.
2. **Every row names how it was last verified**, as a command and its observed result. A status word
   is not evidence; `pytest tests/… → 1295 passed` is.
3. **A row is closed with a date and a commit** or an artefact, never with "done".
4. **If a row disagrees with the code, the code wins and the row is fixed.** Same clause as our
   HANDOFF, and the reason ours is short.
5. **Cross-project rows say who owes whom**, so "open" is never ambiguous at handoff time.
6. **Nothing is restated from a project's own tracker.** Link it. Two copies of one status is one
   copy too many.

## Open — Studio Hub owes

| # | Item | Truth lives in | Last verified | Evidence of done |
|---|---|---|---|---|
| SH-1 | **The Models tab: split by Studio, per-generator facts, per-model editable defaults.** Four of five lanes are built (chat, image+music, voice+subtitle); the native screen is in progress. | `components/model_defaults.py`; `docs/lane-execution-contract.md` | 2026-09-25 — `pytest tests/ → 1295 passed, 11 skipped` | `bash native/build.sh` green and the Models screen rendering a control per published parameter, with a negative control per piece |
| SH-2 | **Release 0.6.64**, carrying the listen default, the bind publication, the model-defaults surface and the Models tab. | `VERSION` | 2026-09-25 — 0.6.63 `a4a78dc` is on the DMG and all four kits | New stage → install → DMG → four kits → `verify-kit.sh` PASS on each |
| SH-3 | **Voice publishes no below-tier warning**, and there are two more missing links: the Audio `NativeModel(…)` passes no `memoryWarning`, and the page has no render site. Cannot fire on this fleet — the copy step skips any voice model above the machine's tier. | `components/voice/app/backend/catalog.py`; `native/StudioAPI.swift` | 2026-09-25 — `tests/test_below_tier_warning.py` fails if a worker publishes a `memory_warning` no page renders | A voice row carrying `memory_warning` and a page drawing it |
| SH-4 | **FLUX.2 Klein 4B MLX 4-bit's `79.34 s` lane runtime traces to nothing.** Z-Image's pair was reconciled to the sourced `166 s` / `236 s`; this one has no probe file either. | `components/image/app/backend/generation.py` | 2026-09-25 — `docs/tasks/ST-20.md` records it as an open item | The stored run, or the row dropped the way two Voice numbers were in 0.6.61 |
| SH-5 | **The model-exposure approval surface cannot be used, for any modality.** No chat file publishes a `genstudio_candidate` block, and `monitor.candidate_models()`, `model_exposure.approve()` and `.revoke()` have **no caller anywhere**. | `components/hub/backend/model_exposure.py` | 2026-09-25 — AST scan; no caller in `components/`, `packaging/` or `tests/` | A route or UI that approves a model/operation pair, or a decision that the gateway does not need one |
| SH-6 | **Chat's memory tier is unmeasured** (8/16/24 GB throughput). 0.6.63 changed the default so a 16 GB+ Mac holds the model between replies. | `qualification/README-chat-tiers.md` | Harness ready; no run | The tier table, which turns "warn on 8 GB" into a qualified tier or a documented exclusion |
| SH-7 | **The 8 GB Z-Image-Turbo 6B MLX 4-bit run.** The copy step now carries it on 8 GB Macs from `CARRY_BELOW_TIER` — *"the owner asked to try it there"*, explicitly not proof. | `qualification/copy_models_for_this_mac.py`; `qualification/README-8gb-z-image.md` | 2026-09-25 — the table prints `floor 8 GB from CARRY_BELOW_TIER … still unmeasured` | A run, which moves the entry to `PROVEN_SMALLER` or documents the exclusion |
| SH-8 | **`max_output_duration_s` is published by no family.** The API field and its validator are real; no voice family declares the shape, so no lane contract offers it. Pre-existing, not from this work. | `components/voice/app/backend/lane_contract.py` | 2026-09-25 — the shape table has one entry, no family references the name | A family that declares it, or the dead shape removed |
| SH-9 | **Video is shelved by the owner.** No worker, no contract, nothing published. Do not schedule it. | `AGENTS.md` | 2026-09-25 — owner decision relayed | The owner reopening it |

## Open — Studio Hub is waiting on

| # | Item | Owed by | What we need |
|---|---|---|---|
| SH-10 | **`_handoffs/2026-09-25-gateway-chat-lane-shipped.md` has never been read by Studio Hub.** Only its one-line summary reached us. If it asks Studio Hub for anything beyond the 401 (which is GatewayKH's to fix by re-joining that node), that ask is currently invisible to us. | GatewayKH | The document, or a line confirming the summary is the whole of it |
| SH-11 | **The Story Studio model-id / two-pass question.** Studio Hub's part is answered: the field is `lane_contract.structured_output`, stating *how* the JSON guarantee is kept rather than asserting a technique. **It is NOT in 0.6.63** — it is committed as `363e50e` and ships in **0.6.64**. GatewayKH re-measured the running 0.6.63 build and found it absent from `/api/hub/catalog`, `/studio/chat/api/catalog` and `/api/hub/model-packages`, and they were right; this row said "live on every chat row" when it was live only in an unpushed working tree. | Story Studio (via GatewayKH) | 2026-09-25 — `git show a4a78dc:components/chat/app/backend/main.py \| grep -c structured_output` → `0`; `git show HEAD:…` → `1` | Whether two-pass is needed at all. If it is, the trigger belongs in Story over the published facts, **not** an id shape — three separate defects in this project came from matching a name where a fact could have been published. GatewayKH needs only the **version**: **0.6.64**, commit `363e50e` |
| SH-13 | **Disagreement about the lane-contract key sets on three surfaces.** GatewayKH measured `/api/hub/catalog` → `…, cancel, dispatch, …`; `/studio/chat/api/catalog` → the same without `dispatch`; `/api/hub/model-packages` → `…, deadline_seconds, …, runtime_seconds, …` with neither `dispatch` nor the same set. **Studio Hub cannot reproduce it from the code**: `dispatch` appears nowhere in `components/hub/` — it is a **Voice-lane** field (`components/voice/app/backend/lane_contract.py`), and the Hub publishes a worker's contract verbatim except for adding `deadline_seconds`. So a *chat* row cannot carry `dispatch` on any Hub surface. Most likely the three rows are three different models. | GatewayKH | 2026-09-25 — `grep -rn '"dispatch"' components/hub/` → no hits; the only publisher is the Voice lane | Each key set re-measured **with its model id beside it**. If all three rows really are the same model, this is a real inconsistency and we want it |
| SH-12 | **Whether GatewayKH wants `--fleet-mode` wired into its install flow.** The flag exists and persists (listen automatic + start at login + start services at login); it deliberately does not touch sleep. | GatewayKH | A yes/no, and whether sleep belongs in scope |

## Closed, with what closed them

| # | Item | Closed | By |
|---|---|---|---|
| — | Chat dispatchable by a Fleet credential (review finding F6) | 2026-09-25 | `7267e62`; `tests/test_chat_hub_wiring.py` |
| — | The listen setter GatewayKH asked to be named | 2026-09-25 | `--listen-auto`; automatic by default; `7fcb0fb` |
| — | The bind as a readable fact | 2026-09-25 | `/api/health` + `/api/hub/settings`, one helper; `154f0b4` |
| — | Subtitle's warning published where nothing read it | 2026-09-25 | `9506c8c` |
| — | The staging guard refusing a bundle over a dead import | 2026-09-25 | `95ad36e` |
