# Studio Hub task board

## ▶ TAKE OVER HERE

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

## ▶ FIRST TASKS FOR THE NEXT SESSION (specific, verified starting points)

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

Updated: 2026-09-16 (memory contract, connections, statistics fix, ST-11 kit). [Design](../standalone-plan.md) · [Execution plan](../implementation-plan.md).
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
