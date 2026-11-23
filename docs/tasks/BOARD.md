# Studio Hub task board

> **Historical as of 2026-10-21 (release 0.6.194).** Current state lives at the top of
> `docs/tasks/HANDOFF.md`; the block below is retained as a record and is not the task to resume.

## ▶ TAKE OVER HERE

### ST-SHOPS-BOOTSTRAP-20261021 — INTEGRATED SOURCE on `release/0.6.194`; gates PENDING; no commit/build/install/publish

Owner request: fix the three Shops Macs that never connected (`office-imac-blue-m1-8-256-hub`,
`grace-2fs-imac-hub`, `kt-ksw-imac-blue-16-512-001-hub`). Integrated in worktree
`_worktrees/studio-hub-kh/release-194`, branch `release/0.6.194`, over canonical main
`439584a69565780e1721cdf4c17cbb5489aaf200` (0.6.193), from `fix/shops-checkin-20261021`.

Two source-proven fixes: (1) `release_sync.controller_target()` required `role == "agent"`, but the
app's join (`/api/hub/fleet/adopt`) records the adopted credential and `parent_controller_url`
without the role, so a joined Mac (default `standalone`) never mirrored its controller's release and
could not pull the build that would make it visible — it now keys on `fleet_nodes.membership()`
(adopted credential + validated address) and excludes a `controller`. (2) `GET
/api/hub/fleet/nodes/{id}/status` was authenticated but recorded nothing, so a Mac that could reach
its controller still read `never_connected` — `fleet_nodes.note_status_seen()` now stamps
`last_status_at` (and `last_seen_at`, forward-only) as presence and nothing else, never overwriting a
`check-in`/`probe` reason (so `dial_is_failing` and the pull transport are unchanged), and
`standalone.fleet_node_status` stamps it only for a node's own credential against a live roster row.
`never_connected` now clears on a check-in, a successful probe, or a status ask.

Files: `components/hub/backend/fleet_nodes.py`, `components/hub/backend/release_sync.py`,
`components/hub/standalone.py`; new `tests/test_fleet_status_presence.py`,
`tests/test_release_sync_target.py`; accuracy-only wording in `tests/test_fleet_pull.py`,
`tests/test_fleet_pull_end_to_end.py`, `tests/native/api_main.swift`, `docs/fleet-pull-channel.md`.

Status: INTEGRATED SOURCE only. Gates PENDING; nothing committed, built, installed or published.
Coordinator owns gate/release. **Runtime outcome for the three stays UNRESOLVED** — current evidence
is a failed reverse dial and no recorded call from them; their outbound reachability and whether they
were running were never proven, so code and a controller-only rollout cannot resolve it and the
three need local/on-site checks. **Correction to 0.6.193:** its entry claimed the three were
"switched on and checking in every minute" / "calling home every 60 s"; that is **unsupported** — no
0.6.185/192/193 controller build recorded a call from them.

**Everything below this block is historical: superseded statuses, kept for the record. Do not read
them as current state or as an action list.**

### ST-SSD-MODEL-LOCATION-20261018 — 0.6.185 SOURCE PREPARED; gates UNRUN, not integrated

Owner request: unify the SSD installer's model folder with the folder the app uses, and add a "Show in Finder" control for the current model folder. Seven source paths are prepared on `release/hub-0.6.185` (worktree `_worktrees/studio-hub-kh/release-185`, cut from verified main `7d556ea`): the new resolver `components/model_library.py` and `tests/test_model_library_root.py` (new); tracked edits to `native/ShellCore.swift`, `native/StudioHub.swift`, `native/StudioViews.swift`, `qualification/copy_models_for_this_mac.py`, `tests/test_copy_models_for_this_mac.py`; metadata (CHANGELOG/release-notes/VERSION 0.6.185/HANDOFF/BOARD). Product candidate independently accepted SOURCE-only (`E/hub-ssd-model-location-20261018/independent-f1f5-review-20261018.md`, bd756); F1–F5 correction frozen at `FREEZE-F1F5-corrections-20261018.md` (`a40b0c94…`). **Nothing is committed/staged/built/tested/installed/published and no gate has run** (runner current at `01b3adc2…`: lifecycle residuals independently closed, the final #7 source-identity delta PASSED SOURCE-only; gates UNRUN). REMAINING: execution grant → full Python gates on the current final 0.6.185 source with matching VERSION 0.6.185/headings → two explicit-path commits (the seven source paths, then the five metadata paths) with all bytes unchanged → fresh native gate on the clean final 0.6.185 commit → packaging/DMG → main integration → paced rollout. Hold the 0.6.184 candidate and the partial 0.6.184 fleet (10 of 11 required Macs at 1100) unchanged. Plan: `E/hub-ssd-model-location-20261018/ADDENDUM-2-185-gate-plan-f1f5-20261018.md`.

### ST-NATIVE-CHAT-QUEUE-20261018 — 0.6.184 CANDIDATE INTEGRATED; gates and release pending

Accepted native Chat queue opt-in (`8db7c88`, independent fixture review CLEAN ACCEPT) cherry-picked onto `release/hub-0.6.184` from verified main `33f64f0` as `f51e446` (identical tree `aec1aa26`); CHANGELOG/release-notes/VERSION 0.6.184/HANDOFF metadata prepared. Native gate exit 0 on the exact candidate (hub-chat-queue-gate/09-12; release binary `d8b69cde…` retained); Python focused 71 passed on the same bytes. REMAINING: independent final delta review → explicit execution grant → full Python gates (0.6.183's two environment-only errors are known and must reproduce, never waved green) → native gate decision per the builder's provenance checks → packaging/DMG → main integration → paced rollout This Mac→1100→controllers. Plan: `agent-reports-20261016/hub-0184-release.md`.

### ST-FLEET-CHAT-RELAY-20261017 — 0.6.183 RELEASE

Reviewed `98d4fa3` + blocker fix `12db653` (independent re-review ACCEPT, `agent-reports-20261016/fleet-chat-relay-re-review.md`) fast-forwarded over main `5b60256`: a controller relays a node-only Chat model to one node, and a candidate whose catalog went stale is skipped rather than dispatched (fail-closed). Python only; `native/` unchanged, so the 0.6.182 native binary is reused byte-for-byte. Pairs with Gateway 0.2.183 (chat lane records the Mac that answered). Known limitations recorded in the release report: paused local-holder short circuit, buffered SSE with no bounded relay wait, absent node job id / caller disconnect can orphan, imprecise all-unprovable busy message. Receipts: orchestra `hub-0183-release/`.

### ST-TRASH-NO-HARD-DELETE-20261016 — 0.6.182 RELEASE

Reviewed fix/hub-trash-no-hard-delete (`ae28ffc`, accepted) fast-forwarded over main `efb04dc`: no automatic hard deletes (moves to the Trash Folder / macOS Trash), fleet Trash Folder list/move/delete routes for Gateway 0.2.181, Show Trash Folder in Finder. Native source changed (StudioAPI/StudioViews/UpdateClient): fresh native source gate. Receipts: orchestra `hub-0182-release/`.

### ST-REJECTED-NARRATION-20261016 — 0.6.181 SHIPPED; This Mac + 1100 verified, paced fleet delivery running

Reviewed fix `5970ea0` (fix/rejected-narration-review-candidates; independent Opus review ACCEPT) integrated over main `a655fe1`: a Voice section's final rejected take is kept as an inactive review-only candidate bound to the failed Hub item and served through authenticated byte-verified GETs. Python-only; native source unchanged from 0.6.180. Receipts: orchestra `hub-0181-release/`.

### ST-NATIVE-CATALOG-ARRAY-20261015 — 180 RELEASE PREPARATION; installed fleet remains179

Root owns independent native-catalog release integration in native-fleet-management/release/native-catalog-array-180. Reviewed two-file feature478ec03 was integrated over actual main47c97aed at1845d2f. Array-decoder RED/GREEN and independent exact-source review are complete. Notes and VERSION180 are prepared; remaining appropriate native/metadata source gates, main push, primary DMG, installed proof and paced fleet delivery are not complete. No unfinished renderer source is included. Refer to orchestra hub-0180-release/ and current living handoff before any execution; sole cross-project test/compiler lease must be reconciled.

### ST-LOGIN-REGISTRATION-20261014 — 178 SOURCE + INSTALLER VERIFIED; fleet delivery pending

Astra/root owns integration. Same bounded worker completed frozen four-file candidate on9f06; independent corrected actual-diff review clean (ae25c89d...). Genuine timeout/Settings/signal-reason RED→GREEN retained. Final complete API/full lifecycle/secret/view/optimized compile passed; root missing boundary and affected Python132/3artifact-skips passed. Core changes: actual-lock guard, serialized refresh/owner-toggle ordering, bounded truthful outcomes and process-local quarantine for unknown registration. Source/primary installer verified at product0de6178; graph595files/17055nodes/39054links/paid0, sixstagedchecks/fourimports/DMGverify passed. Primary DMG SHAe5a9411a57eed157ea16935d09d230e99558d766379e8a14d0dc43f151ac648e. Fleet deployment remainspending. Version178 notes allocated after fresh main9f06/VERSION177 proof. Root owns integration/mainpush/code-onlygraph/primaryDMG/normalpaced deployment after affected accepted transfers/sourceleases settle. Operational four-Mac recovery remains separate. Plan: `.planning/2026-09-10-standalone-app-feasibility/login-agent-registration-refresh-20261014.md`.


### ST-FUNCTION-PRIORITY-20261008 — 0.6.157 SOURCE VERIFIED; delivery receipt owns running status

Owner-approved per-Mac ranked functions or No preference are source-verified, locally in Hub and centrally in Gateway. Base Hub30c6f65/Gatewaye75ce2c. Canonical plan: `.planning/2026-09-10-standalone-app-feasibility/function-priority-20261008.md`. Hub integration267 and backend regressions374 passed; native API/lifecycle/secret/boundary/build and actual-view renders passed. Independent findings resolved. Preserve pause/function/model choices and Qwen qualification worktree. Coordinator's `~/Developer/_orchestra/function-priority-20261008/DELIVERY.md` owns final commit/push/DMG/install/graph status; source status alone never establishes a live157 installation.

### ST-WORK-POLICY-20261007 — 0.6.156 SOURCE VERIFIED; delivery receipt owns running status

Integrated over `1bb472a` / 0.6.155. Local function choices, finish-current/queued-LLM-first dispatch with idle lending, bounded Gateway waiting, verified JPEG exports and Image-local preview errors are source-verified. Full discovery recorded 2,270 tests, 15 skips, two fixture failures and no errors; the repaired affected replay passed 70 of 71, then its remaining synthetic-library Chat fixture passed in a targeted full-discovery-context run. No production behavior change repaired those gate failures. Native boundary/API passed; after a view-only privacy compile fix, unchanged API hashes guarded the passing secret/lifecycle/release compile tail. Rendering produced 24 actual-view captures; the coordinator inspected seven across Settings/Image/Video, both widths and themes. Independent review found no high-impact issue. See canonical handoff for exact logs and capture limits.

Commit, installer and measured running identities belong to `~/Developer/_orchestra/codex-remediation-20261007/WORK-POLICY-DELIVERY.md`, which the coordinator updates during delivery. Source verification does not claim installation. Keep the owner's Gateway This Mac pause. Plan source/UI checks are recorded; delivery stays open until that receipt verifies it. The 0.6.155 qualification block below is preserved.

### ST-GEMMA32-QUAL-20261007 — 0.6.155 INSTALLED on This Mac

Owner accepts Gemma's observed985/800/805-word bodies and verified Story Chapter8/08 numeric equivalence. Qualified only at32GiB;24/16 excluded. Code/payload3ac4a48 installed with local canonical model verified; livecatalog ready, tiny directserving smoke returnedREADY and memoryreleased. Curated32k combinedcontext,16k workeroutputallowance;Qwen9 remainsdefault, other newMoE candidates pending. Independent102 focusedchecks and verifiedDMG completed. All4 SSD qualification records updated with backups intact, no SSDapp/fleet rollout. GatewayordinaryThisMacdispatch remainspaused. See canonicalhandoff and coordinatorrelease receipt.


### ST-MOE-PREP-20261007 — selected MLX 4-bit candidates — PREPARED, qualification pending

All3 owner-selected checkpoints and all12 model/SSD copies fully hash-verified; Hub wiring committed/pushed in0.6.153 with an installable DMG. No selected-candidate inference or activation has run; ThisMac remains paused. Physical qualification requires owner initiation, one model at a time. Plan: `.planning/2026-09-10-standalone-app-feasibility/moe-preparation-20261007.md`; detailed receipts in the coordinator's audit directory.

**SSD launcher follow-up — COMPLETE, physical qualification pending:** three model-specific `.command` launchers and portable standalone helpers committed atf2e8a17. All10 helper copies SHA256-verified on each of4 SSDs and localstudy/Technical; all15 static launcher checks passed without inference. Coordinator independently passed10 focused checks, strict probe selfcheck and real synthetic owned-process-group cancellation. Results stay in dated Desktop/StudioHub LLM Qualification folders, with SUMMARY.txt, report.json, run.log and raw English outputs. No application release/install, production activation, fleet-state change or model measurement was performed. Evidence: coordinator audit/SSD_LLM_LAUNCHERS.json.


**Extended workload follow-up — PREPARED, execution pending:** implementation766aa97 adds three1200wordchapters in one response (12288token allowance) and12/25/50scene batches (6144/12288/16384 allowances) with4000UTF16charactercaps on finalstyle-prefixedprompts. Coordinator independently passed21 relevantchecks andprobe selfcheck. All10helpers fully hash-verified in4SSDTechnical sets andlocalTechnical; all15 staticcommandchecks passed. Same `.command` names, no application release/install or productionsettingchange. Older helpers/receipts preserved in eachTechnical/_trash/moe-test-kit. Actual extendedmodel runs remain owner initiated; evidence in audit/EXTENDED_LLM_TEST_KITS.json.


### ST-19 — No model may need a code change — **DONE in 0.6.61**, 2026-09-25

Owner: *"my rule is never hardcoded — everything must be dynamic for all generators and models,
because we always have a new model to add."* Recorded in `AGENTS.md`; **implemented** in 0.6.61.

**Reported symptom:** khmer-tts not appearing. **Cause, broader than reported:** the native
`formCompatible` decided voice support from a hardcoded family allowlist (`qwen3-tts`, `voxcpm*`,
`omnivoice`), so six of thirteen voice packages — `khmer-tts-mms` (VITS), `khmer-tts-female-onnx`
(ONNX), `chatterbox-mlx` (4-bit, 8-bit) and `fish-audio-mlx` (8-bit, bf16) — were invisible in the
Audio picker while the catalog, Hub, manifests, library and all four kits offered them correctly.
**Third instance of one shape** after the 0.6.57 `chat-mlx` outage, and the first where a test
asserted the broken behaviour.

**Disposition.** `research/hardcoded-model-lists-audit.md` — 73 findings, **Tier A (18) and Tier B
(40) all fixed**, Tier C kept as contracts with its two named caveats fixed. Every fix was verified
against the tree, not against a worker's report: `tests/` **935 passed, 11 skipped**;
`tests/hub_regression/cases` **172 passed**; `bash native/build.sh` green.

| job | scope | outcome |
| --- | --- | --- |
| A | native support/mode derived from catalog data | done — 12/12 voice packages selectable, duplicate whisper rows deduped |
| B | `research/hardcoded-model-lists-audit.md` | done — the standing inventory |
| C | ST-13.A `pocket-tts-4bit` adoption + inert-wiring cleanup | done — offline tokenizer proof, ~1000 lines of dead wiring removed |
| D | Hub Tier-A traps (A2, A7, A8, A11–A14, HUB-19) | done — new `components/hub/backend/vocabulary.py` |
| E | worker discovery (A1, A5) | done — Subtitle and Image catalogs are manifest-driven |
| F | music per-repo fallbacks (A3, A4) | done — per-model facts, byte-identical output proved |
| G | packaging component ladders (A15–A17) | done — `COMPONENT_WORKERS`/`COMPONENT_STAGERS`, byte-identical stages |
| H | voice studio (A6, A9, A10, B16–B21, C4, C15, licensing copy) | done — one wiring table, family flags on the registry |
| I | Hub + Chat (B3–B12, C9, `modality`, defects) | done — `modality` and `lane_contract` on the package route |
| J | companion cache + subtitle (B13–B15, pocket-tts tokenizer) | done — measured offline proof; companion mirror into the HF cache |
| K | native (B22–B31) | done — every modality list derived or exhaustive |
| L | packaging/qualification tooling (A18, B32–B40, Annex 2) | done — all 24 packages reproducible, silent coverage holes closed |

**Also delivered here, from Gateway Studio's measured ask** (`docs/tasks/ST-20.md`): `modality` and
per-model `lane_contract` on `/api/hub/model-packages`, so dispatch is data too; real version
fields on every worker; and a refusal that names what it could not reach.

**Open, and named rather than guessed** — see `docs/tasks/HANDOFF.md`: Pocket TTS 4-bit's eight
preset voices need a per-model `preset_speakers` roster plus a published `modes` list and the app
offering a model in each mode; audited reference-audio profiles exist only for the Qwen families;
the voice smoke pilot names a model ST-13.B cut; and `capture --write` still withholds the two
multi-source music packages instead of overwriting them.

### ST-18 — Z-Image kit rollout: what has to move before it ships

Recorded so the packaging side is not discovered during a 50-machine install. All of this is
**measured from the repo and the drives**, not assumed.

**The kit layout is fixed and Z-Image does not match it yet.** Every shipped model sits at

```
model-library/<package_id>/<revision>/model/<weights>
```

verified against `flux2-klein-4b-mlx-4bit/7fd2482…/model/{transformer,text_encoder,tokenizer,vae}`
on all four drives, and it is what `qualification/copy_models_for_this_mac.py` resolves to
(`folder = library / package_id / revision`) and what `model_packages.import_folder` validates
against the manifest's `model/...` paths.

Z-Image is currently at `optional-models/z-image-turbo-mflux-4bit/{transformer,text_encoder,…}`
— **no `model/` prefix**, and mixed in with a README, three example images and two dotfiles.

**When the adapter lands, the drive copy must become:**

```
model-library/z-image-turbo-mflux-4bit/b3a8f31115a11f2f9e2fa0bfbc8d78dcc3e6568b/model/
    transformer/  text_encoder/  tokenizer/  vae/
```

with the README kept outside `model/`. Then `verify-kit.sh`'s advertised count goes 20 → 21 and
the kit stops being able to over-promise, because the model becomes a real one.

**Do not move it before the adapter ships.** It sits outside `model-library/` deliberately: the app
has no adapter yet, so the kit must not advertise it. Moving it early would put a model in the
advertised list that no Mac can run.

**The manifest is in the catalog while the adapter is still being written, and that is safe.**
`native/StudioAPI.swift` builds every model list from the **worker's** `/api/catalog` and uses the
Hub's packages only as a lookup (`packages[item.repo]`); nothing synthesises a row from a package.
So Z-Image is currently a downloadable package with no picker entry, which is exactly the
intended order — it becomes selectable when the worker advertises it and not before. Checked by
grepping for any `packages.map` / `packages.values` construction: there is none.

**Ship path needs no code change.** `packaging/stage_voice.py --image-runtime <dir>` accepts any
runtime, calls `validate_runtime` on it and records its own `tree_digest` as
`components.image.runtime_sha256`. Nothing pins the old runtime's digest, so shipping the bump is
the stage argument `.build/image-runtime-mflux020` and nothing else. The
`c9cd56a6…` in `qualification/focused-release-manifest.json` is a **record of the 0.17.5 runtime**,
not an enforced pin, and it becomes historical when the new runtime ships — per AGENTS.md that is
a compatibility target until re-qualified on the shipped bundle.

**Scope, checked rather than assumed — Z-Image needs NO native change and no mandatory Hub change.**

- `native/StudioAPI.swift` and `StudioViews.swift` are already model-generic: `imageModel` is a
  repo string, `imageConfigurations[imageModel]` reads per-model settings from the catalog, the
  picker selects `models.first { $0.function == .image && $0.supported }`, and the model's own
  `generation_defaults` / `image_sizes` drive the controls. The only occurrences of "FLUX" in the
  native shell are **two code comments** — no display string, no logic. A second image model that
  the worker advertises appears in the picker with its own steps and sizes.
- The Hub has exactly **one** hardcoded model id, `memory_admission._FLEET_DEFAULTS` for
  `aitrader/flux2-klein-4b-mlx-4bit` — a fleet-qualified memory default, not a dispatch gate.
  `capabilities.py` is generic and keyed off the worker's catalog. A `_FLEET_DEFAULTS` entry for
  Z-Image is **optional** and becomes worth adding once the 8 GB tier is measured.
- So the whole remaining change is: the worker adapter, the manifest, the library import, the
  stage argument, and the kit move. Nothing in the native shell.

**A model's memory floors come from the worker's catalog, not from its manifest — and the
live-free floor has a 2 GB default nobody chose for these models.** Traced through
`memory_admission.py` / `workload_policy.py`: the total-memory floor is the stricter of
`catalog_entry["min_unified_memory_gb"]` and a repo-prefix override in
`_MIN_MACHINE_MEMORY_GB_BY_REPO_PREFIX` — and that table contains only the two
`qwen3-tts-12hz-0.6b-` prefixes, so **no image model matches it** and the catalog entry is the
sole source. The free floor is `catalog_entry["min_free_memory_gb"]`, else
`DEFAULT_MIN_FREE_MEMORY_GB` = **2.0 GB**.

**Correction, from the adapter job's own tracing: the total-memory floor is computed but NOT
enforced.** `memory_admission.describe` returns `effective_min_total_memory_gb = 0.0` unless an
operator override names one — `min_unified_memory_gb` only feeds the *displayed* default (FLUX 8,
Z-Image 16). The gate that actually refuses work is `broker._memory_gate` against
`effective_min_free_memory_gb`. So the free floor is the only enforced one, which makes it the one
that matters, and it also means **Z-Image's 16 GB tier does not keep it off an 8 GB Mac** — the 8 GB
tier stays unclaimed rather than being refused.
*(Superseded 2026-09-30: the total-memory requirement is now a hard eligibility floor — measured,
else manifest, else catalog — see `memory_admission.requirement_gb`.)*

The FLUX entry publishes `min_total_memory_gb` and `min_unified_memory_gb` but no
`min_free_memory_gb`, so it runs on that 2 GB default today; Z-Image publishes **8.0**, the measured
bounded footprint, so it waits for ~8 GB free instead of dispatching onto 3 GB free. For Z-Image — measured 17.6 GB
footprint at the engine default, 7.1-7.2 GB with low-RAM — a 2 GB free floor means the Hub will
dispatch onto a Mac with 3 GB free and let it thrash rather than refusing cleanly. Z-Image should
publish a free floor consistent with its chosen memory mode (≈8 GB if low-RAM), and the 8 GB tier
stays unclaimed until the owner's run returns. Raised with the adapter job.

**Two verifier hazards found during the kit stocking, recorded but NOT fixed — both fail in
directions that waste an operator's time:**

1. **`verify-kit.sh` can report a FALSE FAIL.** ATLAS's first Phase B run returned `RESULT: FAIL`
   while its library line still read 21/21. `verify-launch.sh` guards port 47873 with
   `/usr/sbin/lsof -nP -iTCP:$PORT -sTCP:LISTEN`, which misses a port held in a **non-LISTEN**
   state; the launch then dies and the verifier blames the copy. A re-run passed. So a good drive
   can be reported as broken, which is the worst direction for a check whose entire job is telling
   you whether a drive is safe to hand to a node. Workaround until it is fixed: `PORT=<n>`.
   Nothing was changed in `verify-kit.sh` — it needed no change for the Z-Image layout.
2. **`prepare-kit.sh` overwrites several drive files in place, displacing only the app bundle.**
   `1 - Install the app.command`, `2 - Copy the models.command`, `Technical/copy_models_for_this_mac.py`,
   `Technical/verify-kit.sh`, `NODE-SETUP-CHECKLIST.md`, `Studio Hub.app.tree-sha256` and
   `focused-release-manifest.json` are all replaced with no copy kept, while `Studio Hub.app` alone
   goes to the volume's `_trash/`. Nothing was lost here — the drive's verifier was diffed against
   the repo's before the swap and they were byte-identical — but the project's never-delete rule
   arguably covers these too, and the next repo-side edit to them would silently replace a drive's
   copy with no way back.

**Two defects found by the adapter job and NOT fixed here — recorded because both are real:**

1. **`components/model_packages.py` cannot import a package from a normal Hugging Face cache.**
   `_source_stream` allows a `blobs` directory only as `folder.parent.parent/'blobs'`, but the shared
   HF cache layout is `hub/models--<org>--<repo>/blobs/<2>/<sha>`, which is deeper and does not match
   — so a symlinked snapshot file is refused as "Broken or external source link". The adapter hit
   this and imported from the SSD copy instead. Anyone installing from a cache rather than a
   download would hit it. Outside the adapter's file ownership, so reported rather than edited.
2. **The measured low-RAM penalty does not reproduce.** The record carries "166 s → 236 s" for
   low-RAM at 1280x720; the worker measured **166.9 s low-RAM against 187.0 s default** — low-RAM
   was *faster*. The 236 s figure came from the earlier standalone qualification. Either the engine
   moved under it or the original sample was noisy; what matters is that the memory-saving
   configuration is not reliably slower, which strengthens rather than weakens the case for
   choosing it on small hosts. The recorded penalty should not be quoted as fact.

**Also noted, correcting my own commit message:** `86d06ff` says `validate_request` "gained
`math.isfinite` on guidance". It did not — the check was already there at `3fa8b53` line 66. The
commit is published and is not being rewritten; the record is corrected here.

**Verified this round, independently of the agents that produced them:**

- **The new runtime's audit, from its own build report** (`.build/image-runtime-mflux020/manifest/macho-audit.json`,
  read rather than taken on trust): `status: passed`, target macOS 14.0, **193 binaries, 0 failures**.
  The shipped 0.17.5 runtime reports 194/0 on the same target — one fewer, consistent with mflux
  0.20.0 dropping the twine-only subtree. The runtime is read-only by design, so re-running the
  audit in place cannot write its report; the build's own is the artefact.
- **The drive pack satisfies the new manifest exactly.** All **15/15** files verified by SHA-256
  and size against the manifest's `source_path` entries, totalling **5,907,438,792** bytes —
  byte-for-byte the declared total. So `model_packages.import_folder` will accept the kit copy
  with no re-download, which is the path all fifty machines take.
- **The library import landed in the intended shape:**
  `~/StudioHubModels/z-image-turbo-mflux-4bit/b3a8f311…/model/{transformer,text_encoder,tokenizer,vae}`
  with a receipt entry in `.studiohub/installed.json` (37 → 38). The `model/` prefix is real on
  disk, so the worker's `Path(model_folder) / 'model'` needs no special case — which is what
  the layout decision two rounds ago was for.

**Cross-check that passed:** the Z-Image pack on `/Volumes/KH-STUDIO-ATLAS/…/optional-models/` is
byte-identical to the HF cache snapshot — 19 non-dotfile files, same sizes and SHA-256 — so the
kits can supply these weights without a re-download.

**Correct byte total for the manifest:** 15 model files, **5,907,438,792** bytes. The figure
5,911,138,787 that appears in earlier notes counts the README and the two dotfiles and must not
reach a manifest.

### ST-17 — Image text-rendering tests, 16:9 spec (owner, 2026-09-24)

**▶ The 8 GB tier run is ready and is the one open measurement in the Image work.** One command on
one of the two 8 GB M1/M2 Macs, kit drive plugged in:
`bash ~/Desktop/eight-gb-z-image-run.sh` (AirDrop `qualification/eight-gb-z-image-run.sh` to its
Desktop first). It finds the pack and the engine itself, runs four generations, and prints one block
to send back. **Instruction: [`qualification/README-8gb-z-image.md`](../../qualification/README-8gb-z-image.md).**
The harness was verified on the 32 GB M2 Max — including a real 512×512 low-RAM smoke generation
through the installed app's mflux 0.20.0 (22 s, peak MLX 5.86 GB, identical decoded-image hash on
two runs) — but **every 8 GB figure is still unmeasured**, so no tier is claimed.

Owner's standing spec from 2026-09-24: **16:9 landscape, 1280x720**, two images —
one with a 4-5 word golden magical title, one with titles plus a long body of text.
Redo only what does not match, for **Z-Image-Turbo** and **FLUX.2 Klein**, with speeds.

Runner: [`qualification/run-16x9-image-tests.sh`](../../qualification/run-16x9-image-tests.sh)
(`golden` | `dense`). FLUX runs on the shipped runtime (mflux 0.17.5, 4 steps, guidance 1.0 —
what the app submits today, per the R07 receipt); Z-Image on mflux 0.20.0 at 9 steps. Each model
runs the schedule its own engine expects; one setting for both would misreport one of them.

**Done — `golden` (1280x720, seed 42):**

| Model | Steps | Wall (incl. load) | Title rendered | Peak MLX | Footprint |
| --- | --- | --- | --- | --- | --- |
| Z-Image-Turbo | 9 | 185.6 s | **THE GOLDEN LIGHT OF DREAMS** — correct | 9.84 GB | 17.67 GB |
| FLUX.2 Klein | 4 | 79.3 s | THE **GOLEN** LIGHT OF DREAMS — wrong | n/a | 24.07 GB |
| FLUX.2 Klein | 9 | 136.8 s | THE **GOLEN** LIGHT OF **DREEAMS** — wrong | n/a | 24.07 GB |

FLUX drops a D in GOLDEN at 4 steps and drops it *and* doubles an E at 9 — so this is not a
step-count problem, and raising steps costs 57 s and makes it worse. Z-Image is 2.3x slower at 4
steps but right, and **lighter on the machine**: compressor delta 0.75 GB against FLUX's
10-11.9 GB, with FLUX pushing system swap from 2.1 GB to 5.3 GB on a 32 GB Mac while Z-Image's
swap fell. Same class as the "A Bediime Story" miss the model was chosen to fix.

Images: `~/Desktop/StudioHub Image Text Review/10-12`.

**Done — `dense` (1280x720, seed 42, a 2,599-character prompt).** The owner clarified the
2000-3000 characters are the *prompt*, not text inside the picture. Prompt kept at
`.build/text-tests/dense-prompt.txt`; it asks for a titled cover with a subtitle.

| Model | Wall (incl. load) | Title | Subtitle | Peak MLX | Footprint |
| --- | --- | --- | --- | --- | --- |
| Z-Image-Turbo | 201.7 s | The Golden Light of Dreams — correct | A STORY FOR SLEEP — correct | 9.84 GB | 17.64 GB |
| FLUX.2 Klein | 55.2 s | THE GOLDEN **LIGF REEMS** — wrong | A STORY FOR **SLEP** — wrong | n/a | 24.07 GB |

FLUX failed both text elements on the long prompt, Z-Image got both. A longer prompt made FLUX
*faster* (55 s vs 79 s on the short one) and Z-Image slightly slower (202 s vs 186 s).

**Both scenes are now done.** Across four generations the pattern is the same every time:
FLUX.2 Klein is 2.3-3.7x faster and misspells; Z-Image-Turbo is slower and correct, and far
lighter on the machine (compressor delta 0.2-0.8 GB against FLUX's 10.7-11.9 GB, with FLUX
driving a 32 GB Mac from ~2.1 GB to ~4.8-5.3 GB of swap while Z-Image's swap fell).

### ST-16 — Z-Image-Turbo: LANDED in 0.6.59 (staged, not yet installed) — 2026-09-24

**Done and verified.** Z-Image-Turbo is selectable beside FLUX.2 Klein; the worker's live catalog
advertises both:

```
FLUX.2 Klein   steps 4  guidance_ignored False  total=16.0  free=None  ready cached
Z-Image-Turbo  steps 9  guidance_ignored True   total=16.0  free=8.0   ready cached
```

Coordinator-verified evidence, not relayed:

- Z-Image through the worker's own path renders **"THE GOLDEN LIGHT OF DREAMS"** correctly — the
  whole reason the model was chosen, since FLUX writes "THE GOLEN LIGHT OF DREAMS".
- **FLUX is pixel-identical across the refactor.** Run through both the legacy path and the new
  registry, all three outputs hash to `16a5535f…` — also the value the 0.20.0 re-qualification
  recorded, so the refactor changed nothing and independently reproduces another job's number.
- The memory plan cross-validates against the CLI: low-RAM peaked at **6,262,527,774 bytes**, the
  same 6.26 GB standalone `--low-ram` measured. `memory_mode` is recorded per job.
- 34 image tests, native boundary/API/lifecycle, 62 Hub tests — all pass.

The engine bump (mflux 0.20.0 / mlx 0.32.2) and the FLUX re-qualification are committed at
`3fa8b53`; the adapter at `86d06ff`; the release is cut at `5182ece`.

**Still owed:**
1. **Install + DMG + the four kits.** Staged and verified at `.build/StudioHub-0659` but
   deliberately not installed, so the release lands as one consistent state.
2. **Kit stocking** — Z-Image must move from `optional-models/` to
   `model-library/z-image-turbo-mflux-4bit/b3a8f311…/model/` **and be registered in the kit's
   install receipt**. In flight.
3. **The 8 GB tier** — unclaimed pending the owner's run. It moves one of two named constants
   (`Z_IMAGE_LOW_RAM_BELOW_BYTES`, `low_ram_vae_tile_size`), left as single knobs so a measurement
   settles it rather than a guess.
4. **The adapter's own report** was not needed: the code was inspected, the evidence reproduced
   and the work committed independently of it.


Owner asked for this to start. The bump is the gate: Z-Image cannot be selected until the
`image-mflux` engine runs 0.20.0, and FLUX.2 Klein must be re-qualified on it first.

Read-only research is **done and verified**: [`research/mflux-020-upgrade-findings.md`](../../research/mflux-020-upgrade-findings.md).
Three findings change the plan, and the coordinator re-checked each against the repo rather than
taking them on trust:

- **The image runtime cannot be rebuilt right now.** `packaging/image-runtime/build.sh` builds
  strictly offline from `.build/downloads` and exits if the pinned CPython archive is absent.
  `.build/downloads`, `.build/uv-cache` and `Developer/.build/downloads` are all gone, and nothing
  matching them is in `_trash/`. **Repopulating the cache is step 1**, not a detail — the first
  person to edit `requirements.in` and run `build.sh` will otherwise hit "Pinned CPython archive
  is unavailable" and think the bump is broken.
- **`mlx` is a hard conflict.** The lock pins 0.31.2; mflux 0.20.0 requires `mlx>=0.32.0,<0.33.0`.
  Re-pin **0.32.2** (0.32.1 has a recorded matmul regression) and move the lock hash in
  `build.sh:44` with it.
- **FLUX.2 Klein's pixels change, silently**, `67a27847…` → `16a5535f…` on decoded pixels. The
  coordinator looked at both: same picture, one extra rock on the left bank. Numeric drift, not a
  quality change. The engine cannot be blamed specifically — mflux and MLX moved together.
- **The planned re-qualification oracle is broken and must be fixed before it is used.** The R07
  plan says to compare the PNG *hash*, but the PNG carries EXIF with `generation_time_seconds`
  and `mflux_version`, so a whole-file hash can never match — which is why both R07 receipts
  already say `"matches_historical": false`. Compare **decoded pixels** instead; they are
  run-to-run deterministic, and they are host-class specific.

Good news that shrinks the risk: source compatibility is **total** — zero entry points removed,
`Flux2Klein`/`generate_image`/`GeneratedImage.save` signatures compatible,
`flux2_weight_definition.py` byte-identical across the bump, and the shipped FLUX pack was
**measured loading and generating** on 0.20.0 through the worker's own code path.

**Sequencing:** repopulate caches → re-pin mlx 0.32.2 + lock + build.sh hash → build into a fresh
directory → `smoke_imports` → real FLUX load + generate comparing **decoded pixels**, and
re-check the two **owner text scenes** (R07 is a landscape and says nothing about typography) →
then the Z-Image adapter (import the pack, write the manifest, handle that its root has **no
`model/` subdir** unlike FLUX, and never pass the bare `z-image-turbo` alias, which pulls ~31 GB
of bf16 weights).

**Not started:** nothing has been rebuilt. Also open from the same work — the 8 GB tier run, which
the owner is running himself with `qualification/eight-gb-z-image-run.sh`.

### ST-15 — Fleet pool visibility (0.6.57) — 2026-09-24

Owner report: *"after connecting each of the nodes, it doesn't show its own pool. And also in
the controller, I don't see the connected pool of nodes too. We need to have and show that."*

Both were real, and both came from one gap: **the group was not a fact either end held.** The
controller's Fleet overview was built from its dispatch registry, so a just-joined node read as
*"this Mac has not reported in yet"* for the ~4 s until the 12 s peer poll caught up, and a member
running no studio was missing entirely. A node had no record of *whose* credential it held — the
controller's address lived only in the app's preferences file, and `parent_controller_url` was
wiped by any save made while the role was not `"agent"`.

**Implemented, tested, committed, staged and installed.** Evidence:
[qualification/fleet-pool-2026-09-24.md](../../qualification/fleet-pool-2026-09-24.md).

- Adoption records the controller: `POST /api/hub/fleet/adopt` takes `controller_url`, and the app
  sends the controller's literal private address (resolving a name first). An adoption that does
  not name a controller clears the recorded one — stale is worse than unknown.
- `parent_controller_url` now lasts exactly as long as `fleet_credential_origin == "adopted"`.
- `fleet_nodes.pool()` answers the whole group from either end; a node reads the roster back from
  the controller it joined. `/api/hub/fleet/overview` is built from it and probes every member.
- Native: Controller/Node badge + address per row, a header naming the group, a node's "Joined
  nodes" section naming its controller, and no "Send here" on a node (a node does not dispatch).

**Released and pushed:** `/Applications` = 0.6.57 / `12e377f`, DMG
`dist/StudioHub-0.6.57-12e377f.dmg`, all four `KH-STUDIO-*` kits refreshed and verifying PASS.
Both `implementation/voice-runtime` and `main` are on `origin` at `34014d5`; the remote had been
at `46e5d89` (0.6.54), so 0.6.55–0.6.57 were all unpublished until then. `main` moved by
fast-forward — no history rewritten.

**Open follow-ups from this work:** collapse the two per-node probe passes on the Fleet page onto
one shared read before the fleet reaches the 64-node cap; per-tier throughput and the 8 GB M1/M2
Z-Image run are still unmeasured.

### ST-12 — Khmer VITS families, 0.6.55 cut locally — 2026-09-24

Owner direction: add two surveyed Khmer TTS models to Voice Studio and the model library. Both
are non-MLX, so each got its own engine under a new `voice-vits` packaging engine:
`khmer-tts-mms` (MMS VITS via transformers) and `khmer-tts-onnx` (Coqui VITS via ONNX Runtime).
Implemented, tested and verified with real Khmer renders; card and evidence at
[ST-12](ST-12.md) and
[research/khmer-tts-adoption-2026-09-24.md](../../research/khmer-tts-adoption-2026-09-24.md).

Release **0.6.55** is staged, installed, DMG'd and the four drive kits refreshed — **but not
pushed**; the owner runs the two pushes in `HANDOFF.md` step 8. Local commit `3ab8984`. Voice
suite 716/2; root suite 659/11/1 pre-existing Subtitle failure. Owner ear check of the two
voices is the remaining open item.

### ST-14 — Local Chat Studio (MLX text model) — 2026-09-24

Owner request: stock a local LLM and add a real **Chat** Studio, taking it out of
"Coming later". Owner-selected shape: **MLX-first**, local Chat Studio + stocking first,
`Qwen3.5-9B` only — stocked build `mlx-community/Qwen3.5-9B-4bit` @ `8b2b98c0…` (5.98 GB;
revised from OptiQ, whose index requires an 8.22 GB complete snapshot), text-only accepted.
Card: [ST-14](ST-14.md); implementation record
[chat-studio-adoption-2026-09-24.md](../../research/chat-studio-adoption-2026-09-24.md).
**Integrated and proven on the 32 GB tier (2026-09-24).** Worker, Hub
routing/admission/memory, native Chat page in `Create`, packaging spec and CLI are committed on
`implementation/voice-runtime`; the manifest is registered; all 11 model files are
SHA-256-verified in `~/StudioHubModels/qwen3-5-9b-4bit/8b2b98c0…/model/`. Through the installed
0.6.56 bundle (`928ebd4`) the packaged worker proved `/api/health`, `/api/catalog`, a real
completion with **no thinking trace**, and **JSON mode with `json_valid: true`** (peak MLX
**5.22 GB**); the packaged Hub then started all five workers and one authenticated
`POST /api/hub/chat/completions` returned a real completion. The thinking fix
(`enable_thinking=False` by default + a per-request toggle) is committed with a test.
Evidence: [qualification/st14-chat-2026-09-24.md](../../qualification/st14-chat-2026-09-24.md).

**Still owed:**
1. ~~**Kit stocking**~~ — **done:** all four `KH-STUDIO-*` kits carry the chat-capable app and
   the model, and each verifies 20 installed / 20 advertised with `RESULT: PASS`.
2. **Per-tier throughput** (8/16/24 GB) is unmeasured; the manifest publishes a 16 GB floor that
   nothing has yet qualified.
3. **ST-14.8 independent review — DONE, and the verdict is NOT PASSABLE.**
   [qualification/st14-review-2026-09-24.md](../../qualification/st14-review-2026-09-24.md), 10
   findings, all reproduced. The feature cannot currently work from the app, for reasons that
   have nothing to do with the engine:
   - **F1 (fixed in 0.6.58)** — `chat-mlx` was missing from the Hub's engine→worker map, so
     `GET /api/hub/model-packages` answered **HTTP 500** and the native `refresh()` read that as
     "Hub unreachable", blanking **every** model picker. Shipped in 0.6.57; fixed, verified in
     the installed bundle, and guarded by a new catalog test.
   - **F2** — the model has **no install receipt** in `~/StudioHubModels/.studiohub/installed.json`.
     All 11 files hash-match the manifest, so "imported" is true for the bytes and false for the
     registration the app gates on: the Hub reports `not_installed` and **Send is disabled**.
   - **F3** — `sendChat()` posts `model: ""`, so the Hub's paused/not-ready gates never run and
     the model picker is decorative.
   - **F4** — a streaming request whose client disconnects **wedges the model lock
     indefinitely** (measured still held 120 s after disconnect); every later request blocks
     forever.
   - **F5** — an abandoned request leaks the local machine lease, so the next chat request gets
     **409 MACHINE_BUSY** for up to the 6 h TTL.
   - F6–F10: the fleet surface cannot carry chat though `HANDOFF.md` says it does; a latent
     `activity` `KeyError: 'chat'`; input validation far weaker than the Image worker's (NaN and
     negative sampling accepted, `thinking:"false"` turns thinking **ON**); the native chat tests
     are nine source-text greps; and the default memory policy unloads the model between turns
     (~2.4 s cold load per message).
   Owner priority is image first, so the fixes are **deliberately not started**. F2 and F3 are
   the two that must land before Chat can be relied on at all.
4. **gateway-kh** is read-only here and does not serve chat on its `studio_hub` kind
   (`research/chat-studio-gateway-bridge.md`).
**Shared-file edits made:** `components/model_packages.py` engine allowlist now includes
`chat-mlx`; the `chat` modality is registered in `components/hub/backend/activity.py`,
`components/hub/standalone.py` (engine map, worker map, peer-work predicate, native route) and
`components/hub/standalone_config.py` (`_REQUIRED_WORKERS`); `packaging/hub_worker.py` now builds
a guarded chat worker spec, inert until `stage_voice.py` records the component. The peer-path
contract test and the Swift API fixture were updated for the new modality; all affected suites
re-run green.

### Local verification and GatewayKH continuation — 2026-09-21

Owner direction: continue work runnable/testable on this Mac. **GatewayKH replaces
GenStudio as the current external integration target.** Historical receipts and
legacy protocol identifiers are not evidence of GatewayKH compatibility.
R01/R02 are merged at `b5506178cd0543a4734911c4fdb3c91d21ad271b` (PR #12).
**R03 locally verified**, clean candidate `936b3e5`; integration is tracked by
[PR #13](https://github.com/theng12/Studio-Hub-KH/pull/13), whose merge state and
merge commit are authoritative. R05's SDK/lifecycle-build slice and GatewayKH
copy/contract inventory are included; the rest of R05/R09 remains open. Worktree:
`~/.codex/worktrees/handoff-completion/studio-hub-kh`, branch
`codex/local-readiness-gatewaykh`. Holder: root, completing Git integration;
workers are finished. Root full gate: 641 tests OK/7 skips, Voice722/19, Hub108,
native build and four-worker model-free package checks passed.
[Exact evidence and limits](../../qualification/local-readiness-2026-09-21.md).
**R04 locally verified and self-reviewed on `9a4570d`** (DeepSeek autonomous
takeover): the four node-owned admission bypasses (model protection, studio
disable, storage block and physical-machine reservation) are repaired, with
permanent proxy negatives, lease-lifetime coverage and two-real-Hub tests for
Studio-disable and storage. **R05 records reconciled** (`r05-report.md`; Image
offline rebuild probed and gated on a declared wheel cache). **Host-identity PATH
robustness fixed** (`b68e7b2`). **R09 Hub-side selection/no-fanout proven.** **R06
native Music workflow locally verified (`r06-report.md`): `native/build.sh` exit 0,
three native suites pass, Music renders at 1040×680 and 1920×1053, root suite 655
OK/11 skips.** Independent review remains pending for each. See
[status](../Studio%20Hub%20Project%20Status.md).
**All currently authorized locally executable work is complete:** R06's staged app
passed the model-free four-worker smoke (Music health/catalog 200, unauth 401,
owned shutdown, bundle unchanged) and R10's local candidate/identity/licence
inventory is recorded (`release_kit.py` exits 1 with 34 "advertised but not
installed" because no model library exists here). Remaining is gated: R07 needs
real models, R08 real jobs/GUI interaction, R09 counterpart ACK/cancel/Music edits
need separate GatewayKH scope authority, R10 a model library and clean/original-M1
hardware, R05's Image cache a network fetch, and independent review a separate
reviewer. GatewayKH still needs ACK/cancel/Music and end-to-end qualification; no live
connection or enrollment.
All earlier "next" statements below are historical where they differ.

### Active takeover — new computer, resumed 2026-09-21

[MEASURED] **Ordered execution authorized:** the owner asked to plan all remaining
work and start fixing top to bottom. The full R01–R10 completion plan is at the
top of [the canonical plan](../../.planning/2026-09-10-standalone-app-feasibility/task_plan.md).
Wave-1 started at PR #11 integration `1267a51170503a7801786c021a16b6a6a3d3b8af`.
**Verified:** R01 native lifecycle N-01/N-02/N-03, then R02 reference
validation C-01/C-02, committed in that order at `0e11671` and `7ec9f89`.
Integration is tracked by [PR #12](https://github.com/theng12/Studio-Hub-KH/pull/12);
its merge state and merge commit are authoritative. Root owns the board, plan
and Git integration. **Next after PR #12 is merged:** R03 Voice receipt/payload
repair, starting from the fresh integration HEAD. No R03 worker has been dispatched.
Worktree: `~/.codex/worktrees/handoff-completion/studio-hub-kh`,
branch `codex/handoff-completion`. Nothing later is marked qualified by this batch.
Both repairs have RED→GREEN evidence and clean independent review. Root's native
build/boundary/API/lifecycle rerun passed. Clean `7ec9f89` candidate passed 632
tests (7 skips), nested Voice722/19, Hub85, 21 release checks, five original probes,
and four-worker model-free startup/auth/owned shutdown with an unchanged bundle.
[Full wave-1 evidence](../../qualification/handoff-completion-wave1-2026-09-21.md).
R03 false-completion and truncated-WAV defects are reproduced, not yet repaired.

[MEASURED] **Holder:** Codex orchestrator `/root`. Repository:
`theng12/Studio-Hub-KH`; integration/default branch: `implementation/voice-runtime`.
This takeover started from `ca7928a`. H-02 through H-04 are integrated through reviewed
[PR #6](https://github.com/theng12/Studio-Hub-KH/pull/6) at `47e7e1c` and
[PR #7](https://github.com/theng12/Studio-Hub-KH/pull/7) at `ea1e622`, and
[PR #8](https://github.com/theng12/Studio-Hub-KH/pull/8) at `a012c0a`; H-05 is fixed
by reviewed [PR #9](https://github.com/theng12/Studio-Hub-KH/pull/9), and the recovered
Music package/engine contract is integrated through reviewed
[PR #10](https://github.com/theng12/Studio-Hub-KH/pull/10) at `9f25c03`. GitHub reports zero
Actions workflows and the tree contains no workflow directory; keep hosted compute disabled.

**START:** [new-computer handoff](../NEW-COMPUTER-HANDOFF.md) →
[bug/fix backlog](../../qualification/bug-audit-2026-09-20.md). This block and the
canonical planning file are the current plan. Everything under Historical handoff is
provenance, not current instruction.

[MEASURED] **This computer:** Apple Silicon `arm64`, Mac14,13, 32 GiB RAM, macOS 27.0
(26A428), Swift 6.4, clang 21, and 310 GiB free at intake. Command Line Tools are active;
the full Xcode app is not selected. A pinned Voice runtime was built locally. Its FFmpeg
and FFprobe now record macOS 14.0 as their minimum after reviewed
[PR #3](https://github.com/theng12/Studio-Hub-KH/pull/3). This is deployment-metadata
evidence, not physical macOS 14 qualification.

[MEASURED] **Audit progress:** H-01 through H-05 are fixed by reviewed PRs
[#4](https://github.com/theng12/Studio-Hub-KH/pull/4),
[#6](https://github.com/theng12/Studio-Hub-KH/pull/6),
[#7](https://github.com/theng12/Studio-Hub-KH/pull/7),
[#8](https://github.com/theng12/Studio-Hub-KH/pull/8), and
[#9](https://github.com/theng12/Studio-Hub-KH/pull/9). All five original probes pass.
H-05 verification passed 106 affected Subtitle/Hub tests with seven real-model skips,
73 Hub regressions, and independent fault-injection/restart review. The recovered Music
package/engine contract is integrated. Its next bounded runtime/staging/supervision segment is
implemented on `codex/music-package-runtime`: a hash-locked 1.7 MB overlay reuses the Voice
runtime, 618 pinned ACE-Step source files stage without Git metadata, and a complete local proof
bundle owns and routes Music without loading a model. Native Music workflow and real-model
qualification remain incomplete.

[MEASURED] **Runtime handoff:** reviewed product commit `c9eadca`; integration is
tracked by [PR #11](https://github.com/theng12/Studio-Hub-KH/pull/11), whose merge
state and merge commit are authoritative. The preserved worktree is
`codex/music-package-runtime`, based on `9f25c0310dcbe1223f323da94e845d174e043960`,
at `~/.codex/worktrees/music-package-runtime/studio-hub-kh`.
The coordinator owns packaging, canonical records and integration. The bounded
worker finished canonical accepted-parameter recovery and terminal-save rollback.
Independent review found no remaining findings. The clean-commit candidate and
21 release/phonemizer tests also passed. **Single next task after integration:**
reproduce/repair native lifecycle N-01/N-02/N-03, then reference validation
C-01/C-02, before extending native Music UI. Root remains the coordinator; no
worker owns the next task yet.

[MEASURED] Resumed checks: 111 affected tests plus 122 subtests, all five original
audit probes, native boundary/API/full build, and a model-free assembled
Hub/Voice/Subtitle/Music startup/authentication/identity/shutdown probe pass.
The installed app's Image runtime has the exact tracked dependency lock and passes
its manifest verifier. A separate copy at the main checkout's
`.build/st03-image-runtime-worker` also verifies; two Image process tests pass.
Only this immutable runtime was copied; installed app state and models were not copied.

[MEASURED] **Current gate:** final root discovery passed 631 tests in 442.126 seconds
(7 skipped), including the restored Hub bridge (85 passes) and Voice product suite
(722 passes, 19 skips). Native build and all five original audit probes passed.
The phonemizer test now checks the actual bundled-library path; release checks
explicitly select the candidate and verify its source commit. The full log includes
test-fixture socket/process ResourceWarnings; the warned process exited and the
separate packaged smoke proved owned shutdown. See the
[resume receipt](../../qualification/new-computer-resume-2026-09-21.md) for exact
artifact identity, commands, remaining audit findings and qualification limits.

[MEASURED] **Preserved inputs:** the protected legacy library remains at
`~/Downloads/model-library` (about 38 GiB) and has not been scanned, adopted, moved, or
modified. `~/StudioHubModels`, `dist`, and a staged build are absent at intake. The Music
draft remains unintegrated at `0eebc42` on `codex/recovered-music-draft`; its read-only
audit worktree is `~/.codex/worktrees/music-recovery-audit/studio-hub-kh`.
Do not merge it wholesale: its package/engine contract was recovered separately;
the runtime segment is integrated through PR #11; native workflow integration remains open.

### Historical handoff (superseded where the current takeover differs)

**Current holder:** DeepSeek Harness session, last active 2026-09-16. Earlier sessions
(Codex, then this one) are recorded below. Read this block, then the table below, then the
one task card you are claiming. The bullets between here and "Everything below this line"
are current; the longer narrative under it is historical and is kept for provenance.

- **Integration line:** branch `implementation/voice-runtime` (the only integration branch;
  run `git log -1` for the current tip). Every task branch is merged into it.
- **Backup:** remote `icloud` (a bare repo in iCloud Drive) plus a dated `git bundle`
  beside it. There is no hosted remote. `git push icloud --all` before you yield.
- **Owner scope decision (2026-09-15):** finish the WHOLE app, including the fleet /
  GenStudio connection. Model-fit checking is the owner's call — do not spend time proving
  a model fits a given RAM tier. Distribution is "copy the built app to the Mac and open it
  with right-click → Open"; signing/notarization stays out of scope.
- **Voice cloning requirement (owner, 2026-09-15, stated after planning):** TTS must use
  **clone-based generation for voice consistency**, not preset speakers. Both origins are
  required and must converge on ONE Hub reference-asset contract: a reference clip chosen
  locally in the app, and a clone pushed from GenStudio through the controller. See design
  §2 and checkpoints ST-04.CLONE / ST-10.CLONE. This is the primary Voice mechanism.
- **Generation-history requirement (owner, 2026-09-15, stated after planning): SATISFIED.**
  Every generation page (Image / Voice / Subtitle) shows its own history, and the unified Jobs
  page is tabbed with All plus one tab per studio. See ST-08.HISTORY.
- **DONE:** ST-00 planning · ST-01 runtime/model contracts · ST-02 bundled Hub ·
  ST-07 output storage · ST-09 startup/recovery.
- **One house layout, the Image page's (`9a2e059`, 0.5.4).** The owner asked twice for the Image
  generator's left-aligned form on every page, and for the fix to be applied rather than described.
  Audio and Subtitle were restructured to that shape - **form on the left** (script and voice
  settings; file, format, Transcribe and recognition), **result on the right** (recent audio;
  transcript plus recent subtitles) - and Image was brought onto the identical frame so the three
  cannot drift. Subtitle's primary action moved off the foot of a flexible column to sit with the
  file and format it acts on; its "Result format" label moved above its control instead of wrapping
  in half; the Models page's library note moved above its list (below a flexible split it landed at
  the foot of the window); "This Mac is a Not reporting" became "This Mac is Not reporting".
  **`native/render-page.sh` is why this could be done at all**: nothing in this environment can see
  the window (`screencapture` is refused, the sidebar cannot be driven), so three earlier UI fixes
  went out blind and one of them three times. It compiles the real views with its own entry point,
  hosts a page in an offscreen window and writes PNGs at any size, several pages per compile
  (`all` renders every page). Renders at 1920x1053, 1100x700 and 980x620 were inspected before
  this was handed over, and `tests/test_audio_layout.py` (8 checks) pins the arithmetic, the shared
  column widths, the form/result placement and the top alignment.
- **Shared voices: one push per controller, installed on its nodes (`a914d82` + the fleet-token
  read, 0.5.3, 2026-09-19).** The gateway asked for a door to create/update/delete a voice on a
  controller, with the controller fanning out to its own nodes, and for two decisions. Both are
  answered and built: `POST/GET/DELETE /api/hub/shared-voices` (+ `/{id}`, `/audio`, `/{id}/sync`),
  the reconciler is started, and `shared_voices` - complete and uncalled - is now wired. The caller
  may keep its content-hash id (one id across all six controllers, bytes decide identity, re-push
  refreshes rather than forks, conflicting id is a 409). **Timings are required with a transcript**:
  a transcript without aligned segments installs everywhere and then refuses to clone on the models
  that need them, so it is refused at the door, carried in the fan-out, stored by the receiver, and
  its stored count is checked before a target reads `synced`. The 1.7B Qwen reference path now
  passes those segments into `reference_audio.prepare` - otherwise storing them was decorative, and
  a locally prepared voice had the same gap. **Two blockers, both found by pushing a real voice to a
  real joined node:** the fleet credential was not allowed on the `fleet-sync` receiver at all (403,
  so nothing could ever arrive), and Connections offered the **Hub** token for joining while
  `fleet_nodes` presents the **fleet** token - a Mac that joined correctly would have answered 401 to
  its own controller. The one-token "fix" for the second was caught by two suite tests and reverted:
  it hands full controller authority to every node. The screen now shows both tokens, and the app
  reads the fleet credential from the Hub's own `hub/` state dir (it looked one level too high and
  answered nil on every Mac). Evidence: `tests/test_shared_voices.py` (17) and
  `FleetSharedVoiceTests` (6, two real Hubs).
- **A restart no longer breaks a group (`6588a34` + this, 0.5.2, 2026-09-19).** The Hub took its
  port from the operating system on every start, so both directions of a join were perishable: a
  node's stored controller address died when the controller restarted, and the controller's stored
  node address died when the node did - with nothing to repair either, because the app never
  re-joined. The port is now pinned to **47873** (`peers.DEFAULT_HUB_PORT`, the number the fleet
  code already assumed), an occupied port is refused *before* starting instead of costing three
  failed starts, a successful join is remembered in the app preferences, and
  `rejoinControllerIfNeeded()` calls the already-idempotent join route once the Hub is ready. It
  runs only when the credential origin is `adopted`; promotion to controller forgets the old
  controller. The node's own credential *is* the controller's after adoption, so nothing secret is
  stored twice. **Connections** shows the port and the address to hand a node. Verified live on this
  Mac: the Hub binds 47873, the preferences carry port and controller, and a restart comes back on
  the same address. The two-Mac enrolment is still the owner's test - see the checklist.
- **Leaving a group, and the omission that made the re-join inert (same commit, 0.5.2).** A Mac can
  now **Leave this group** under Connections: the Hub mints a fresh local credential, the origin
  goes back to `minted`, the role returns to `standalone`, and the app asks the controller to drop
  it from the roster first, while the borrowed token still works. `POST /api/hub/fleet/leave` is
  deliberately outside the fleet allowlist, so a controller cannot un-join a node. Writing its test
  exposed the real find: `save_settings` writes an explicit key list and `fleet_credential_origin`
  was not on it, so `fleet_adopt`'s write was dropped and every reader was told `minted`. Silent
  consequences, both serious - the automatic re-join (adopted only) could never fire, and promotion
  containment rested on the operator having set the role to Node by hand. Fixed, with a rule-shaped
  test that every key in `defaults()` survives a settings write. Two live Hubs prove leaving end to
  end: the group's token is refused with 403 to leave, the borrowed credential 401s afterwards, and
  the fresh one differs.
- **The model library moved out of `~/Downloads` (`6113476`, 0.5.1, 2026-09-19).** Every launch
  from the app hung. All three workers started, the Voice worker printed
  `[gen] loaded 4 jobs from history` and then slept forever inside `open()` while reading
  `~/Downloads/model-library`; `subprocess.poll()` stayed `None` for the whole 120 s wait, so no
  readiness record was written and the app reported only "the local services did not become ready
  within two minutes". Nothing logged a reason because nothing failed. macOS privacy protection
  (TCC) covers `~/Downloads`: `sample` showed the worker blocked in `_io_FileIO___init__` →
  `open`, and `tccd` logged an access request against `kTCCServiceSystemPolicyAllFiles` attributed
  to this app. The same library at `~/StudioHubModels` brings the Hub up with all five workers in
  about two seconds, and an empty folder outside Downloads does too, which rules out the library's
  contents. The app now prefers `~/StudioHubModels` and still offers a legacy
  `~/Downloads/model-library`; the setup card and **Settings → Model library** name a protected
  folder before a start is attempted; the copy step renames a legacy tree instead of copying
  34 GB again. Kit documents follow, and `focused-release-manifest.json` was regenerated
  (**0.5.1, 15 models, 34.2 GiB, `problems: []`**). Verified in the packaged app: ready in 6 s
  with 5 workers and 10 voices, a real Whisper transcript (3.1 s clip, 23.8 s wall) and a real
  Qwen clone artifact (HTTP 200, 161 KB WAV) both through the app's own Hub. Suite **375 tests,
  5 skips** - one fewer skip than before because `tests/test_peer_path_contract.py` had been
  looking only for the versioned bundle name and its live router probe never ran.
- **Distribution is two double-clicks, and copying models is now incremental
  (`41…`, this session).** The kit root holds `1 - Install the app.command` and
  `2 - Copy the models.command`. Both are location-independent (they failed on the real drive
  when the copy in the kit *root* resolved its helper one directory too high), and the copy
  step is safe to re-run: it copies models this Mac lacks, replaces models whose shipped files
  changed, leaves identical ones untouched, prunes the superseded revision of a model it just
  updated, and **re-writes the app's own receipt** with the app's bundled `model_packages`
  module so an updated model reads as installed. Measured on the real 28.5 GB library: 12/12
  copied and `files_verified`, second run a no-op in seconds. 24 new tests
  (`tests/test_model_copy_script.py`), including the launcher behaviour and a
  `model_packages.preflight` round trip.
- **WORKING END TO END (verified in the packaged app): Image, Subtitle, Qwen cloning and
  OmniVoice.** Each produced real output through the real app: a FLUX image, Whisper transcripts
  on all three sizes, and cloned WAVs from a local reference clip. What is *not* yet done is the
  formal side — ST-03's manual GUI Save click-through, ST-04's cancellation/restart receipts, and
  independent review of every card from ST-03 onward.
- **ST-04 merged** (`71611bb` + follow-up): the unified voice-reference staging contract (local
  upload + GenStudio push), Qwen Base clone requirements, the native clone UI, and **real
  offline receipts for both tiers** — QWEN06 and QWEN17, TER/CER 0.0 on reference and output,
  0 retries. **ST-06 merged** (`0d792c9`) and its packaging integration is applied: a staged app
  now starts a `subtitle` worker with `modality: transcription` and serves the
  `/studio/subtitle/api/*` reads. Integrated-tree evidence: root suite **188 OK (5 skipped)**
  (150 before ST-06), Hub regression passed, Voice regression **664 passed / 2 skipped**, native
  build passes. Both tasks remain **PARTIAL and not DONE**: ST-04 needs cancellation/restart
  receipts, an assembled real-model clone job and review; ST-06 needs the native Subtitle screen,
  Subtitle history and review.
- **Coordinator decision recorded:** public Subtitle jobs are served by the `subtitle` worker;
  Voice keeps its Whisper quality check internal to the parent Voice job and does not advertise
  `modality == "transcription"` to the packaged public scheduler.
- **Whisper Base and Small work now** (`da1a8f4`). They were catalog-only because those pinned
  revisions ship `weights.npz` while `mlx-audio` globs `*.safetensors`; the shared view converts
  npz → safetensors once, app-side, leaving the verified library bytes untouched. Both were
  verified transcribing through the packaged app.
- **Native Subtitle screen landed** (`f2df056`): the studio reads its availability from the
  Subtitle worker, shows its own durable history ("Recent subtitles") from the Hub's
  transcription jobs, and appears as the **Subtitle tab** in the unified history. The
  owner-facing name is now "Subtitle" in the sidebar and Overview, while the page's stable raw
  value stays `Transcription` because it is also the function lookup key and persisted state.
  Verified live: a staged app shows the Subtitle entry; the tab/filter logic is covered by the
  native API fixture. Sidebar navigation cannot be driven reliably by Accessibility automation,
  so the click-through remains a manual check.
- **Subtitle now works end to end in the packaged app.** Running real audio through the app
  found and fixed **four** release blockers that the fixture suites had all missed: the persisted
  batch had no `modality` (`bc0119b`); `monitor.py` refreshed Whisper availability only for
  `voice`-modality studios, so the `subtitle` worker's cache was never populated (`ce7caa0`); the
  dispatch failure was completely silent (now recorded in `governor_note`); and the Hub and the
  worker used different identifiers for the same model (`418da53`). **Proof:** audio submitted
  through the packaged app returned *"Studio Hub Subtitle Routing Check! The quick brown fox
  jumps over the lazy dog."* with `studio: subtitle`, `state: done` — see
  `qualification/evidence/st06-packaged-e2e.json`. Root suite now **192 OK**.
- **Voice is blocked by a library-format gap, not by the Base models.** Reading the actual values
  in the packaged app shows **every** Qwen entry — Base *and* CustomVoice — reports
  `cache.state: "absent"`, `path: null`, `bytes_complete: 0`. The Voice worker's cache resolver
  therefore understands nothing in the ST-01 verified-package library, and the broker's
  `is_cached` gate (`monitor.py:149`, `broker.py:2205`) refuses every Voice dispatch. CustomVoice
  worked earlier only because that proof used an HF-cache-shaped library. Image is unaffected —
  ST-03 generated a real image from this same library. **This is a user-facing gap**: the app
  would say a model is installed and verified, then refuse to use it. Next step is on the ST-04
  card: the resolver is `components/voice/app/backend/cache.py` (`hub_dir`/`repo_cache_dir`/
  `snapshot_revision`/`status_snapshot`), which only understands an **HF cache**
  (`<hf_home>/hub/models--org--name`) and never looks at `<models-dir>/<package>/<revision>/`.
  The fix is to answer from the verified package library, falling back to the HF cache, counting
  only **verified** packages as cached. Image already resolves the package layout, which is why
  it works.
- **Four engines produce real output through the packaged app: Image (FLUX.2 Klein), Subtitle
  (Whisper turbo / small / base), Qwen voice cloning, and OmniVoice.** Everything below this line
  that describes those as blocked is historical; the current position is this block.
- **One memory-policy contract now covers every Studio.** Voice already published
  `GET/PUT /api/memory-policy` + `POST /api/memory/release`; the packaged Subtitle worker was
  answering on `/api/memory/policy` instead, and `transcription` was missing from the Hub's target
  modalities, so a Whisper model could stay resident across a handoff on an 8 GB Mac. Subtitle now
  answers the fleet paths and publishes the same field vocabulary, Image publishes the same
  contract with an honest no-idle-policy answer, and the Hub exposes
  `GET/PUT /api/hub/memory/policy` for the app. `9d83e6a`.
- **Settings has a Model memory section.** Per Studio: the saved mode, the Studio's own
  machine-aware default (under 16 GiB releases immediately), the model it is holding right now,
  and a machine-wide Release button. Verified against the staged bundle with all three workers up
  — `qualification/evidence/st10-memory-policy-packaged.json`.
- **The fleet is complete in the app.** A job submitted to a controller runs on a joined node for
  all three studios; the artifact lives on the node and is pulled through the controller; cancel
  and retry work; and a node that dies mid-job is regenerated elsewhere rather than marked
  uncertain. A node adopts the controller's fleet credential as part of joining, so one new Mac
  needs only the controller's address and fleet token. `machine:<id>` pins a Mac — for Subtitle
  too, whose own dispatcher used to ignore the pin — and an unknown name is refused at submission.
  **Connections → Fleet overview** is one row per Mac (reachable, version, chip, memory, free
  disk, completed jobs, measured average seconds) from the new `GET /api/hub/fleet/overview`, and
  **Send here** sets the standing pin. **Settings → General → Start services at login** exists,
  defaults **off**, and gates only an at-login launch.
- **Connections is real.** It shows the address the Hub bound, role, station and controller IDs,
  the Hub version and migration stage, and the Hub token masked with owner-only Reveal/Copy. It
  used to say "Controller connection is not available in this build." `d114c40`.
- **A defect the fixtures had agreed with:** the app was calling `/api/hub/version`, which does not
  exist — the Hub serves `/api/version`. The native fixture had been written to match the mistake,
  so only driving a real Hub process caught it. **When you add a native fixture route, confirm it
  against the real Hub.**
- **Completed Subtitle jobs were invisible to the Hub's statistics.** A subtitle job produces no
  asset row, so `ledger.stats` counts it from `activity_events`; the dispatcher recorded only the
  `running` transition. Every completed subtitle was missing from `total`, `by_machine`,
  `by_model`, `matrix` and the timeline while the live view showed it. Fixed in `7e42a07`, with a
  test that drives the real item path — the existing statistics fixture planted its own `done`
  event, so it could never have noticed that nothing wrote one.
- **ST-11's software checkpoints are done.** Release candidate `dist/Studio Hub 0.4.0.app` staged
  from a clean tree at `7e42a07`; `packaging/release_kit.py` generates the kit manifest and fails
  if the library is missing an advertised package; `qualification/evidence/measure_machine.py`
  runs one generation per model on a machine and writes its receipt;
  `qualification/focused-release.md` is the operator guide and `qualification/st11.md` records the
  gates. **Every remaining ST-11 checkpoint needs physical hardware this session does not have**
  — see the condition table in `st11.md`.
- **Still not done, and not inferred:** VoxCPM2 has never produced output on any host (its own
  memory message on this 16 GiB Mac); 8-bit has never been run; no macOS floor is established on
  an original M1; no second Mac has run the kit; ST-04/05/06/10/11 have not had independent
  review.
- **The rule that will bite you:** run tests with `.build/voice-runtime/bin/python3` (3.12.14).
  The system `python3` is 3.9.6 and fails. The adopted Voice suite must run from
  `components/voice/`, not `components/voice/app/` — its `pytest.ini` excludes four upstream tests
  whose artifacts are deliberately not shipped.
- **Model verification is `ctime`-sensitive.** `components/model_packages._stamp()` includes
  `st_ctime_ns`, so anything that hard-links a library file makes the app report
  `File changed; verify again` even though the bytes are unchanged. Remedy is a **Verify** pass in
  Models; proof harnesses should copy rather than hard-link.
- **Never delete `.worktrees/`**, never assume a "pending" line in a receipt is still true, and put
  every result in a committed file — not in a chat message.
  true, and put every result in a committed file — not in a chat message.

## Historical first tasks — superseded

These commands and status claims belong to the earlier handoff. Follow
[the new-computer guide](../NEW-COMPUTER-HANDOFF.md) and the current takeover block
at the top of this board; do not run the versioned paths below as current instructions.

1. **Run the kit on the owner's machines.** `dist/Studio Hub 0.4.0.app` plus `model-library/`,
   right-click → Open, choose the library, then
   `qualification/evidence/measure_machine.py --label '<machine>'` on each Mac. That clears
   ST-11.3, the RAM/disk half of ST-11.4, and produces the per-machine speed comparison the owner
   asked for. **VoxCPM2 has never run on any host** — a 24/32 GB Mac with the other engines idle is
   the only way to know, and 8-bit has never been attempted at all.
2. **Independent review** of ST-04, ST-05, ST-06, ST-10 and ST-11, reusing the pattern that found
   seven real defects in ST-03. Every one of those cards is still unreviewed. The two most
   productive questions so far have been "does the fixture agree with the real server?" and "does
   the production path ever actually write the row the query reads?" — both found real defects.
3. **ST-08 is nearly closed**: Models has Verify and Import Folder, Subtitle export acknowledges
   delivery, the Overview shows the Hub's measured generation times per model and per machine, and
   Settings shows storage limits with the protected bytes cleanup may not touch. What is left is
   the manual GUI click-throughs, ST-08.OS (hardware), and 08.PILOT (waits on independent review of
   the ST-04/ST-06 receipts).
4. **ST-10.4 / ST-10.6 / ST-10.DATA** need a second real machine and a counterpart, so they stay
   partial. If a second Mac becomes available, per-location aggregation and node deduplication are
   the first things to exercise.
5. **ST-11.OS** needs an original M1 at the oldest candidate Sonoma release. Until then the only
   OS-floor evidence is Image's own refusal below macOS 14.0.

**Staging and copying must go together.** `stage_voice.py` writes `Studio Hub.app.tree-sha256`
beside the app, and `release_kit.py` fails when that file does not match the app's contents. Copying
a newer app onto the drive without re-running the staging tool therefore ships a kit whose own
integrity check fails - which happened on 2026-09-18, and was only visible by reading
`focused-release-manifest.json`'s `problems` list. Whenever the app is replaced, run staging and
then regenerate the manifest, and check `problems` is empty before handing the drive over.

**Cutting a release is now four edits and one gate.** `VERSION` is the source; `release-notes.md`
is what the app's **What's New** screen shows and `CHANGELOG.md` is the engineering record. Add an
entry to both, raise `VERSION`, and run `tests/test_release_notes.py`, which fails when the newest
heading in either file is not the version being shipped, when the packaged `Info.plist` disagrees,
or when the notes are not inside the bundle. The app bundle is called `Studio Hub.app` with no
version in the name, so a bump does not invalidate the launchers, the verify scripts or the
instructions — the old launchers still accept `Studio Hub 0.4.0.app` as a fallback for a Mac
carrying a previous kit.

**Verify every one of these by running the packaged app, not only the suites.** Seven integration
defects in the Voice/Subtitle chain passed a fully green 192-test suite and were only visible when
a real job ran (`st04-packaged-clone-e2e.json`, `st06-packaged-e2e.json`). `bash native/build.sh`,
`.build/voice-runtime/bin/python3 -m unittest discover -s tests -p 'test_*.py'` and
`PYTHONPATH=$PWD/.build/test-deps .build/voice-runtime/bin/python3 -m pytest tests/hub_regression -q`
are the quick gates; staging with `packaging/stage_voice.py --runtime .build/voice-runtime
--native-shell .build/StudioHub --image-runtime .build/st03-image-runtime-worker` is the real one.

*Plain-English position:* the app is finished enough to copy onto a Mac and use. Four engines
produce real output through it — FLUX images, Whisper subtitles, Qwen voice cloning and OmniVoice
— and the shipped kit is generated, hash-recorded and checked against the library. The app now
shows what each Studio is holding in memory and lets you choose when it lets go, shows the address
and credential a controller needs, and reports per-machine speed for every studio including
subtitle. What is left is not code: VoxCPM2 has never produced output on any host (it needs a
24/32 GB Mac), no second machine has run the kit, no original M1 has established the macOS floor,
and none of ST-04/05/06/10/11 has had independent review. Every one of those is named as a
condition with the exact step that clears it, in `qualification/st11.md`.

Updated: 2026-09-19 (model library out of ~/Downloads in 0.5.1; a pinned port and automatic
re-join in 0.5.2). [Design](../standalone-plan.md) · [Execution plan](../implementation-plan.md).
ST-01 foundation, ST-02 bundled Hub and ST-07 output storage are reviewed and locally
integrated; ST-09 startup/recovery is reviewed and integrated. ST-03 Packaged FLUX was
merged into `implementation/voice-runtime` and a **real packaged 1K generation is
evidenced** ([receipt](../../qualification/st03-image-real-inference.md)) with the
**export/receipt acknowledgement now proven** and the native save path wired; restart-history
is verified at the packaged level. Retry counts (ST-03.6) and the remaining review fixes are
open. Historical 0.3.0
evidence does not complete these cards.

| ID | Phase / deliverable | Depends on | Status | Owner/session |
| --- | --- | --- | --- | --- |
| ST-00 | Source review and narrowed plan | — | DONE | Current planning session |
| [ST-01](ST-01.md) | A · Runtime/model contracts | ST-00 | DONE | root / implementation/st-01 |
| [ST-02](ST-02.md) | A · Bundled local Hub | ST-01 | DONE | root / implementation/st-02 |
| [ST-03](ST-03.md) | B · Packaged FLUX | ST-02 | IN_PROGRESS | st03_image (Sol/high), coordinator reviews; **real image + receipt ACK + restart history proven**; needs one manual GUI Save click-through |
| [ST-04](ST-04.md) | C · Qwen Base | ST-03 | IN_PROGRESS | coordinator; **Qwen Base clone works end-to-end in the packaged app** (`6f8fa8b`); needs cancellation/restart receipts + review |
| [ST-05](ST-05.md) | C · VoxCPM2 / OmniVoice | ST-03 | IN_PROGRESS | coordinator; **OmniVoice works end-to-end**; VoxCPM2 is host-memory-bound (re-probe on 24/32 GB) + UI exposure + review |
| [ST-06](ST-06.md) | C · Independent Subtitle | ST-03 | IN_PROGRESS | coordinator; **real transcript in the packaged app** (`418da53`); needs native SRT export/ack + review |
| [ST-07](ST-07.md) | C · Per-device cleanup | ST-02 | DONE | st07_storage (Terra/high), root integrated |
| [ST-08](ST-08.md) | D · Native workflows/settings | ST-03, ST-07 | IN_PROGRESS | coordinator; clone UI, heavy voices and the Model memory + Connections screens are in; Subtitle export/ack remains |
| [ST-09](ST-09.md) | D · Startup/recovery | ST-02 | DONE | st09_startup (Luna/high), root integrated `7aa7775` |
| [ST-10](ST-10.md) | E · Fleet/GenStudio | ST-02, ST-09 | IN_PROGRESS | coordinator; Connections, the independent-client proof, credential rotation and the clone contract are in; 10.4/10.6/DATA partial, no second machine, needs review |
| [ST-11](ST-11.md) | F · Qualification/install kit | ST-04, ST-05, ST-06, ST-08, ST-10 | IN_PROGRESS | coordinator; 11.1/11.2/11.7 done, kit + measurement tooling proven, hardware checkpoints open, needs review |
| [ST-14](ST-14.md) | G · Local Chat Studio | ST-01, ST-02 | IN_PROGRESS | the lane works end to end — a real completion answers through the installed Hub; worker, Hub wiring, native page and kits done (24/24 on all four drives); **reviewed twice** ([review](ST-14-review.md)); remaining: the 8/16 GB tier run (harness ready), a human in the Chat page, and the tier-refusal decision |

## Status rules

- WAITING: prerequisites unfinished.
- READY: prerequisites complete; available to claim.
- IN_PROGRESS: named owner, start commit and worktree recorded.
- REVIEW: implementation/evidence ready for independent review.
- DONE: reviewed, verified and integrated; exact evidence recorded.
- BLOCKED: concrete unmet requirement and next action recorded.

One coordinator updates the shared board. Workers update only their assigned card/handoff in isolated worktrees. Whole-task dependencies require DONE. Named partial model receipts may unlock only the explicitly identified ST-08.PILOT checkpoint after independent review and integration; they never mark a parent model task complete.

## ST-00 evidence

Baseline: 29d0baa. Deliverables: revised PRODUCT.md, DESIGN.md, README.md, canonical design, execution plan, task cards and four research/rescope-*-2026-09-15.md source inventories.
Proof: 20 Markdown files and 142 local links validated; 11 task cards match the acyclic board dependencies; git diff --check clean. Independent scope_hub_storage review resolved sequencing/storage findings and confirmed no remaining design/handoff blocker. Recorded in canonical progress. Documentation integrated into the local working tree; no app implementation or new release commit.
No application code, runtime, credentials or installed node changed. No new model qualification claimed.

Each card records owner/session, start/final commit, worktree, paths, commands/results, artifact/model/runtime identities, reviewer/findings, limitations and next task.

## Intermediate pilot and final gates

ST-08 may begin after ST-03/ST-07 with native fixtures. ST-08.PILOT additionally
requires integrated, independently reviewed QWEN06 and Whisper Turbo receipts from
ST-04/ST-06; remaining Vox/Omni variants need not finish first.
ST-10 token/contract work depends on Hub and ST-09 software lifecycle, not physical
reboot qualification. ST-11 waits for all complete model/UI/connection tasks and owns
physical login/reboot plus all-model assembled-app acceptance.

## Owner additions after initial review

Complete GitHub/original-host download and copied-folder model setup are now explicit
ST-01/ST-08/ST-11 checkpoints. Location membership, generation data/statistics and an
IP/address + token API suitable for future standalone GenStudio are explicit ST-02,
ST-08 and ST-10 requirements. ST-10.TOPOLOGY is resolved: preserve existing GenStudio-to-location-controller-to-nodes
routing, using existing Hub/fleet tokens. No new gateway or direct node connections.
Independent model/local work continues.

Original-M1/older-OS support is now explicit in ST-01/02/08/09/11. The baseline
candidate is Sonoma 14.0, subject to real per-model and point-release qualification.
Keeping a compatible release working while OS upgrades are deferred is required;
generation during an OS installation restart is not promised.

## ST-01 evidence and next owner

Implementation `072abf6` is reviewed and locally integrated on 2026-09-15. See
[phase receipt](../../qualification/st01.md): 724 passing tests, ten pinned manifests,
real Turbo offline import/relocation/tokenizer and real small-file HTTPS verification.
The retained Image audit's 115 relocation issues are explicitly ST-03.RELOCATE work;
new model inference and physical M1/Sonoma qualification remain later checkpoints.

**ST-02 is DONE.** Implementation `4a5124c` is reviewed and locally integrated.
[Phase receipt](../../qualification/st02.md) records the 0.4.0 native Hub/Voice artifact,
107 project tests, 640 Voice tests, final admission/retained regressions and assembled
process/job proofs. ST-07 is reviewed and integrated at `0e97d98`; its 12 focused tests
also pass after integration. See the [storage receipt](../../qualification/st07.md).
ST-09 is DONE: implementation `c0855d4`, integrated by `7aa7775`; see the
[startup receipt](../../qualification/st09.md).

## Integration state — 2026-09-15

`implementation/voice-runtime` @ `cd6ed5d` is the single integration line. All five task
branches are now merged into it; `implementation/st-03` was the last one and was merged
on 2026-09-15. No task work remains stranded on an unmerged branch.

Recorded verification of the integrated tree (local; Apple M4, 16 GiB, macOS 26.6.2):
root `unittest discover -s tests -p 'test_*.py'` — 124 tests OK, 1 skipped; retained Hub
regression wrapper — passed; adopted Voice `pytest` — 639 passed, 2 skipped; `bash native/build.sh`
— boundary and API contract checks passed. The Image private-server smoke is skipped
unless the pinned Image runtime is built. Staged artifacts: `dist/Studio Hub 0.4.0 Integrated Proof.app`
(Hub + Voice) and `dist/Studio Hub 0.4.0 Image Proof.app` (`implementation_commit 86b11d3`,
payload `0cabfe857b9afaef74e22d9cd54dd90c4fbacfb2cd0179d931fcfb0402a6f63a`, bundling
`runtimes/image` + `image_worker.py`). The pinned Image runtime is preserved at
`.build/st03-image-runtime-worker` (tree SHA `c9cd56a6…`, verified). The earlier recorded
0.4.0 artifact is preserved.

What the 2026-09-15 reconciliation changed: merged `implementation/st-03`; committed 71
pre-existing progress lines plus two stranded research handoffs; marked ST-09 DONE with
its integration commit; fixed the wrong build-runtime path in `tests/test_image_bundle.py`;
preserved the previously unrecorded real Image generation as tracked evidence under
`qualification/evidence/`; and wired verified Image saves to the Hub receipt route, proving
the contract end to end. Undo point for all of it: branch `backup/pre-handoff-7aa7775`.

## Cautions for the next session

- **Never delete `.worktrees/`.** `st-01`, `st-02` and `st-03` retain build-side runtime
  and model proof that is not reproducible from Git. The checkout is ~54 GB but only
  ~2.7 MB is tracked; `model-library/`, `.build/` and `dist/` are intentionally ignored.
- **The only remote is `icloud`** — a bare repo at
  `~/Library/Mobile Documents/com~apple~CloudDocs/studiohub-standalone-backups/studiohub-standalone.git`,
  with a dated `git bundle` beside it. There is no hosted remote, so nothing is shared or
  reviewed externally. `git push icloud --all` before yielding.
- **Re-staging over an existing app fails on read-only runtime files.** Run
  `chmod -R u+w "<app>"` before `rm -rf`; `rm -rf` alone reports permission-denied for the
  bundled runtime and leaves a partial bundle.
- **Run root and Hub tests with the build runtime, not system Python.** System `python3`
  is 3.9.6 and fails on `dict | None` and on pytest 9 (`typing.TypeAlias`). Use
  `.build/voice-runtime/bin/python3` (3.12), with `PYTHONPATH=.build/test-deps` for pytest.
- **Records live in Git, not in chat.** A commit SHA or result that exists only in a
  handoff message is lost to the next session. Record it on the card.
- **DONE requires integration.** This board listed ST-09 as `IN_PROGRESS` after `7aa7775`
  had already merged it. Check Git before trusting a status row.
- **Test counts in receipts are historical.** Re-run before citing; a recorded count does
  not automatically hold after a merge.
- **Evidence under `.build/` is disposable.** `.build/` is gitignored; anything that must
  survive belongs in `qualification/` or `research/` as a tracked file.
- **Whole-task dependencies require a DONE prerequisite,** so ST-04/05/06/08 correctly
  remain WAITING while ST-03 is IN_PROGRESS. Do not start them yet.
- **ST-09 start-at-login is source/fixture-qualified only.** No live `SMAppService`
  registration was performed; physical login/logout/reboot belongs to ST-11. The running
  app exposes it as a `Start at login` switch, observed off on 2026-09-15.
- **Stop owned processes before yielding.** A st-03 session left the assembled app, Hub
  worker and Image worker running for 3 h 44 min. Verify with
  `pgrep -fl 'StudioHub|hub_worker|voice_worker|image_worker'`.
- **Pixel screenshots need Screen Recording permission** for the agent host;
  `screencapture` otherwise fails with `could not create image from display`. The
  Accessibility tree is available and was used instead.
- **DEFECT — `vibevoice-realtime-0-5b-4bit` cannot load, so the VibeVoice offer is dead.**
  Found 2026-09-24 while sampling the kit. The kit checkpoint's `config.json` has
  `model_type: vibevoice_streaming`, which `mlx_audio` 0.4.7 remaps to `vibevoice` in
  `MODEL_REMAPPING` — but `get_model_class()` only applies the remap inside
  `if model_name is not None and model_type_mapped != model_type:`, and the checkpoint carries
  no `model_name`, so the loader passes an **empty list**. `[] is not None` is true, the
  `for part in []` loop never runs, the `elif` that would apply the remap is skipped, and the
  import is attempted as `mlx_audio.tts.models.vibevoice_streaming`, which does not exist. The
  app calls the same `load_model`, so the offer fails for every user. Fix is a one-line
  `"model_name": "vibevoice"` in the model's `config.json`, or an upstream patch to treat an
  empty list like `None`. Evidence: [tts-archive-walkthrough-2026-09-24.md](../../research/tts-archive-walkthrough-2026-09-24.md).
