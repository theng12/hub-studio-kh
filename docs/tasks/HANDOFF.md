# Studio Hub — session handoff

**Read this first in a new session.** It is kept current with every release; see the rule in
`AGENTS.md`. If it disagrees with the code, the code wins — and then fix this file.

Last updated: 0.6.66 (the tab was filtering the models that could reach the tab, 2026-09-25).

**Three open items closed on 2026-09-26, and each guard was proved to fail on the defect it was written for.** *SH-4*: FLUX.2 Klein 4B MLX 4-bit published `79.34 s` for a scene it described exactly, and **the number was in no file in this repository**. The stored run of that scene is **105.943 s** (`qualification/st03-image-real-inference.md`, job `af4913b2`, 2026-09-15) — and the host was wrong too: the row claimed a 32 GiB M2 Max, the record says **Apple M4, 16 GiB**, which is a measurement on the tier the fleet runs. `packaged_inference` said `pending` while that completed run sat in the tree. *SH-8*: `max_output_duration_s` was enforced by the worker and published by no family, so no contract offered a bound that was being applied; the `qwen3-tts` family declares it now, and it is the only family that honours it. *SH-18*: Voice's "lane decides" exception is the only one in the fleet (Image, Music, Subtitle and Chat all fill an omitted field unconditionally) and now has four tests on its boundary.

**The lesson each of the three taught, in one line each.** A published number needs an artefact, so the test opens the file the row names — a scene description is not a source. A shape in a table nobody references is not a contract. And **a guard the runner does not collect is not a guard**: the first version of the SH-18 test class was named `LaneDecidesBoundaryTests`, pytest collected nothing, and the suite reported the same count as before — which is how a boundary goes unguarded while looking guarded.

**0.6.66 in one paragraph.** The owner found the Audio page "dodgy": he had Khmer TTS installed and
could not see it. It was a **closed loop between two controls** — the voice tabs were the *selected
model's* modes, and the model picker listed only models publishing *the current tab's* mode — so a
model that could not do the mode you wanted also hid every model that could, and there was no tab to
press to discover the missing one. The tabs are now the **union of what this Mac's models publish**
(over models that can actually run), and a tab click moves the model to one that publishes it. The
page also no longer **opens on a model that cannot run**: `reconcileSelections` accepted `supported`,
which is only "the Hub ships a package for this", so it opened on `voxcpm2-4bit` — whose folder is
gone while its receipt still said it was installed — and sat there disabled. Availability is read
now, **on a transition**, so deliberately selecting a model in order to install it is not undone by
the next poll. And `[Errno 2] No such file or directory: 'voxcpm2-4bit'` was being rendered to the
operator **twice**: `preflight()`'s catch-all turned every `OSError` into `state: invalid` with
`str(exception)` as the reason, and the Hub republishes a package's `reason` verbatim. Six of his
packages carried it.

**The question to ask first, because it has now found the same bug three times.** **An omitted
parameter takes the value in force; a supplied one is the caller's answer and wins.** Every worker
already implements it. What keeps breaking is a **consumer that always supplies** — so when a Models
default "doesn't work", do not start at the worker: ask *"does the page send this even when the
operator never touched it?"* Three found that way: the pace (`speed`, a flat `1.0`),
the word timings (`word_timestamps`, a persisted `false` with **no control in the app at all**), and
the voice (`preset_speaker`, a non-optional `String` carrying `""` **or a name seeded from the
roster**, so an untouched picker looked like a choice).

**SH-16 closed on both sides, and publishing the contract alone would have been a trap.** The roster
moved to one home (`catalog.PresetRoster`, carried on the family, feeding both the picker payload and
the contract) and `lane_contract` publishes `preset_speaker` for a model that offers preset **and**
has a roster — nothing for Khmer (one built-in voice), nothing for a cloning tier. **No shipped row
qualifies today**: the roster's own model was pruned in ST-13.B, so the fix is data-driven and
dormant, and a CustomVoice tier returning to the catalogue gets it with no code change.

**Two traps met while doing this.** *A source regex is a parser and it is wrong in the same direction
every time*: under `tests/native/`, the fixture's mock of the CustomVoice contract had to be updated
to match the producer, or the fixture proves the app against a model shape that can no longer exist.
*A fix that is right for one state can be wrong for the state beside it*: `preflight`'s early return
covered both "no receipt at all" and "the manifest moved under a receipt while the bytes are present";
reporting both as a bare `not_installed` made the app say **"download it"** for a model the operator
already has. They carry different sentences now.

**Chat's 16 GB tier is now MEASURED, and this is the first tier claim in this project that rests on runs rather than arithmetic.** The owner ran `chat-tier-run.sh` on three 16 GB Macs on 2026-09-25 — M1, M2, M4 — and all three completed a 256-token completion: **12.40–12.48 tok/s (M1), 14.14–14.02 (M2), 18.33–18.60 (M4)**, peak MLX **5.23 GB on every one of them** (5,226,237,062 bytes, identical — it is the model's footprint, not the host's), load **9.08–13.69 s**, swap 0.00 MB on two and 0.25 MB on the third. `Qwen3.5-9B 4-bit`'s manifest now says `status: qualified` with those runs and their source in `memory_evidence`; the previous note was catalog arithmetic inferred from the weight size. **8 GB is still unmeasured, and the reason is now recorded**: the harness *refused* there on available memory — below its floor of the checkpoint's 5.98 GB + 0.5 GB of working room, with macOS and the owner's other apps holding the rest. **A refusal is not a result.** It says that Mac had too little free memory at that moment, not that the tier cannot run the model, and it must not be quoted as though it did — which is exactly the mistake the old `8 GB is not viable` note made. That gate was also a dead end: on an 8 GB Mac the floor is essentially never met, so the one tier needing an answer was blocked by a check that tier cannot pass. **Fixed**: the 8 GB tier attempts by default, bounded by the swap cap and the timeout, and the verdict block declares `STARTED BELOW THE FLOOR` so nobody reads the number as a clean run. The app's 8 GB warning is unchanged until a run actually starts.

**Two things that measurement exposed, both fixed in the same pass.** The harness's verdict block said *"the shipped default is immediate, which would release the model between runs"* — false on the very tier it measures: the shipped default is `auto`, which resolves to **`balanced`** at 16 GiB and above and **holds the model for ten idle minutes** (`components/memory_modes.py`). The run pins `performance` so no measurement can be disturbed by a release, which is right; its explanation was not, and it is the one line of that block an operator would act on. And `packaging/capture_model_manifests.py` used `sys` five times without importing it, so `--write` died with a `NameError` in the branch that *refuses* to replace a manifest — the branch that protects a package from being silently regenerated.

**`docs/model-benchmarks.md` is the fleet's model record, and it is generated.** The owner asked for it in as many words — *"something i can keep and store so that future me knows this benchmark and stuff… i just don't remember. that's why i want something lasting."* It answers, on one page: what each of his four machine sizes can run, what each model's RAM floor is and whether that floor was **measured or only declared**, and every reading that exists with the record it came from. `qualification/model_record.py --write` rebuilds it from the manifests, the Voice lane's measured runtime evidence and `qualification/model-readings.json`; `tests/test_model_record.py` regenerates it in memory and fails on a one-byte difference, so it cannot drift. Readings are added to `model-readings.json` with a `source_path`, and **the generator refuses a row whose source is gone** — a number may not outlive its evidence. It ships on every kit drive as `Technical/model-benchmarks.md`, because the Mac that needs the answer is the one the drive is plugged into.

**Two honest gaps it makes visible rather than papering over.** The **Music** catalogue labels (`ACE-Step 1.5 Turbo (2B DiT)`, `Stable Audio 3 Medium (1.4B)`) state no quantization, unlike the voice, image and chat labels which carry it — so the page shows what those labels say and the test that checks quantization is scoped to the rows where a reading states one. And **21 of 24 packages have no run behind them**; their floors are catalog metadata, which the page says plainly and lists by name.

**The TTS prune is done, and it was done before it started.** The owner reviewed the voice models and confirmed the set is what he wants: a prune on 2026-09-24 (`~/StudioHubModels/_trash-tts-prune-2026-09-24`, now empty) removed the nine packages that were broken or absent on his Mac. All four drives hold **24 receipts with 0 missing folders, every package still shipped name for name** — nothing to move, nothing to forget. The prune *feature* stays; it removes whole models the kit stops shipping and deliberately never one a Mac merely cannot run.

**Next in the owner's order: the LLM phase**, then video last (shelved). The whole LLM surface is **one package** — `Qwen3.5-9B 4-bit`, floor 16 GB, `qualified` on the three 16 GB Macs and excluded on 8 GB. So there is nothing to prune there either, and the open question is the one the 8 GB exclusion creates: **chat runs on 5 of his 7 Macs.**

**SH-15(b) was not a flake, and it is solved.** The bridge test ran Voice's suite in a child interpreter; `components/voice/app/tests/test_storage_policy.py::test_release_notes_follow_installed_changelog` asserts the worker's newest changelog version equals the repo's `VERSION`, so **bumping `VERSION` before writing the changelog entry fails it deterministically** — a normal mid-release state. It read as a flake only because its own log was overwritten by the next green run. Two lessons, both already paid for: keep the failing log (done, and proved), and **write the changelog before bumping `VERSION`** — or expect that gate to fire, correctly, and mean it.

**0.6.65 in one paragraph.** The owner's ruling for every generator — *"we have a default but control
on the page is overwrite… stuff that can be change or altered such as speed must be given to the
user"* — closed SH-14. A value set in Models is what a job uses; a control on a generator page is the
operator's own answer and always wins; and a control nobody has touched supplies nothing at all,
which was missing everywhere it mattered. Voice's `speed` was a flat `1.0` sent on all four
generation paths, and `0.5...2.0` was written into the app rather than read from the lane contract;
Subtitle's `word_timestamps` was a persisted `false` submitted on every request with no control
anywhere in the app. The weight format is the row's own `weight_format`, not the extension of a file
name, and the app stopped sending a `language` to the **12 of 13** voice families whose lane
publishes none.

**Read this before trusting a lane default.** The rule the whole release turns on: **an omitted
parameter takes the value in force; a supplied one is the caller's answer and wins.** Every worker
already implemented it (`model_fields_set` in `_job_parameters`; `set(form.keys())` in Subtitle's
`with_model_defaults`; `validate_request` in Image and Music). What was broken was only ever the
**app**, which supplied everything unconditionally. So when a Models default "doesn't work", the
first question is not whether the worker reads it — it is whether the page sent the field anyway.

**The tests that were pinning it are the reason nobody looked.** Three tests in
`tests/test_peer_path_contract.py` asserted `var speed: Double`, that every audio path passed
`speed: audioSpeed`, and that the slider used a `0.5...2.0` literal **declared in the app** — green
for as long as the defect shipped, and one of them *required* the hardcoded range. They are replaced
by `GenerationDefaultOwnershipTests`, whose general half is: **a generation control the app owns
must be optional**, because "the operator has not chosen" is a state the request has to express.

**A source-scanning trap, met twice in one session.** `tests/native_model_defaults.py` extracts a
struct with `body()`, which stops at the first line beginning `    }`. Adding a method among
`NativeModel`'s stored properties silently truncated the extraction and hid the property the test
was looking for; the helper lives in an `extension NativeModel` for that reason, and says so. The
same class bit the audio-path regexes, which matched the dead `AudioRequest` struct as though it
were a live request body — it is deleted. **A regex over Swift source is a parser, and it is wrong
in the same direction every time: it reads a declaration as a use.**

**A flaky gate, second symptom, both seen once.** `tests/test_voice_product_suite.py` failed inside
a full `tests/` run; the very next full run was green with the same deterministic order. The child's
log was written to a fixed path and overwritten by each run, so the failing child's output was gone
before it could be read. Fixed in the same release: see the SH-15(b) note above. The flake itself is
still unreproduced. The native `build.sh` cancellation race is SH-15(a).

**0.6.64 in one paragraph.** Every node on controller 1100 was unreachable — five of five
`ConnectTimeout` — because each Mac was answering on an address frozen the day it was set up. A Mac
now recomputes its listen list on every launch from its own current private and Tailscale
addresses, **additively**, so nothing that worked is ever removed; `--listen-auto`, `--listen-host`
(both persisted now) and `--fleet-mode` are the supported setters; `/api/health` and
`/api/hub/settings` publish what the Hub is actually bound to, so "loopback only" is a fact rather
than a timeout. The Models tab is rebuilt around what each lane publishes, with per-model editable
defaults for four of five lanes (0.6.65 is what made those defaults actually reach the engine).

**The lesson this release cost, and it is now a rule in `AGENTS.md`:** the 0.6.63 handoff
documented this release's fixes as though the running build had them — *thirteen commits apart* —
and GatewayKH diagnosed an entire fleet against promises the installed binary could not keep.
**"It is implemented" and "it is in the build you are running" are different claims**, and only the
second is worth sending. Check the **installed bundle**, not the checkout. This was the third time
in two days a claim outran its artefact.

**0.6.63 in one paragraph.** Four owner asks drove it, and each turned out to be a number or a
control published somewhere other than where it was read. Image's low-RAM configuration became an
operator control (`Auto / Low RAM / Full`, published per model as `loading_modes`, and **no control
at all** for a model that has one loading mode). Every Studio gained a fifth memory mode, **`auto`**,
and it is the default — the one rule that is right on an 8 GB and a 32 GB Mac at once, and the
reason chat and music now hold their models for 10 idle minutes on the larger machines. Chat became
**dispatchable by the fleet** and its work is filed on the node that ran it, with tokens/second
published from the worker's own generation time. And the kit's copy step now **prunes** what the kit
no longer offers and **carries Z-Image-Turbo 6B MLX 4-bit on 8 GB Macs** at the owner's request.

**Shipped state:** implementation commit `f49966e`, installed to `/Applications`; DMG
`dist/StudioHub-0.6.67-f49966e.dmg`. `development_build: false` on the stage, read from
`release-manifest.json` **before** installing, on a tree confirmed clean first.
**Verified in the installed bundle, not the checkout:** version `0.6.67`, commit `f49966e`, and the
running installed Hub answers `version: 0.6.67`, **5 of 5 studios up**, bound on
`100.75.85.116` (tailnet), `192.168.1.243` (LAN) and loopback.
**Kits:** all four (`NOVA`, `UGREEN`, `ATLAS`, `ORICO`) at `0.6.67`, `files 60641`, `"problems": []`,
each library still **24 installed / 24 advertised** with all 208 recorded model files checked by size
(70.5 GB), and each ran its own `verify-kit.sh` → `RESULT: PASS`.
**Gate on this commit:** `tests/` **1322 passed, 11 skipped, 997 subtests**; `bash native/build.sh`
green; `components/voice/app/tests` green inside the bridge.
**Pushed:** `main` and `implementation/voice-runtime`.
**Superseded:** `c0fa6c6` (0.6.66), `37cb07b` (0.6.65), `fabe498` (0.6.64) and `a4a78dc` (0.6.63) in
`_trash/dist/`.

**A flaky native test, seen once:** `bash native/build.sh` failed with *"the in-flight read must
have been torn down by the cancellation"* and passed on an immediate re-run with `native/` provably
unmodified (`git status` clean). Recorded because a gate that fails once in a while is a gate people
learn to re-run, which is the same as not having it. Not chased; it needs a deliberate reproduction.

**A second flake, also seen once — and its evidence now survives.** In the 0.6.65 gate,
`tests/test_voice_product_suite.py` failed inside a full `tests/` run; the next full run was green
(`1306 passed`, exit 0) at the same deterministic order (`-p no:randomly`). The test wrote its
child's output to `.build/voice-product-suite.log` on **every** run, so the failing child's log was
overwritten by the next pass before anyone could read it — which left exactly one fact, that the
child exited nonzero. **Fixed**: a failure now writes `.build/voice-product-suite.failing.log`,
which a passing run leaves alone, headed with the outcome and the UTC time so a log left over from
an earlier failure cannot be read as a current one. A hang keeps its partial output too;
`subprocess.run` used to raise before anything was written, so the suite that never *finished* was
the one case with no evidence at all. **Verified end to end**: the child was forced to fail
(`STUDIOHUB_TEST_RUNTIME=/usr/bin/false`), the failure log appeared with `Outcome: exit 1`, and the
next green run left it **byte-identical** (same SHA-256). The synthetic log was moved to
`_trash/voice-suite-logs/` so it cannot be mistaken for a real one. **The flake itself is still
unreproduced** — SH-15(b) stays open for that, but the next occurrence can now be read rather than
guessed at. The native `build.sh` race is SH-15(a).

**A trap, walked into and out of on the same day:** reading `tests/test_hub_jobs.py`, two
`class HubJobProofTests` by `grep` look like one shadowing the other and eleven tests never running.
They do run — `_PROOF = r'''…'''` spans lines 19–733, so the first is *inside a string*, the
packaged-Hub program a subprocess executes. `87c1e04` is a revert of exactly that conclusion, and it
says so. The parser saw four classes while `grep` saw six. **Ask the parser.** App identity **`0.6.63 a4a78dc`,
`development_build: False`**; each kit's library is 24 packages plus the Hugging Face cache
(70,529,417,547 bytes) and every kit reports `problems: []`; `verify-kit.sh` reports **PASS** on
ATLAS, NOVA, ORICO and UGREEN. Both `main` and `implementation/voice-runtime` are pushed to
`a4a78dc`.

> **A trap worth remembering, hit and fixed during this release.** `stage_voice.py` sets
> `development_build` from `git status --porcelain`, so a bundle staged while *any* file is
> uncommitted is published as a development build. The first 0.6.63 stage was — two test files a
> worker had not yet handed over — and it was caught only because `verify-kit.sh` prints the
> identity line and `development_build: True` looked wrong next to 0.6.62's `False`. **Stage from a
> clean tree, and read the identity line on the first kit before doing the other three.**

**Verification for this release** (all on the frozen tree): `tests/` **1156 passed, 11 skipped, 762
subtests**; `tests/hub_regression/cases` **175 passed** — that suite is excluded from root
collection by its own `collect_ignore`, so **run it explicitly**; `components/voice/app/tests`
**726 passed, 19 skipped**; `bash native/build.sh` and `tests/native/pool-probe.sh` green;
`tests/native/negative_controls.py` mutates one real source line per new UI piece and all four are
caught.

**Five traps this release paid for, so the next one does not:**

1. **Read `HEAD`, not the working tree**, when deciding whether something already shipped. A
   shared tree with agents in it makes the working tree a claim about the future: a render site
   that was believed to pre-exist was in fact the same worker's uncommitted edit, and a correction
   was issued on the strength of it.
2. **A two-ended invariant does not see a hole in the middle.** "A worker publishes
   `memory_warning`" and "a page renders `selected*Model?.memoryWarning`" were both true for
   Subtitle while the sentence could not travel, because the Transcription page builds its models
   from `/studio/transcribe/availability`, not from the catalog row. Pin the *middle* — the
   mapping that joins them.
3. **`staged_import_gaps` now runs on every build and it is not decorative.** It refused this
   release over `registry.py`'s import of `enrollment_repair_transport`, a module that has never
   existed; the 0.6.61 fix had caught the *symptom* (the ImportError) and left the pointer. The
   import is deleted, not exempted.
4. **A green fixture can describe a product that does not exist.** The native Image fixtures had
   Z-Image and FLUX the wrong way round — every test passed while asserting the opposite of what
   ships.
5. **An allowlist is the security boundary.** Adding `chat` to `_FLEET_STUDIO` grants exactly the
   GET shapes that regex is reused by; it was verified against the whole compiled allowlist rather
   than assumed, and the POST/DELETE rows spell their modalities out so they gained nothing.

**0.6.61 — the hardcoding audit, closed. New models are now data.** The owner's rule from
2026-09-24 (*"my rule is never hardcoded — everything must be dynamic for all generators and
models"*) is implemented rather than aspirational. `research/hardcoded-model-lists-audit.md` is the
inventory: 73 findings, **18 Tier A and 40 Tier B all fixed**, 15 Tier C kept as contracts with the
two named caveats inside them fixed too. The audit doc carries a `Fix status` section; the rows
themselves are the snapshot and their `file:line` citations are deliberately not rewritten.

**Start here for anything model-related:**

- `AGENTS.md` has three new standing rules from this release: **never publish a number the model did
  not state** (provenance beside every value, or publish nothing), **a version field must carry a
  version**, and **one lane, one published contract**.
- `docs/lane-execution-contract.md` is the shape every lane publishes; `docs/tasks/ST-20.md` is that
  workstream (Gateway Studio's seven asks, with where each is answered).
- `tests/test_catalog_visibility.py` is the standing *"does every model show"* sweep the owner asked
  for. It is the test to run first when a model is reported missing.
- `tests/test_document_inventory.py` recomputes the counts stated in `model-manifests/README.md` and
  `docs/runtime-contract.md`, so a stale number in a document fails a test instead of misleading the
  next reader.

**0.6.62 shipped** (superseded): commit `3e1a0eb`, DMG `dist/StudioHub-0.6.62-3e1a0eb.dmg` — both
DMGs are in `_trash/dist/`, and 0.6.62's app is in each drive's `_trash` as `Studio Hub.app-0.6.62`.
Its verification was `tests/` 935 passed / 11 skipped and `tests/hub_regression/cases` 172 passed.

**Three things this release changes about the owner's Macs, all of them one-time:**

1. `chatterbox-4bit` (MLX 4-bit) and `chatterbox-8bit` (MLX 8-bit) need verifying **by hand**, once,
   because the manifest's companion list moved from the legacy `dependencies` field to
   `requires_packages` and that changed the receipt digest. **Correction (2026-09-25): this note
   used to say they "re-verify once on the next launch". Nothing re-verifies automatically, and
   pressing Verify Files could not repair them either — `verify_installed` returned `not_installed`
   in 0.0 s whenever the receipt did not already agree with the digest, so it could only re-hash a
   package that was already fine.** That dead end is fixed (see the verify entry below); on a build
   before the fix the operator has to re-import. Everything else is unchanged — measured, not
   assumed.
2. The five companion-bearing voice packages (chatterbox 4-bit/8-bit, pocket-tts-4bit MLX 4-bit,
   qwen3-tts-12hz-0-6b-base-8bit MLX 8-bit, qwen3-tts-12hz-1-7b-base-4bit MLX 4-bit) report
   `companion_cache_missing` until the Hub's first catalog refresh writes the cache mirror. It is a
   hard link, so it costs no disk.
3. A read-only library keeps them named-not-ready with the reason shown in the app instead of
   failing at generation time.

**0.6.62 ships everything below.** The five commits that were pushed but not installed are in this
release, so the Clone tab fix is finally in front of the owner. (They were:) the A9 reference derivation (`96eb792`), the
staging fix and corrections (`3c3d9d0`), the voice `modes`/runtime/smoke work (`1e1873d`), the Clone
tab rebuild (`c1fde64`), the Hub's voice-mode fix (`849d211`) and the audit row (`1ed03de`). The
shipped identity is still `f20fd1a`. **A re-release is what puts the Clone tab fix in front of the
owner**, and until it happens he cannot see it.

**The Clone tab is fixed and the fix is verified on a real bundle.** The owner reported no voice
picker on the Clone tab until he visited the Voices page. Measured cause: a voice-library read
cancelled mid-flight wiped the list and published "the voice library is unavailable" — the store
treated cancellation as failure. The diagnosis was half wrong and the correction mattered: a
cancelled `URLSession` read throws `URLError -999`, not `CancellationError`, and it was being
wrapped into a transport error before the store saw it, so a `CancellationError` guard alone would
have fixed nothing. The library is now read once **as part of connecting**, so no page task is on
the critical path; a cancelled load changes nothing; the picker says whether it is reading, empty,
unreadable, or short of transcripts; and every saved voice is listed with the unusable ones marked
rather than hidden.

**The Clone tab is library-only, per the owner's decision (2026-09-25).** His words: *"i don't want
to just drop in any voice and starts cloning no. i want to have a button to 'Add Voices' and bring
to the voice tab to add 'voices' to the app. that way it can works across all… no need to have a
picker for choose a file."* The clip chooser, the reference-clip and transcript blocks, `Transcribe
Clip` and `Prepare Voice` are gone from the Audio page; the saved-voice picker plus `Add Voice…` /
`Manage Voices` remain, and adding or deleting a voice happens only on the Voices page. The
`POST /api/generate/txt2speech/reference` route stays for Gateway/GenStudio.

**Verified end to end on a staged bundle, because the app now requires it**: all **13** voice rows
serve `modes`, and `/api/hub/capabilities` maps every one into its control vocabulary —
`[reference_audio_clone, voice_design]` for VoxCPM2 4-bit/8-bit/bf16 (MLX) and OmniVoice bf16 (MLX),
`[preset_voice]` for khmer-tts-mms (MMS VITS) and khmer-tts-female-v2 (ONNX VITS),
`[reference_audio_clone, preset_voice]` for pocket-tts-4bit (MLX 4-bit). The Hub's own derivation
had been **losing** `voice_design` for those four models and inventing `preset_voice` for
pocket-tts; it now reads the producer's field through one table and fails loudly on an unknown mode.
The native build has no fallback for a missing `modes`: a model that publishes none stays listed
with no tabs and generation blocked, naming what to update. **App and worker must ship together**,
which they do — one bundle.

**Verify Files was a dead end; it verifies now — and the LLM lane is proven end to end.** The Chat
page told the owner "Its files are not verified. Open Models and press Verify Files." Pressing it did
nothing: `verify_installed` decided from the receipt alone and returned `not_installed` in **0.0 s**
whenever the receipt was missing or its digest had moved, so it could only re-hash a package that was
already correct. Measured on the shipped 0.6.62 with Qwen3.5-9B 4-bit's 5,977,071,067 bytes sitting at
the conventional folder and no receipt: `POST /api/hub/model-packages/qwen3-5-9b-4bit/verify` → 200,
`not_installed`, nothing hashed, and Hub-side chat refused `503 MODEL_NOT_READY`. The same early
return made `chatterbox-4bit` (MLX 4-bit) and `chatterbox-8bit` (MLX 8-bit) unrepairable after their
digest moved — their receipts are exactly `manifest_digest` of the manifest **with the companion list
emptied**, which is what the legacy `dependencies` spelling produced.

It now hashes whatever is on disk and records the result: a package with no receipt registers, one
whose digest moved verifies against the current manifest and rewrites its receipt, and one with
nothing on disk stays `not_installed` with a reason naming both paths it looked in. Bytes that do not
match are refused by name — package, file, measured size/sha256 — and the route publishes that
refusal instead of republishing preflight's `not_installed`. Measured after: verify **2.97 s** →
`files_verified`, `dispatch_state: eligible`; and a real completion through the installed Hub —
**`POST /api/hub/chat/completions` → 200 in 7.61 s**, "The capital of Cambodia is Phnom Penh.", usage
24/10/34, `finish_reason: stop`. Longer term this is why "the app says a model is missing when its
bytes are present" must never be answered with an instruction that cannot work.

**Open, with the exact fix rather than a guess:**

- **Pocket TTS 4-bit (MLX 4-bit)'s eight preset voices are WON'T-FIX**, decided by the owner on
  2026-09-25: *"pocket tts's 8 eight can be skipped. it's not needed as long as it has voice clone."*
  The model ships cloning and that path works, so the `preset_speakers` work is not being done. The
  roster is still derivable from the `pocket-tts-preset-voices` package (`alba`, `azelma`, `cosette`,
  `eponine`, `fantine`, `javert`, `jean`, `marius`) if that decision is ever reversed.
- **The operator no longer adds a voice by picking a loose clip file.** The owner retired that
  affordance on 2026-09-25 — *"i want to have a button to 'Add Voices' and bring to the voice tab to
  add 'voices' to the app. that way it can works across all… no need to have a picker for choose a
  file"* — so the Audio page's Clone tab is **library-only**: the saved-voice picker plus a button to
  the Voices page, which is the one place a voice is added or deleted. The
  `POST /api/generate/txt2speech/reference` route itself stays (Gateway/GenStudio transports source
  bytes through it) and no longer refuses for an un-audited model: it derives the reference contract
  from the model's own published row and labels it `profile_audited: false` with a source per value,
  refusing only when a catalogued cloning model publishes nothing derivable (409, with the missing
  field and both files to fix). See the reference-prep entry below.
- **Reference-audio profiles, resolved rather than demanded.** A9's first fix refused every
  un-audited cloning model instead of using a guessed 24 kHz copy — a wrong result traded for no
  result. The contract is now derived from the model's own published facts (`sample_rate_hz` from its
  catalog row; the transcript rule from its published guardrails or its family's lane parameters),
  marked `profile_audited: false` with a per-field source, and a bound nothing publishes is recorded
  as *not published* rather than defaulted. Measured: VoxCPM2 4-bit (MLX) resolves **48 kHz** where
  the guess was 24 kHz; Pocket TTS 4-bit (MLX) 24 kHz; Qwen3-TTS 1.7B Base 4-bit (MLX) keeps its
  audited contract verbatim. The owner's saved-voice workflow was never affected — for VoxCPM2,
  Chatterbox, Fish and Pocket TTS the library path does not call `prepare()` at all (the sole
  library caller is gated on `1.7b-base in repo`). **Still missing:** `channels` and reference
  min/max duration are published nowhere, so an un-audited model enforces no length bound (the 25 MB
  upload cap still applies) and makes no channel claim — recorded as *not published*, not invented.
- **The voice smoke pilot records a shipped model now, and it found a real bug doing it.** The
  historical 0.1.0 entry stays as evidence; `qualification/model-inputs.json` adds a `shipped_smoke`
  entry for **Qwen3-TTS-12Hz-0.6B-Base-8bit (MLX 8-bit)** with the tracked reference clip and its
  transcript. `smoke_voice.py` waited for uvicorn's `Uvicorn running on …` line, which uvicorn never
  prints when the worker supplies its own sockets — so it could never see a worker that had in fact
  started; it now waits on the worker's ready file and reaches `/api/catalog` in about 2 s. The
  parameters were proven through the shipped hub-inclusive 0.6.61 bundle: `done`, a 230,444-byte
  24 kHz WAV, 24.26 s (receipt under `qualification/evidence/voice-runtime-2026-09-25/`).
  `_stage_voice` now stages `model-manifests/`, which it never did, so a **voice-only** artifact can
  resolve its own validator and processor; proved inert for a shipped bundle (same tree, with and
  without the line: `payload_sha256` and every component's `source_sha256` byte-identical).
- **Two model-keyed *measurement* tables remain on purpose**, and the audit's `Fix status` names
  both: `memory_admission._FLEET_DEFAULTS` (FLUX.2 Klein 4B MLX 4-bit's measured 7.5 GB total floor,
  which is *lower* than its published 16 GB tier and so cannot be expressed through the published
  fields without being masked by `max()`). It is the *only* one left: the voice lane's curated
  `_MEASURED_RUNTIME` map was deleted and its numbers now come from
  `qualification/evidence/voice-runtime-2026-09-25/measured-runtimes.json`, each row naming the probe
  file it came from — and two numbers were **dropped** rather than kept, because they existed only as
  prose: Pocket TTS 4-bit (MLX 4-bit)'s 0.30 s and VoxCPM2 4-bit (MLX 4-bit)'s 61.9 s / 738.4 s. They
  are listed in the record's `withheld` block with their reasons. Neither table is a gate: the total
  floor is not enforced, and the runtime record is pinned to shipped repos by a test. The pattern to follow for anything similar is the one
  the owner-approved Qwen3-TTS 12Hz 0.6B Base 8-bit floor now follows — **it moved onto the model's
  own catalog row** (`min_free_memory_gb: 3.2`) once the producer published it, and the Hub's
  model-keyed table was deleted.
- **An unexplained shell anomaly, recorded rather than explained away.** While building the fix,
  a hosted-window harness (on-screen and off-screen, four variants) showed the Audio page's **outer**
  `.task(id:)` / `.onAppear` chain never firing at all, while the Voices page's `.task(id: connected)`
  does and the Audio page's inner views do appear. A minimal SwiftUI replica of the same shapes fires
  correctly, so it is specific to that view in this tree and nobody has explained it. **The fix does
  not depend on it** — the voice library is read during connecting — but any *future* page that puts
  required loading in an outer `.task` should not assume it runs. Worth an hour with the real app
  open; needs a human driving the UI.
- **Two tracked test modules share the basename `test_qwen_base_standalone`** (one in `tests/`, one
  in `components/voice/app/tests/`, both added in `d24150f` on 2026-09-15), so the two suites cannot
  be collected in one pytest invocation. **Deliberately not fixed:** renaming one would edit an owned
  path documented in `docs/tasks/ST-04.md` and diverge from the historical run log at
  `qualification/evidence/takeover-2026-09-20/final-project-tests.log`, and the alternatives
  (`__init__.py` in either directory) would break the sibling-module imports both suites rely on.
  The gate already runs them separately, and `tests/test_voice_product_suite.py` is the bridge that
  keeps the component suite in `unittest discover`. The cost is one extra command, which is cheaper
  than the change.
- **`capture_model_manifests.py --write` still withholds two packages** (`ace-step-1-5-turbo`, whose
  planner and two files come from other sources, and `stable-audio-3-medium`, whose publisher path
  differs in case). It refuses and exits 3 rather than overwriting; extending the capture to
  multi-source and publisher-path mapping is the follow-up.

**0.6.59 — a second Image model, and the engine it needed.** Z-Image-Turbo is selectable beside
FLUX.2 Klein and renders text FLUX gets wrong: asked for "THE GOLDEN LIGHT OF DREAMS", FLUX writes
"THE GOLEN LIGHT OF DREAMS" at 4 steps and worsens at 9, while Z-Image gets all five words. The
engine moved for it — mflux 0.17.5 → **0.20.0**, mlx 0.31.2 → **0.32.2**, re-locked 75 → 63
packages with nothing unrelated moved. FLUX was re-qualified rather than assumed: its pixels do
change (94.2 % differ, SSIM 0.972) but the distance sits on glyph edges and flat interiors are
near zero, and on the typography scene the title is identical including the pre-existing typo.

The Image worker had run exactly one model — repo, revision, class import and defaults were module
constants. It is now a registry, and the catalog advertises both models with their own steps,
limits, memory policy and qualification. Z-Image's memory mode is chosen from the Mac's actual
memory and applied through the CLI's own `CallbackManager._register_memory_saver` mapping, because
`low_ram` is not a constructor parameter. It does **not** claim the default fails on 16 GB — that
figure is cache-inflated from a 32 GB host. **The 8 GB tier is unclaimed** pending the owner's run.

**0.6.59 is staged and verified but NOT installed, and the drives are untouched.** Staged at
`.build/StudioHub-0659`, verified as an artefact: version 0.6.59, the registry in the bundled
worker, the shipped runtime reporting mflux 0.20.0 / mlx 0.32.2, both manifests, and the release
manifest recording the new runtime digest `c3acf766…` (replacing 0.17.5's `c9cd56a6…`). Steps 4-6
of the release loop are deliberately held so the release lands as one consistent state with the kit
stocking below.

**This refresh should also close ST-13's drive debt.** The handoff has recorded since the TTS
prune that the drives' installed app predated the reduced Voice catalog, so a re-stage was owed.
Checked against the installed 0.6.59 bundle and the restocked ATLAS library: the app advertises
**21** packages and the library carries **21**, with **zero** drift in either direction — nothing
advertised that the kit lacks, nothing in the kit that the app does not offer, and no pruned voice
model still on the menu (voice-mlx 11, voice-vits 2, image-mflux 2, subtitle-mlx 3, music-mlx 2,
chat-mlx 1). So swapping the drive app to 0.6.59 re-stages the reduced Voice menu as a side effect
of the Z-Image release. Confirm per drive with `verify-kit.sh` rather than assuming it.

**Kit stocking is the open step:** the four drives carry Z-Image in `optional-models/`, outside
`model-library/`, because the app had no adapter when they were built. It needs moving to
`model-library/z-image-turbo-mflux-4bit/b3a8f311…/model/` **and registering in the kit's install
receipt** — the second part is the easy one to miss, since `verify-kit.sh` checks every file the
receipt records. Without it the kit advertises 21 and carries 20; `verify-kit.sh` reports that as a
note rather than a failure, but a Mac would then download 5.9 GB that the drive exists to supply.

**0.6.58 (hotfix) — 0.6.57 shipped a broken build.** The Chat manifest reached the release catalog
but `chat-mlx` was missing from the Hub's engine→worker map, so `/api/hub/model-packages` raised
`KeyError` and answered **HTTP 500** on every Mac. The native `refresh()` reads that route with a
hard `try`, so it did not degrade one list — it threw the whole refresh, set the Hub unreachable
and **left every model picker empty**. Fixed, verified in the installed bundle, and guarded by a
test that walks the real catalog. **Do not install 0.6.57.**

**0.6.57 — a node and its controller can both see the group.** The owner connected nodes and
neither end showed the pool. Two faults, one cause: the group was not a fact either end held.
A controller's Fleet overview was built from its dispatch registry (so a just-joined node read
as *"this Mac has not reported in yet"* until the 12 s peer poll caught up, and a node running
no studio was missing); a node held a borrowed credential with **no record of whose** — the
controller's address lived only in the app's preferences file, and `parent_controller_url` was
cleared by any save made while the role was not `"agent"`. Fixed by recording the controller at
adoption (`POST /api/hub/fleet/adopt` takes `controller_url`), tying `parent_controller_url` to
`fleet_credential_origin == "adopted"`, and building the overview from `fleet_nodes.pool()` —
which a node answers by reading the controller's roster back. Evidence:
[qualification/fleet-pool-2026-09-24.md](../../qualification/fleet-pool-2026-09-24.md).
**Not yet staged** — the app, DMG and the four drive kits are still 0.6.56.

**0.6.56 (hotfix) — 8 GB Macs could not generate images.** `7187d8d` (0.6.55) made a
model's RAM *qualification tier* the first `blockReason`, so the Image worker's advisory
`memory_warning` set `runtimeReady=false` and `usableModel` refused every submit. FLUX.2
Klein was unusable on the whole 8 GB tier (Chat too); 16/24/32 GB were never affected. Fixed
in `native/StudioAPI.swift` (`blockReason` no longer returns the warning; the worker's
`memoryEligible == false` still blocks). Image label also restored to
`FLUX.2 Klein 4B MLX 4-bit`. Native boundary/API/lifecycle suites and 14/14 Image tests
pass. If a future change wants the tier to gate dispatch, it must do it in the worker or the
Hub's admission — never in the app's `blockReason`.

## Where things stand

- **The shared checkout is on `implementation/voice-runtime` (2026-09-24).** All four
  parallel sessions' work is committed in dependency order: image fix + label (`2f2dabd`), TTS
  prune (`7f6e663`, `e60d194`), Chat Studio (`d4cefc8`, `02b81da`), video research (`258d6e6`),
  the Chat qualification (`4e87afd`, `66f2ce2`) and the Z-Image record (`94224d9`), plus the
  coordinator docs. **0.6.57 — fleet pool visibility — is the newest work and is committed but not
  staged.** The image text-rendering qualification is recorded in
  `research/text-rendering-model-shortlist.md`, `research/image-memory-pressure.md` and
  `qualification/z-image-turbo-2026-09-24.md`: **Z-Image-Turbo is the owner-approved model and
  the only candidate that reaches the 16 GB fleet tier** — Ideogram 4 (22.5 GB peak) and
  Qwen-Image-2.1 both abort at their larger size on the 32 GB M2 Max `Mac14,13` with macOS's
  Metal "Impacting Interactivity" error.

- **Two known-failing tests in the root Python suite, both pre-existing and neither a product
  fault (measured 2026-09-24):** `tests/test_voice_product_suite.py` gives the voice product suite
  a 300 s subprocess timeout, and that suite runs past 16 minutes on the 32 GB M2 Max under
  parallel load — it is a timeout, not a failure. And
  `test_subtitle_standalone.py::CatalogContractTests::test_a_small_mac_is_warned_about_not_refused`
  passes in isolation and fails only in the full run: an ordering/isolation fault. The staging
  tests in `test_voice_bundle.py` **were** genuinely broken and are fixed in 0.6.57 (their fixture
  predated the Chat component and never copied `packaging/chat_worker.py`).

- **The four SSD kits carry ~29 GB of optional image-model backup (2026-09-24).** Each drive's
  `Video Generation Kit/Studio Hub KH/optional-models/` holds `z-image-turbo-mflux-4bit`
  (5.91 GB), `ideogram-4-mflux-q4` (14.88 GB) and `qwen-image-2.1-mlx-serve-4bit` (10.69 GB),
  plus a README. They sit **outside** `model-library/` deliberately: the app has no adapter for
  any of them, so it must not advertise them, and `verify-kit.sh` still reports PASS on all four
  drives. Copy integrity was checked file-for-file against the sources. Z-Image needs the mflux
  0.17.5 → 0.20.0 bump plus a per-model adapter before the app can use it
  (`research/three-model-rollout-plan.md`).

- **0.6.57 is released and pushed.** Staged (`.build/StudioHub-0657/`), installed to `/Applications`
  (`0.6.57 / 12e377f`, all six components), DMG built and verified
  (`dist/StudioHub-0.6.57-12e377f.dmg` — mounted, read back 0.6.57 with chat+hub+image+music+
  subtitle+voice), all four `KH-STUDIO-*` kits refreshed to 0.6.57 (60,630 files each) and each
  verified `RESULT: PASS`. The displaced `Studio Hub.app-0.6.56` was preserved into each drive's
  own `_trash/` — `.build/prepare-kit.sh` now moves it there instead of `rm -rf`, and points at
  `StudioHub-0657`. Kit readmes updated and copied. **Step 8 is done:** both
  `implementation/voice-runtime` and `main` are pushed and `origin` now carries `34014d5` — the
  remote had been sitting at `46e5d89` ("Kit readmes to 0.6.54"), so 0.6.55, 0.6.56 and 0.6.57
  were all unpublished until 2026-09-24. Pre-publish scan found no secrets, no token files and no
  model weights in the 22 commits; `main` moved by fast-forward, so nothing was rewritten.

- Release **0.6.56** is superseded. The 8 GB `blockReason` fix from it is carried forward.
- **0.6.55 adds two Khmer voice families** — `khmer-tts-mms` (`khmerttsopensource/khmer-tts`,
  MMS VITS via transformers/torch) and `khmer-tts-onnx` (`sengtha/khmer-tts-female-v2`, Coqui
  VITS via ONNX Runtime). Both are single-speaker, Khmer-only, non-MLX, under the new
  `voice-vits` packaging engine. Prepared through `.build/voice-runtime`.
- The four SSDs carry `Video Generation Kit/` holding `GatewayKH/`, `Story Studio/` and
  `Studio Hub KH/`, refreshed from the current build on every release.
- The app is **unsigned**. First launch needs right-click → Open; the kit's
  `1 - Install the app.command` clears the quarantine itself. (The owner has decided not to
  sign — do not raise it.)
- The canonical plan and history are in `.planning/2026-09-10-standalone-app-feasibility/`,
  with `progress.md` appended to throughout and `CHANGELOG.md` + `release-notes.md` per version.

## The release loop, exactly

1. `VERSION` → the new number.
2. Write the `CHANGELOG.md` and `release-notes.md` entries (both, every time; the release notes
   are read by the owner, the changelog by a developer).
3. Stage:
   ```sh
   .build/voice-runtime/bin/python3 -B packaging/stage_voice.py \
     --runtime .build/voice-runtime --native-shell .build/StudioHub \
     --image-runtime .build/st03-image-runtime-worker \
     --music-runtime .build/music-runtime \
     --music-engine .build/music-engine-source \
     --output ".build/StudioHub-<n>/Studio Hub.app"
   ```
4. Install: `bash packaging/install_local.sh ".build/StudioHub-<n>/Studio Hub.app"`.
   It verifies what landed. **Never** `cp` a bundle into `/Applications` by hand, and never
   chain a swap with `|| true` — that is how a stale app went unnoticed for three releases.
5. DMG: `bash .build/make-dmg.sh "<stage>" "dist/StudioHub-<version>-<build>.dmg"`, and move the
   previous DMG into `_trash/dist/`.
6. Drives: point `.build/prepare-kit.sh` at the new stage, then run
   `bash .build/prepare-kit.sh <VOLUME>` for each, and verify each with
   `sh "/Volumes/<VOLUME>/Video Generation Kit/Studio Hub KH/Technical/verify-kit.sh"`.
7. Kit readmes: edit `packaging/kit-readmes/` and copy `README.md` and `Studio Hub KH.md` into
   each drive's kit.
8. Push `implementation/voice-runtime`, then `git branch -f main HEAD` and push `main`.
9. **Update this file.**

## Paths

| What | Where |
|---|---|
| Build runtime (Python, holds `cryptography`) | `.build/voice-runtime` |
| Native shell binary | `.build/StudioHub` |
| Image runtime | `.build/st03-image-runtime-worker` |
| Music overlay (177 files) | `.build/music-runtime`, digest pinned in `packaging/stage_voice.py` as `MUSIC_OVERLAY_SHA256` |
| Music engine source | `.build/music-engine-source` |
| The Mac's model library | `~/StudioHubModels` |
| Hub data (settings, jobs DB, keys) | `~/Library/Application Support/StudioHub Standalone/` |

The overlay digest is **content-only** on purpose: permissions were in it once, and a copy that
changed 0444→0644 stopped the Music worker and therefore the whole Hub from starting.

## Rules that cost us time — do not relearn them

- **Move, never `rm`.** Every removal goes to `_trash/` (see `AGENTS.md`). `_trash/` itself is
  the owner's to empty.
- **Verify the artefact, not the handover.** Mount the DMG, read its version and build, check the
  hash — before it goes in the kit.
- **App Transport Security: declare `NSAllowsArbitraryLoads` ALONE.** A granular key
  (`NSAllowsLocalNetworking`) makes macOS ignore it, and the tailnet's `100.64/10` is not treated
  as local, so a join is refused with code -1022.
- **A runtime identity must not include file permissions**, for the reason above.
- **The runtime tree is read-only**: `chmod -R u+w .build/voice-runtime` before adding anything.
  The same applies to a previous `.build/StudioHub-<n>` stage: `chmod -R u+w` it before `rm -rf`,
  or the delete half-fails and the next stage lands on top of a broken tree.
- **Staging ships `git ls-files`, not the working tree.** `stage_voice.py`'s `copy_tracked_tree`
  copies only tracked paths, so a brand-new manifest or catalog file that was never `git add`ed
  silently does **not** reach the bundle — the staged app then looks fine while missing it. Run
  `git add` on new shipped files before staging; check the bundle afterwards (0.6.55 caught this
  when only 35 of 37 manifests were bundled).
- **The Hugging Face `/tree` API pages at 50 entries.** A manifest capture that reads one page
  looks complete while omitting the weights — the MMS Khmer repo captured 2 of 9 files. The capture
  tool now follows the `Link` header; `expand=true` refuses a page larger than 100.
- **The drives are frequently ejected.** `prepare-kit.sh` expects
  `KH-STUDIO-{NOVA,UGREEN,ATLAS,ORICO}`; if a drive is renamed, pass the new name rather than
  assuming. Four kit drives, plus sometimes an unrelated drive appears (`ORICO 2TB` is not a kit).
- **A test that passes against a fixture is not a test of the wire.** The native suites stub the
  network; the two-Hub harness drives Python HTTP, which is not subject to ATS. That is exactly
  why the join bug reached the owner.
- Confirmed pre-existing and unrelated: `tests/test_subtitle_standalone.py::test_a_small_mac_is_warned_about_not_refused`
  fails when run in a full-file order and passes alone.

## Do not touch

- Other repos and checkouts: `~/Developer/gateway-kh` (read-only), `story-studio-kh`,
  `media-studio-kh`, `label-studio-kh`, `chrome-extensions`.
- `~/Downloads/model-library`, `~/StudioHubAppBackups/`.
- Anything inside any `_trash/`.
- GatewayKH's checkout content — it is another session's project.

## Surviving on an unattended Mac (0.6.53)

- **Crash**: a launchd agent at `~/Library/LaunchAgents/local.studiohub.standalone.plist`
  supervises the app's own executable with `RunAtLoad` and `KeepAlive={SuccessfulExit:false}` -
  relaunch on crash, respect a deliberate Quit. It sets `STUDIOHUB_LAUNCHED_AT_LOGIN=1` so the
  app can still honour "Start services at login". Never supervise `/usr/bin/open`.
- **Rejoin**: 15/30/60/300 s while the Mac is a node and the rejoin is failing; three 401/403
  answers are reported once and drop to the slow cadence.
- **Service ladder**: 1/3/10/60/120 s then every 300 s, with a phase-aware banner; a manual start
  resets it and quitting cancels it.
- **Migration**: the old SMAppService login item is removed once on first launch, recorded in
  preferences, and the new agent is reconciled from the existing "Start at login" setting.
- **Never `launchctl bootstrap` a `RunAtLoad` job from the running app.** It starts the job at
  once, which for our own agent means a second copy of the app - 0.6.53 shipped exactly that and
  the owner saw it immediately. Enabling writes the plist and stops; launchd reads it at login.
- **Not exercised by any test**: a real crash-and-relaunch, a real power cycle, and the real
  `launchctl`/`SMAppService` calls. Prove those on hardware before trusting them fleet-wide.

## In flight / open

**Opened by 0.6.63 — none of these blocks the release; each is recorded where it was found.**

- **Voice publishes no below-tier warning, and there are two missing links, not one.** Its catalog
  row publishes `min_unified_memory_gb` (a real qualification tier) but no `memory_warning`
  sentence; the Audio `NativeModel(…)` in `combineModels` passes no `memoryWarning`; and the Audio
  page has no render site. Subtitle's fix in this release is the template for all three. It cannot
  fire on this fleet today — the copy step skips any voice model above the machine's tier, so a
  below-tier voice model is never installed to be warned about — which is why it is recorded rather
  than built.
- **FLUX.2 Klein 4B MLX 4-bit's `79.34 s` lane-contract runtime traces to nothing.** Z-Image's pair
  was reconciled in this release (185.61 s → the sourced 166 s); FLUX's number has no probe file
  either and nobody has yet found the run it came from. Either produce the record or drop the row,
  the way two Voice numbers were dropped in 0.6.61.
- **The model-exposure approval surface cannot be used, for any modality.** No chat file publishes
  a `genstudio_candidate` block, so `model_exposure.candidate_summary` returns `None` for a chat
  row; and separately `monitor.candidate_models()`, `model_exposure.approve()` and `.revoke()` have
  **no caller anywhere** in this tree. So there is no route or UI by which the owner approves a
  model for the gateway. The chat model is visible in `GET /api/hub/capabilities` as a direct
  observable (`chat.completion`, audit and exposure `null`). Whether this is needed depends on
  which path GatewayKH actually uses — see the handoff doc.
- **Chat's memory tier is still unmeasured** (16 GB first, then one 8 GB; a 24 GB Mac is not tier
  evidence), harness ready in `qualification/README-chat-tiers.md`. 0.6.63 changed the default so a
  16 GB+ Mac holds the model, which makes the first reply fast and every later one faster — but the
  tier measurement is what turns "warn on 8 GB" into a qualified tier or a documented exclusion.
  **It is on every kit drive now** (`Technical/chat-tier-run.sh`, and a double-clickable
  `Technical/RUN-CHAT-TIER.command` beside it), because the two Macs that have to run it are the
  ones the drives travel to, and it previously lived only in this repo — so the instruction "run
  `chat-tier-run.sh`" pointed at a file the machine did not have. **A `.sh` is not double-clickable
  on macOS**: Finder has no application bound to it, so a double-click opens TextEdit and looks
  broken. The `.command` wrapper is the fix, and it is the convention the drive already used for
  `install_app.command` and `verify-this-app.command`.
- **The 8 GB Z-Image-Turbo run is now more valuable, not less.** The copy step carries the model on
  8 GB Macs from `CARRY_BELOW_TIER` in `qualification/copy_models_for_this_mac.py` — "the owner
  asked to try it there", explicitly *not* the proof table. One command,
  `qualification/README-8gb-z-image.md`; the result either moves the entry to `PROVEN_SMALLER` or
  documents the exclusion.
- **The kit prune has nothing to do on the owner's Mac today** (0 candidates; all 15 folders are
  still shipped) and will drop **18** stale receipts whose packages no longer ship. Verified by a
  read-only dry run, not assumed.

- **0.6.55, 0.6.56, 0.6.57 and 0.6.58 are all pushed** — `main` and `implementation/voice-runtime`
  were brought up to date at `34014d5` after sitting at 0.6.54, and every commit since is on both.
  The Khmer adoption is written up in `research/khmer-tts-adoption-2026-09-24.md` and tracked by
  `docs/tasks/ST-12.md`.
- **ST-13 TTS menu pruning — source, tests and catalog are green; the drives need one final
  re-stage.** The owner cut the Voice menu after a full listening review
  (`research/tts-archive-walkthrough-2026-09-24.md`, `research/tts-offer-review-2026-09-24.md`).
  `catalog.py` carries the reduced menu; the 18 cut manifests are preserved in
  `_trash/model-manifests-pruned-2026-09-24/`; all four kit libraries and the Mac library are
  pruned; the capture tool's cut rows are removed. **The reduced-menu tests are green** — the
  eight voice modules run **216 passed / 2 skipped**, and `test_qwen_base_standalone`,
  `test_section_policy` and `test_model_manifest_catalog` all pass. The earlier "~58 failures"
  claim in this file was stale. **CLOSED by the 0.6.59 refresh** — see the kit stocking note above: the app now advertises
  exactly the reduced catalog. Historically the drives' installed app predated it, so a re-stage was owed: `verify-kit.sh` passes on the pruned libraries, but the app
  bundle on them advertises the old menu. `generation.py` still carries inert per-family wiring
  for the cut families. ST-13.A (pocket-tts adoption) is planned but not started.
- **ST-14 local Chat Studio — built, packaged and proven on the 32 GB tier (2026-09-24).** The
  owner directed that Chat is no longer deferred. A packaged MLX chat worker
  (`components/chat/`, `packaging/chat_worker.py`) with JSON mode and seed support; Hub wiring
  (`POST /api/hub/chat/completions`, the `/studio/chat/...` peer proxy, the `chat-mlx → chat`
  engine map, `chat` in `WORKER_FOR_MODALITY`, `activity.VALID_STUDIOS` and
  `standalone_config._REQUIRED_WORKERS`); and the native Chat page moved from "Coming later" into
  Create. Chosen model: `mlx-community/Qwen3.5-9B-4bit` @ `8b2b98c0…`, 5.98 GB (revised from
  OptiQ, whose index requires an 8.22 GB complete snapshot).
  **Proven 2026-09-24** through the installed 0.6.56 bundle (`928ebd4`) against the real
  snapshot: the packaged worker answers `/api/health` and `/api/catalog`, returns a real
  completion with **no `Thinking Process:` trace**, and **JSON mode now returns
  `json_valid: true`** — the case that failed before the fix; peak MLX memory **5.22 GB**. The
  packaged Hub then started all five workers (voice, subtitle, music, image, **chat**) and one
  authenticated `POST /api/hub/chat/completions` returned a real completion. Evidence:
  [`qualification/st14-chat-2026-09-24.md`](../../qualification/st14-chat-2026-09-24.md). The
  thinking fix (`apply_chat_template(enable_thinking=False)` by default, plus a per-request
  toggle) is committed with a test.
  **Kit stocking is done, and the numbers in this paragraph were stale** — re-measured 2026-09-25:
  all four `KH-STUDIO-*` kits carry the chat-capable app at **0.6.62 / `3e1a0eb`** and the 5.98 GB
  model, each verifying **24 installed / 24 advertised**, 215–234 files, 70.5–72.5 GB, `RESULT:
  PASS`. (This used to say `4e87afd`, 20/20, 181 files, 64.5 GB — the 0.6.56 kit.)
  **ST-14.8 independent review has happened, twice** — the first on 2026-09-24 (verdict *not
  passable*, 10 findings, commit `40453b9`) which was never recorded on the card, and a second on
  2026-09-25 that closed ST-14.1/14.3/14.4 and corrected the card. See
  [`ST-14-review.md`](ST-14-review.md). **What that review leaves open is now the honest list:**
  chat's 16 GB tier and then an 8 GB run are unmeasured (harness ready: `qualification/README-chat-tiers.md`, carried on every kit drive as `Technical/RUN-CHAT-TIER.command`), no
  human has typed in the packaged Chat page, streaming-end-to-end through the Hub is unexercised,
  and **nothing in the product refuses on memory tier** — the worker publishes the manifest's 16 GB
  as a *warning* and stays eligible, the Hub's `effective_min_total_memory_gb` is deliberately 0,
  and the app ignores the warning, so an 8 GB Mac tries rather than refusing. That is a decision
  to make, not a measurement to take, and it matches the image precedent (Z-Image-Turbo 6B MLX
  4-bit warns on 8 GB rather than refusing).
  gateway-kh remains read-only here and does not serve chat on its `studio_hub` kind
  (`research/chat-studio-gateway-bridge.md`). ~~chat is absent from `auth._FLEET_STUDIO`, so a
  remote fleet caller gets 403~~ — **resolved in 0.6.63:** the owner chose to make chat
  dispatchable (*"all models and generators should be shown and runnable"*), so `chat` is in
  `_FLEET_STUDIO` for its read contracts and `POST /studio/chat/v1/chat/completions` is the one
  work route; a fleet completion is filed as a job on the node that ran it. The handoff for the
  GatewayKH session is [`docs/gatewaykh-handoff-0.6.63.md`](../gatewaykh-handoff-0.6.63.md).
  **What GatewayKH still has to do is on its side:** its `KIND_STUDIO_HUB` modality set and its
  `studio-hub-local` provider do not list `chat`, and `StudioHubBackend.generate` refuses it with
  `MODEL_MODALITY_MISMATCH` before the Hub is reached.
  Streaming through the Hub is buffered by `studio_proxy`, so the app uses non-streaming.
  **Fixed since that paragraph was written:** the model's missing receipt and the Verify dead end
  (a real completion now answers through the installed Hub — see the verify entry above); the
  `thinking:"false"` inversion, the stream-disconnect lock wedge, the cancelled-request lease and
  the silent parameter drops are with workers now.
- **The VibeVoice offer is dead in 0.6.55** (loader remap bug, see the board's cautions). It is
  one of the families ST-13 cuts, so it disappears with the re-stage; the bug is still worth
  fixing upstream.
- **Khmer quality was accepted by ear by the owner (2026-09-24):** both voices judged good and
  all four generated clips (short + long-form for each) accepted. Clips are in
  `.build/khmer-adoption/audio/`. Whisper cannot score Khmer (it labels even the purpose-built
  MMS model Vietnamese), so the ear judgement — not the tests — is the quality evidence.
- **Remote update**: built on both sides (Hub controller routes + node UI), **never exercised
  across a real fleet**. GatewayKH publishes → controllers mirror → nodes pull. Trust is the
  publisher key pinned on the node; `POST /api/hub/release/trust` is Hub-token only and now has a
  UI under Connections → App updates.
- **Honest gaps**: no SwiftUI render tests; the live `hdiutil` mount/swap is unit-tested only;
  no resume test at 1.2 GB; no independent review of any of this work — it is all self-reviewed.
- **GatewayKH's contracts** are answered in 0.6.48 (capacity/worker/model-health) and 0.6.49
  (the activity reporter).

## Secrets

None in this repo. Controller addresses and Fleet tokens live on the drives (`Token and Stuff.md`
in each kit) and with the owner. If a task needs them, ask for them.
