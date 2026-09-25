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
7. **`.models[0]` is not a comparable row across two endpoints.** GatewayKH measured three surfaces
   by taking the first element of each list and concluded the surfaces disagreed; index 0 was a
   *voice* model on one, a *chat* model on another and a *music* model on the third, so they had
   measured a **lane** difference and written it up as a surface difference. Compare by model id, or
   do not compare. This is a general trap, not theirs: any two endpoints that return a list will
   differ at index 0 for reasons that have nothing to do with either endpoint.

## Open — Studio Hub owes

| # | Item | Truth lives in | Last verified | Evidence of done |
|---|---|---|---|---|
| SH-3 | **Voice publishes no below-tier warning**, and there are two more missing links: the Audio `NativeModel(…)` passes no `memoryWarning`, and the page has no render site. Cannot fire on this fleet — the copy step skips any voice model above the machine's tier. | `components/voice/app/backend/catalog.py`; `native/StudioAPI.swift` | 2026-09-25 — `tests/test_below_tier_warning.py` fails if a worker publishes a `memory_warning` no page renders | A voice row carrying `memory_warning` and a page drawing it |
| SH-4 | **FLUX.2 Klein 4B MLX 4-bit's `79.34 s` lane runtime traces to nothing.** Z-Image's pair was reconciled to the sourced `166 s` / `236 s`; this one has no probe file either. | `components/image/app/backend/generation.py` | 2026-09-25 — `docs/tasks/ST-20.md` records it as an open item | The stored run, or the row dropped the way two Voice numbers were in 0.6.61 |
| SH-6 | **Chat's memory tier is unmeasured** (16 GB first, then one 8 GB; a 24 GB Mac is not tier evidence). 0.6.63 changed the default so a 16 GB+ Mac holds the model between replies. | `qualification/README-chat-tiers.md` | Harness ready; no run | The tier table, which turns "warn on 8 GB" into a qualified tier or a documented exclusion |
| SH-7 | **The 8 GB Z-Image-Turbo 6B MLX 4-bit run.** The copy step now carries it on 8 GB Macs from `CARRY_BELOW_TIER` — *"the owner asked to try it there"*, explicitly not proof. | `qualification/copy_models_for_this_mac.py`; `qualification/README-8gb-z-image.md` | 2026-09-25 — the table prints `floor 8 GB from CARRY_BELOW_TIER … still unmeasured` | A run, which moves the entry to `PROVEN_SMALLER` or documents the exclusion |
| SH-8 | **`max_output_duration_s` is published by no family.** The API field and its validator are real; no voice family declares the shape, so no lane contract offers it. Pre-existing, not from this work. | `components/voice/app/backend/lane_contract.py` | 2026-09-25 — the shape table has one entry, no family references the name | A family that declares it, or the dead shape removed |

## Open on the Studio Hub side, after 0.6.65

Three items, none of them blocking the owner.

| # | Item | Why it is still open |
|---|---|---|
| SH-3 | **Voice publishes no below-tier warning** — three missing links (no sentence on the row, no `combineModels` wiring, no render site). | It cannot fire on this fleet: the copy step skips any voice model above the machine's tier, so a below-tier voice model is never installed to be warned about. Recorded rather than built, which is a decision and not an oversight. |
| SH-4 | **FLUX.2 Klein 4B MLX 4-bit's `79.34 s` lane runtime traces to nothing.** | Nobody has found the run it came from. Either the record or the row goes — the same choice that dropped two Voice numbers in 0.6.61. Small, and worth doing before the next GatewayKH reader trusts it. |
| SH-15 | **A flaky native test.** `bash native/build.sh` failed once with *"the in-flight read must have been torn down by the cancellation"* and passed on an immediate re-run with `native/` provably unmodified. | A gate that fails occasionally is one people learn to re-run, which is the same as not having it. Needs a deliberate reproduction — I have not done that, and I am recording it rather than shrugging at it. |
| SH-16 | **`preset_speaker` is published by no voice lane contract.** The Voice worker accepts and reads it (`Txt2SpeechBody.preset_speaker`), the app sends it on every preset job from the model's own published roster, and no lane's `parameters` lists it — so a scheduler reading only the contract cannot know how a speaking voice is chosen. Checked across the catalog: `preset_speaker` appears in no family's parameter list. | Found while fixing SH-14, and it is the same rule read in the other direction. Not fixed with it: publishing it means deciding what a contract-shaped enum for a per-model roster looks like, which is a worker-contract design question rather than a page fix. The app's behaviour is correct — it offers only the roster the model itself published. |

**Needing a machine, not code:** SH-6 (chat's 16 GB tier, then an 8 GB run, harness in
`qualification/README-chat-tiers.md`) and SH-7 (the 8 GB Z-Image run,
`qualification/README-8gb-z-image.md`, script now on every kit drive).

## Open — Studio Hub is waiting on

| # | Item | Owed by | What we need |
|---|---|---|---|
| SH-10 | **`_handoffs/2026-09-25-gateway-chat-lane-shipped.md` has never been read by Studio Hub.** Only its one-line summary reached us. If it asks Studio Hub for anything beyond the 401 (which is GatewayKH's to fix by re-joining that node), that ask is currently invisible to us. | GatewayKH | The document, or a line confirming the summary is the whole of it |
| SH-12 | **Whether GatewayKH wants `--fleet-mode` wired into its install flow.** The flag exists and persists (listen automatic + start at login + start services at login); it deliberately does not touch sleep. | GatewayKH | A yes/no, and whether sleep belongs in scope |

## Closed, with what closed them

| # | Item | Closed | By |
|---|---|---|---|
| — | Chat dispatchable by a Fleet credential (review finding F6) | 2026-09-25 | `7267e62`; `tests/test_chat_hub_wiring.py` |
| — | The listen setter GatewayKH asked to be named | 2026-09-25 | `--listen-auto`; automatic by default; `7fcb0fb` |
| — | The bind as a readable fact | 2026-09-25 | `/api/health` + `/api/hub/settings`, one helper; `154f0b4` |
| SH-11 | The Story Studio id / two-pass question | 2026-09-25 | Answered by `lane_contract.structured_output`; GatewayKH asked only for the **version**, and confirmed the absence themselves (`git show a4a78dc:… \| grep -c structured_output` → 0). Ships in **0.6.64**, commit `363e50e`. **Correction to their note:** it is no longer unpushed — `363e50e` is an ancestor of `origin/main` (`2bb74f7`) as of this release |
| SH-1 | The Models tab, and per-model defaults for four of five lanes | 2026-09-25 | `308ff36`, `3e62bc8`, `f8efed4`; `bash native/build.sh` + 12/12 negative controls; two app-side consumptions remain — see SH-14 |
| SH-2 | Release 0.6.64 — reachability, node-local generation, the bind, the Models tab, `structured_output` | 2026-09-25 | `fabe498` installed, on all four kits, `RESULT: PASS`, pushed |
| SH-5 | The model-exposure approval surface has no caller | 2026-09-25 | **Closed by the answer to HUB-12**: the gateway does not need one, verified from both sides. `approve()`/`is_approved()` have zero callers, nothing is gated by exposure, and an unapproved model still publishes through `_model_capability`'s direct-evidence path |
| SH-9 | Video | — | Not an item: the owner shelved it, and it is recorded as a decision rather than work |
| HUB-12 | Model exposure becoming an approval step — *"tell us if it does"* | 2026-09-25 | **Closed by GatewayKH answering it from our own code**, and verified here rather than accepted. Four checks reproduce: `approve()` and `is_approved()` have **zero callers** anywhere in `components/` or `packaging/`; there is **no** exposure reference in `standalone.py` at all; `model_exposures.json` **does not exist** on this Mac, which is what "no caller" predicts; and the dispatch gate (`standalone.py:444`) refuses on `MODEL_MODALITY_MISMATCH` / `MODEL_NOT_READY` (files, runtime, macOS, chip) plus studio liveness, with exposure absent. `_model_capability` builds availability from the worker's own `available` / `runtime_ready` / `memory_eligible` / `execution_ready` booleans — the candidate block is read as evidence and withholds nothing. **No work, nothing to read.** They also dropped their own standing watch deliberately: if it ever became a gate the Hub would answer `MODEL_NOT_READY` at submit, which their adapter already maps to `LOCATION_NOT_READY` and re-routes — a lost re-route, not a lost job |
| SH-13 | The lane-contract key-set "difference" | 2026-09-25 | **Retracted by GatewayKH as their measurement error**, and the retraction is the good kind: they showed what `.models[0]` actually was on each surface (voice / chat / music) and re-ran it joined by model id — 21 model ids on both surfaces, packages adds `deadline_seconds` and drops nothing, 21 of 21; `dispatch` is Voice's own field on both surfaces, 13 of 13. No defect, no spec line. Verified here independently: `deadline_seconds` is added by spread at `standalone.py:333`, and `\"dispatch\"` appears in exactly two files, both Voice |
| — | Subtitle's warning published where nothing read it | 2026-09-25 | `9506c8c` |
| — | The staging guard refusing a bundle over a dead import | 2026-09-25 | `95ad36e` |
| SH-14 | **The page control is the override, and the model's own default is what it overrides** — the owner's ruling for every generator: *"we have a default but control on the page is overwrite… stuff that can be change or altered such as speed must be given to the user."* Closed by making an untouched control supply **nothing**, so the Models tab's stored value is what the worker applies, and by reading ranges and values from the lane contract the producer publishes. Voice's `speed` was the named case: the app sent a flat `1.0` on every job, so a `speed` default set in Models could never reach the engine *from the app*. Subtitle's `word_timestamps` was worse — a persisted `false`, submitted on every request, with **no control anywhere in the app**, so the Models value was unreachable and the operator could not reach it either; it now has a toggle, offered only where the model's runtime claims word timings. The second half is closed too: the weight format is the row's own `weight_format`, not the extension of a file name (`index, safetensors` was never a format). Found alongside it: the app sent `language` to **12 of 13** voice families whose lane publishes no such parameter — the lane discards it — so the field is now offered only where the lane publishes one, and a lane that *requires* one is refused clearly rather than filled in with an invented code. Three tests that pinned the old behaviour (`var speed: Double`, "every audio path sends `speed: audioSpeed`", "the slider uses the audited range" declaring `0.5...2.0` in the app) were **replaced by the invariant they should have asserted** — they were green while the defect shipped | 2026-09-25 | `GenerationDefaultOwnershipTests` in `tests/test_peer_path_contract.py` (5 tests); the native pace, timestamp and weight-format assertions in `tests/native/api_main.swift`; worker halves already proven by `components/voice/app/tests/test_model_defaults.py` and `tests/test_subtitle_standalone.py` |
