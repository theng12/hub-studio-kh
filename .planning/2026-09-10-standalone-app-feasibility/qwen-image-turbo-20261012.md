# Qwen Image 2.1 Turbo 4-bit — implementation and qualification

Owner-authorized scope, 2026-10-12. Base: fdeabefd4174f4bf099aefa9c2e810c317d4b67a (main, 0.6.170). Worktree: ~/Developer/_worktrees/studio-hub-kh/qwen-image-turbo; branch feature/qwen-image-turbo.

Purpose: faster images on 16 GB and larger Macs, keeping the existing low-RAM Qwen lane. No 8 GB claim; no replacement of the ordinary Qwen package. Use the Image worker, FIFO, physical-machine lease, output checks, cancellation and library guard already present.

## Constraints
- The current Hub task (now C2b) is in progress and C3 is running in the shared iCloud HANDOFF.md. No release publication before the current Hub task is Done; no inference or running-Hub change on This Mac before C3 Done. Preserve pauses and one generation per machine.
- Pin the public Turbo 4-bit package revision and every file checksum; verify the official eight-node schedule from its checkpoint. CFG off; 1024x1024, 1280x720, 720x1280 only; zero MLX cache, prompt materialization, component eviction, tiled decoding, no resident model.
- Historical preparation rule, superseded by the owner's 2026-10-13 production-adoption decision below: do not borrow ordinary Qwen measurements, keep the candidate disabled, and wait for physical Turbo qualification.
- Workers must not commit, publish, deploy or recursively delegate. Removals go to project _trash. No real profile/library access from tests.

## Bounded tasks
1. Runtime adapter and test-first checks: reuse QwenLowRam, read/validate the pinned eight-node schedule, implement first-order unshifted Euler through the packaged MFLUX scheduler API. Keep ordinary Qwen behavior unchanged. Test schedule, steps/CFG refusal, release/cache behavior and cancellation; characterize the real pinned MFLUX runtime without loading weights.
2. Package/capture: add only the new CHOICES/DETAILS entry and reproducible manifest with sha256 and immutable URLs. Check checkpoint layout against the pinned runtime before promising compatibility. Keep the candidate unqualified and out of stocking.
3. Root integration: register ImageModel, document the single bounded recipe, extend the existing qualification runner to choose Turbo without changing its ordinary default. Test production refusal, catalog/limits, package verification and runner safety. Offline checks and independent review before committing/pushing the feature branch.
4. After the current Hub task and C3 are Done: update to latest main, stage an isolated app, qualify on physical 16 GB first and each supported tier/chip; record worker footprint, clean free-memory start, swap, pressure, cancellation, usable outputs and wall time. Compare normal Qwen at identical sizes/prompts for speed and quality. Set floors and seconds_per_step only from receipts.
5. Release only after physical proof and independent review: changelog/release notes, next free version, DMG in dist, commit/push main through normal flow, paced rollout This Mac → 1100 → remaining sites. Update shared handoff Done/Next with accurate deployment state.

## Review focus
Official Turbo is not Viggle Turbo LoRA; generic eight-step linear sampling is wrong. Converted packages can have changed component names/layout. A disabled candidate must be refused by production entry points, not merely hidden in a picker. A larger Mac trial cannot qualify 16 GB. Cache restoration and cancellation must survive failure. All images must be validated and every removed artifact preserved.

## Evidence / ledger
- Baseline: 10 existing test_image_qwen*.py checks passed with the existing hub-gate Python; no model inference.
- Native worktree creation unavailable for the projectless task (Not a git repository); Git fallback used at the owner's requested path.
- Code-only Graphify extraction completed for 573 tracked Python/Swift files: 16,554 nodes / 40,656 edges before Turbo edits. Current graph will be refreshed after structural work; no paid model calls. Native Swift parse coverage has one extractor limitation, unrelated to this image-only change.

### Preparation review evidence
- Package task: fresh pinned capture equals stored manifest; independent reviewer verified all 19 hashes/size/shard references and official schedule. 28 package/catalog checks passed. P3 defaults provenance corrected: owner recipe and checkpoint schedule have separate source fields; scoped re-review clean.
- Runtime task: ordinary qwen_lowram.py unchanged. New adapter normalizes saved names while preserving MLX array layout; independent header proof retains every required learned tensor (297 transformer, 398 text, 226 VAE) and all 481 q4 weight/scales/bias triples. All eight scheduler transitions verified in float32/bfloat16 using only tiny arrays. Scoped runtime review clean; no model construction or inference.
- Integration task: private candidate opt-in plus production refusal, target-tier admission, fixed8steps/CFGoff/1MP presets, physical16/24/32 runners. Independent review reproduced an8GiB bypass and missing standalone process checks; both fixed test-first and re-reviewed. Root found real MFLUX model-command suffixes were missed too; suffix/negative regressions now pass.
- Root: 85 focused tests passed (one existing absent archived runtime fixture skip); latest affected qualifier/admission/deadline 21 checks passed. Fresh whole-branch review and its regression-fix re-review are clean. Final full offline rerun completed: 2,350 tests, one independently reproduced pre-existing local kit-helper failure, 14 skips. No new Turbo code failure remains.
- Public checkpoint indexes and headers retained in ignored graphify-out/turbo-checkpoint-evidence; worker receipts under graphify-out. No weight payload download, liveHub change, pause change, inference, install or release.

### Coordination / next
Latest shared iCloud HANDOFF.md shows C2b Building and C3 71/82 done (C2 progressed while source preparation ran). Do not infer completion from existing main0.6.170. After source review/gate: commit and push feature branch only. Source preparation is not a qualified/released model. Physical tests, measured metadata, next-version assignment, DMG/main integration and rollout remain next work under those explicit coordination gates.

### Full-gate repairs and final review
- Initial full offline gate: 2,347 tests, 8 failures + 1 error, 23 skips. Five source catalog count/size prose checks and two generated benchmark checks needed updates for the disabled candidate. The other two failures were missing worktree fixtures (.build/prepare-kit.sh and pytest test-deps), not product changes.
- Updated catalog prose to 39 / 168,958,543,918 bytes and described the unchanged production set (36 / 120.8 GB) plus three withheld candidates accurately. Regenerated model-benchmarks from manifests/evidence. No drive was refreshed or model installed. 24 document/record checks passed; root document/record/qualification rerun31passed.
- Prepared worktree-local read-only references to existing runtimes/test dependencies/kit fixture. All3failed-fixture module checks passed; child Voice product suite998passed28skipped.
- Model-free packaged Image import smoke reproduced a pre-existing fdeabef failure: -I could not find the existing components...QwenLowRam class. The checker now resolves and validates the registry root before adding it to the isolated import path. Both immutable baseline and candidate registry imports pass in the composite runtime (MFLUX0.20.0 / MLX0.32.2), including QwenTurbo. Two isolated regression checks pass.
- Fresh final review and the above repair-wave re-review found no actionable code findings. Physical quality, footprint, clean start floor, swap/pressure, per-chip speed and mid-denoise cancellation are still unqualified.
- Code-only Graphify updated after the core structural change (578files,16,644nodes,40,837edges); final import-checker graph refresh next. swiftc -parse native/StudioAPI.swift passed, confirming the graph's unrelated Swift parse warning is an extractor limitation.

### Final preparation verification
- Full rerun: 2,350 tests in817.609s,1failure,14skips. The onlyfailure is `test_tts_8gb_runner.RunnerTests.test_the_kit_step_carries_all_of_it_and_does_not_prune_it`: the local untracked `.build/prepare-kit.sh` lacks `RUN-8GB-BREEZE.command`. The exact unchanged test and required files from immutable base fdeabef reproduce the same failure against the same existing helper (graphify-out/base-kit-helper-check/result.log). The original/shared helper was not edited; this is retained as a release/kit follow-up, not silently skipped or called green.
- All initial9failures caused by candidate inventory/docs and absent fixture setup were repaired; focused reruns passed. Voice product child998passed28skipped. Packaged runtime model-free import smoke passes with Turbo in its four Pythonmodelclasses. No modelconstructor/inference/weightpayload download.
- Final code-only graph refresh:579files,16,651nodes,40,856edges; graph stays ignored. Native parser check passed; no native source change.
- Candidate scope review and repair-wave review are clean. Source can be committed/pushed as disabled preparation; it is NOT hardware-qualified, installed, released or ready for production. Current C2b/C3 gates remain in force. Keep worktree for continuation.

### Takeover integration — 2026-10-12
- Owner authorized ChatGPT to take over production and fixes. Hub main `c2c6644` (0.6.171) is rolled out, including This Mac; YuE2 package repair 24/24. Current jobs/ownership are in the shared handoff. Earlier C2b/C3 coordination entries above are historical.
- Merging latest main into the published feature branch without rewriting history; keep both released YuE2 and unreleased Turbo notes. Merge verification/commit pending.
- Download remains the single resumable PID35445 process; 13/19 files now verified, 7,262,868,526 of 9,963,443,598 bytes. No final receipt or inference yet.
- Primary untracked kit helper Breeze omission repaired with a backup under `studio-hub-kh/_trash/build-helpers/`; targeted proof still pending while Story holds the sole test lease.

### Owner production adoption — 2026-10-13
- The owner explicitly adopted Qwen Image 2.1 Turbo MLX 4-bit for normal production on 16 GB and larger Macs and waived a separate physical Turbo memory-fit qualification. This supersedes the disabled/private-qualification rules above. Regular Qwen Image 2.1 Low-RAM MLX 4-bit already operates on 16 GB; that is the owner's adoption basis, not a Turbo measurement.
- The normal manifest sets `production_allowed: true`, `minimum_unified_memory_gb: 16` and `recommended_unified_memory_gb: 16`. The Hub's manifest-backed admission and the ordinary kit copy flow accept 16 GB and larger Macs and exclude 8 GB Macs. Turbo is not in `NOT_STOCKED`.
- The registry retains the one bounded recipe, exact eight steps, CFG off, roughly-one-megapixel presets and per-job release. It uses 8.0 GB free-before-start as a provisional inherited operational assumption from regular Qwen Image 2.1 Low-RAM MLX 4-bit. This is not Turbo-specific evidence and replaces no future measured floor.
- Turbo-specific peak memory, speed and output results remain unmeasured. No `seconds_per_step`, measured peak or 8 GB claim is published; scheduling and cancellation code are unchanged. The generic explicit-`false` production gate remains fail-closed for future candidates.
