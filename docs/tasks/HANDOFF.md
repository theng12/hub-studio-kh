> **0.6.216 R3 FAILED — test-only corrections verified; fresh full gate required (2026-11-03).** Source `f0ef14f`: image-smoke0, native0, unittest3396run/24skips/2failures/0errors in997.730s, exit1. No package/artifact/install. Failures: the automatic-listen UI test expected the old single ValueRow string replaced by Copy controls; the chat listing test isolated its ledger but read unrelated in-memory broker jobs from earlier fixtures. Updated only the markup assertion and isolated broker.batches for that test, restoring the original dictionary afterward. Deterministic reproduction with an unrelated sentinel job fails before and passes after; both affected classes pass together (7tests/0skips/0failures). Native capacity/copy source unchanged. R3 evidence retained under `_orchestra/hub-lead/release-0.6.216-20261103/gate-r3/`; next source-pinned gate must use fresh gate-r4 evidence, serialize with Updater artifacts/Story gate, then package and perform the already authorized quiet local install. No fleet/SSD writes.
>
> **0.6.216 REVIEW CORRECTION — fresh R3 gate required, no artifact/install (2026-11-03).** Capacity review verified the core conversion and found two settings-only regressions: decimal storage-limit previews/save notices and Trash Folder totals were formatted as binary GB beside decimal inputs/caps. Restored the existing decimal storage/file-size formatter; physical free disk in storage details uses the binary capacity formatter directly. Added a 50 GB input/preview regression; fleet RAM/disk, setup capacity and existing visual proof unchanged. Gate R2 image smoke passed, then the owned gate was cancelled during native compilation before this source change; one orphan compiler was verified by command/path and stopped. No production Hub or workers touched. R1/R2 evidence retained; neither is a passing gate. Fresh source-pinned R3 canonical gate/package and the already authorized local install remain next. Updater gate-c completed, ending the preceding test-window wait.
>
> **0.6.216 R1 GATE FAILED — no artifact/install (2026-11-03).** Candidate `29d90e4`: image smoke passed; native boundary passed; combined native API test compilation found the newly added local name `absent` already used later in the same test function. Renamed only the new test variable to `unreportedMachine`; product display code and verified captures unchanged. Full fresh R2 gate/package required. R1 evidence retained at `_orchestra/hub-lead/release-0.6.216-20261103/gate-r1/`; never count it as a passing release.
>
> **0.6.216 CAPACITY DISPLAY CORRECTION — verification/release pending (2026-11-03).** The owner added consistent 1024-based memory/disk labels: total RAM reads 32 GB for a 32 GiB Mac; free RAM, free disk and total disk show one decimal. Conversion is native presentation only; decimal-GB wire values, resource tiers/fractions, admission, governor thresholds and settings remain unchanged. Setup RAM already arrives in binary units and is not converted again. Connections wide/compact rows also show total disk beneath free disk. Temporary fleet fixtures now publish RAM using the producer's decimal wire units. Six extracted Swift formatter checks and release-notes checks (19 run, 3 expected unstaged skips) passed. Native capture compilation/render exited 0 for Connections/setup, controller/standalone, both requested sizes. Four Connections captures and the compact setup capture opened: 32 GB RAM, 15.1 GB free memory, one-decimal disk free/total and offline dashes verified. Compact controller top shows offline rows; its local row is below the scroll viewport, while standalone proves the compact resource layout. Setup uses its own 16 GB fixture and displays binary free disk. Full native API regression and complete release gate remain pending. The earlier detached queue at source `30e62fb` was cancelled before any gate stage started; it is not a gate result. A fresh clean source pin will be queued behind Story's quiet test window. Copy/image evidence below remains valid. No 216 artifact/install/rollout/SSD write yet; local install after a passing gate is authorized.
>
> **0.6.216 PREPARED — full gate, DMG and local installation pending (2026-11-03).** Branch `release/0.6.216`, worktree `_worktrees/studio-hub-kh/release-0.6.216`, cut from released main `15a7cb5`. Image modes `5ef6eec` and Connections Copy `777ea19` are merged with feature history retained. Connections copies each concrete listening/reachable address, the complete node join address with port and controller join values; tokens retain masking and the shared Copy control briefly shows Copied. Independent source review found no defects. Actual native mouse-click proof R13 passed in controller and standalone fixtures at both requested window sizes: exact clipboard address/port, settings unchanged, all four Copied screenshots opened and visually checked. Temporary render seams only observe button geometry/callback; the actual shipped button receives mouse events. Earlier failed driver/OCR attempts remain evidence, never passing checks. No new inference or real test library. VERSION/CHANGELOG target 0.6.216; packaging generates What's New from CHANGELOG. Pinned 215 runtimes are cloned into this isolated worktree; primary `.build` untouched. Full gate/package will run after the active Story gate releases the quiet test window, followed by the explicitly authorized local installation. Fleet rollout and SSD writes remain with Developer; neither has started for 216. Published 215 remains current; this Mac remains paused. Evidence: `_orchestra/hub-lead/connections-copy-20261103/` and `_orchestra/hub-lead/release-0.6.216-20261103/`. Repository is now `https://github.com/theng12/hub-studio-kh`; local folder names are unchanged.
>
> **NEXT RELEASE ONLY — image modes and reference inputs, 2026-11-03.** Branch `feat/image-mode-inputs-20261103`, base `15a7cb5`. Producer `image_modes` publishes Text to image (`txt2img`) and Reference to image (`edit`) with live availability/reason and the existing `lane_contract.references` object. Intrinsically text-only models publish only Text to image; supported but unavailable reference lanes retain their reason. Native Image reads these rows, offers the published 1–2 reference pickers, prepares PNGs at the published pixel bound and sends references through the durable image request. Retained references cannot silently become text generation; unsupported/unknown modes, conflicting mode keys and malformed reference lists are refused plainly. Native history preserves inline, URL and stored-asset references. Qwen Image 2.1 Low-RAM mflux 4-bit and Turbo mflux MLX 4-bit now publish/enforce multiples of 32 and offer 1024×576 / 576×1024 / 1024×1024; the separately measured mlx-serve 4-bit row is unchanged. Independent review complete; fixes preserve all retained references visibly when model bounds shrink, compare legacy uncertain jobs with the original generation settings, and preserve a missing-runtime producer reason. Final focused Python run: 101 tests, 3 unstaged-bundle skips, zero failures/errors (9.902 s), exit 0. Final native API contracts (including changed-settings uncertainty and URL/asset history regressions), actual native segment action, reference preparation and published pixel-bound assertions passed, exit 0. Refreshed 1920×1053 capture visually checked. Capture: `~/Desktop/All YouTube Stuff/Labels and Sounds/Hub image modes 2026-11-03/image-1920x1053.png`. No new generation, no version bump, no release gate/package/install/publication/rollout for this feature. Exact pending schema: `_orchestra/hub-lead/image-mode-inputs-20261103/CATALOG-CONTRACT.md`; evidence in that same topic folder.
>
> **0.6.215 RELEASE COMPLETE — supersedes the historical R1/R2/assembly status below (2026-11-03).** Main source `15a7cb59b408e1edb8b35588568b5a3b0c5a420d`. All eight R3 gate stages passed; the receipt-only Python wrapper failure was recovered and accepted by Developer. Rollout exit 0: 60/60 eligible Macs healthy on 215 across seven sites. All four SSDs verified with 215 current and 214 previous, other app rows preserved; writer window released and drives remain mounted. Hub Studio KH naming shipped. This Mac remains paused. Image-mode fields described above are not in live 215.

> **0.6.215 R2 gate failed — no artifact (2026-11-03).** Candidate23028b6: image smoke0; unittest3382 run/16skips/1failure/0errors,964.201s, exit1. Chat and previous self-route/overview failures are resolved in this run. Sole failure: the other startup route round-trip test returned queued at the same0.2s fixture deadline. Both route checks now share explicit real sync/delivery/ack plus actual outcome reader; separate threaded RequestTests still exercise asynchronous delivery, queued and expired states. No product waits changed and no checks skipped.55 focused chat/identity/startup/controller tests pass together. Fresh full gate/package remains required;214 stays published under latest Developer direction. R1/R2 evidence retained separately.

> **0.6.215 R1 gate failed — no artifact (2026-11-03).** Image smoke exit0; unittest3382 run/16skips/3failures/0errors, exit1. Failures: chat priority routed queue fixture received CHAT_QUEUE_EXPIRED; remote-startup self route returned queued within its0.2s fixture deadline; standalone overview still expected the retired generic This Mac label. Overview now compares configured controller name and stable ID. Startup passed unchanged in isolation; its route-focused case now explicitly delivers/acknowledges the queued request before using the real outcome reader, avoiding a scheduling race without changing product waits. Combined identity/startup/controller-registry checks51 passed. Chat cause proved: cancelled request tombstone kept registry length3, so a len==2 fixture wait always consumed its full1000iterations before releasing the Mac; under load the unchanged2s queue ceiling expired. Test now waits on the two actual IDs. Instrumented wait1.385s→0.006s, full chat module2tests and adjacent fleet relay2tests pass; five stress runs pass. Product queue/expiry/cancel source and timeouts unchanged. Fresh full gate/package required after these test-only fixes; never quote R1 as passed. Evidence `_orchestra/hub-lead/release-0.6.215-20261103/gate-r1/`.

> **0.6.215 — release assembly, 2026-11-03. NOT YET GATED, BUILT OR INSTALLED.** Worktree `_worktrees/studio-hub-kh/release-0.6.215`, branch `release/0.6.215`, cut from origin/main `56cc07a`. Canonical kit paths `67c790f` merged with history retained; token-only change `1689596` cherry-picked without the unqualified YuE2 preset commit. Native display name and sidebar What's New link are implemented and native build/boundary/API/secret/view/lifecycle checks pass. CFBundleDisplayName is Hub Studio KH; executable/bundle identity, CFBundleName and `Studio Hub.app` remain unchanged. VERSION now0.6.215 matches CHANGELOG; focused release-notes suite19tests passes with3expected unstaged-bundle skips. Isolated native What's New fixture render exited0; opened1920×1053 and980×620 captures, name/footer visible and notes readable. Unbundled fixture version reads Development/unknown; packaged version and actual footer click still require staged/installed proof. No real local inference or live library. Controller identity context is implemented and reviewed: raw `machine`/`machine_id`, persisted controller/site IDs and the real controller row stay unchanged. Controller row label uses its configured name. Terminal evidence adds executor `machine_label` plus reporting `controller_id`, `controller_label`, `site_id`, `site_name`; remote labels come from the registry or raw ID fallback, never the controller's name. No global uniqueness claim or migration. Worker focused suites report92+10+80 passing checks; the new identity file contains6 tests, including remote executor separation. Combined full gate remains pending. Native naming/footer committed as877a523. Gateway owns its own admin schema/display. `local-site` is a Hub default, not Gateway location identity. The proof generator's earlier provider comparison was wrong; preserve its original receipt and fix the comparison, not routing. Image edit acceptance is functional only, not exact background/lighting preservation. Integrated kit/document/Yam/token checks:120 tests, zero failures/errors, exit0. Evidence and detached runner: `_orchestra/hub-lead/release-0.6.215-20261103/`; pinned runtimes and agent kit cloned from the gated214 worktree, no primary `.build` writes. Developer authorized normal corrected-release gate/package/release; SSD writes remain serialized with Updater. Published214 DMG is immutable. This Mac remains paused; no autonomous local inference. Three existing fleet image jobs are lead-verified: raw Hub done/succeeded, source/output hashes, M4/17.18GB assigned hardware and both reference jobs' explicit worker counts1. Txt2img submitted no references; worker count absent, not explicit0. Exact source/output comparisons accept functional green apple edit and reference reuse on sunset beach, without exact background/lighting preservation. Original receipt hashes unchanged; corrected provider comparison receipt saved separately. No persistent proof generator containing that comparison exists under the proof folder. Qualification is limited to demonstrated FLUX.2 Klein4B MLX4bit intents, no new modes or duplicate jobs. Existing214 fleet rollout completed exit0 at2026-11-02T19:44:13UTC: five remaining sites accepted their held214 build once each;1100/1300/1400 are11/11,1500 is5/5,1600 is9/10 with explicit warning1610 still offline at window end. Together with1200 11/11 and1700 2/2 canaries,60/61 eligible Macs verified current. No duplicate push; no studios down among reporting members. Local pause value unchanged throughout. Source receipt `_orchestra/hub-lead/release-0.6.214-20261102/rollout-0.6.214-remaining-final.json`.

> **NEXT RELEASE ONLY — Connections token display, 2026-11-02.** Owner approved keeping both credentials while showing only Fleet token under “Give this out”. Hub token and its existing masked/copy control are inside a native Advanced disclosure, closed by default, with “This Mac's master key. Never give it out.” Token storage/authentication/joins are unchanged. `qualification/NODE-SETUP-CHECKLIST.md` now directs Gateway/node setup to the Fleet token and describes the separately issued node credential. Focused credential-wiring checks: 4 passed; actual native views compile. Closed-state screenshot is visually verified and delivered with the Fleet token visible. Open-state proof remains incomplete: the fixture's click automation has not expanded the disclosure; this is not a proved product defect. Failed capture attempts and the one-off driver are retained under `_orchestra/hub-lead/token-advanced-20261102/`. No release bump, full next-release gate, install, DMG, publication or rollout yet.

> **0.6.214 — release assembly, 2026-11-02. PREPARED, NOT YET GATED OR INSTALLED.** Branch `release/0.6.214`, worktree `_worktrees/studio-hub-kh/release-0.6.214`, cut from main `4be63f0`. Includes controller recovery 466c64f, stable tabs 40f980e, one-source What’s New 4475832 + 9441307, Gemma 4 26B-A4B QAT MLX 4-bit 49,152/8,192 (653b457), kit readmes 76f14fa + d62ac9e. Real image prompt limits remain held until Story 1.4.376 is installed. Developer authorized assembly, detached gate, DMG and local installed-build proof; paced rollout still requires Developer’s go. The installed 0.6.213 runtimes are cloned into this worktree to preserve the shipped inputs; no runtime upgrades. Release evidence goes in `.build/release-0.6.214/`. No SSD write or fleet rollout is authorized by this assembly task.

> **R4 failed and produced no artifact (2026-11-02).** Image smoke exit0. Unittest:3,376 run/16skips/1failure/0errors; `FleetSharedVoiceTests.test_a_pushed_voice_arrives_on_the_node_with_its_timings` never saw the fake node reachable within45s. The suite printed its result after1555s, then stayed alive until the outer2700s timeout (receipt124). All6shared-voice tests subsequently passed on unchanged source, exit0; the cause is unproven. Failure diagnostics now include the last target statuses and fake-node requests without changing the45s wait or assertions. The6tests passed again with diagnostics. The gate runner now prints surviving non-daemon thread names/IDs and stack traces after the result; after120s without interpreter exit, a daemon watchdog emits shutdown stacks and fails exit1 even if the tests were green. It never turns a stuck process into a pass. Real child-process self-checks (watchdog shortened only in the check) verified normal exit0, successful tests with a leaked thread exit1, and failed tests with a leaked thread exit1. Six shared-voice checks also pass with the thread diagnostics. Shutdown investigation and a fresh complete gate remain required; this is not release proof.

> **R3 gate failed one timing threshold, not a product failure proved (2026-11-02).** Image smoke and native exit 0. Unittest: 3,376 run, 16 skips, 1 failure, no errors; stream elapsed 0.256 s exceeded a 0.250 s wall-clock threshold. Generation tests now advance isolated engine/route clocks through fake load and generation calls and assert exact durations; product timing code unchanged. Chat suite:101 tests, zero skips/failures/errors. In-memory negative control moves stream timing before model load; both stream and API assertions correctly reject 0.58 instead of0.08 seconds. Fresh full gate/build evidence is under `_orchestra/hub-lead/release-0.6.214-20261102/r4/`, per folder tidiness.

> **SSD layout added while 0.6.214 is open (Developer, 2026-11-02).** All install SSD consumers now target `1 Install Apps/<App>/`, including `Studio Hub/` and `Gateway/`. R2 gate was deliberately superseded during native build (receipt 143), not passed; its owned test/compiler processes were stopped before source edits. Only source and temporary-drive fixtures changed; no install SSD was written. Focused SSD/index/updater/notes/library-safety checks: 111 run, 3 expected staged-bundle skips, zero failures/errors; changed shell/Python syntax checks pass. Generic library discovery already covers the new depth and is unchanged. A fresh combined gate (`.build/release-0.6.214-r3/`), DMG and installed proof remain required. The owner’s Hub Studio KH display rebrand is explicitly queued after 0.6.214; installed app lookup identifiers stay unchanged.
> **First combined gate failed, not release evidence.** Native passed; unittest ran 3,375 with 8 failures and 15 skips. Five source-text checks still pinned the old segmented controls/restart spelling; corrected those tests without changing product code. Image smoke requires mflux: ordinary gate now skips explicitly and the same two tests pass under the pinned Image runtime. Two elapsed-time assertions failed under the full run, then passed unchanged in the focused run; cause not established. Focused rerun: 65 tests, 1 explicit runtime skip, zero failures/errors; Image runtime: 2 tests, zero skips/failures. Full gate rerun and artifact proof remain required. Original receipts retained in `.build/release-0.6.214/`; next run uses `.build/release-0.6.214-r2/`.

> **0.6.213 - studios retry a failed launch start; rollout requires services_running - 2026-11-01.** Released ahead of the real image prompt limits because the owner called it urgent. `StudioController.start` always enters the recovery ladder; `LaunchStart.decide` in `StartupRecovery.swift`. Cause in code is proved; the real trigger at 1300/1400 is not, since the fleet records no failure reason. Still open: real prompt limits wait for Story 1.4.374 (Hub refuses over its published figure; Story must fit first), moving jobs off a dead site (Gateway). The fleet-watch launchd job stays on until a site has recovered by itself.
>
> **0.6.212 gate — all four stages green on `33e3e40`, 2026-10-31.** Runner `scripts/hub_offline_gate.py`, evidence `_orchestra/hub-0212-release-20261030/gate-final/`, stage exits in `stage-exits.txt` there. Unittest **run=3346 skipped=24 failures=0 errors=0**; regressions **448 passed**; voice **1021 passed / 28 skipped**; native checks passed; all exit 0. Also in this release since the entry below: drain counts a member's running item, `--full` re-sends the whole image, short sections Whisper hears in full are not refused as silent, Overview tabs and origin tags, voice tags shown read-only and filterable. **A first gate run failed 39 tests and is not evidence:** `copy_models_for_this_mac.main()` sets `STUDIOHUB_MODEL_PRESETS` for its own process and a test let it leak into later ones (fixed in the test), and two native checks read old wording. That run's process also never exited after printing its result; the runner script now stops a stage 120 s after its result line and records it, and this run did not need that. **Open:** DMG, landing, one real Mac, offer, 1500's `--full`.
>
> **0.6.212 — the Hub reads no removable or network drive at all; an SSD install is the drive's own preset command; a late worker result after cleanup is discarded; a full rollout push gets the 2-hour window and a busy-site option is off by default — 2026-10-30. PREPARED ON `fix/hub-never-reads-drives-20261030` (worktree `_worktrees/studio-hub-kh/release-0.6.212`), cut from `main` `830bd72` (0.6.211).** VERSION `0.6.212`. **Gate and DMG have NOT been run yet — the coordinator asked for all code first.** Nothing is pushed, merged, published, signed, installed or rolled out; no Gateway was written and no SSD was written.
> - **The owner's question.** *"all the errors and issues stem from needing to check the removable drives. why? we didn't need to in the past. can we make or change the way we access or install the models so this no longer applies?"* 0.6.211 still opened a kit-named drive at start-up so the install screen could name the mounted kit; on an ad-hoc signed build that read is what macOS asks about after every update, and the owner confirmed every frozen or Hub-not-running Mac today came from that Allow prompt.
> - **What ships.** The automatic check is **gone, not narrowed**: `components/hub/backend/removable_volumes.py`, its start-up child, its cache, the `removable_volumes` field on `/api/health`, `/api/hub/resources` and fleet rows, `model_install_plan`'s `kits`/`kits_state` and `SOURCE_KIT`, the native `NativeRemovableVolumes`/`kitsState` models and their notes, and the Setup-page drive sentence. Removed sources and their 20 tests are in `_trash/removed-volume-probe-0.6.212/`. A Hub reading a model library the person chose under `/Volumes` is unchanged, with the existing warning; nothing reads it unless chosen.
> - **The SSD install path chosen: the drive's own command, no new button.** **`2 - Copy the models.command`** now lists the four presets and copies the chosen one plus its companions, only what is missing. This is the path a person starts in Terminal, so macOS may ask its permission there once; the Hub never asks. The alternative — a "copy from a folder…" native open panel — was not built: it would add a button and a second copy path, and the script already honours the same presets. **`2 - Copy the models (everything).command`** keeps the no-internet/no-peer path. Kit readmes, `START-HERE.md`, `NODE-SETUP-CHECKLIST.md` and the launcher comments say the same. `packaging/stage_voice.py` now stages `model-presets/` into the bundle, which the command needs.
> - **What macOS may still ask in that path.** The Terminal (the app's own Python inside it) may ask once for access to the removable volume, because a person ran the command; the Hub process is not involved and asks nothing. **What can only be observed on a real fleet Mac:** the absence of the prompt at Hub start and after an update. This Mac's terminal already holds the permission and no privacy setting may be changed, so no live prompt was shown or clicked.
> - **A late result after automatic cleanup is discarded, not silently lost.** `discard_uncertain_item` keeps `studio`, `studio_job_id`, `worker_id`, revisions and `worker_accepted_params`, records `recovery.phase = "discarded"` and the reason, and still releases the machine (state is `cancelled`, so `busy_machines()` drops it). `_record_worker_success` refuses to adopt a result for an item whose `error_code` is `DISCARDED_UNCERTAIN`, including a result that arrives during the metadata await after cleanup ran; a newer accepted result stays protected by the existing `state == done and asset_id` guard. Tests in `tests/hub_regression/cases/test_discard_uncertain.py`: late result after cleanup, newer result present, no late result.
> - **Rollout: a full push gets 2 h, and `--allow-busy` is an explicit off-by-default option.** `982de7b` was cherry-picked as `09306c9`: `PUBLISH_TIMEOUT_S` is the rollout window, `--publish-timeout` bounds it, a timed-out Gateway POST is recorded rather than raised, and the controller is read once after a non-outcome reply (`RECOVERED_HELD`). **The busy-site gate: publishing only stages the release; it starts no install.** Each Mac — controller included — installs from its own update tick only after its own work finishes (`releaseTick` → `activeWorkCount == 0`, then drain), so the offer cannot interrupt a running job. The option still refuses an unreachable controller or an unreadable Gateway gate, and records that the site was busy. Default behaviour is unchanged.
> - **"Hub not running" analysis.** The code's only process-terminating path is the shell's two-minute startup readiness timeout (`StudioHub.swift` `startupTimedOut` → `process.terminate()`); after readiness, a failed health check only sets "Local services not responding" and never stops the process. The 0.6.210/0.6.211 probe ran in a daemon thread after the app was ready, so it cannot be shown from code to have caused a closed port; the observed port-closed Macs are consistent with threadpool exhaustion making the port look dead, but **that is not proven**. In 0.6.212 no volume is opened by the Hub at all, so that class of block is gone.
> - **Proof (focused, not the gate).** `tests/test_no_drive_reads.py` (3) scans every `components/hub/*.py` for `/Volumes`, `/sbin/mount`, `getfsstat` and `diskutil`, and starts the real app with an audit hook over `/api/health`, `/api/hub/resources` and `/api/hub/model-presets`, asserting no volume path is opened and that the hook does record a real open. **It cannot catch** a path assembled at runtime from a chosen library (allowed), a read by the native shell or a worker, or a real macOS prompt. `tests/test_copy_models_for_this_mac.py` (35) pins preset choice, cancel, invalid choice, companions and "moves nothing already here". `tests/hub_regression/cases/test_discard_uncertain.py` (11), `test_discard_route_fleet.py` (2) and `test_image_uncertainty.py` (16) pass. `scripts/test_hub_rollout.py` (39) passes. `tests/test_hub_model_presets_route.py` (21) and `tests/test_release_notes.py` (17, 3 bundle checks skipped without `STUDIOHUB_TEST_APP`) pass.
> - **Still open.** Full four-stage gate, DMG build, read-only mount verification, installer acceptance, and any fleet rollout. The packaged bundle's own Hub was not started. The rollout tool was not run against the live fleet.
> **0.6.211 — a Mac with no SSD kit attached reads no volume, so macOS has nothing to ask about; the Lite Mode copy step leaves the kit and the kit readmes stop selling packs — 2026-10-30. PREPARED ON `fix/no-volume-prompt-20261030` (worktree `_worktrees/studio-hub-kh/release-0.6.211`), cut from `main` `56b3da1` (0.6.210).** VERSION `0.6.211`. **Four commits:** `061b87d` (the volume fix), `496c5d6` (two numbers in its own notes), `d2f03fb` (the Lite Mode kit retirement and the kit-readme wording, owner 2026-10-30) and the handoff commit that follows it. **The gated commit is `d2f03fb`** — the gate ran on it, four stages green; the handoff commit after it changes only this file, which is not staged into the bundle, so every shipped byte of the artifact equals the gated source. **Nothing is pushed, merged to `main`, published, signed, notarised, installed or rolled out; no Gateway was written and no SSD was written. The fleet Macs and 1301 were not contacted.**
> - **The owner's problem.** 0.6.210 stopped the *freeze* by moving the `/Volumes` walk into a bounded child process. It did not stop the **prompts**: that child still opened every mounted volume at every Hub start, and the walk exists only so the install screen can say which SSD kit is mounted. On a Mac with no kit attached the only thing it could find was somebody else's drive — a USB disk ("access files on a removable volume") or a mounted share ("access files on a network volume"). An **ad-hoc signed** build is asked again after every update, so the owner clicked Allow on Mac after Mac for an answer the app never needed.
> - **What ships.** `components/hub/backend/removable_volumes.py`: the child reads `/sbin/mount` (the kernel's mount table — it reads no volume) and opens **only** a mount whose name begins `KH-STUDIO-`, and **never** a network filesystem (`smbfs`, `nfs`, `afpfs`, `cifs`, `webdav`, `autofs`, `devfs`) even when it carries that name. `/Volumes` itself is no longer listed, and no entry of it is stat'ed. `KIT_VOLUME_PREFIXES` is the one place the naming lives (the parameter `packaging/prepare-kit.sh` already takes, `KH-STUDIO-XXXX`). Names beginning with a dot are skipped, as the old `glob("*")` skipped them: macOS's per-volume metadata folders return **EPERM** and the first draft recorded four healthy kit drives as unreadable because of it (caught by the live proof, not by a test — the test for it was written afterwards). The child process, the 2 s hard timeout, once-per-process, and the cached `status()`/`kits()` guarantees are unchanged.
> - **Which calls ask macOS, and how that is known.** Reading **inside** a removable or network mount — opening a directory for listing, opening a file, or a path lookup through it — is what the Files-and-Folders permission gates; the owner's Macs showed the prompt at Hub start for exactly the reads the old child made, and clicking **Allow** cleared it. The audit hook in the live proof records precisely those calls (`os.scandir`, `os.stat`, `open`) against every mount. `getfsstat`/`/sbin/mount` read the kernel's mount table and touch no volume; the same hook records no path access of any kind when the table is the only input. **What cannot be observed here:** this Mac's terminal already holds the permission, and changing a privacy setting is forbidden, so a real prompt (and its absence) can only be seen on a fleet Mac.
> - **Honest states.** `probe()` now publishes `looked_inside` (the drives it read), `unreadable` (path + error) and `not_read` (a kit-named mount on a network filesystem) beside `state`/`reason`/`kits`. `needs_permission` is kept for "I could not tell": a kit-named drive macOS refused, a timeout, a dead child, an unreadable answer. `ready` with no kits says **which** answer it is — no drive named like a kit is mounted (nothing was opened), a kit drive holds no `model-library`, or the only mount with that name is a share — and `looked_inside` shows "I did not look in every volume" in the data, not only in the words. A refused drive beside a healthy one stays `ready` with the healthy kit and names the path that could not be read. `native/StudioViews.swift` shows `kits_state.reason` when no kit was found, so "No SSD kit is mounted" is printed only when that is true.
> - **The one behavioural limit, deliberate.** A kit drive that has been **renamed** is not found automatically: the name is the only fact about a drive that can be learned without reading it, and reading it is the prompt. Nothing else depends on the name — the model library is still chosen by hand on the Models page, and the kit's own commands (`prepare-kit.sh`, `copy-models.command`, `prune-build-scratch.sh`) keep the convention. `packaging/kit-restructure.sh` still walks every volume by content, but it is operator tooling run in a terminal, not the Hub.
> - **The Lite Mode copy step is gone from the kit (owner, 2026-10-30).** `copy-lite-models.command` and the drive's **"2 - Copy the Lite Mode models.command"** are deleted, not merely retired: since 2026-10-28 the launcher copied nothing (it printed a note and ran the ordinary copy step) and nothing in `packaging/` wrote it any more. **Lite Mode itself is unchanged** — ticked in Settings, `components/workstation_models.py` still the runtime allowlist, the Hub still refusing any other model at such a Mac's door. Removed with it, because nothing else called them: the `STUDIOHUB_WORKSTATION` filter in `qualification/copy_models_for_this_mac.py` (no file has set that variable since 2026-10-28, so the branch was unreachable) and `tests/test_shop_kit.py` (its two subjects were exactly that launcher and that filter). `packaging/prepare-kit.sh` moves a drive's leftover copy to that volume's `_trash` on its next build. `packaging/release_kit.py` never shipped the file — its one Lite Mode sentence describes the surviving feature and stays. Both removed sources are in `_trash/retired-lite-mode-kit-20261030/`. Kit README, `NODE-SETUP-CHECKLIST.md`, `prepare-kit-extras.sh`, `components/hub/workstation.py`, `components/workstation_models.py` and `AGENTS.md` no longer describe a Lite-only install set.
> - **Kit readmes: packs are downloaded, never bought (owner, 2026-10-30).** `packaging/kit-readmes/README.md`, `Story Studio.md` and `GatewayKH.md` now say the in-app **Studio Library** (served by GatewayKH) is where Content Packs are downloaded and installed per Mac, with no balance and nothing sold. The error code `CONTENT_STORE_NOT_CONNECTED` is left as it is.
> - **Proof.** `tests/test_removable_volumes.py` **20 cases** (was 11; the gate's own count confirms it: 3362 = 3342 + 20). The owner's case is pinned with an **audit hook**: non-matching drives are given real `model-library` folders (so a content walk *would* find one), a network share named `KH-STUDIO-*` is added, and the hook records every path the child opens, stats or scans — none of them the plain drive, the share, or `/Volumes` itself; the same test asserts the hook did record the drive that *was* read, so an empty log cannot pass. Also pinned: the three-level kit layout and both older layouts inside a kit-named drive, a kit drive with no library (its own sentence), a kit drive macOS refuses (`needs_permission`, `Click Allow`), one refused drive not hiding a found kit, hidden macOS metadata folders skipped, and `/sbin/mount` naming the root filesystem. **Live, on this Mac, with all four `KH-STUDIO-*` drives plus `/Volumes/Recovery` and a mounted disk image (`/Volumes/Shottr`) mounted:** the 0.6.210 child scanned **all six**; the 0.6.211 child scanned the **four kit drives only** and found the same four libraries; the child took **45–103 ms** against its 2 s budget. Evidence `_orchestra/hub-0211-release-20261030/live-volume-proof.json` and `.out` (script beside them).
> - **Gate — all four stages green on `d2f03fb`.** Runner `scripts/hub_offline_gate.py --root . --evidence .build/gate-evidence-211c/<stage>` (the unittest stage repeated into `-211d/`, see the note below). Offline unittest **GATE RESULT: run=3352 skipped=15 failures=0 errors=0** (exit 0, 1033.1 s in the repeated run; 0.6.210 was 3353 — one fewer module because `tests/test_shop_kit.py` was retired with the Lite-only copy path). Hub regressions **443 passed** (exit 0, 52.27 s). Voice **1016 passed / 28 skipped** (exit 0, 57.51 s, run with `PYTHONPATH=.build/test-deps` and the 0.6.207 voice runtime that ships). Native: `bash native/build.sh` plus the boundary, API contract, secret-masking, view and lifecycle checks **all passed** (exit 0). Logs and stage exits: `_orchestra/hub-0211-release-20261030/gate-211c-*.log`, `stage-exits-211c.txt`, and the repeated unittest log `gate-211d-unittest.log`.
> - **One unittest run failed exactly one test, and the stage was repeated rather than quoted.** In `.build/gate-evidence-211c/` the run ended `failures=1` on `test_video_retired.InstalledRetiredModelsTests.test_the_retired_receipt_leaves_installed_json_with_the_folder`: the retired model's receipt was not dropped from `installed.json` (`kept` held all four ids). That test touches nothing this release changed (`components/retired_models.py`, `model_packages.forget_packages`), it **passes alone** in 2.1 s, and the whole stage re-run on the same commit was green (3352/0). **The cause is not identified** — it is recorded here rather than explained away, and it is the one thing in this release a later reader should not assume was understood. `test_model_copy_script`'s kit-launcher check also skips on this Mac (`the SSD is not mounted on this machine`), as it did before.
> - **A first native run of `bash native/build.sh` failed the lifecycle check `authenticated healthy uptime did not reset the restart budget`** while the first unittest stage was running; re-run alone it passed in 22.7 s, and the gate's own native stage then passed twice (211b and 211c). It is load-sensitive, not a defect in this change.
> - **Artifact.** `dist/StudioHub-0.6.211-1b088a9.dmg` in this worktree, **1,507,063,808 bytes**, sha256 **`11e34b38da8968c66b0937d5d696917b175a0524d427e255a7ef07e51807ab67`**. Staged and built **after** the final commit, so its `implementation_commit` is that commit, **`1b088a91e022a9a3a57ac71d48bb148b07a2423c`**, and `development_build` is **false**. Every shipped byte of `1b088a9` is the gated source `d2f03fb`: the only tracked difference between them is this handoff file, which `stage_voice.py` does not stage (it copies `VERSION`, `CHANGELOG.md` and `release-notes.md`, never `docs/`). Built with `.build/hub-gate/bin/python packaging/make_dmg.py ".build/staged/Studio Hub.app" "dist/StudioHub-0.6.211-1b088a9.dmg"`; `hdiutil verify` reported the checksum **VALID**. Mounted read-only at `/tmp/shub-211-dmg` and detached afterwards (see the next bullet), mounted app tree digest **`fdc3e541…`** equals the staged app and its recorded `Studio Hub.app.tree-sha256`. `release-manifest.json` reports `version` **0.6.211**, `implementation_commit` **`1b088a9`**, `development_build: false`, `qualification: unqualified-local-proof`; `Info.plist` is 0.6.211 / "Studio Hub". `HUB_RELEASE_ARTIFACTS` passes (`missing_release_artifacts()` returns `[]`): three runtimes and all nine worker entry points present, `runtimes/voice` **`709a0278…`**, `runtimes/image` **`b720c5bc…`** and `runtimes/music-yue2` **`75465dc4…`** digest-exact. The kit rides in `components.hub.khu_agent` (agent **0.1.1**) and there is **no `private.key` anywhere in the bundle**. `tests/test_release_notes.py` against the mounted bundle with `STUDIOHUB_TEST_APP`: **17 ran, 0 skipped, 0 failures**. The bundle's `release-notes.md` carries the 0.6.211 section, including the Lite Mode one.
> - **A drive was ejected by a detach that named a mount point, and it needs the owner (2026-10-30).** `hdiutil attach -mountpoint /tmp/shub-211-dmg` reported the image as `/dev/disk5`; the following `hdiutil detach /tmp/shub-211-dmg` answered `"disk4" ejected` and **`/Volumes/KH-STUDIO-ATLAS` disappeared** — the physical SSD, not the image. Disk numbers had shifted while the image was attached, and the path-to-device mapping chose the wrong disk. **Nothing was written to the drive** (only the image was read), but it is detached at the device level: `diskutil list` no longer shows that 1 TB disk and there is no device node to mount, so **it must be re-connected by hand**. The other three drives (`NOVA`, `ORICO`, `UGREEN`) are untouched and still mounted. **Do not detach a mounted image by mount point on this Mac**: eject by the device identifier `attach` printed, and re-read `diskutil list` first.
> - **Build inputs (links into this worktree's `.build`).** `hub-gate`, `test-deps` and `prepare-kit.sh` from the primary checkout; `voice-runtime` from `release-0.6.207/.build/voice-runtime` (`709a0278…`); `image-runtime` from `release-0.6.202/.build/image-runtime` (`b720c5bc…`, the `IMAGE_RUNTIME_SHA256` pin); `yue2-runtime` from the primary checkout (`75465dc4…`); `khu-agent-kit-0.1.1` from `release-0.6.209` (passed as its **real** path: staging refuses a symlinked kit). Render tools: `/build/Developer/story-studio-kh/bundled/ffmpeg` (both pins match).
> - **Still open (coordinator).** Push, landing on `main`, publication to the Gateway, fleet rollout, signing and notarisation, the SSD kit write (**no SSD was written**), and the installer-acceptance check — none of which this worker ran. **What is not proven by a live run:** no macOS permission prompt was shown or clicked (no `tccutil`, no privacy change); no Mac without the permission was available; the packaged DMG's own Hub was not started (the live proof used the source checkout); no screen of the shipping app was clicked to see the new Setup-page sentence. **Needs the owner:** `KH-STUDIO-ATLAS` was ejected by the detach described above and has to be re-connected by hand. **Open:** the one unittest failure described above was never explained; it passed alone and the whole stage was green when repeated, so it is a known unknown, not a closed one.
> - **Disk.** Gate evidence is `.build/gate-evidence-211/` (a unittest run on the earlier code commit), `.build/gate-evidence-211b/` (the first full four), `.build/gate-evidence-211c/` (the four on `d2f03fb`, whose unittest run failed the one test above) and `.build/gate-evidence-211d/` (that stage repeated, green). Removed sources are in `_trash/retired-lite-mode-kit-20261030/`; a mis-placed first staging is in `_trash/staging-mistake-211/` (staged to `.build/staged` instead of `.build/staged/Studio Hub.app`, moved, not deleted); `_trash/dmg-scratch/` receives `make_dmg.py`'s own scratch, and any superseded artifact goes to `_trash/`.

> **0.6.210 — no request walks `/Volumes`, so an unanswered macOS permission prompt can no longer freeze the Hub; a Mac now says "needs permission (Removable Volumes)"; and every Mac has "Check for update now" — 2026-10-30. PREPARED ON `fix/no-protected-access-at-idle-20261030` (worktree `_worktrees/studio-hub-kh/hub-0210`), cut from `main` `af4630f` (0.6.209).** VERSION `0.6.210`. The gated commit is **`26d086a75875272fc3973579503f2ce87a3c1768`**. **Nothing is pushed, merged to `main`, published, signed, notarised, installed or rolled out; no Gateway was written and no SSD was written. The fleet Macs and 1301 were not contacted.**
> - **The cause, confirmed by the owner at the Macs.** 0.6.206 added the first walk of `/Volumes`; it ran inside `GET /api/hub/model-presets`, and the app read that route from `refresh()` on every connect and poll. An **ad-hoc signed** build loses its macOS Removable Volumes permission on every update, so the blocked `open()` waited on an unanswered prompt and held a threadpool slot until `/api/health` and `/api/hub/resources` stopped answering. Clicking **Allow** revived the Mac — that is how the owner found it. 0.6.209 still had it. **Owner decision: no Developer ID and no signing change**, so the fix is in how the walk runs.
> - **What ships.** `components/hub/backend/removable_volumes.py` runs the listing in a **child process with a hard 2 s timeout**, once per process, from the Hub lifespan, and caches the answer; `status()`/`kits()` never probe or block, and a timeout, refusal, dead child or unreadable answer all become **`needs_permission`** with the reason (never an empty kit list). `model_install_plan.snapshot()` takes `kits` and `kits_state` as arguments and has no `/Volumes` access. `resources.host_stats()` publishes `removable_volumes` beside `connection`; `/api/health` and every fleet row carry it. The app shows **needs permission (Removable Volumes)** in `startupNote`, on **Set up this Mac** and on a fleet row, distinct from busy and unreachable; `ShellCore.protectedFolderWarning` covers a `/Volumes` library. `model_removal` records a removal (in-process flag plus a marker in the Hub's own data dir) and the cleanup timer calls `handoff()` only when that is true — an idle Hub does **zero** library reads. The app reads `/api/hub/model-presets` on demand (`loadInstallPlan` on the Setup page, and after an install/remove), never from `refresh()`; the Gateway's per-node preset route runs `model_presets_screen()` in `run_in_threadpool`. Every Mac gets **Check for update now** (sidebar beside the version, and Connections → App updates), which clears the ten-minute release interval and then uses the **existing** verified install path; with work running it says *"Will update when the current work finishes"* and never restarts mid-job. The probe also finds the library where the 2026-10-28 drives actually keep it (`<drive>/Video Generation Kit/Studio Hub KH/model-library`), which the old two-level walk missed; older layouts still work. `START-HERE.md` now describes install → **Set up this Mac** → the no-internet copy step and the retired Lite Mode step; the kit-readme fallbacks are current; `packaging/prepare-kit.sh` is **tracked** and derives `ROOT` from the checkout it is run in (the shared `.build/prepare-kit.sh` carries the same line).
> - **The gate's required reproduction, both halves.**
>   1. **Test, with a removable volume mounted and an access that blocks:** `tests/test_removable_volumes.py` `test_a_timed_out_probe_does_not_stop_the_routes_and_host_stats_says_so` starts the probe with a child that sleeps 30 s (the stand-in for an unanswered TCC prompt) and, while it is blocked, `/api/health` answers in under 1 s; after the timeout `/api/hub/resources` carries `needs_permission` and `/api/hub/model-presets` answers with `kits_state.state = needs_permission`. `tccutil` and every privacy setting were left alone, as the owner required.
>   2. **Live, on this Mac, with all four `KH-STUDIO-*` drives mounted:** a Hub started from this checkout with a throwaway data dir and an empty temporary model library ran its one probe in a child (audit trace: `python -I -c …`) and reported **ready** with all four `.../Video Generation Kit/Studio Hub KH/model-library` paths. The three routes answered **200** in 0.004 s / 0.017 s / 0.033 s, an audit hook recorded **zero** `/Volumes` accesses in the Hub process, and the install screen published the four kits with `kits_state.state = ready`. Evidence: `_orchestra/hub-0210-release-20261030/live-volume-proof.json` (script beside it).
> - **Gate — all four stages green on `26d086a`, each stage alone, one at a time.** Runner `scripts/hub_offline_gate.py --root . --evidence .build/gate-evidence-210b/<stage>`. Offline unittest **GATE RESULT: run=3353 skipped=15 failures=0 errors=0** (exit 0; 0.6.209 was 3342 — the 11 new cases are `tests/test_removable_volumes.py`). Hub regressions **443 passed** (exit 0, 53.25 s; unchanged). Voice **1016 passed / 28 skipped** (exit 0, 60.67 s, run with `PYTHONPATH=.build/test-deps` **and the 0.6.207 voice runtime that ships**). Native: `bash native/build.sh` plus the boundary, API contract, secret-masking, view and lifecycle checks **all passed** (exit 0), including the new `/Volumes` warning and the four **Check for update now** cases. Logs and stage exits: `_orchestra/hub-0210-release-20261030/gate-210b-*.log`. A first unittest run (`.build/gate-evidence-210/`) was green but was made on the working tree while two Swift files were still being edited, so it was **not quoted**; the stage was re-run on the committed sha into `-210b/`.
> - **Two staging faults were caught before the artifact was accepted, and fixed by re-staging.** (1) The first stage pointed `.build/voice-runtime` at the **primary checkout** — the pre-`mlx-vlm` tree, digest `e253aa42…`/staged `bae1132a…` — instead of 0.6.207's built tree; the runtime digest check caught it, and the stage and its DMG were **moved to `_trash/superseded-0.6.210-wrong-voice-runtime/`**, the link repointed to `release-0.6.207/.build/voice-runtime` (`709a0278…`), and the voice stage re-run on that interpreter. (2) The first clean-tree stage reported `development_build: true` because an untracked `.test-work/` from the live proof made `git status` dirty; the fixtures were moved to `_trash/test-fixtures/hub-0210-live-volume-20261030/`, that stage went to `_trash/superseded-0.6.210-first-stage/`, and the app was staged again from a clean tree.
> - **Artifact.** `dist/StudioHub-0.6.210-26d086a.dmg` in this worktree, **1,475,044,352 bytes**, sha256 **`521031efa7313c562caa0f43857e88a3ebc0a5ddcf771ab92373ca0e0abb36d5`** (0.6.209: 1,475,033,600). Built with `packaging/make_dmg.py ".build/staged/Studio Hub.app" "dist/StudioHub-0.6.210-26d086a.dmg"`; `hdiutil verify` reported the checksum **VALID**. Mounted read-only at `/tmp/shub-210-final` and **detached afterwards** — no drive was unmounted and nothing was installed. Mounted app tree digest **`c1ac76f9…`** equals the staged app and its `Studio Hub.app.tree-sha256`. `release-manifest.json` reports `version` **0.6.210**, `implementation_commit` **26d086a75875272fc3973579503f2ce87a3c1768**, `development_build: false`, `qualification: unqualified-local-proof`; `Info.plist` is 0.6.210 / "Studio Hub" / build `26d086a`. `HUB_RELEASE_ARTIFACTS` passes (`missing_release_artifacts()` returns `[]`): three runtimes and all nine worker entry points. `runtimes/voice` **`709a0278…`** (the tree 0.6.207 built and 0.6.209 shipped), `runtimes/image` **`b720c5bc…`** (the `IMAGE_RUNTIME_SHA256` pin) and `runtimes/music-yue2` **`75465dc4…`** are digest-exact. `khu-agent/publisher.json` is `testAuthority: false` and there is **no `private.key` anywhere in the bundle**. `tests/test_release_notes.py` against the mounted bundle with `STUDIOHUB_TEST_APP`: **17 ran, 0 skipped, 0 failures**.
> - **Build inputs (links into this worktree's `.build`).** `hub-gate`, `test-deps` and `prepare-kit.sh` from the primary checkout; `voice-runtime` from `release-0.6.207/.build/voice-runtime` (`709a0278…`); `image-runtime` from `release-0.6.202/.build/image-runtime` (`b720c5bc…`, checked with `stage_voice.tree_digest`); `yue2-runtime` from the primary checkout (`75465dc4…`); `khu-agent-kit-0.1.1` from `release-0.6.209` (passed as its **real** path: staging refuses a symlinked kit). `.build/StudioHub` was regenerated by the native stage's own `bash native/build.sh`.
> - **Still open (coordinator).** Push and land onto `main`, publication to the Gateway, fleet rollout, signing and notarisation, the SSD kit write (**no SSD was written**), and the installer-acceptance check (older installers ACCEPT the mounted app) — none of which this worker ran. **What is not proven by a live run:** no macOS permission prompt was actually shown or clicked (the owner forbade `tccutil` and privacy changes; the blocked child is the stand-in), no screen of the shipping app was clicked to press **Check for update now** (the store path and fixture Hub prove all four outcomes), and no real node was asked for an update. The live proof used the source checkout's Hub with a temporary library, not the packaged DMG.
> - **Disk.** The superseded stage and DMG are in this worktree's `_trash/superseded-0.6.210-wrong-voice-runtime/` and `_trash/superseded-0.6.210-first-stage/`; `make_dmg.py` preserved its own scratch under `_trash/dmg-scratch/`; the live-proof fixtures are in `_trash/test-fixtures/hub-0210-live-volume-20261030/`. Gate evidence is `.build/gate-evidence-210b/` (and the superseded `-210/`). `_trash/` is on the same volume, so nothing is freed until the owner empties it.

> **0.6.209 — a per-request delivery direction for a voice, in each engine's own documented form; the Hub publishes it with the model card's own guidance, and no app screen sends it yet — 2026-10-29. PREPARED ON `release/0.6.209` (worktree `_worktrees/studio-hub-kh/release-0.6.209`), cut from `feat/delivery-instruction-20261029` (`39df613`), which is Hub main `d958e56` (0.6.208) plus one feature commit.** VERSION `0.6.209`. The gated commit is **`4947dc4c4463f12f90571c664759da050f81209e`**; this worker's own three commits on top of the feature are **`00778da`** (VERSION, CHANGELOG, release-notes), **`5154bb1`** (the gate-isolation fix below) and **`4947dc4`** (the notes correction below — the source the artifact was built from). **Nothing is pushed, published, signed, notarised, installed or rolled out; no Gateway was written and no SSD was touched.**
> - **What ships.** A Voice request may carry `delivery_instruction` (<= 500 characters, request-only, never a stored default), and each engine applies it in its own documented form: **Breeze TTS 2** passes it as `instruct` **beside** the reference clip, so a cloned voice is directed without changing who it is, and applies the card's own **`cfg_scale` 4** through `breeze_cfg_scale` only while a direction is present (a value the request supplies wins); **VoxCPM2** puts a `(style)` prefix in front of every rendered section, stripping parentheses from the instruction because they are the prefix's own delimiter. A family that states no form **refuses** the field — 400 on both speech routes, and the same rule again in `GenerationManager.start` — rather than accepting and dropping it. The model row publishes the **`delivery-instruction`** capability (derived from the family's engine, not copied into each checkpoint's row) plus a `delivery_instruction` guidance object under `controls` with the form, the bound, up to three card examples, the card's language rule, the documented inline vocal events and where they go, the directed `cfg` value and its request field, and the `source_url`; `components/hub/backend/capabilities.py` re-bounds every value before the Gateway sees it and drops a row that names no form, no `https` source or no positive bound. The worker records what the engine ran with (`text`, `sent_text`, `engine_form`, argument or prefix, `cfg_parameter`/`cfg_scale`) and the Hub carries it onto the batch item and its terminal result; `_shared_params_with_local_reference` lifts a top-level `delivery_instruction` into `sharedParams` instead of dropping it. The CHANGELOG has the long form.
> - **No app screen sends it yet — stated in the release notes as well.** `native/*.swift` is untouched, and the Voice page keeps its older `instruct` style box, which a cloned voice does not read; the one visible change is the "delivery direction" capability label on a voice model (`capabilityLabel`, rendered per model at `components/voice/app/frontend/index.html:1436`). The first draft of `release-notes.md` read as though the page had a box for the direction; it said so plainly after `4947dc4` and the bundle carries the corrected text. **Do not promote this release as "you can type a direction on the Voice page".**
> - **Gate — all four stages green on the gated commit `4947dc4`, each stage alone, run while a Story Studio full gate was running on this Mac (allowed for Hub modules).** Runner `scripts/hub_offline_gate.py --root . --evidence .build/gate-evidence-209c/<stage>`. Offline unittest **GATE RESULT: run=3342 skipped=15 failures=0 errors=0** (exit 0, 1069.7 s; 0.6.208 was 3306 — the 36 new cases are `tests/test_delivery_instruction.py`). Hub regressions **443 passed** (exit 0, 55.1 s; unchanged). Voice **1016 passed / 28 skipped** (exit 0, 51.9 s; unchanged; run with `PYTHONPATH=.build/test-deps`, as 0.6.207/0.6.208 were). Native: `bash native/build.sh` plus the boundary, API contract, secret-masking, view and lifecycle checks **all passed** (exit 0), including the malformed-catalog reproduction and recovery probe (`catalog=malformed-200 reachable=false` -> `catalog=200-after-malformed reachable=true`). Logs and the stage exit list are in `_orchestra/hub-0209-release-20261029/` (`gate-209c-<stage>.log`, `stage-exits.txt`). **No installer test ran in parallel with anything; the installer-acceptance check was not run at all** (see Still open).
> - **The first gate attempt failed, and it was a real defect in the new test file — fixed here, not waived.** On `00778da` (evidence `.build/gate-evidence-209/`, logs `gate-209-*.log`) the unittest stage exited **1**: `run=3342 skipped=15 failures=1 errors=0`, the other three stages green. The failure was `test_hub_jobs.EveryJobLaneIsListedOrDeclaredTests.test_a_chat_page_completion_is_summarisable_through_the_listing`, which asserts `/api/hub/jobs` holds exactly its own two completions and instead saw `'8981f98c83'` and `'relay-1'`. **Both ids come from `tests/test_delivery_instruction.py` `RelayTests`**, which put a batch into the module-level `broker.batches` and never removed it — `submit_batch(...)` in `test_admission_keeps_a_shared_instruction` and a literal `broker.batches["relay-1"]` in `test_the_dispatch_post_carries_it_to_the_worker`. The gate runs every test in one process, so a leak is a row every later module sees. `5154bb1` clears both through `addCleanup` (so it happens even when an assertion fails); a focused run of the two modules **in that order** in one process went from failing to **46 tests, 0 failures** (evidence `.build/gate-evidence-209/focused-pollution{,-2}`). Nothing in the product changed — but the leak was in this release's own code, so it was fixed and the whole four-stage gate was re-run from a fresh evidence directory rather than quoting the green stages of the failed run.
> - **The notes were corrected after the first artifact, so the artifact was rebuilt.** `00778da` + `5154bb1` produced `dist/StudioHub-0.6.209-5154bb1.dmg` (1,479,247,872 bytes, sha256 `a54e77ec…`), which shipped a `release-notes.md` that read as though the Voice page had a direction box. `4947dc4` rewrote that section (and the CHANGELOG's) to say the opposite in plain words; the gate was re-run on `4947dc4` and the app was **re-staged and re-built** from it, so the bundle carries the corrected notes (checked inside the mounted image: "Not on the Voice page yet" is present). **The superseded first build is in this worktree's `_trash/superseded-0.6.209-first-build/`** (staged app, its tree digest, and the `5154bb1` DMG) — never deleted.
> - **Artifact.** `dist/StudioHub-0.6.209-4947dc4.dmg` in this worktree, **1,475,033,600 bytes**, sha256 **`cf2365117f15942fb965cf26f69b9a2b2e3a32c744f0d8ccf3ba1f5368cd40e7`** (0.6.208: 1,488,218,112). Built with `packaging/make_dmg.py ".build/staged/Studio Hub.app" "dist/StudioHub-0.6.209-4947dc4.dmg"`; `hdiutil verify` reported the checksum **VALID**. Mounted read-only at a private `/tmp/shub-209-final` and **detached afterwards** — no `/Volumes/KH-STUDIO-*` was touched (all four were mounted throughout and still are) and nothing was installed: `/Applications/Studio Hub.app` is still **0.6.207**. Mounted app tree digest **`5cab0b6e…`** equals the staged app and its recorded `Studio Hub.app.tree-sha256`. `release-manifest.json` reports `version` **0.6.209**, `implementation_commit` **`4947dc4c4463f12f90571c664759da050f81209e`**, `development_build: false`, `qualification: unqualified-local-proof`; `Info.plist` is 0.6.209 / "Studio Hub". `HUB_RELEASE_ARTIFACTS` passes (`missing_release_artifacts()` returns `[]`): three runtimes and all nine worker entry points present. `runtimes/voice` **`709a0278…`**, `runtimes/image` **`b720c5bc…`** (the `IMAGE_RUNTIME_SHA256` pin) and `runtimes/music-yue2` **`75465dc4…`** (the `YUE2_RUNTIME_SHA256` pin) are digest-exact, and `runtime_packaging.py verify` exits **0** on the mounted voice and image trees; the yue2 tree ships no `manifest/runtime-files.json`, so that checker does not apply to it and its digest is the check. The kit rides in `components.hub.khu_agent` — agent **0.1.1**, kit manifest `5534689772b0…` — `khu-agent/publisher.json` is the owner's descriptor with `testAuthority: false`, and there is **no `private.key` anywhere in the bundle**. `tests/test_release_notes.py` against the mounted bundle with `STUDIOHUB_TEST_APP`: **17 ran, 0 skipped, 0 failures**.
> - **Build inputs (links into this worktree's `.build`).** `hub-gate`, `test-deps`, `prepare-kit.sh` and `yue2-runtime` from the primary checkout; `voice-runtime` from `release-0.6.207/.build/voice-runtime` (the tree 0.6.207 shipped and 0.6.208 carried, `709a0278…`, checked with `stage_voice.tree_digest` before staging this time); `image-runtime` from `release-0.6.202/.build/image-runtime` (`b720c5bc…`, equal to the pin). `.build/khu-agent-kit-0.1.1` was **rebuilt from source** with `kh-updater/packaging/build-agent-kit.sh 0.1.1` at kh-updater `6b82b2d`, and its five files hash **identical to the kit 0.6.207/0.6.208 shipped** (`bin/khu` `2061bcaa…`, `bin/khu-stub` `e5fb6388…`, `install-agent.sh` `be5a876d…`, plist `2664ac4a…`, `agent-config.example.json` `6de8d92c…`) and the staged `MANIFEST.json` is `55346897…` — the same artefact, not a new one. Exact stage: `.build/voice-runtime/bin/python3 -B packaging/stage_voice.py --runtime .build/voice-runtime --native-shell .build/StudioHub --image-runtime .build/image-runtime --yue2-runtime .build/yue2-runtime --hub --render-authority descriptor-only --render-tools-dir /build/Developer/story-studio-kh/bundled/ffmpeg --khu-agent-kit .build/khu-agent-kit-0.1.1 --khu-publisher-json ~/Library/KHUpdater/publisher.json --output ".build/staged/Studio Hub.app"`. `.build/StudioHub` is regenerated by the native stage's own `bash native/build.sh` before anything is staged.
> - **Fleet rollout — what the coordinator needs, recorded here because HANDOFF had not named it.** The tool is **`scripts/hub_rollout.py`** (+ `scripts/test_hub_rollout.py`, 31 tests, no network) and it is still **not run against the live fleet**. The rollout is driven from **1301**, the Gateway Mac, in a working directory **`~/hub-rollout-0206/`** on that Mac; that directory does **not** exist on this Mac and this worker neither read nor created one. Publish the artifact by its **bytes** above: `hdiutil verify` VALID, sha256 `cf236511…`, 1,475,033,600 bytes — `hub_rollout.py` accepts a site only from its own recomputation of size + SHA-256. The binding rule is unchanged: **nothing is published into a site with work running** (its own `running_jobs` empty and no non-terminal Gateway job routed to it), one accepted POST per site, a rerun never republishes, and one busy or unreachable site never halts another. Two members of the published set are new in this build — `delivery_instruction` on the request body and the `delivery-instruction` capability plus a `controls.delivery_instruction` object on a model row. The request field is optional, so a caller that never sends it is unaffected; **what an older Gateway or an older Hub does with the new capability token and the new `controls` key was not tested this run** (no mixed-version reader was exercised), so treat that as unproven rather than as a compatibility claim.
> - **Still open (coordinator).** Push and land onto `main` (this branch is cut from the feature branch, which is itself one commit off `main`), publication to the Gateway, fleet rollout (above), signing and notarisation, the SSD kit write (**no SSD was written**) and the **installer-acceptance check** (older installers ACCEPT the mounted app) — none of which this worker ran, and no installer test was run in parallel with anything. **What is not proven by a live run:** no line was ever rendered with a direction — the Breeze TTS 2 and VoxCPM2 weights were not loaded here, so the contract, the engine forms, the refusal and the record are proven by `tests/test_delivery_instruction.py` and the catalogue's own registry validation, not by audio; and no app screen sends the field (see above), so no person has used it from the Voice page.
> - **Disk.** The superseded first stage and DMG are in this worktree's `_trash/superseded-0.6.209-first-build/`, and `make_dmg.py` preserved its own scratch under `_trash/dmg-scratch/` (same volume, frees nothing until the owner empties it). Gate evidence is kept under `.build/gate-evidence-209/` (the failed first attempt), `-209b/` (the rerun after the test fix) and `-209c/` (the gated run), plus the focused runs under `-209/focused-pollution{,-2}`.
> **0.6.208 — the Studio "did not answer" banner is gone; a Studio that stops answering is a small sidebar line, only when this Mac serves it, and any Hub note now clears when the read answers again — 2026-10-29. PREPARED ON `feat/studio-status-note-20261028` (worktree `_worktrees/studio-hub-kh/studio-status-note-20261028`), cut from `release/0.6.207`.** VERSION `0.6.208`. Release commit **`996de722daff4b8ff6024e1007510f0595638970`**. **Nothing is pushed, published, signed, notarised, installed or rolled out; no Gateway was written and no SSD was touched.**
> - **What was wrong.** `native/StudioAPI.swift` appended "A studio did not answer; the rest of this page is up to date." to `startupNotes` whenever either availability read (`GET /studio/transcription/api/transcribe/availability`, `GET /studio/voice/api/generate/availability`) returned nil through `try?`. Two faults stacked: `startupNote` was only ever *replaced* when a later refresh had notes of its own, so one missed poll latched the banner until the operator pressed Dismiss; and the app never asked the Hub whether a Studio was enabled or even listed. Evidence on this Mac (0.6.207, 2026-10-28): `hub-worker.log` carries exactly **one** 502 on the transcription availability read for the whole evening (line 1318, just after the 10:01 PM restart) and no path that clears the note afterwards; `hub/machine_flags.json` has `voice.enabled: false` and `subtitle.enabled: false`, and the live `/api/hub/work-policy` agrees (`machine_enabled: true`, both Studios off). Both endpoints answered `200` when probed. Owner, 2026-10-28: *"what is it and should we get rid of it. or make it disappear or sth? or can we have it like in a notification icon somewhere instead?"*
> - **What ships.** The sentence is deleted. `NativeWorkspaceStore.noteStudioAvailability` counts consecutive misses per read and publishes `studioTrouble: [StudioTrouble]`; it reports only at >= 3 consecutive misses (`studioMissLimit`, commented: at the visible two-second poll one miss is a worker restarting), only for a modality the Hub lists with `enabled: true` while `machine_enabled` is true — read from `GET /api/hub/work-policy` on a missed poll with a 5 s timeout and never fatal, so a switched-off Studio never raises anything, a paused Mac says nothing, and toggling a Studio off takes the line away on the next poll. Wording: "still starting" while nothing has answered since connect and the connection is under 120 s, "not answering" after that; the first answered poll clears it. `StudioTroubleNote` (`native/StudioViews.swift`) draws it in the sidebar footer beside "Local services ready" — warning triangle and a short line — with the detail on hover (`.help`) and on click (popover).
> - **The banner's latch is fixed, not just narrowed.** `refresh()` now assigns `startupNote` unconditionally (`refreshNotes.isEmpty ? nil : joined`), so a note a failed read raised goes when the next refresh has nothing to report — the same latch, for the package list, an unplaceable batch and an unreadable finished job. `recoveryNote` (a local file set aside at startup) is composed into every refresh's notes, so it still waits for its dismissal; `tests/native/api_main.swift` pins both halves.
> - **Proof (native, `bash native/build.sh`, exit 0, boundary/API/secret/view/lifecycle all pass).** `tests/native/api_main.swift` pins: one missed poll raises nothing and never reaches the banner; a Studio the owner switched off raises nothing over four polls; a paused Mac raises nothing; a persistent failure names the Studio ("Voice Studio") in `studioTrouble` with a detail that says it clears itself, and leaves `startupNote` without the old sentence; recovery empties it. The latch is pinned as a pair on a fixture Hub with no other notes (a `setUnknownModalityBatch(false)` switch): the package-list sentence appears while the read fails, and `startupNote == nil` after the next clean refresh — a mutation restoring the old `if !refreshNotes.isEmpty { … }` fails it with the sentence still there. `tests/native/view_checks.swift` renders `StudioTroubleNote` offscreen: no trouble draws nothing, a Studio down adds a line, and two notes differing only in the Studio's name must not render identically (blanking the drawn name fails that check), plus the wording checks. Each new assertion was proved able to fail by mutating the line it guards, then restored.
> - **Gate — all four stages green on `996de72`, each stage alone, never alongside an installer test.** Runner `scripts/hub_offline_gate.py --root . --evidence .build/gate-evidence-208/<stage>`. Offline unittest **GATE RESULT: run=3306 skipped=15 failures=0 errors=0** (exit 0, 1019 s). Hub regressions **443 passed** (exit 0, 53.4 s). Voice **1016 passed / 28 skipped** (exit 0, 56.5 s; run with `PYTHONPATH=.build/test-deps`, as 0.6.207 was). Native: `bash native/build.sh` plus boundary, API contract, secret-masking, view and lifecycle **all passed** (exit 0). **The first unittest attempt exited 1 on one error that was a missing build input, not the product:** `.build/prepare-kit.sh` was not linked into this fresh worktree, so `test_model_copy_script` raised `FileNotFoundError` and nine kit tests skipped (24 skipped instead of 15). The link was made (the same one 0.6.207 used) and the stage re-ran on the same commit into `.build/gate-evidence-208b/unittest`: 3306 / 15 / 0 / 0. Evidence and logs are in `_orchestra/hub-0208-release-20261029/` (`gate-208-<stage>.log`, `gate-208-unittest-rerun.log`, `stage-exits.txt`).
> - **Artifact.** `dist/StudioHub-0.6.208-996de72.dmg` in this worktree, **1,488,218,112 bytes**, sha256 `1755434683afce3a328945860523708cc9cdc37e1e74d26c450a4bde5944dae3`; `hdiutil verify` reported the checksum **VALID**. Built with `packaging/make_dmg.py ".build/staged/Studio Hub.app" "dist/StudioHub-0.6.208-996de72.dmg"`. Mounted read-only at a private `/tmp/shub-208-dmg` and **detached afterwards** — no `/Volumes/KH-STUDIO-*` was touched and nothing was installed. Mounted app tree digest **`1ee38db3…`** equals the staged app and its recorded `Studio Hub.app.tree-sha256`. `release-manifest.json` reports `version` **0.6.208**, `implementation_commit` **996de722daff4b8ff6024e1007510f0595638970**, `development_build: false`; `Info.plist` is 0.6.208 / "Studio Hub". `HUB_RELEASE_ARTIFACTS` passes (`missing_release_artifacts()` returns `[]`): three runtimes and all nine worker entry points present. The six Hub workers carry the 0.6.207 Voice runtime **`709a0278…`** and `runtimes/image` the **`b720c5bc…`** pin, digest-exact. `khu-agent/publisher.json` is `testAuthority: false` and there is **no `private.key` anywhere in the bundle**. `tests/test_release_notes.py` against the mounted bundle with `STUDIOHUB_TEST_APP`: **17 ran, 0 skipped, 0 failures**.
> - **Build inputs (links into this worktree's `.build`).** `hub-gate`, `test-deps`, `prepare-kit.sh` and `yue2-runtime` from the primary checkout; `voice-runtime` from `release-0.6.207/.build/voice-runtime` (the tree 0.6.207 shipped, `709a0278…`); `image-runtime` from `release-0.6.202/.build/image-runtime` — **the first stage attempt was rejected with exit 1 because the link pointed at the primary checkout's older image runtime (mflux 0.20.0) instead of the `b720c5bc…` pin, and that rejected 2.6 GB stage was moved to `_trash`, never deleted.**
> - **Disk.** This run's leftovers are in `/build/Developer/_trash/hub-0208-test-leftovers-20261029/` (6.9 GB: 164 `render-assets-fd-tests-*` containers from the unittest stage, the rejected stage, and `make_dmg.py`'s preserved scratch). The worktree's own `_trash` is empty; the owner empties the shared one.
> - **Still open (coordinator).** Push and land onto `main`, publication to the Gateway, fleet rollout, signing and notarisation, the SSD kit write, and the installer-acceptance check — none of which this worker ran. **What is not proven by a live run:** the footer line and the banner's self-clearing were never seen on a running app; they are proven by the store tests and the offscreen view checks, and by the mounted bundle's own release-notes suite. `dist/StudioHub-0.6.207-d246fd3.dmg` remains the last released artifact on `main`.
> **0.6.207 — a render is ranked like every other kind of work, every kind has its own on/off switch, and the Voice runtime at last carries mlx-vlm — 2026-10-28. PREPARED ON `release/0.6.207` (worktree `_worktrees/studio-hub-kh/release-0.6.207`) cut from `main` (`f5ba0e7`, the 0.6.206 docs commit; `main` is `f5ba0e7`).** VERSION `0.6.207`. Three branches: `feat/render-ranking-20261028` (`f8bf808..436b53e`), `feat/work-order-switches-20261028` (`9e08e72`) and `feat/mlx-vlm-runtime-20261028` (`0cd079e`, merged `--no-ff` as `2a02246`). **Nothing is pushed, published, signed, notarised, installed or rolled out; no Gateway was written and no SSD was touched.**
> - **One conflict, in `components/hub/backend/resources.py`.** The render-ranking branch adds `measured_render_speed()` (a render Mac publishes `render_speed` on its host stats) and 0.6.206 adds `connection` (the network link) to the same `host_stats()`. Both were kept, render speed first: the merged file sets `stats["render_speed"]` and then `stats["connection"]`, and neither can overwrite the other. Nothing else conflicted, and no digest needed hand-editing: `0cd079e` had already moved both `runtime_lock_sha256` values in `research/st01-runtime-contracts.json` to the new lock, which is the only digest pin this release touches.
> - **What ships.** A render is one entry in each Mac's saved `priority_order` instead of jumping ahead of everything; `vocabulary.DEFAULT_PRIORITY_ORDER` is the single named default, read by `registry.with_render_default` and `broker.render_wins_next_free`, so a Mac that never saved an order keeps today's behaviour; `render_while_paused` is a separate opt-in, off by default. `measured_render_speed()` publishes `{"speed", "renders"}` on the host stats from this Hub's own timed `render_history` — absent, never `0`, when there is no measurement. Every kind of work has a per-Mac on/off switch beside the work order (`registry.function_enabled` / `set_function_enabled`, a strict reader where absent or malformed reads as **on** and only a real boolean `false` is off), enforced in `broker.next_work`, `_eligible_studios`, `render_dispatch_allowed`, `_pending_render_for_machine` and `acquire_external_machine`, published on `operator_controls.work_policy` and carried on peer snapshots and fleet rows; the native work-order page shows a toggle on each row. The CHANGELOG has the long form.
> - **The Voice runtime was rebuilt here from the new lock — this is what turns Chat image input on.** `packaging/voice-runtime/build.sh` ran in this worktree (default output `.build/voice-runtime`; the primary checkout was not touched) and its own log proves the three things that matter: **`uv pip sync` resolved and installed all 152 lock entries** (`Resolved 152 packages`, `Prepared 82 packages in 13m 06s`, `Installed 152 packages in 1.06s`); the runtime's `manifest/requirements.lock.txt` is sha256 **`f384e7ba9a8d0085db1837352012cb67618bfc3e6c3c2b01e406f14f9cded714`**, equal to the tracked lock and to both `runtime_lock_sha256` values in `research/st01-runtime-contracts.json`; and **`smoke_imports.py` passes with `mlx_vlm`** — payload `{"fastapi": "0.141.1", "mlx": "0.31.2", "mlx-audio": "0.5.7", "mlx-vlm": "0.6.3", "pyopenjtalk": "0.4.1", "torch": "2.14.0", "torchaudio": "2.11.0", "transformers": "5.14.1"}`, `machine: arm64`, `python 3.12.14`, `user_site_enabled: false`, run from the relocation path and again from the final tree. `runtime_packaging.py audit --target-macos 14.0` wrote `manifest/macho-audit.json` with **`status: passed`, 478 Mach-O binaries, 0 failures**; `manifest` wrote 32,370 file entries; `verify` exits 0 on the built tree, on the staged `runtimes/voice`, and on the mounted image's `runtimes/voice`. The built tree is **1.9 GB** and digests **`709a0278456de17a1b7f8df8aec19d841434b3f30d7a09efb4937ea9456fac18`** — every release from 0.6.202 through 0.6.206 shipped `bae1132a…`, and that tree answered `ModuleNotFoundError: mlx_vlm`. `strip-tests` runs in the build, so this runtime carries **no pytest**; the gate's voice stage was therefore run with `PYTHONPATH=.build/test-deps` (pytest 9.1.1 and its four deps — the same versions the primary checkout's hand-installed pytest provided), and none of them reach the bundle: the staged and mounted voice trees verify clean.
> - **`release-notes.md` had no 0.6.207 entry — the gate would have refused it.** `tests/test_release_notes.test_the_owner_facing_notes_have_an_entry_for_this_version` requires the newest `## x.y.z` heading in both documents to be the shipped version, so the owner-facing notes were written and committed as `d246fd3` (three plain-language sections: the render in the work order, the per-kind switch, Chat reading a picture). Re-run against the mounted bundle with `STUDIOHUB_TEST_APP`: 17 ran, **0 skipped, 0 failures**, including the three bundle checks.
> - **Gate — all four stages green on `d246fd3`, each stage alone, first attempt.** Runner `scripts/hub_offline_gate.py --root . --evidence .build/gate-evidence-207/<stage>`. Offline unittest **GATE RESULT: run=3306 skipped=15 failures=0 errors=0** (exit 0; 0.6.206 was 3259 — the 47 new cases are `tests/test_render_ranking.py`). Hub regressions **443 passed** (exit 0, 47.7 s; unchanged because this change adds unit cases, not regression cases). Voice **1016 passed / 28 skipped** (exit 0, 56.7 s; unchanged). Native: `bash native/build.sh` plus the boundary, API contract, secret-masking, view and lifecycle checks **all passed** (exit 0), including the malformed-catalog recovery probe (`catalog=200-after-malformed reachable=true`). Evidence is kept at `.build/gate-evidence-207/<stage>` in this worktree and as `gate-207-<stage>.log` in `_orchestra/hub-0207-release-20261028/`, with `stage-exits.txt` recording `unittest 0 / regressions 0 / voice 0 / native 0`.
> - **Artifact.** `dist/StudioHub-0.6.207-d246fd3.dmg` in this worktree, **1,474,555,904 bytes**, sha256 `54449778780b4b90853cba45019480f8962e96d1790022113461512f644e178c` (0.6.206: 1,413,548,032; the ~58 MB growth is the new Voice runtime). Built with `packaging/make_dmg.py ".build/staged/Studio Hub.app" "dist/StudioHub-0.6.207-d246fd3.dmg"`; `hdiutil verify` reported the checksum **VALID**. Mounted read-only at a private `/tmp` mountpoint and **detached afterwards** — no `/Volumes/KH-STUDIO-*` was touched (all four drives were mounted throughout) and nothing was installed: `/Applications/Studio Hub.app` is still **0.6.203**, its `Info.plist` unchanged since Oct 27 01:42. Mounted app tree digest `7b357f40…` equals the staged app and its recorded `Studio Hub.app.tree-sha256`. `release-manifest.json` reports `version` **0.6.207**, `implementation_commit` **d246fd3496fcf56c1f39499593dd2dc2fd6e5472** and `development_build: false`; `Info.plist` is 0.6.207 / "Studio Hub". `HUB_RELEASE_ARTIFACTS` passes (`missing_release_artifacts()` returns `[]`): three runtimes and all nine worker entry points present. `runtimes/voice` **`709a0278…`** (the newly built tree), `runtimes/image` **`b720c5bc…`** (the `IMAGE_RUNTIME_SHA256` pin, from the release-0.6.202 worktree) and `runtimes/music-yue2` **`75465dc4…`** (the `YUE2_RUNTIME_SHA256` pin) are digest-exact. The kit rides in `components.hub.khu_agent` — agent **0.1.1**, kit manifest `55346897…`; `khu-agent/publisher.json` is `testAuthority: false` and there is **no `private.key` anywhere in the bundle**.
> - **Still open (coordinator).** Push and land onto `main` (a fast-forward from `f5ba0e7`), publication to the Gateway, fleet rollout, signing and notarisation, the SSD kit write (**no SSD was written**), and the installer-acceptance check (older installers ACCEPT the mounted app) — none of which this worker ran. **What is not proven by a live run:** no render was executed and no image was sent to a chat model, and no paid call was made. Render ranking, the `render_speed` publication and the per-kind switches are proven by their unit and regression suites; Chat image reading is proven by the runtime's own import smoke (`mlx_vlm` 0.6.3), the verified staged tree, and the merged code path's tests — a real multimodal load on this Mac remains unmeasured.
> **0.6.206 — the install-time model picker, real model removal, a one-time preset cleanup, Chat image input, and the network link on every Mac — 2026-10-28. PREPARED ON `release/0.6.206` (worktree `_worktrees/studio-hub-kh/release-0.6.206`) cut from `release/0.6.205` (`09b0224`; 0.6.205 is built, gated and on the four SSDs but not on `main`, which is `eb572fc` = 0.6.204).** VERSION `0.6.206`. Three branches merged `--no-ff`, no squash: `feat/model-picker-20261028` (`3c32bed..05a5341`, merge `d14af91`), `feat/chat-vision-gemma4-20261027` (`ed2a51c`, `730c09b`, merge `cd02878`) and `feat/link-speed-20261028` (`1afa1b4`, `750a8bf`, `710a2ac`, merge `1322f93`). **Nothing is pushed, published, signed, notarised, installed or rolled out; no Gateway was written and no SSD was touched.**
> - **One conflict, in `docs/tasks/HANDOFF.md`.** Both `release/0.6.205` and `feat/model-picker-20261028` prepended an entry; both are kept, 0.6.205 first. No code conflicted, and no inventory count, digest or document needed hand-merging: the counts that matter are recomputed by `tests/test_document_inventory.py` from `model-manifests/*.json`, and the catalog is 40 packages / 177,026,562,705 bytes with `measured_kit_packages() == measured_packages() == 40` (§ the shelving deletion below is why that changed).
> - **The install-time model picker (P0–P6).** RAM is the only gate: `components/shelved_models.py`, `SHELVED_MODELS`, `keep_all_models`, the Hub's start-up move-out, both download refusals by name, the kit's "Shelved, not copied" skip and its `NOT_STOCKED` list are **deleted**. Four presets as data in `model-presets/*.json` — Essentials · 8 GB (9 packages), Creator · 16 GB (14), Creator Plus · 24 GB (16), Studio Pro · 32 GB+ (18) — read by `components/model_presets.py`; each lists its own additions **and** every package the smaller presets carry, pinned by a superset test, and no companion is listed (`requires_packages` carries it). `GET /api/hub/model-presets` (`components/hub/backend/model_install_plan.py`) answers the screen in one request: this Mac's memory, the **library's** volume free (not the data dir's), the four presets with counts from `model_packages.preflight` receipts, and every model by job with bytes/floor/eligibility/sources. Native `Set up this Mac` (`StudioPage.setup`, `native/StudioViews.swift` `setupWorkspace`). `2 - Copy the models.command` copies nothing by default; `STUDIOHUB_KIT_COPY=1` (`2 - Copy the models (everything).command`) restores the old path for a Mac with no internet and no peer. Lite Mode's install set is retired — its runtime allowlist (`components/workstation_models.py`, `peers.lite_mode_allows`) is unchanged and Essentials · 8 GB carries every one of its six packages (verified, not assumed).
> - **One home for a memory floor.** `PROVEN_SMALLER`, `CARRY_BELOW_TIER` and `memory_admission._FLEET_DEFAULTS` each carried a number their manifests state, so an edited catalog row could lose to a table nobody could see overriding it. FLUX.2 Klein 4B MLX 4-bit and Breeze TTS 2 MLX 4-bit moved 16 → 8 in `model-manifests/` with their evidence (Klein stays `unqualified`: its 8 GB is a production-use floor with no stored peak receipt); the three tables are gone or empty and `packaging/capture_model_manifests.py` follows.
> - **Real removal.** `DELETE /api/hub/model-packages/{id}` (`components/hub/backend/model_removal.py`) **moves** the package folder to `<library>/_trash/<package>-<stamp>/` with a `.studiohub-removed.json` receipt and drops the installed receipt in the same breath — a receipt whose folder is gone would otherwise make the app offer a verify on a package that is not there. Refused, naming which, for a running job, a paused model whose Studio holds the weights, another installed package that requires it, or a Hub that does not own the library. `GET /api/hub/model-removals`, `POST /api/hub/model-removals/restore`; 30 days later the existing cleanup pass hands it to macOS Trash through `output_storage._macos_trash` — one deleter, not a second one.
> - **One-time preset cleanup (owner addition).** `components/hub/backend/preset_cleanup.py`: a **dry run that moves and writes nothing** (`GET /api/hub/preset-cleanup`, per Mac), an approved `POST` that calls the same `plan` and moves only on that answer, in-use/requires/paused refusals reported by name, an owner `keep` list, and `<data dir>/preset_cleanup.json` written only by an apply that ran, so a second run is a no-op that says so. The target preset is the owner's choice, else the starter for the tier — **never derived from what the Mac holds**. A test pins the owner's Gemma 4 case: it is in Studio Pro, so a 30-day dry run on a 32 GB Mac does **not** list it as would-move.
> - **Chat image input — the code is here, the loader is NOT in this build.** `feat/chat-vision-gemma4-20261027` makes `components/chat/app/backend/llm_engine.py` read the checkpoint's own `vision_config` and load such a model with `mlx-vlm`; the route accepts OpenAI-shaped content parts (`text` + `image_url` data URIs) and renders them through the model's own chat template; `chat_jobs._messages` passes parts through instead of flattening them; the catalog row publishes `supports_vision` and a `vision` capability. `730c09b` charges a picture its own soft tokens (`vision_soft_tokens_per_image`, 280 on Gemma 4) instead of the single `<|image|>` placeholder, and returns `None` when the checkpoint states none. **The branch edited only `packaging/voice-runtime/requirements-pilot.txt` and never relocked `requirements.lock.txt` or rebuilt `runtimes/voice`, which is what actually ships**, and the current `.build/voice-runtime` has no `mlx_vlm` (checked: `import mlx_vlm` → `ModuleNotFoundError`). So on this build's Chat lane a vision checkpoint loads its language tower with mlx-lm, sets `vision_unavailable_reason`, and **refuses an image with a sentence naming the missing loader** while text keeps working. That is the designed degradation, not a crash — but **vision is not delivered by 0.6.206** and the CHANGELOG/release notes say so. Turning it on needs a relocked `requirements.lock.txt` plus a rebuilt Voice runtime — an ffmpeg-compile job deliberately NOT run here (see the machine note below). A constrained relock was computed and adds 16 packages (`mlx-vlm`, `datasets`, `opencv-python`, `llguidance`, `pandas`, `pyarrow`, `aiohttp`+its six, `xxhash`+`multidict`) with **zero** version changes to the 133 already pinned; a plain relock would also have moved 53 pins (`fastapi` 0.141.1→0.162.0, `pillow` 12.3.0→13.0.0, `torch` 2.14.0→2.14.1, …), which is why constraining against the current lock is the correct shape when someone does it. **That relock is done on `feat/mlx-vlm-runtime-20261028`:** `refresh-lock.sh --keep-locked-pins` (the new flag; it constrains the refresh by the previous lock) adds exactly those 16 packages with zero version changes to the 133 already pinned, `mlx_vlm` is now in `packaging/voice-runtime/smoke_imports.py` so a built runtime missing it fails the smoke instead of degrading at run time, and the two voice `runtime_lock_sha256` values in `research/st01-runtime-contracts.json` follow the new lock (`f384e7ba…`). A scratch venv resolved from that lock imports `mlx_vlm` 0.6.3 under mlx 0.31.2 / transformers 5.14.1 / tokenizers 0.22.2. **Still open: rebuild the Voice runtime (`packaging/voice-runtime/build.sh`) and re-stage it** — the existing `.build/voice-runtime` still answers `ModuleNotFoundError: mlx_vlm`, so Chat vision reaches the fleet only after that rebuild.
> - **The network link.** `components/hub/backend/network_link.py` reads this Mac's primary connection — the interface the kernel gives the default route, the kind from the hardware port `networksetup` names for that device (`en0` is Wi-Fi on one Mac and Ethernet on another, so the device name is never the test), and for Ethernet the negotiated media (`1000baseT <full-duplex>` → 1000/full, `100baseTX`, `10baseT/UTP <half-duplex>`); Wi-Fi reports its `system_profiler` PHY rate on its own 10-minute timer. **Nothing guessed, never a zero**: `autoselect` with no cable states no speed and an unreadable read is `unmeasured` **with a reason**. Bounded subprocesses (5 s, 20 s for `system_profiler`), as this user, changing nothing. Cached — `/api/hub/resources` is on every controller's poll, so a request only reads the last answer; a daemon re-reads every 180 s. Published as `connection` on `resources.host_stats()`, on the host snapshot a node answers with (`750a8bf`, so the one Mac nobody can dial still reports it on check-in) and on every `/api/hub/fleet/overview` row; an older Hub publishes no key and a reader treats that as unknown. `710a2ac` moves the in-flight flag clear into a `finally`, so a read that died of a signal cannot leave `status()` refusing to start another for ever.
> - **Gate — all four stages green on the release commit `06df224`, each stage alone.** Runner `scripts/hub_offline_gate.py --root . --evidence .build/gate-evidence-206b/<stage>`. Offline unittest **GATE RESULT: run=3259 skipped=15 failures=0 errors=0** (exit 0, 964 s; 0.6.205 was 3127 — the 132 new cases are the model presets, preset route, removal, preset cleanup, chat vision, chat soft tokens and network link files). Hub regressions **443 passed** (exit 0, 44 s; same as 0.6.205). Voice **1016 passed / 28 skipped** (exit 0, 48 s; same as 0.6.205). Native: `bash native/build.sh` plus the boundary, API contract, secret-masking, view and lifecycle checks **all passed** (exit 0), including the malformed-catalog recovery probe (`catalog=200-after-malformed reachable=true`). **A first unittest attempt on this tree was invalid and must never be quoted: it ran in `.build/gate-evidence-206/unittest` (3259 run, 15 skipped, **1 failure**) while the release commit was being written, and the failure was `test_release_notes` reading `VERSION` a minute before the bump landed — not a product defect.** The tree has since produced the clean run above; `.build/gate-evidence-206/` is kept so the difference is checkable. **A filtered, mid-edit or otherwise disturbed run is not a gate result.**
> - **Artifact.** `dist/StudioHub-0.6.206-06df224.dmg` in this worktree, **1,413,548,032 bytes**, sha256 `f4d374e1f4fb69c020c8d6ab7ccbd1e732aa40540f7d9310c767f920f92bba8a` (0.6.205: 1,413,443,584). Built with `packaging/make_dmg.py ".build/staged/Studio Hub.app" "dist/StudioHub-0.6.206-06df224.dmg"`; `hdiutil verify` reported the checksum **VALID**. Mounted read-only at a private `/tmp` mountpoint and **detached afterwards** — no `/Volumes/KH-STUDIO-*` was touched (all four drives are still mounted), and nothing was installed: `/Applications/Studio Hub.app` was never read or written by this run. Mounted app tree digest `98d3fe3e…` equals the staged app and its recorded `Studio Hub.app.tree-sha256`. `HUB_RELEASE_ARTIFACTS` passes (`missing_release_artifacts()` returns `[]`: the `runtimes` and `workers` sets are complete, all nine worker entry points and three runtimes present). `release-manifest.json` reports `version` **0.6.206**, `implementation_commit` **06df224f9af98b3bf208a77b94187f39e0b533e3** and `development_build: false`; `Info.plist` `CFBundleShortVersionString` is 0.6.206 and `CFBundleName` "Studio Hub". `runtimes/voice` `bae1132a…`, `runtimes/image` `b720c5bc…` and `runtimes/music-yue2` `75465dc4…` are **digest-identical to 0.6.205's** (this release changes no runtime), and `runtime_packaging.py verify` exits 0 on the staged voice and image trees. The kit rides in `components.hub.khu_agent` — agent **0.1.1**, `bin/khu` `2061bcaa…` and `install-agent.sh` `be5a876d…` inside the mounted image equal to the rebuilt kit and to what 0.6.203 shipped; `publisher.json` is `testAuthority: false` and there is **no `private.key` anywhere in the bundle**. `tests/test_release_notes` run with `STUDIOHUB_TEST_APP` pointed at the staged bundle: 17 ran, **0 failures**, the three bundle checks (`the_packaged_plist_carries_the_current_version`, `the_notes_are_readable_from_inside_the_bundle`, `the_release_manifest_names_this_source_commit`) all **ok** — without that variable they skip, so the skip is not proof. **The installer-acceptance check (older installers ACCEPT the mounted app) was NOT run by this worker.**
> - **render-ranking NOT included — coordinator instruction, 2026-10-28.** `feat/render-ranking-20261028` (`f8bf808..436b53e`) is committed but its tests have not run. The coordinator's instruction was explicit: do **not** merge it into 0.6.206; it goes in the next round. Confirmed absent: neither `f8bf808` nor `436b53e` appears in `git log release/0.6.206`. The release slot was left open and nothing was merged speculatively.
> - **Still open (coordinator).** Push/merge to `main` (`release/0.6.206` is cut from `release/0.6.205`, which is itself not on `main`), publication to the Gateway, fleet rollout, signing and notarisation, the SSD kit write (deliberately not this worker's scope — **no SSD was written**), and the installer-acceptance check. **Chat image input is not delivered by this build**: the code is merged but the shipped Voice runtime carries no `mlx-vlm`, so a vision checkpoint answers text and refuses an image naming the missing loader. Turning it on needs a relocked `requirements.lock.txt` plus a rebuilt Voice runtime (see the chat entry above for the zero-version-change relock that was computed). The three provisional floors (Qwen 3.6 35B, Nemotron, Gemma 3 12B) still await physical measurement.
> - **Build inputs — two links inherited from 0.6.205 were dangling and one input was rebuilt.** The 0.6.205 handoff pointed `.build/image-runtime` and `.build/khu-agent-kit` at `_worktrees/studio-hub-kh/release-0.6.203`, and **that worktree has since been moved to `_trash/2026-10-28-finished-worktrees/hub/`**, so both links resolved to nothing. `.build/image-runtime` now points at the 0.6.202 worktree's tree, whose digest is `b720c5bc…` — the `IMAGE_RUNTIME_SHA256` pin in `packaging/stage_voice.py`, checked by computing `stage_voice.tree_digest`, not assumed. `.build/khu-agent-kit` was **rebuilt from source** with `kh-updater/packaging/build-agent-kit.sh 0.1.1`, at kh-updater `6b82b2d` (the commit 0.6.205 named): agent **0.1.1**, and `bin/khu` `2061bcaa…`, `bin/khu-stub` `e5fb6388…` and `install-agent.sh` `be5a876d…` are **byte-identical to the 0.1.1 kit 0.6.203 shipped**, so it is the same artefact and not a new one. The kit was built to `.build/khu-agent-kit-0.1.1` and linked, because `stage_khu_agent.validate_source_kit` **refuses a symlinked kit path** and `--khu-agent-kit` must be given the real one. `.build/{hub-gate,test-deps,voice-runtime,yue2-runtime,prepare-kit.sh}` are symlinked from the primary checkout as 0.6.203/0.6.204/0.6.205 record. Runtime digests re-computed and equal to 0.6.205's inputs: `.build/voice-runtime` `e253aa42…` (the staged `runtimes/voice` is `bae1132a…` after `strip-tests`, as before), `.build/image-runtime` `b720c5bc…`, `.build/yue2-runtime` `75465dc4…`. `.build/StudioHub` is **copied from 0.6.205 and is therefore stale** — the native stage's own `bash native/build.sh` regenerates it from this branch's sources before anything is staged. One slipped rule, recorded: the dangling `.build/khu-agent-kit` symlink was replaced with `rm -f` + `ln -sfn` where `ln -sfn` alone would have done it; nothing with content was deleted, and the note is at `/build/Developer/_trash/hub-206-notes/REMOVED-symlink-2026-10-28.md`.
> - **render-ranking NOT included.** `feat/render-ranking-20261028` was not mergeable when this release was prepared: there is no such branch and no worktree at `_worktrees/studio-hub-kh/render-ranking` (checked at the start and again at the end of this run). The slot was left open and nothing was merged speculatively.

> **0.6.205 — models are governed by RAM, never blocked by name, and Gemma 3 12B Instruct MLX 4-bit joins the catalog — 2026-10-27. PREPARED ON `release/0.6.205` (worktree `_worktrees/studio-hub-kh/release-0.6.205`) cut from Hub main `eb572fc` (0.6.204).** VERSION `0.6.205`. `unlock-models-20261027` (`e00d158`, `d7d900b`, `8c3b5e6`) merged `--no-ff`, no squash; release commit `ded38a0`. **Nothing is pushed, published, signed, notarised or rolled out, and no Gateway was written. The four kit SSDs WERE written** (see the SSD entry below) — that is this worker's assigned scope, not a rollout.
> - **Owner decision, 2026-10-27.** *"They are models we can run so we should have them installable. Able to run on the machine or not is based off its RAM. We no longer block or lock any model."* Two name-based locks carried two facts at once — *not measured* and *not offered* — so a Mac that could hold them could not install them either. Both are gone.
> - **`qwen36-35b-4bit` and `nemotron-lightning-4bit` lose `production_allowed: false`.** That flag was the only thing putting them in `admission_state: production_blocked` / `dispatch_state: ineligible` on every Mac, and the only thing keeping them off the kit copy step (`not yet qualified; retained on SSD`). Both now publish a **provisional** floor — 24 GB minimum / 32 GB recommended, labelled `PROVISIONAL, NOT MEASURED` in `memory_evidence` — because a null floor reads as *fits every Mac* to the copy step and to dispatch, and these packages are 19.03 GiB and 16.57 GiB of weights. A physical qualification run must replace it.
> - **`gemma-3-12b-it-4bit` leaves `RETIRED_MODELS` and is admitted with a manifest.** It was retired on 2026-10-02 as LTX's text encoder; the LTX video **product** stays retired (its own two entries untouched). New `model-manifests/gemma-3-12b-it-4bit.json` — chat-mlx, `mlx-community/gemma-3-12b-it-4bit` at immutable revision `86cc6a8dedbc456dd0e4af01a9d09f396f77e558`, **13 files / 8,068,018,787 bytes** — with a provisional 16 GB minimum / 24 GB recommended floor (16 GB is the smallest fleet tier that can hold 8.07 GB of weights; 8 GB cannot). It publishes **text only**: the package ships the processor configs but the Hub's chat worker handles text input; vision is a separate branch (`feat/chat-vision-gemma4-20261027`).
> - **The catalog is 40 packages; 39 are copyable.** `florence-2-large-ft` and `phonon-2-mlx` ship manifests but have no weights on any kit drive, so the kit manifest lists them under `models_not_shipped` — an honest subset, not a defect. The four documents that state the copyable inventory (kit README, SSD README, node checklist, `docs/runtime-contract.md`) now say all 39 production-allowed packages are copyable and RAM decides; `tests/test_document_inventory.py` asserts `measured_kit_packages() == measured_packages()`, so a pending flag or withheld-name list coming back without its documents fails the gate.
> - **Gate (this release worker, worktree `release-0.6.205`, runner `scripts/hub_offline_gate.py`, each stage alone).** Offline unittest **GATE RESULT: run=3127 skipped=15 failures=0 errors=0** (exit 0; 0.6.204 was 3126 — the new catalog/retirement case). Hub regressions **443 passed** (exit 0; same as 0.6.204). Voice **1016 passed / 28 skipped** (exit 0). `bash native/build.sh` plus its boundary, API, secret-masking, view and lifecycle checks **passed** (exit 0). `.build/{hub-gate,test-deps,voice-runtime,yue2-runtime,prepare-kit.sh}` symlinked from the main checkout; `.build/{image-runtime,khu-agent-kit}` from `release-0.6.203`.
> - **Artifact.** `dist/StudioHub-0.6.205-ded38a0.dmg` in this worktree, **1,413,443,584 bytes**, sha256 `9139a30b0c4ac8aff21cdf71f254046bc3d08744d426f068d081c2334b6fe579` (0.6.204: 1,420,151,808). Mounted read-only at a private `/tmp` mountpoint and detached afterwards. Mounted app tree digest `0c962b07…` equals the staged app and its recorded tree. `HUB_RELEASE_ARTIFACTS` passes (`missing_release_artifacts()` returns `[]`): all three runtimes and all nine worker entry points present. `runtimes/voice` `bae1132a…`, `runtimes/image` `b720c5bc…`, `runtimes/music-yue2` `75465dc4…` are **digest-identical to 0.6.204** (this release changes no runtime), and `runtime_packaging.verify_file_manifest` reports **0 mismatches** on the mounted voice and image trees. No top-level `khu-agent` component; the kit rides in `components.hub.khu_agent` (agent **0.1.1**), `publisher.json` is JSON-equal to the owner's source with `testAuthority: false`, and there is no `private.key` anywhere in the bundle. `development_build` is false and `implementation_commit` is `ded38a0`. **The installer-acceptance check (older installers ACCEPT the mounted app) was NOT run by this worker.**
> - **SSD kit WRITTEN — all four drives** (`KH-STUDIO-ATLAS`, `-NOVA`, `-ORICO`, `-UGREEN`), coordinator authority, 2026-10-27. `prepare-kit.sh` re-rooted at this worktree (`ROOT` overridden, `STAGE_DIR` = `.build/staged`) so the drive is prepared from this release's sources and never from the primary checkout's. Per drive: the old `Studio Hub.app` moved to `<drive>/_trash/Studio Hub.app-0.6.195`; the 0.6.205 app copied in (**66,457 files, commit `ded38a0`**, plist `0.6.205`); `focused-release-manifest.json` regenerated (**40 models listed, 2 not shipped, `problems: []`**); Technical, the launchers, the verifier, `NODE-SETUP-CHECKLIST.md`, `model-benchmarks.md`, the kit READMEs and the stamped index document (`0.6.205 · ded38a0`) refreshed; `SHA256SUMS.txt` regenerated from the DMGs actually present. **`gemma-3-12b-it-4bit` copied into every `model-library`** at the pinned revision (13 files / 8,068,018,787 bytes) and its receipt registered through the release app's own `model_packages.verify_installed` — `files_verified` on all four. Each drive's `SHA256SUMS.txt` now lists **five** entries (`0.6.205`, `0.6.204`, `0.6.203`, `0.6.195`, `installers/0.6.195`); every 0.6.205 DMG hashes `9139a30b…` on the drive, equal to the build. Nothing was unmounted or ejected; **`KH Updater signing key backup` untouched on all four** (32 bytes, mtime 2026-10-26T03:10:46/51, sha256 `f19bd938…` on every drive — named here only as a hash, never read out).
> - **Kit copy-step proved on the drive's own files.** `Technical/copy_models_for_this_mac.py` run from the ATLAS kit with its own app bundle and a throwaway HOME: a **32 GB** Mac is offered **qwen36-35b-4bit, nemotron-lightning-4bit and gemma-3-12b-it-4bit** (30 models, 146.0 GB); an **8 GB** Mac leaves all three behind as *too small* (17 models, 31.3 GB). **Neither run prints `not yet qualified; retained on SSD` or `Pending models`** — the phrase the owner's rule was about is gone, and the only reason a model is now skipped is its published floor.
> - **Retired models: no copy exists on any drive.** Owner instruction 2026-10-28 — the four retired packages (ACE-Step 1.5, Lance 3B Video, its Qwen2.5-VL-3B-Instruct processor, LTX-2.3 22B) must leave every SSD. An exhaustive walk of each **whole volume** (70,312 directories on ATLAS alone, sanity-checked by finding `gemma-3-12b-it-4bit` on the same walk) found **zero** matching paths inside any `Video Generation Kit`, and zero bundle names elsewhere on the drives. No `model-library` folder, no index entry, no `components/video/retired` park (0.6.205 stages no `components/video`), no Hugging Face cache entry. **Nothing was moved because nothing was there — 0 GB freed per drive.** The one near-miss worth recording: the retired models' weights were never on these drives; their manifests stay in the repository (`components/video/retired/`), which is the documented restore path and is not a kit artefact.
> - **Fleet read-only check, 1301's Gateway (GET only; nothing changed).** `/admin/api/fleet`, `/admin/api/models`, `/admin/api/fleet/disk` read with the key read on the host and never printed. **2,752 `local_models` rows across 8 sites and 64 machines scanned: zero rows for any of the four retired packages** (no `ace-step-1-5-turbo`, no `lance-3b-video-bf16`, no `lance-3b-processor-qwen2-5-vl`, no `ltx-2-3-22b-ram-12gb-mlx`), and no retired name anywhere in the models catalogue or the disk report. So no fleet Mac reports one installed — **there is nothing for the owner to clean up.** For context on the newly unlocked set: `gemma4-26b-a4b-qat-4bit` is `cached` on 1 machine and `missing` on 63; `nemotron-lightning-4bit` and `qwen36-35b-4bit` are `missing` on all 64. Every Mac reports Hub `0.6.204` (one on `None`), i.e. the fleet is one release behind this one.
> - **Still open (coordinator).** Push/merge to `main`, publication to the Gateway, fleet rollout, signing and notarisation, and the installer-acceptance check — none were done here. The three provisional floors (Qwen 3.6 35B, Nemotron, Gemma 3 12B) await physical measurement. Gemma 3 12B vision input is not wired (text only).
> - **Disk.** No build scratch moved this run beyond `make_dmg.py`'s own preserved staging under this worktree's `_trash/dmg-scratch/` (4.0 GB, same volume, frees nothing until the owner empties it). The Gemma staging folder double-nested one `model/` level on first download; it was flattened to the manifest's layout and re-hashed (13/13 OK) before anything was copied.

> **The install-time model picker (Hub + Gateway Fleet), P0-P6 — 2026-10-28. PREPARED ON `feat/model-picker-20261028` (worktree `_worktrees/studio-hub-kh/model-picker`) cut from `unlock-models-20261027` (`8c3b5e6`). VERSION NOT BUMPED.** **Nothing is pushed, published, installed, rolled out, version-bumped or written to any SSD.** Owner approved 2026-10-28.
> - **RAM is the only gate.** `components/shelved_models.py` (`SHELVED_MODELS`), `keep_all_models`, the Hub's start-up move-out, both download refusals by name, and the kit's "Shelved, not copied" skip are **deleted**, together with the kit's `NOT_STOCKED` list. The seven formerly shelved packages and `qwen-image-2-1-mlx-serve-4bit` are ordinary catalog entries: listed, tickable, in no preset, gated by their own manifest floor. `components/retired_models.py` is untouched and is now the only list that moves an installed package out.
> - **Four presets, as data.** `model-presets/*.json` - Essentials · 8 GB, Creator · 16 GB, Creator Plus · 24 GB, Studio Pro · 32 GB+ - read by `components/model_presets.py`. A fifth preset is a JSON file. Each file lists its own additions **and** every package the smaller presets carry, and a test pins the superset. No companion is listed; `requires_packages` carries it.
> - **One home for a floor.** `PROVEN_SMALLER`, `CARRY_BELOW_TIER` and `memory_admission._FLEET_DEFAULTS` all carried numbers their manifests now state. FLUX.2 Klein 4-bit and Breeze TTS 2 4-bit moved 16 -> 8 in `model-manifests/` with their evidence (Klein stays `unqualified`: its 8 GB is a production-use floor with no stored peak receipt), and the three tables are gone or empty. `packaging/capture_model_manifests.py` follows.
> - **`GET /api/hub/model-presets`** - this Mac's memory, the free space on the **library's** volume (not the data dir's, which `resources.host_stats` reports), four presets with counts from `model_packages.preflight` receipts, and every model grouped by job with bytes, floor, eligibility and the sources that actually exist (LAN share -> mounted SSD kit -> pinned internet URLs). `components/hub/backend/model_install_plan.py`. A preset is complete only when every package is `files_verified`. `florence-2-large-ft` and `phonon-2-mlx` (manifests with no weights on any 2026-10-28 drive) report `["internet"]` and keep their preset incomplete.
> - **Native `Set up this Mac` page** (`StudioPage.setup`, leading Library; `native/StudioViews.swift` `setupWorkspace`). Memory and the models' drive's free space first, four preset cards with "9 of 14"/"Installed" and "copies X · Y free after", then every other model by job with size, floor, purpose, sources, and a greyed-out row naming its floor and this Mac's memory for a model that cannot run here. One request per package to install; no batch endpoint.
> - **The kit stops choosing.** `2 - Copy the models.command` copies nothing by default and points at the screen; `plan_prune` is not even called, so a Mac set up from the screen is never reorganised by a kit run. `STUDIOHUB_KIT_COPY=1` (new `2 - Copy the models (everything).command`) restores the old path for a Mac with no internet and no peer. **Lite Mode's install set is retired**; its runtime allowlist (`components/workstation_models.py`, `peers.lite_mode_allows`) is unchanged and the 8 GB preset carries every model on it.
> - **Real removal.** `DELETE /api/hub/model-packages/{id}` moves the package to `<library>/_trash/<pkg>-<stamp>/` with a `.studiohub-removed.json` receipt and drops its installed receipt; refused (naming which) for a running job, a paused model, or another installed package that requires it. `GET /api/hub/model-removals` lists what is recoverable; `POST /api/hub/model-removals/restore` puts it back. 30 days later the existing cleanup pass hands it to macOS Trash through `output_storage._macos_trash` - one deleter. `components/hub/backend/model_removal.py`.
> - **Gateway Fleet.** `GET/POST .../machines/{id}/model-presets|model-preset` and `DELETE .../model-packages/{id}` in `gateway/startup_control.py` + `gateway/admin.py`; `menubar/FleetModelPreset.swift` adds "Model Preset…" and "Remove a Model…" to a Mac's "…" menu, offering that Mac's own presets and installed models. The two writes are owner-only; an offline Mac is `NODE_UNREACHABLE`, reported not failed. Swift compiles with zero errors and zero warnings.
> - **One-time cleanup (owner addition).** `components/hub/backend/preset_cleanup.py`: a **dry run that moves and writes nothing** (`GET /api/hub/preset-cleanup`, per Mac), an approved `POST` that calls the same `plan` and moves only on it, in-use/requires/paused refusals reported by name, an owner `keep` list, and a record in `<data dir>/preset_cleanup.json` that makes a second run a no-op. The target preset is the owner's choice else the starter for the tier - **never derived from what the Mac holds**. A test pins the owner's Gemma 4 case: it is in Studio Pro, so a 30-day dry run on a 32 GB Mac does **not** list it as would-move.
> - **Tests.** New: `tests/test_model_presets.py` (22), `tests/test_hub_model_presets_route.py` (24), `tests/test_model_removal.py` (27), `tests/test_preset_cleanup.py` (24), gateway `tests/test_fleet_model_presets.py` (13). Updated for the deleted mechanisms: ~18 files, listed in the commits. `bash native/build.sh` boundary/API/secret-masking/view/lifecycle all pass.
> - **Gate evidence (this worker, worktree `_worktrees/studio-hub-kh/model-picker`).** Runner `scripts/hub_offline_gate.py`; `.build/{hub-gate,voice-runtime,prepare-kit.sh}` linked from the main checkout (a worktree needs them, as 0.6.203/0.6.204 record). Offline unittest **GATE RESULT: run=3213 skipped=15 failures=0 errors=0** (exit 0). Hub regressions **443 passed** (exit 0). Voice **1016 passed / 28 skipped** (exit 0). `bash native/build.sh` boundary, API contract, secret-masking, view and lifecycle checks **all passed** (exit 0), and the Set up page was rendered offscreen at 1920x1053 with a fixture plan. Gateway: `tests/test_fleet_model_presets.py` 13, plus `test_fleet_model_install` 20, `test_lan_model_share` 25, `test_fleet_current_job` 3, `test_fleet_health` 17 and `test_fleet_action_banner_layout` all OK.
> - **Still open.** Nothing pushed; no version bump (the 0.6.205 branch is the coordinator's); the Gateway bundle step cannot finish in a worktree because `data/final-render` is git-excluded (`build.sh` stops at "selected render authority source is unavailable", identically on an untouched branch) - the Swift compiles clean and the bundle must be built in the primary checkout. No fleet/Gateway live write was made and no SSD was touched.

> **0.6.204 — a files-only release is no longer capped against a disk image that does not exist — 2026-10-27. PREPARED ON `fix/delta-cap-files-only-20261027` (worktree `_worktrees/studio-hub-kh/delta-cap-20261027`) cut from Hub main `ad1544c` (0.6.203).** VERSION `0.6.204`. **Nothing is pushed, published, signed, notarised, installed or rolled out, and no SSD drive was written.**
> - **The defect, and why 0.6.203 turned it into a hard stop.** `release_sync._run_stage` handed `descriptor.get("bytes")` to the delta stage as `artifact_bytes` on every run, so `release_delta.stage` refused any change set over `MAX_DELTA_FRACTION` (0.5, `release_delta.py:31`) with `over_threshold`. A release that arrived as a manifest has no image anywhere in the chain — `app_release.store_release` deletes `ARTIFACT_FILE` when the body carries no artifact (`app_release.py:632-636`) — so the refusal's only answer (download the image) was the guaranteed 404 of 0.6.201's site 1100. 0.6.203 stopped treating the image as an option for a manifest push, which was right, and thereby made a stage that had worked by luck into one that can never succeed: a Mac far enough behind for more than half its app to differ was refused `over_threshold` on every check, and the app's own guard then said only *"it arrived as files only, and this Mac has no disk image of it to install instead"*.
> - **The fix.** `release_sync._delta_cap_bytes(descriptor)` returns the descriptor's `bytes` only where an image can actually be installed: this Mac's own held `ARTIFACT_FILE`, or — on a node, whose only fallback is a relay — a controller whose own `GET /api/hub/release` says it holds one (`delta.artifact`), recorded in `release_sync._controller_image` against the release signature it was answered for (`sync_once` sets it after the descriptor verifies and is stored). Everything else passes `None`, so the stage builds. The default when a node has not polled since it started is *no cap*, which prefers a buildable stage over a preference for the smaller download; the cap is a preference, never a safety property. `_await_blobs` is unchanged (a node does not wait for gateway blobs either way).
> - **Proof.** `tests/test_release_delta.py` is unchanged and remains the contract for `stage(artifact_bytes=…)` itself (31 passed / 1 skipped / 11 subtests). Three cases added to `tests/hub_regression/cases/test_release_delta_routes.py`: a **controller** with a files-only release staging a delta bigger than the cap, a **node** whose synced controller answered `delta.artifact: false` doing the same, and a release pushed **with** an image still ending `fallback` / `over_threshold` (so the cap is pinned as alive, not merely absent). Both new `staged` cases were run red (they end `fallback` / `over_threshold`) against 0.6.203's line before the fix and green after.
> - **Still open — the bootstrap.** A Mac **already** stranded on 0.6.203 cannot run 0.6.204's stage code, so it needs 0.6.204 pushed **as a disk image** (or installed by hand); 0.6.203 refuses files-only stages for a Mac far enough behind. Any Mac that can install 0.6.204 gets the fix, and its own later manifest pushes are then uncapped. Nothing here changes the native app.
> - **Gate / build (this release worker).** Runner `scripts/hub_offline_gate.py --root <worktree> --evidence .build/gate-evidence-204/<stage>`, each stage alone. Offline unittest **GATE RESULT: run=3126 skipped=15 failures=0 errors=0** (exit 0; unchanged from 0.6.203 — this change adds regression cases, not unittest ones). Hub regressions **443 passed** (exit 0; 0.6.203 was 440 — the three new cases). Voice **1016 passed / 28 skipped** (exit 0). `bash native/build.sh` plus its API, secret-masking, view and lifecycle checks **passed** (exit 0). A worktree needs `.build/{hub-gate,test-deps,voice-runtime,yue2-runtime,prepare-kit.sh}` linked from the main checkout before the gate is meaningful.
> - **Build inputs.** `.build/voice-runtime`, `.build/image-runtime` (`b720c5bc…`, linked from the 0.6.203 worktree) and `.build/yue2-runtime` (`75465dc4…`) symlinked from their existing trees; `.build/StudioHub` native shell; `.build/khu-agent-kit` (kh-updater `6b82b2d`, agent **0.1.1**, kit manifest `55346897…`) symlinked from the 0.6.203 worktree; publisher descriptor the owner's `~/Library/KHUpdater/publisher.json`. `packaging/stage_khu_agent.validate_source_kit` **refuses a symlinked kit path** (it may not resolve one file and ship another), so `--khu-agent-kit` was given the real path; the first stage attempt ended on that refusal and its partial bundle was **moved to `/build/Developer/_trash/hub-204-failed-stages/`**, never deleted. Exact stage: `.build/voice-runtime/bin/python3 -B packaging/stage_voice.py --runtime .build/voice-runtime --native-shell .build/StudioHub --image-runtime .build/image-runtime --yue2-runtime .build/yue2-runtime --hub --render-authority descriptor-only --render-tools-dir /build/Developer/story-studio-kh/bundled/ffmpeg --khu-agent-kit <real kit path> --khu-publisher-json ~/Library/KHUpdater/publisher.json --output ".build/staged/Studio Hub.app"`, then `packaging/make_dmg.py`.
> - **Artifact.** `dist/StudioHub-0.6.204-8c20499.dmg` in this worktree, **1,420,151,808 bytes**, sha256 `1aba1966d33293c54b4e23783df432da7189b99e0b1a8f50cc393bf585c92998` (0.6.203: 1,413,754,368). Mounted read-only at `/tmp/shub-204-dmg` and detached afterwards; **no `/Volumes/KH-STUDIO-*` was touched**. Mounted app tree digest `6c539546…` equals the staged app. `HUB_RELEASE_ARTIFACTS` passes: three runtimes and every worker entry point present; `runtimes/voice` `bae1132a…`, `runtimes/image` `b720c5bc…` (pinned, `runtime_qualified: true`) and `runtimes/music-yue2` `75465dc4…` (pinned) are digest-identical to 0.6.203's; `runtime_packaging.py verify` reports no mismatch on the mounted voice and image trees (YuE2 ships no `manifest/runtime-files.json`). No top-level `khu-agent` component; the kit rides in `components.hub.khu_agent` (agent **0.1.1**, kit manifest `55346897…`), `khu-agent/publisher.json` is present and there is no `private.key` anywhere in the bundle; `development_build` is false and `implementation_commit` is `8c20499`. **The installer-acceptance check the 0.6.198-0.6.203 releases ran (old installers ACCEPT the mounted app) was NOT run by this worker** — the artifact is a local build only.
> - **Disk.** The rejected first stage went to `/build/Developer/_trash/hub-204-failed-stages/`; this worktree's `_trash/dmg-scratch/` is `make_dmg.py`'s own preserved staging (4.0 GB), left in this worktree's `_trash/` rather than the shared one. `_trash/` is on the same volume, so nothing is freed until the owner empties it.

> **0.6.203 — a files-only update that never asks for a missing image, a fan-out rollout, and an agent installer that starts the agent — 2026-10-27. PREPARED ON `release/0.6.203` over Hub main `962f37c` (0.6.202).** VERSION `0.6.203`. Three branches merged with `--no-ff`, no squash: `fix/files-only-update-20261026` (`2934f88`), `feat/fanout-rollout-20261026` (`dd22dfd`) and `docs/rules-refresh-20261026` (`033d051`), plus one release-commit change of this worker's own (the SSD kit installer). Release commits `6c351e8` (version, notes, changelog). **Nothing is pushed, published, signed, notarised, installed or rolled out, and no SSD drive was written.**
> - **A files-only release never falls back to a disk image that does not exist.** At site 1100 a member Mac's stage ran long, the app abandoned it and downloaded the image, and the relay answered 404 (`Nothing was installed.`); it recovered only when a fresh process found the stage done, after the rollout driver had halted. A manifest push carries `files_sha256` and leaves no image anywhere in the chain. `GET /api/hub/release` now publishes `delta.artifact` (does this release have an image to install), the app reads it (`readRelease`), a release carrying a file manifest is never fallen back from (still running, unreadable or past its deadline -> `not_ready`, retried next check; a Hub refusal -> the Hub's own reason), and `release_sync` gives one stage **one** `BLOB_WAIT_S` deadline instead of one per missing file. New native cases fail on 0.6.202 with "1 image" installed for a files-only release.
> - **`scripts/hub_rollout.py` is the fleet rollout tool** (owner, 2026-10-26: *"it shouldn't block the rest ... we can just fan it out"*). One worker per site; a site is publishable only when reachable, its own `running_jobs` empty, and the Gateway has no non-terminal job routed to it (`queue_depth: None` with nothing running is *queue unknown*, never a zero). A busy site is re-polled in its own bounded 2 h window, an unreachable one is *deferred: unreachable* and retried, a silent member is a warning — none halts another site. One accepted POST per site, receipts opened `x`, no republish on rerun, and `local_paused_at` read and recorded but never written. Acceptance is from the bytes on disk (size + SHA-256 recomputed, `hdiutil verify` VALID, mounted read-only at a private temp mountpoint, never under `/Volumes`). `scripts/test_hub_rollout.py` is 31 tests against fake Gateway responses, no network. **Not run against the live fleet.**
> - **KH Updater agent 0.1.1 — installing it also starts it.** kh-updater `main` was fast-forwarded `f87a6f4 -> 6b82b2d` (`feat/kit-activates-20261026`); `packaging/install-agent.sh` now loads the LaunchAgent after a completed install with the rule `khu_agent.Bootstrap.activate` uses (probe `launchctl print gui/$(id -u)/com.khupdater.agent`, bootstrap only when not loaded), and a failed start keeps the finished install, prints launchctl's reason and the retry command, and exits non-zero. `./build.sh test` 358 passed / 0 failed (exit 0); `python3 -I Tests/test_packaging.py` 16 passed / 0 failed. Kit rebuilt with `packaging/build-agent-kit.sh 0.1.1 .build/khu-agent-kit`; `bin/khu` and `bin/khu-stub` are **byte-identical to 0.1.0's** (`2061bcaa…`, `e5fb6388…`) — only `install-agent.sh` moved — and the kit manifest digest is `55346897…`. `./Tests/test_publisher_init.sh` was **not** run by this worker.
> - **The SSD kit's installer follows the same rule.** `packaging/kit-khu/khu-install.py` used to trash the whole finished install on a failed `activate()`. `Bootstrap.activate()` now raises `StartFailed` (launchctl's stderr + the plist path); the run prints it and `launchctl bootstrap gui/<uid> <plist>`, keeps the install and exits 1; a job already loaded is left alone and the run says so. A refused enrolment still trashes the partial install. `tests/test_khu_kit.py`'s old trash-pinning test was replaced (both new tests failed against the previous installer) — 44 passed in that file, 80 with the two `khu_agent` files. `Install KH Updater.command` and `packaging/kit-readmes/KH Updater.md` say the same.
> - **Rules refresh.** HANDOFF is START HERE (BOARD.md is historical as of 0.6.194); release scope is `HUB_RELEASE_ARTIFACTS`; only edits to the **same** shared files are serialised; `.build/` has one narrow carve-out (`packaging/prune-build-scratch.sh`); `scripts/hub_offline_gate.py` is the tracked gate runner. `tests/test_video_retired.py` is rewritten off `RETIRED_MODELS` itself; `packaging/hub_worker.py` names the retired manifest once (`VIDEO_MODEL_PACKAGE`). The retirement entries and `components/video/` stay deliberately.
> - **Gate / build (this release worker, worktree `_worktrees/studio-hub-kh/release-0.6.203`).** Runner `scripts/hub_offline_gate.py --root <worktree> --evidence <dir>`; each stage ran alone, one at a time, while a Story Studio full gate was running on this Mac (allowed: Hub modules alongside a Story gate, one at a time for memory). Offline unittest **GATE RESULT: run=3126 skipped=15 failures=0 errors=0** (exit 0; 0.6.202 was 3125 — the new case is the khu-kit already-loaded-job test). Hub regressions **440 passed** (exit 0; 0.6.202 was 439). Voice **1016 passed / 28 skipped** (exit 0). `bash native/build.sh` plus its boundary, API, secret-masking, view and lifecycle checks **passed** (exit 0). The first unittest attempt reported one error — `test_model_copy_script.TheUpdateCommandSkipsAnUnchangedAppTest.test_the_kit_ships_it` reads `.build/prepare-kit.sh`, which this worktree did not yet have linked; the link was made (the same link 0.6.202's worktree carries) and the whole stage was re-run from a fresh evidence directory. **A worktree needs `.build/prepare-kit.sh`, `.build/hub-gate`, `.build/test-deps`, `.build/voice-runtime`, `.build/yue2-runtime` linked from the main checkout** before the gate is meaningful.
> - **Artifact.** `dist/StudioHub-0.6.203-6c351e8.dmg` in this worktree, **1,413,754,368 bytes**, sha256 `1e06c07d375120a2c09f87bf00870c7368ebfbbd3c712409440187c019961fe8` (0.6.202: 1,421,014,016). Mounted read-only at `/tmp/shub-203-dmg` and detached afterwards; **no `/Volumes/KH-STUDIO-*` was touched** (all four were mounted throughout and are still mounted). Mounted app tree digest `e8ad3721…` equals the staged app and its recorded tree. `HUB_RELEASE_ARTIFACTS` passes: all three runtimes and all nine worker entry points present; `runtimes/voice` `bae1132a…`, `runtimes/image` `b720c5bc…` and `runtimes/music-yue2` `75465dc4…` are **digest-identical to 0.6.202** (this release changes no runtime), and `runtime_packaging.verify_file_manifest` reports **0 mismatches** on the mounted voice and image trees, with `test_only_hits` empty. There is **no top-level `khu-agent` component**; the kit rides inside `components.hub.khu_agent` (**agent 0.1.1**, kit manifest `55346897…`), and the `install-agent.sh` inside the mounted image carries the activating logic (`COMMITTED` guard, `launchctl bootstrap` retry line). `khu-agent/publisher.json` is JSON-equal to the owner's source and there is no `private.key` anywhere. `development_build` is false, `implementation_commit` is `6c351e8`, `version` is `0.6.203`. The **0.6.198, 0.6.200, 0.6.201 and 0.6.202 installers all ACCEPT** the mounted app (and 0.6.198 also accepts the installed 0.6.202 app, so the check is not vacuous); the 0.6.198 validator was separately shown to **REFUSE** an app with `runtimes/image` removed.
> - **The Image runtime was reused from 0.6.202's build, and the first clone was wrong.** `cp -cR` from `release-0.6.202/.build/image-runtime` did not preserve file modes, so `bin/python3` and eight other 0555 files arrived 0755 and `verify_file_manifest` reported 9 `mode` mismatches on the staged tree. That stage and its DMG were **moved to `_trash/`** (never deleted) and the runtime was re-cloned with `cp -cRp`; the tree digest `b720c5bc…` was equal both times (the digest does not cover modes), which is exactly why the file-manifest check is the one that caught it. **A runtime clone must use `cp -cRp`.**
> - **SSD kit DRY run — nothing written.** `DRY=1 KHU_AGENT_KIT=<worktree>/.build/khu-agent-kit KHU_PUBLISHER_JSON=~/Library/KHUpdater/publisher.json bash packaging/kit-restructure.sh` exited 0 and named every write for ATLAS, NOVA, ORICO and UGREEN: each `Video Generation Kit/KH Updater/khu-agent/` (the existing one) **moved to that drive's `_trash/superseded-kh-updater-2026-10-27/`** and re-staged from the named inputs; `Install KH Updater.command`, `khu-install.py`, `apps.json` and the KH Updater README re-copied; the four kit readmes re-copied; `GatewayKH/SHA256SUMS.txt` and `Story Studio/SHA256.txt` regenerated from the installers already on each drive. A fifth volume (`Recovery`) was named and skipped as not a kit. Verified afterwards: no `_trash/duplicate-kit-2026-10-27` on any drive, every drive's `KH Updater signing key backup` reported **untouched** by the script's own before/after digest.
> - **Still open.** No live rollout with `hub_rollout.py`; `./Tests/test_publisher_init.sh` (kh-updater) not run by this worker; the files-only update has not been proved on a real member Mac over a real manifest push (the native cases are the proof); no Mac has installed agent 0.1.1 from this DMG yet; publication, signing, installation and rollout are the coordinator's.
> - **Disk.** Superseded build scratch (the mode-broken stage, its DMG, the mode-broken runtime clone, the validator negative-control copies, the first gate evidence) was moved to `/build/Developer/_trash/hub-203-superseded/`, and 3,286 old `khu-*` test directories (513 MB) from `$TMPDIR` to `/build/Developer/_trash/tmpdir-khu-test-20261027/`. `$TMPDIR` went from 1.4 GB to 923 MB. `_trash/` is on the same volume, so this frees nothing until the owner empties it — `packaging/prune-build-scratch.sh` is the only sanctioned way to reclaim.

> **0.6.202 — Reference to Image, and the mflux 0.23.0 image runtime — 2026-10-26. PREPARED ON `release/0.6.202` over Hub main `2f21291` (0.6.201).** VERSION `0.6.202`. One branch merged with `--no-ff`, no squash: `feat/ref-to-image-20261026` (`e207832`). Release commits `abf9894` (source, version, notes, staging pin) and `8b60f09` (the `python-multipart` fix and the runtime it rebuilt). **Nothing is pushed, published, signed, notarised, installed or rolled out, and no Gateway was called.**
> - **Reference to Image.** `POST /api/generate/edit` is multipart with one `images` part per reference plus the lane's own fields. The worker validates count, readability and media type, stages uploads under `<data>/inputs/<job id>/`, deletes them at terminal, and records `mode: "edit"`, `reference_count` and `reference_digests`; the broker resolves every reference in order, refuses a count outside the published window, refuses a worker that does not advertise `edit`, and republishes `reference_count`. FLUX.2 Klein 4B MLX 4-bit (`Flux2KleinEdit`) and Qwen Image 2.1 Turbo Low-RAM MLX 4-bit (`QwenTurboEdit` over mflux's `QwenImage21Edit`) are **edit classes on the existing rows** (`edit_model_class`), not new rows and not new packages, so the qualified text-to-image lane is unchanged. The published window is 1-2 references at up to 1024 px: what was measured, not mflux's own cap of 10.
> - **Per-lane measured memory tiers.** `ImageModel.reference_memory`; `memory_plan(lane="edit")`; `plan_configuration()` names `default` / `bounded-staged` / `bounded-streamed`; each edit job records `reference_mode`; `/api/catalog` publishes `loading_modes.edit`; `loading_mode` `auto`/`low`/`full` applies to edit exactly as to text with no forcing rule. A Mac whose chosen configuration has no measured floor is refused, with the reason. Floors: Klein `default` 32.0 GB total / 14.0 GB free (only host measured), `bounded-staged` 12.0/8.0, `bounded-streamed` 12.0/9.5 — two references need 7.47 GB of working set, so 8 GB Macs are refused and the real minimum is 12 GB. Qwen `default` publishes **no** floor (24.2 GB peak, 9.5 GB of swap at two references); `bounded-staged` 24.0/14.8, which the 16 GB tier does not clear.
> - **The packaged Image worker did not start, and no gate could see it.** The new edit route declares FastAPI `Form`/`File` parameters, and FastAPI raises during route registration when `python-multipart` is missing — so `create_app` failed and the lane never came up in the packaged app. All 3125 unittest cases, the regressions, the voice suite and the native checks were green, because the test environment already had the package and the shipped runtime did not. Found by starting the worker from the staged bundle and posting a real edit (the first attempt died with `RuntimeError: Form data requires "python-multipart"`). Fix: `requirements.in` pins `python-multipart==0.0.32`, the lock carries it and **nothing else moved** (only that one line was added; new digest `8c202deb…`), `build.sh`'s embedded digest follows, the runtime was rebuilt, `IMAGE_RUNTIME_SHA256` and `research/st01-runtime-contracts.json` follow it, and `tests/test_image_bundle.ImageRuntimeFormSupportTests` keeps the dependency tied to the `Form`/`File` routes and the runtime lock.
> - **The Image runtime was rebuilt here, from the pinned archive.** The previous worker could not build it because the pinned CPython archive was absent; this worker fetched `cpython-3.12.14+20260901-aarch64-apple-darwin-install_only.tar.gz` from `https://github.com/astral-sh/python-build-standalone/releases/download/20260901/` and verified `3ee3ee547cedfeb7c2b16b2b7156039f7b470bb8f857e226fd3d2eb11db83c76` before extracting, plus the pinned mlx-serve 26.10.1 archive (`e53056e48…`), both into this worktree's own `.build/downloads`. `packaging/image-runtime/build.sh` then ran its own stages (sync from the lock, rpath sanitising, Mach-O audit, manifest, mlx-serve leaf, relocation smoke and a second smoke on the final tree) and exited 0. Shipped packages: **python 3.12.14, mflux 0.23.0, mlx / mlx-metal 0.32.4, pillow 12.3.0, urllib3 2.8.0, python-multipart 0.0.32**, 65 distributions; `smoke_imports.py` imports all six registered classes including `Flux2KleinEdit` and `QwenTurboEdit`, and `runtime_packaging.py verify` passes on the built tree and again on the mounted image. **Digest `b720c5bc473aaf13179c70bb1c1faa5715bfd87e0fa7e26fa43dc1d3f1794fef`** (0.6.201: `c5ac478f…`).
> - **The difference from 0.6.201's image runtime is the lock, and nothing else.** File-level comparison of the two manifests: 31 distributions changed version (mflux 0.20.0→0.23.0, mlx/mlx-metal 0.32.2→0.32.4, urllib3 2.7.0→2.8.0, transformers 5.17.0→5.20.2, torch 2.14.0→2.14.1, numpy 2.5.3→2.5.4 and the rest of the closure) and `python-multipart` was added; every added/removed/changed path is inside `lib/python3.12/site-packages/`, `licenses/python/` (their licence records), `manifest/`, or is one of mflux 0.23's two new `bin/mflux-generate-*` entry points. **`mlx-serve/` is byte-identical**, and no file disappeared except the replaced distribution versions. The old tree (`c5ac478f…`) is named in `IMAGE_RUNTIME_SUPERSEDED` so a build handed it says what it is.
> - **Voice and YuE2 did not move.** `runtimes/voice` `bae1132a740513873beff8c4fef2d4fa6391dc8cc6dad1fda5a9c0a73aceb59d` and `runtimes/music-yue2` `75465dc4f1621423511f2500f6d6dec8b7cf3248ce60fc07895350aac41d4fb4` are digest-identical to 0.6.201's staged app; `runtime_packaging.py verify` passes on both from the mounted image.
> - **Gate / build (this release worker, worktree `_worktrees/studio-hub-kh/release-0.6.202`, runner `.build/hub-offline-gate-202.py` — the accepted 0.6.201 runner with only `ROOT` and `EVIDENCE` changed).** Offline unittest GATE RESULT: run=3125 skipped=15 failures=0 errors=0 (exit 0; 0.6.201 was 3084 — the 41 new cases are the reference lane, the edit routes, the broker reference handling, the tier guard and the two `python-multipart` guards). Hub regressions 439 passed (exit 0). Voice 1016 passed / 28 skipped (exit 0). Native build and its boundary, API, secret-masking, view and lifecycle checks passed (exit 0). All four stages were run a second time on the final source, after the `python-multipart` fix, and passed with the numbers above. The runner now lives in the repo as `scripts/hub_offline_gate.py` (`--root` / `--evidence`, refusing an existing evidence directory), so a release no longer copies a per-version `.build/hub-offline-gate-<n>.py`.
> - **Artifact (this commit).** `dist/StudioHub-0.6.202-8b60f09.dmg` in this worktree, **1,421,014,016 bytes**, sha256 `08b09030c05f29d4b0088c231a5faab76bd78c765452625f3bf38fedcdf4da4d` (0.6.201: 1,418,353,152). Mounted read-only at `/tmp/shub-202-dmg` and detached afterwards (only that mountpoint was touched; no `/Volumes/KH-STUDIO-*` was touched). Mounted app tree digest `cd068fa2…` equals the staged app and its recorded tree. `HUB_RELEASE_ARTIFACTS` passes: all three runtimes (`voice`, `image`, `music-yue2`) and all nine worker entry points are present. There is **no top-level `khu-agent` component**; the kit rides inside `components.hub.khu_agent` (agent 0.1.0, kit manifest `3b83eb44…`, the 0.6.201 kit — 0.6.202 changes nothing about the agent). `khu-agent/publisher.json` is JSON-equal to the owner's source, there is no `private.key` anywhere in the bundle, `development_build` is false, `implementation_commit` is `8b60f09` and `version` is `0.6.202`. The **0.6.198, 0.6.200 and 0.6.201 installers all ACCEPT** the mounted app, and all three also accept the installed 0.6.201 app (`d8b5cd4`), so the check is not vacuous. The previous 0.6.202 DMG built before the fix was preserved by `make_dmg.py` under `_trash/dist/`.
> - **The edit job ran through the packaged worker, not the live Gateway.** Started `.build/staged/Studio Hub.app/Contents/Resources/image_worker.py` with the bundle's own `runtimes/image/bin/python3`, a private data dir, a private Hub token, `--offline`, and `loading_mode.json` = `low`; the Klein package was copy-on-write cloned from the ATLAS kit library into a disposable temp library (never the real library in place). One `POST /api/generate/edit`, aspect 1:1, 4 steps, seed 20261026, **two references** at 1024 px: `state: done`, `reference_count: 2`, `reference_mode: bounded-staged`, `memory_mode: low-ram`, hooks `encode-then-free` / `free-transformer-after-loop` / `bounded-vae-decode`, 1024x1024, **106.57 s**, **peak MLX 5.76 GB**, swap **3231.5 → 3215.5 MB (−16.0 MB, no growth)**, one PNG written. Receipt `.build/gate-evidence-202/image-edit-job-0.6.202.json`; the PNG is preserved at `.build/gate-evidence-202/image-edit-output-0.6.202.png` (sha256 `a3197472…`). Both are gitignored scratch; the durable facts are the ones above.
> - **Still open.** No physical 8 GB Mac has run an edit (the tier is refused on measurement); no host above 32 GiB has run Qwen edit's unbounded configuration, so it still publishes no floor; a Gateway-to-Hub end-to-end edit has not been run (this job proves the Hub's packaged worker and its route, not the Gateway half); the Klein `default` configuration's swap growth on a host with other apps resident is explained but not eliminated; publication, signing, installation and rollout are the coordinator's.
> - **Disk.** This Mac was at 5-10 GB free through the build; the coordinator moved idle `$TMPDIR` scratch to `_trash`, and this worker's own build scratch (superseded stages, the pre-fix runtime) was moved to `_trash/` under this worktree too. `_trash/` is on the same volume, so it frees nothing — `packaging/prune-build-scratch.sh` is the only sanctioned way to actually reclaim it.
> - **Fleet rollout tool — owner, 2026-10-26: *"it shouldn't block the rest ... we can just fan it
>   out."*** `scripts/hub_rollout.py` (+ `scripts/test_hub_rollout.py`, no network) fans one release to
>   every site at once: per-site bounded gates, busy/unreachable sites retried on their own, silent
>   members as warnings, one accepted POST per site, no republish on a rerun, and the owner's
>   `local_paused_at` read and recorded but never written. It supersedes the paced one-site-at-a-time
>   driver for rollouts; it has **not** been run against the live fleet.

> **0.6.201 — a Renders tab, a render named by its channel and episode, and a Voice runtime without tests — 2026-10-26. PREPARED ON `release/0.6.201` over Hub main `6e98723` (0.6.200).** VERSION `0.6.201`. Three branches merged with `--no-ff`, no squash: `feat/render-tab-20261026` (`f6c2bde`, `7f7e608`), `feat/voice-runtime-slim-20261026` (`70f5f2c`) and `feat/kit-khu-20261025` (`8e991d3`). Native source moves (`native/ShellCore.swift`, `native/StudioAPI.swift`, `native/StudioViews.swift`), so the app binary is rebuilt. **Nothing is pushed, published, signed, notarised, installed or rolled out, and no SSD drive was written.**
> - **Renders tab.** `components/hub/backend/render_history.py` is a read-only read model over `render_jobs` in `hub.db`; `components/hub/renders.py` publishes `GET /api/hub/renders` (this Mac, and on a controller every joined Mac), `GET /api/hub/renders/{id}` and the per-Mac `GET /api/hub/fleet/nodes/{id}/renders`. A Mac that does not answer is named as not answering — it never fails the page and never reads as "that Mac rendered nothing"; `fleet_nodes.read_node` is a bounded read-only dial so a history page cannot fill a node's check-in queue. Each length, wall time, average and speed is a measured number or absent, never `0` for "the Hub does not know"; `unknown_duration` counts finished renders whose length could not be stated; only a succeeded render contributes output minutes. `render_jobs` gains `started_at` (when the render process was bound), with the row's existing settlement time as the other end. Native: a Renders page in Library (totals, newest-first list, detail sheet that plays the file from disk while this Mac holds it and otherwise says it went back to Story Studio). Renders accepted before this build were never recorded and the empty state says so.
> - **A named render.** Story Studio sends an optional, display-only `display: {channel, episode}` in the closed final-render request envelope; the Hub stores it (migrated column `display_json`, NULL for a row accepted before this release) and publishes it on each row. Nothing dispatches, routes, hashes or accepts on it. A row with no name keeps being titled by its Hub job id — none is invented.
> - **The Voice runtime ships no test files, and staging refuses a tree it could not clean.** `packaging/voice-runtime/runtime_packaging.py strip-tests` removes wheel test trees, `conftest.py` / `pytest.ini` / `tox.ini`, `test_*.py` / `*_test.py` and pytest/pluggy/iniconfig; it rewrites `manifest/runtime-files.json` and refuses a tree it could not clean instead of reporting success. `build.sh` runs it before `audit` and `manifest`, and `stage_voice.stage()` runs the same sweep on the copy, because `.build` trees are reused between releases — a build-time sweep alone cannot clean one that was already built. **A name is not evidence:** anything the remaining sources still import is kept (`mpmath/__init__.py` imports its `tests` package, `jinja2`'s `tests.py` is a runtime module, `scipy/stats/_page_trend_test.py` *is* `page_trend_test`, and `torch/_dynamo/trace_rules.py` imports `torch._inductor.test_operators` at run time — removing that one by filename broke `AutoTokenizer` and with it Whisper transcription). The previous release's proof was taken on the shipped 0.6.200 runtime: 3,209 of 34,895 paths removed (41.0 MB), 31,685 of the 31,686 remaining paths byte-identical by sha256, size, mode and symlink target, the only difference being the rewritten manifest.
> - **`feat/kit-khu-20261025` carried no new kit behaviour.** The branch is 16 commits behind main and its production files (`packaging/kit-khu/`, `kit-restructure.sh`, `release_kit.py`, `kit_stamp_index.py`) are byte-identical to the `5446045` variant 0.6.199 already merged, so this merge added only `tests/test_khu_kit.py` (1,015 lines): the four-folder drive layout, the README-only layout when the publisher key is absent, the environment-variable form of the same two inputs, the signing-key backup folder a run leaves byte-identical and never names in a README or checksum, and the installer that enrols and starts the agent with the config the Hub itself writes. The `release_kit.py` / `kit-restructure.sh` defaults and the 0.6.199 kit behaviour are unchanged.
> - **Gate / build (this release worker, worktree `_worktrees/studio-hub-kh/release-0.6.201`).** Offline unittest GATE RESULT: run=3084 skipped=15 failures=0 errors=0 (exit 0; 0.6.200 was 3006 — the 78 new cases are the Render-tab read model, routes and native views, the named-render envelope, the Voice-runtime sweep and the kit tests). Hub regressions 435 passed (exit 0). Voice 1016 passed / 28 skipped (exit 0). `bash native/build.sh` plus its API, secret-masking, view and lifecycle checks passed (exit 0). Each stage ran alone, one at a time, while a Story Studio release gate was running on this Mac; that serialisation was conservative — Hub test modules are allowed alongside a Story gate (Story Studio AGENTS §10, owner 2026-10-24: "do that whenever possible"), and only Story's own suites must not run beside its gate. Evidence: `.build/gate-evidence-201/`.
> - **Artifact (this commit).** `dist/StudioHub-0.6.201-d8b5cd4.dmg` in this worktree, 1,418,353,152 bytes, sha256 `0cf14972242248a9a7d9853520a11d6ef974f5e9f3e2bad522ec4f7100345fab` (0.6.200: 1,420,750,336). Staged and mounted: every Resources entry, all three runtimes (`voice`, `image`, `music-yue2`) and all nine worker entry points are present; `runtimes/image` `c5ac478f…` and `runtimes/music-yue2` `75465dc4…` are digest-identical to 0.6.198 and 0.6.200; `runtimes/voice` is `bae1132a…` against 0.6.200's `6854e580…`, the difference being the removed test files, and `runtime_packaging.py verify` and `smoke_imports.py` both pass on it. The mounted app tree digest `27a6a313…` equals the staged app and its recorded tree. `khu-agent/publisher.json` is JSON-equal to the owner's source, there is no `private.key` (only public certifi CA bundles), `development_build` is false and `implementation_commit` is `d8b5cd4`. The installer already on every 0.6.198 Mac (compiled from `20670dd:native/UpdateClient.swift`) and the 0.6.200 installer both **ACCEPT** the mounted app, and both also accept the installed 0.6.200 app, so the check is not vacuous. The kit rides inside `components.hub.khu_agent` (agent 0.1.0, kit manifest `3b83eb44…`), so no top-level `khu-agent` component is listed.
> - **What "the Voice runtime ships no test files" means here.** The sweep removed the pytest 9.1.1 / pluggy / iniconfig distributions, `bin/pytest`, all 9 `conftest.py` files and 1,868 of the 2,051 `test_*.py` / `*_test.py` files; 228 paths (39.1 MB) went from the copy staged for this release, and the stage reported that count itself. 183 test-named modules remain, all inside packages the remaining sources still import (`mpmath`'s `tests` package and its `runtests()`, jinja2's `tests.py`, `scipy/stats/_page_trend_test.py`, `numpy/_core/tests`, `jsonschema/tests`, `accelerate/commands/test`, torch Dynamo's `test_operators`) — deleting them is what once broke `AutoTokenizer` and Whisper. `test_only_hits()` re-scans the staged tree and returns none, and `stage()` refuses a tree that still holds one.
> - **SSD kit DRY run — nothing written, nothing unmounted.** `DRY=1 KHU_AGENT_KIT=.build/khu-agent-kit KHU_PUBLISHER_JSON=~/Library/KHUpdater/publisher.json bash packaging/kit-restructure.sh` exited 0 and named every write for ATLAS, NOVA, ORICO and UGREEN: a `Video Generation Kit/KH Updater/` folder carrying `Install KH Updater.command`, `khu-install.py`, `apps.json`, `README.md` and a staged `khu-agent/` built from the two named inputs; the four kit readmes re-copied from `packaging/kit-readmes/`; each drive's existing `GatewayKH/README.md` moved (not deleted) to `_trash/duplicate-kit-2026-10-26/GatewayKH-kit-README.md`; `GatewayKH/SHA256SUMS.txt` and `Story Studio/SHA256.txt` regenerated from the installers already on each drive. A fifth volume (`Recovery`) was named and skipped as not a kit. Verified afterwards: no `KH Updater` folder, no `_trash/duplicate-kit-2026-10-26`, `_trash` entry counts unchanged, and all four `KH Updater signing key backup` folders still present and byte-untouched.
> - **Still open (coordinator).** Main integration, publication and rollout of 0.6.201, the SSD kit's real write to the four drives (a DRY run was taken, nothing was written or unmounted), and a Gateway-to-Hub remote render matching a local one.
>
> **Reference to Image — the Hub half, and the mflux 0.23.0 image runtime — 2026-10-26. PREPARED ON `feat/ref-to-image-20261026` (worktree `_worktrees/studio-hub-kh/ref-to-image-20261026`), rebased onto main `2f21291` (0.6.201).** NO version bump, NO push, NO merge, NO DMG, and no Hub restarted. The Gateway half is `gateway-kh 431f04db` on the same branch name; the two contracts match (capability token `edit`, `images` parts in order, `reference_count` echoed in `terminal_result`, limits from this row).
> - **What the Hub side does.** A reference job is `POST /api/generate/edit` multipart: one `images` part per reference plus the lane's plain fields. The worker validates count/readability/media type and stages the uploads under `<data>/inputs/<job id>/`, deletes them at terminal, and records `mode: "edit"`, `reference_count` and `reference_digests`. The broker resolves every reference in order, refuses a count outside the worker's published window, refuses a worker that does not advertise `edit`, and republishes `reference_count`. Edit items are admitted on the lane's own floors (`memory_admission` reference rung), never the model's text-to-image floors.
> - **Two defects fixed on the way.** `_multipart_fields` dropped `aspect_ratio`; it forwards it and `resolution` now, which fixes a silent 1:1 → 16:9 reference render. A model row can carry a second class (`edit_model_class`) without a second package, so the qualified txt2img lane is unchanged.
> - **Each edit lane has its own measured memory tiers** (owner, 2026-10-26: big Macs at full speed, only small ones slow; `loading_mode` `auto`/`low`/`full` applies to edit exactly as to text, with no forcing rule). `ImageModel.reference_memory` is the lane's own `MemoryPolicy`; `memory_plan(lane="edit")` reads it, `plan_configuration()` names the result (`default` / `bounded-staged` / `bounded-streamed`), every edit job records it as `reference_mode`, and `/api/catalog` publishes the lane's answer in `loading_modes.edit`. A Mac whose chosen configuration has no measured floor refuses the lane (and says so) instead of starting a render it cannot hold.
> - **Klein edit, measured through the worker (mflux 0.23.0, 32 GiB M2 Max, 4 steps, 1024 canvas, references at 1024 px; 1 and 2 references):** `default` 98.03 s / 151.56 s at 12.01-12.02 GB MLX peak (the quiet batch; four earlier runs under heavier load, one beside the full Hub test gate, are in the receipt with their host facts); `bounded-staged` 72.39 s / 101.24 s at 5.69-5.76 GB; `bounded-streamed` 69.13 s / 105.56 s at 5.69-7.47 GB. Bounded was both smaller **and faster** here. The default configuration's 1-reference runs grew this host's swap by 3.1-4.6 GB while other apps were resident, and every 2-reference run reclaimed it: the peak stayed at 12.0 GB with 18-24 GB free, so it is macOS paging idle pages during the render rather than the render exhausting memory. That figure is published, not rounded to zero. Published: `default` floor 32.0 GB (the only host measured), `bounded-staged` 12.0/8.0, `bounded-streamed` 12.0/9.5 (7.47 GB at two references is why 8 GiB Macs are refused — the real minimum is 12 GiB). Receipt: `qualification/evidence/image-edit-2026-10-26.jsonl`.
> - **Qwen Image 2.1 Turbo edit** is the second Reference-to-Image model on the same row and package (`QwenTurboEdit` over mflux's `QwenImage21Edit`, eight steps, guidance 1). Measured: `default` 154.51 s / 191.82 s with a **24.2 GB** peak (and 9.5 GB of swap at two references on this 32 GiB host), `bounded-staged` 162.26 s / 182.96 s with a 10.18-12.76 GB peak and no swap growth. The unbounded configuration therefore publishes **no floor** — a Mac that would select it is refused until a larger host is measured — and the bounded one publishes 24.0 GB total / 14.8 GB free, so 16 GB Macs do not clear it. That is the honest minimum this lane needs; it is no slower than the unbounded one.
> - **Runtime.** `packaging/image-runtime/requirements.in` 0.20.0 → `mflux==0.23.0` plus `urllib3>=2.8.0` and `pillow==12.3.0` (13.0.0 imports a 3.14-only symbol and takes mflux down on 3.12); relocked with `uv pip compile --generate-hashes` and `constraints-sonoma.txt` re-pinned to `mlx`/`mlx-metal` 0.32.4 (mflux 0.23.0 accepts `>=0.32.0,<0.33.0`, and 0.32.4 is the version every measurement ran on); `build.sh`'s embedded lock digest updated to `a732f134…`. The packaged runtime itself is not rebuilt here (the pinned CPython archive is absent) — that is release machinery.
> - **Regression proof on 0.23.0** (`qualification/evidence/image-runtime-0.23-2026-10-26.jsonl`): Klein txt2img 1280x720/4 steps 31.6 s at 9.79 GB; Z-Image-Turbo 9 steps 85.9 s at 10.02 GB (the row publishes 17.67 GB peak from 0.20.0 — 0.23.0 is smaller and faster); Qwen Turbo 8 steps 93.5 s at 5.91 GB (the row publishes no Turbo measurement at all, so this is its first). Nothing regressed. The 0.22-era Qwen weight-renaming adapter had to go: mflux 0.23.0 removed `Qwen21Initializer._load_weights` and loads this pack natively, which is what the CLI proof ran.
> - **Licence record fixed.** `packaging/capture_model_manifests.py` and `model-manifests/qwen-image-2-1-mflux-q4.json` now say `qwen-research` (the weights) instead of `Apache-2.0` (the mlx-community conversion's licence). The licence is not part of `manifest_digest`, so installed receipts stay valid.
> - **Still open.** No physical 8 GB Mac has run Klein edit (the 8 GiB tier is refused on measurement, and two references need 7.47 GB of working set); no host above 32 GiB has run Qwen edit's unbounded configuration (so it publishes no floor); the Klein `default` swap growth is explained but not eliminated (this host always had other applications resident); the runtime DMG rebuild, an end-to-end Gateway edit job, and any publish/rollout are not done here.

> **0.6.200 — the broken 0.6.199 installer rebuilt, and installable on 0.6.198 Macs — 2026-10-26. PREPARED ON `release/0.6.200` over Hub main `b7c12d9` (0.6.199).** VERSION `0.6.200`. The source is unchanged from 0.6.199; only the packaging was wrong. Two defects were found after 0.6.199 was published to site 1100 and before any Mac installed it; this release has the same features as 0.6.199 and fixes both packaging faults.
> - **Incident 1 — the published 0.6.199 DMG carried no Image or YuE2 runtime.** `dist/StudioHub-0.6.199-f7f9fab.dmg` was 592,715,776 bytes (0.6.198: 1,417,708,032) and its `Contents/Resources/runtimes` held only `voice`. `image` (1.3 GB) and `music-yue2` (698 MB) were absent, and so were `image_worker.py` and `yue2_runner.py`. The helper driving `packaging/stage_voice.py` passed only `--hub --khu-agent-kit --khu-publisher-json`: `_stage_image` saw `image_runtime is None`, Music's YuE2 branch saw `yue2_runtime is None`, both returned without an error, the manifest was internally consistent with the smaller bundle, and the build exited 0. The app installed and then could not start Image or sung-song Music. 0.6.198's invocation (this worktree's predecessor) passed `--image-runtime .build/image-runtime` and `--yue2-runtime .build/yue2-runtime`; 0.6.200 passes both again, plus the kit flags.
> - **Incident 2 — every 0.6.198 Mac refused 0.6.199 at manifest validation.** The installer that validates a release is the one **already installed**; every Mac runs 0.6.198's `ReleaseInstaller`, whose `runtimePaths` (commit `20670dd`) knows `voice`, `image`, `video` and `voice+music-overlay` only. 0.6.199's manifest listed a top-level `khu-agent` component, so `verifyDeclaredComponents` threw before downloading anything: *"The release manifest lists component 'khu-agent' with runtime 'khu-agent', which this installer does not know."* Adding the name to 0.6.200's own Swift cannot help — the old binary is what validates. The kit now rides inside the `hub` component's record (`components.hub.khu_agent`, `{agent_version, kit_manifest_sha256}`), which old installers already accept; `Resources/khu-agent/` is unchanged. `khu-agent` is still added to `runtimePaths` and to `COMPONENT_WORKERS` (with a `_stage_khu_agent_component` entry in `COMPONENT_STAGERS` that returns `None`) so a later release can list it as a component once the fleet's installers can read it; that split will also need the `khu-agent_worker.py` the validator derives, or a verifier change.
> - **The guard (incident 1).** `HUB_RELEASE_ARTIFACTS` in `packaging/stage_voice.py` declares the runtimes (`voice`, `image`, `music-yue2`) and worker entry points a Hub release ships. After the component stagers run and before anything is hashed, `stage()` refuses a Hub bundle that lacks any of them, names every missing artifact and the flag that stages it, and preserves the failed bundle under `_trash/failed-stages/`. `tests/test_voice_bundle.py::StagingTests.test_a_hub_stage_without_the_image_and_music_runtimes_is_refused` stages the 0.6.199 shape (voice only) and pins the refusal. A release that deliberately ships fewer edits the table, so the change is in the diff rather than in a forgotten flag.
> - **What the guard cannot catch.** A runtime present under the right name but built from the wrong contents (the Image and YuE2 content pins already do that); a missing model package (models live outside the app); a file altered after staging (the release manifest and the mount comparison do that); a DMG step that drops something staging had (the mounted image must be proved); and it does not validate installer compatibility — that is pinned separately below.
> - **Compatibility guard (incident 2).** `tests/native/fixtures/update-client-runtimes-0.6.198.json` records the fleet installer's runtime set, re-derived from commit `20670dd` by `tests/test_installer_knows_shipped_runtimes.py::FleetInstallerCompatibility.test_the_fixture_is_the_installer_the_fleet_runs`. That class refuses `tests/native/fixtures/release-manifest-0.6.199-f7f9fab.json` (the published 0.6.199 manifest) and pins that the stager writes no top-level `khu-agent`. `tests/test_voice_bundle.py::StagingTests.test_the_manifest_a_release_writes_installs_on_a_0_6_198_mac` stages a real bundle with a synthetic kit and applies the same rule to the manifest it wrote. It cannot catch a change the *older* installer makes that this rule does not model; the real Swift check was also run locally against a staged app (see the build entry).
> - **Build inputs and exact invocation (this release worker, worktree `_worktrees/studio-hub-kh/release-0.6.200`).** Runtimes: `.build/voice-runtime` (main repo, symlinked here), `.build/image-runtime` (digest `c5ac478f…`, the pinned mflux 0.20 tree), `.build/yue2-runtime` (digest `75465dc4…`). Agent kit built from kh-updater `f87a6f4` with `packaging/build-agent-kit.sh 0.1.0 .build/khu-agent-kit` (binaries hash-identical to 0.6.199's kit); publisher descriptor is the owner's `~/Library/KHUpdater/publisher.json` (never committed, never copied). Stage: `.build/voice-runtime/bin/python3 -B packaging/stage_voice.py --runtime .build/voice-runtime --native-shell .build/StudioHub --image-runtime .build/image-runtime --yue2-runtime .build/yue2-runtime --hub --render-authority descriptor-only --render-tools-dir /build/Developer/story-studio-kh/bundled/ffmpeg --khu-agent-kit .build/khu-agent-kit --khu-publisher-json ~/Library/KHUpdater/publisher.json --output .build/staged/Studio\ Hub.app`; then `packaging/make_dmg.py` (or `.build/make-dmg.sh`) for the installer. `--video-runtime` stays off (LTX-2.3 retired).
> - **Gate / build.** Offline unittest GATE RESULT: run=3006 skipped=15 failures=0 errors=0 (exit 0; 0.6.199 was 3001 — the five new tests are the guard, the staged-manifest installability proof and the three fleet-installer compatibility tests). Hub regressions 435 passed (exit 0). Voice 1016 passed / 28 skipped (exit 0). `bash native/build.sh` plus its API, secret-masking, view and lifecycle checks passed (exit 0). Build: the stage command above, then `packaging/make_dmg.py`. Evidence: `.build/gate-evidence-200/`; the artifact proof is recorded in the follow-up commit.
> - **Artifact (this commit).** `dist/StudioHub-0.6.200-a14ee3a.dmg` in the main checkout, 1,420,750,336 bytes, sha256 `9db6cff7c95a5dae485e4b36c12d4c72d1a845ef86870a2252afa2456a37c9ab` (0.6.198 was 1,417,708,032; the broken 0.6.199 was 592,715,776). Mounted and compared with the mounted 0.6.198: every Resources entry and all three runtimes are present (only `khu-agent` is added); `runtimes/image` `c5ac478f…` and `runtimes/music-yue2` `75465dc4…` are digest-identical to 0.6.198; `runtimes/voice` is `6854e580…`, identical to 0.6.199's tree and to 0.6.198's except 222 added pytest/_pytest/pluggy/iniconfig files (0 removed, 0 changed) — the voice gate's runner installed into `.build/voice-runtime`, so no product byte moved. The mounted app tree digest `e2e56b92…` equals the staged app and its recorded tree; `khu-agent/publisher.json` is JSON-equal to the owner's source; there is no `private.key` (only public certifi CA bundles); `development_build` is false and `implementation_commit` is `a14ee3a`. The installed 0.6.198 validator, compiled from `20670dd:native/UpdateClient.swift`, ACCEPTS the mounted 0.6.200 app, REFUSES the 0.6.199 app with the recorded message, and ACCEPTS the installed app. A file-level delta dry run on a throwaway clone of the installed 0.6.198 app (231 added, 17 replaced, 0 removed; `/Applications` untouched) produced a tree digest identical to the mounted 0.6.200 app, and the 0.6.198 validator accepted the result. `dist/StudioHub-0.6.199-f7f9fab.dmg` moved to `_trash/dist/studio-hub/`.
> - **Still open (coordinator).** Publishing 0.6.200 to replace the held 0.6.199 descriptor on site 1100 (another helper owns that publication), rollout, a Gateway-to-Hub remote render matching a local one, and the 0.6.198-voice-runtime difference noted below.
> - **Voice runtime digest moved between 0.6.198 and 0.6.199, and that is not a regression.** 0.6.198 shipped `runtimes/voice` `d3350a58…`; 0.6.199 shipped `6854e580…`, and 0.6.200 ships the same `6854e580…` as 0.6.199. The difference is `pytest`/`pluggy`/`iniconfig` installed into `.build/voice-runtime` for the voice gate; no product package changed. Image (`c5ac478f…`) and YuE2 (`75465dc4…`) match 0.6.198 exactly.
>

> **0.6.199 KH Updater agent active, SSD kit installer, fleet re-point — 2026-10-26. PREPARED ON `release/0.6.199` over Hub main `20670dd` (0.6.198).** VERSION `0.6.199`. Three tested branches merged cleanly: `feat/khu-in-hub-20261025` (3 commits: ab080f9, 1ed9150, 092522e), `feat/kit-khu-20261025` (8e991d3), and `feat/controller-repoint-20261025` (848dbec). Native source moves in the controller-repoint branch, so the app binary is rebuilt. **Nothing is pushed, published, signed, notarised, installed or rolled out.**
> - **The KH Updater agent becomes active when the publisher config is present.** `components/hub/backend/khu_agent.py` now handles re-enrolment when the publisher drops a Mac's token: it probes with `GET /commands` (no `capabilities` parameter, so it never refreshes `lastSeen`), preserves the dead config as evidence, and enrols again through the window-gated flow. The status endpoint reports `tokenRejected: true` when this happens. The kit is staged inside the Hub **only when both** `--khu-agent-kit` and `--khu-publisher-json` are passed to `packaging/stage_voice.py`; the owner's `~/Library/KHUpdater/publisher.json` is now present and bundled at build time (never committed).
> - **SSD kit carries the KH Updater installer for every Mac.** `packaging/kit-khu/` holds the installer (`Install KH Updater.command`), the apps config (`apps.json`), and the installer script (`khu-install.py`, 675 lines). `packaging/kit-restructure.sh` and `packaging/release_kit.py` lay out a `KH Updater/` folder on each drive when both `--agent-kit` and `--publisher-json` are passed.
> - **Fleet re-point without re-joining; leave/reset; editable Machine ID.** `POST /api/hub/fleet/leave` drops a Mac's controller credential and stops its check-in loop; `POST /api/hub/fleet/reset` wipes the machine identity so the next start is a fresh install. The Settings → Machine screen exposes an editable Machine ID field (`POST /api/hub/machine-identity`) that rewrites `machine_flags.json` and triggers a re-enrolment with the new name.
> - **Gate / build (this release worker, worktree `_worktrees/studio-hub-kh/release-0.6.199`).** Offline unittest GATE RESULT: run=3001 skipped=15 failures=0 errors=0. Hub regressions 435 passed (exit 0). Voice 1016 passed / 28 skipped (exit 0). Build: `bash native/build.sh`, then `packaging/stage_voice.py --hub --khu-agent-kit <kit> --khu-publisher-json ~/Library/KHUpdater/publisher.json --render-authority descriptor-only --render-tools-dir /build/Developer/story-studio-kh/bundled/ffmpeg`, then `packaging/make_dmg.py`.
> - **Still open (coordinator):** publish/rollout, a Gateway-to-Hub remote render matching a local one.
>
> **0.6.198 remote render envelope v2, and the KH Updater agent built into the Hub — 2026-10-25. PREPARED ON `release/0.6.198` over Hub main `b07c608` (0.6.197).** VERSION `0.6.198`. Two tested branches merged cleanly: `feat/render-envelope-20261025` (`7a6cee1`) and `feat/khu-in-hub-20261025` (`528b2a3`). No `native/*.swift` source moves, so the app binary is unchanged from 0.6.197; the shipped installer rebuilds the same native inputs. **Nothing is pushed, published, signed, notarised, installed or rolled out, and no remote render has been run.**
> - **Remote final renders accept real episode sizes.** `execution_bounds` gains `bounds_version` 2 (`components/hub/backend/render_profiles.py`): a `canvas_range` (`max_edge` 3840, `max_pixels` 8294400 — any even canvas up to 4K) replaces the single `canvas_pairs` entry, and `working_fps` is `[24,25,30,60]` instead of `[24]`; `max_pixel_frames` becomes 5,374,771,200,000 and work is `width × height × ceil(duration × fps)`. Every other fence, `production_eligible: false` and the codec/container handling are unchanged, and a version-1 profile (`canvas_pairs`) still validates. `packaging/render-authority/PROFILE.json` re-frozen (970 B, sha256 `ad3f1c9d47d5f594…`) and re-pinned in `packaging/stage_render_tools.py`; the same bytes are frozen on the Gateway side. The 24 fps-only bound was the unqualified candidate profile, never a technical limit.
> - **The KH Updater agent is built into the Hub and inactive until the owner's publisher exists.** `components/hub/backend/khu_agent.py` (new; `components/hub/backend/ed25519` for the pinned trust anchor) is a per-user bootstrap started by `create_app` and cancelled at shutdown; `GET /api/hub/khu/status` reports one honest state. It checks, installs, enrols and activates the agent from a staged kit, sending **no credential** on enrolment. The kit is staged inside the Hub **only when both** `--khu-agent-kit` and `--khu-publisher-json` are passed to `packaging/stage_voice.py`; handed one without the other, or neither, the build notes it on stderr and the release manifest carries no `khu-agent` component. The real publisher descriptor does not exist yet, so **this release ships without the kit and the agent loop reports it is waiting**. KH Updater remains the authority for app updates.
> - **Gate / build (this release worker, worktree `_worktrees/studio-hub-kh/release-0.6.198`).** Offline unittest GATE RESULT: run=2937 skipped=15 failures=0 errors=0 (exit 0). Hub regressions 435 passed (exit 0). Voice 1016 passed / 28 skipped (exit 0). Every test that starts a Hub ran against retained empty temp data/model roots (`hub-offline-gate-198.py`, a copy of 0.6.197's runner with `ROOT`/`EVIDENCE` moved); the release-gate environment carried `.build/voice-runtime`, `.build/test-deps` and `.build/prepare-kit.sh` locally, exactly as 0.6.197's accepted run did. Build: `bash native/build.sh`, then `packaging/stage_voice.py --hub --render-authority descriptor-only --render-tools-dir /build/Developer/story-studio-kh/bundled/ffmpeg`, then `packaging/make_dmg.py`; installer written to this worktree's `dist/`.
> - **Still open (coordinator):** main integration, publish/rollout, a Gateway-to-Hub remote render matching a local one, and dropping the owner's `~/Library/KHUpdater/publisher.json` (plus a `kh-updater` agent kit) before any release can ship the agent.
>
> **0.6.195 coordinator verification update — 2026-10-21. Still unreleased and unqualified.** Candidate `f4b3c75` full unittest gate exited 1: 2,855 run, 15 skipped, zero failures and three errors, all from the staging fixture executing a dataclass module before registering it in `sys.modules`. Regressions and voice were not run. The fixture now registers the module temporarily with `mock.patch.dict`; focused `test_voice_bundle.StagingTests` verification exited 0: seven run, zero skipped/failures/errors. A fresh complete gate on the corrected commit remains required. No 0.6.195 installer, rollout or live fleet proof is claimed.
>
> **0.6.195 fleet final-render executor/leaf capability — 2026-10-21. CANDIDATE ONLY; UNQUALIFIED, UNRELEASED, NOT FULLY GATED; NO LIVE PROOF.**
> Prepared on `feat/fleet-render-finish-20261021` (worktree `_worktrees/studio-hub-kh/fleet-render-finish-20261021`) over base `cd18d494` (0.6.194 closeout). This metadata lane changed only `VERSION`, `CHANGELOG.md`, `release-notes.md` and this file; it ran no test, compiler, build, network, render, packaging or deployment step and made no commit. **Nothing is committed, built into a DMG, installed, published or qualified; no render has executed; no Gateway end-to-end or live fleet evidence exists.** No `native/*.swift` moves, so the native binary is unchanged if built. The coordinator's `stage_voice.py`, packaging pins and `packaging/render-authority/` descriptors were left untouched.
>
> **Gate evidence (coordinator runs, not this lane).** Focused Hub service/executor gate b4me9nf1k: actual exit 0, GATE RESULT 60 run / 0 skip / 0 fail / 0 error, wrapper accepted. Hub-regression wrapper b6yl5ilp4: settled, actual exit 0, GATE RESULT 1 regression case, wrapper accepted; underlying `.build/st02-hub-regression.log` line 32: 435 passed, 2 warnings, 35.88 s. Full gates, independent review, packaging/DMG, installed pins, a Gateway-to-Hub render and hardware qualification remain open.
>
> **Capability.** A Mac with installed render authority publishes a `video.final_render` worker (`studio_type: render`, local id `hub-final-render`); the controller republishes each joined leaf as `<node_id>:hub-final-render` from one bounded authenticated read of the node's `GET /api/hub/final-renders/capability`. A leaf without authority is shown honestly with zero slots, `ready: false`, `drained: true`. Selection is by `physical_machine_id`.
>
> **Closed authenticated surface.** `PUT/GET /api/hub/final-renders/blobs/{sha256}` (exact `Content-Range`, 8 MiB chunks), `POST /api/hub/final-renders` (1 MiB closed request, `Idempotency-Key`, exact body hash for replay), status/cancel by id or idempotency key, `GET …/{job_id}/output` (explicit range, exact `If-Match` receipt ETag), `POST …/{job_id}/ack` (releases asset references). Joined nodes get the same operations relayed at `/api/hub/fleet/nodes/{node_id}/final-renders/...` under a fixed header allowlist and validated segments. Only the exact machine credential header is accepted; bearer, cookie, proxy headers and caller-supplied namespaces are refused.
>
> **Execution and durability.** `render_executor.py` claims durably, compiles the deterministic final pass (`final_render_compiler.py`) and runs the pinned universal ffmpeg/ffprobe payloads under the authority's RSS, wall, free-memory and output bounds; it loads no model and calls no Gateway. Contract stages: base video plus premixed audio, overlay layers, colour grade, vignette, film grain, aged-archive film, letterbox, presentation frame, logo. SQLite ledger rows, one compute claim across scopes, exact process-group cancellation that retains the physical lease until the stop is durable, restart recovery that marks an unprovable launch/process `uncertain` and never relaunches it, and an output receipt binding request/job/attempt/profile/tools/compiler/qualification.
>
> **Scheduling and authority.** Render joins the broker's existing ranked FIFO and pause/Lite/drain/quarantine/machine-enabled/exact-owner gates; durable physical holds are restored before dispatch; activity appears in the existing snapshot. Production authority is off by default and requires `render-authority/enabled/<hardware_class>.json` for this Mac's exact hardware class; otherwise only a live one-use qualification intent can run a qualification render with `production_eligible: false`.
>
> **Encode authority is compiled from the same pinned compiler.** `render_encode.py` enumerates the contract's uncapped container/codec/effort/quality grid, compiles each combination through `final_render_compiler` and keeps only tiers the executor's own rule accepts (label, exact encoder in argv, no dropped burned-in features). Workers publish that closed `encode` document in capabilities, and every live qualification candidate carries its own, since a qualification-only worker has no production authority to describe. Admission compiles the real packet and refuses an unsupported encode before any asset reference, permit consumption or ledger row; the executor applies the same rule again at launch. Nothing is parsed from tier names or copied from the encoder table, so advertisement cannot drift from execution. A Gateway that cannot see a common document treats the answer as unknown and the caller keeps the render local.
>
> **Retirement is move-only and lease-aware.** `render_assets.retire_stale` renames aged, unreferenced uploads and their published bytes into `_trash/retired/<UTC date>` (or the Hub's configured Trash Folder), never deleting. Referenced, being-read, `recovery_required`, pending-publication or younger-than-grace assets are retained. `render_service._retire_terminal_attempts` moves attempt folders only for a durably failed/cancelled job after the grace; `_retire_output` retires an `output_ready` attempt only after its ACK, taking a short transfer lease, re-reading the row and refusing while a reader or ACK lease is held. An unavailable Trash root retains rather than relocating, the root is created only inside an existing real directory, and the fallback is announced once.
>
> **Qualification lifecycle.** A live `qualify.json` intent is the only non-production admission: the permit is bound at acceptance to the exact request-body hash, owner scope, qualification digest and dispatch identity, and is spent only inside the accepted row. A measured receipt is written only by a completed qualification run, and production is granted only by an operator copying it byte-for-byte to `enabled/<hardware_class>.json`. No receipt, enabled record or hardware run exists for this candidate; the re-frozen descriptors are worktree bytes, not installed pins.
>
> **Unqualified, stated concretely.** The packaged candidate `PROFILE.json` is `retained-classic-no-font-candidate-v1`, 1920x1080/24, up to 10,800 s, four assets, eight operations, with `production_eligible: false`, `hardware_qualification_record_sha256: null` and `fidelity_record_sha256: null`. No hardware qualification receipt exists, no render has run, no Gateway end-to-end exchange has been proved and no Mac is enabled. Tests were added (`tests/test_render_*.py`, `tests/test_final_render_compiler.py`, `tests/hub_regression/cases/test_render_encode.py`, `tests/hub_regression/cases/test_capability_supply.py` including the qualification-candidate `encode` case, `tests/test_fleet_nodes.py`, `tests/fixtures/final-render-compiler-v1.json`); **this lane ran none of them** — the coordinator's b4me9nf1k and b6yl5ilp4 runs are the only gate evidence. Prior renderer-owned integration in the same commit (not authored by this metadata lane): `components/hub/backend/activity.py`, `auth.py`, `broker.py`, `packaging/stage_voice.py`; the `packaging/render-authority/` descriptors are included as prepared.
>
> **Still open (coordinator):** full gates, independent review, native/build decision, packaging/DMG with `--render-tools-dir` and the chosen `--render-authority` mode, a Gateway-to-Hub end-to-end render, hardware qualification and receipt, main integration and paced rollout. VERSION `0.6.195`.
>
> **0.6.194 gated, artifact accepted, rolled out to all eight sites — 2026-10-21.**
> Product commit `a152792faedf2d7d8d03a0201ce69a3aab9cf98f` (tree `ff8add13…`); this handoff entry
> is a later docs-only commit on top of it, so the product commit and the installer identity are
> unaffected by any later handoff edit. Full gate on `a152792`, exit 0, `source_unchanged` and
> `binary_unchanged` both true (`shops/hub-0194-release/gate-acceptance-194.json`, proof
> `gate-194/gate-finish.json`): focused 14 run / 0 skip / 0 fail; unittest 2,600 run / 15 skipped /
> 0 failures / 0 errors; regressions 425 passed; voice 1,016 passed / 28 skipped. Artifact accepted
> and re-derived from the bytes on disk (`shops/hub-0194-release/artifact-root-verified.json`): six
> staged checks with zero skips, four packaged image imports, `hdiutil verify` exit 0;
> `dist/StudioHub-0.6.194-a152792.dmg`, 1,318,509,056 bytes, SHA-256
> `5ddad999acb74534f5628bdc2bbd665d4fae1696ee5031ea8d3570d587a3c580`, native binary reused from
> 0.6.193 (`275d967c…`, Python-only release); settlement `ARTIFACT-PASSED.settled.json`, state
> `passed`.
> **Rollout complete at all eight sites** (`This Mac → 1100 → 1200 → 1300 → 1400 → 1500 → 1600 →
> Shops`), observed completed 2026-10-21T05:57:42Z
> (`shops/hub-0194-release/rollout-completed-20261021.json`, source `status-fixed.json`): **60 dated
> per-site verified observations** (This Mac 1, 1100 11, 1200 11, 1300 11, 1400 11, 1500 5, 1600 5,
> Shops 5), eight accepted publications, no timed-out site — per-site observations are dated, not
> one simultaneous snapshot. **Three required 1600-site nodes were offline and skipped, never
> verified** — `studio-mac-1604-m1-8-256-hub`, `studio-mac-1605-m2-8-256-hub`,
> `terranes-1606-m1-16-512-hub`.
> **No repair claim for the three original never-connected Shops Macs** (`grace-2fs-imac-hub`,
> `kt-ksw-imac-blue-16-512-001-hub`, `office-imac-blue-m1-8-256-hub`): they were not in the
> reachable required set and stay `never_connected` with `last_status_at` null; a controller-only
> rollout cannot bootstrap them, so they need local/on-site inspection
> (`shops/SHOPS-POST194-CONNECTION-RESULT.md`). Live rollout state:
> `shops/hub-0194-release/status-fixed.json` / `driver-fixed.json`.
> **Hub cleanup is complete** — the two Hub worktrees `release-194` (`a152792`, clean) and
> `shops-checkin-20261021` (`439584a`, 14 paths) were moved to `_trash/worktrees/studio-hub-kh/`
> with branches kept, their diff and untracked files preserved and hash-verified
> (`app-closeout-20261021/hub-cleanup-preserved/retirement.json`, 2026-10-21T05:59:42Z); the
> retained `release-193` Trash and existing Trash were left untouched. Receipts directory:
> `~/Developer/_orchestra/app-closeout-20261021/shops/hub-0194-release/`.
>
> **0.6.194 a joined Mac can mirror its controller's release, and a node's own status call counts as life — 2026-10-21. INTEGRATED SOURCE ON `release/0.6.194`; GATES PENDING; no commit, build, install or publish.** Integrated in worktree `_worktrees/studio-hub-kh/release-194` over canonical main `439584a` (0.6.193) from `fix/shops-checkin-20261021`. No `native/*.swift` moves, so the app binary is unchanged (0.6.193's binary reused). Owner request: fix the three Shops Macs that never connected (`office-imac-blue-m1-8-256-hub`, `grace-2fs-imac-hub`, `kt-ksw-imac-blue-16-512-001-hub`). Two source fixes: (1) `release_sync.controller_target()` required `role == "agent"`, but a join records the adopted credential and the controller address without the role, so a joined Mac (default role `standalone`) never mirrored its controller's release and could not pull the build that makes it visible — it now keys on `fleet_nodes.membership()` (adopted credential + validated address) and excludes a `controller`; (2) `GET /api/hub/fleet/nodes/{id}/status` was authenticated but recorded nothing, so a Mac that could reach its controller still read `never_connected` — a node's own authenticated call now stamps `last_status_at` (and `last_seen_at`, forward-only) as presence and nothing else, never overwriting a `check-in`/`probe` reason (so `dial_is_failing` and the pull transport are unchanged), and `never_connected` now clears on a check-in, a successful probe, or a status ask. Regressions added (not run): `tests/test_release_sync_target.py`, `tests/test_fleet_status_presence.py`. **Correction to 0.6.193:** its entry claimed the three Shops Macs were "switched on and checking in every minute" / "calling home every 60 s" — that is **unsupported**: no 0.6.185/192/193 controller build recorded a call from them, and their outbound reachability and whether they were running were never proven. **Runtime outcome for the three stays UNRESOLVED** — current evidence is a failed reverse dial and no recorded call; code and a controller-only rollout cannot resolve it, so the three need local/on-site checks. Diagnosis: `_orchestra/app-closeout-20261021/shops/SHOPS-CHECKIN-DIAGNOSIS-20261021.md`. Board: `ST-SHOPS-BOOTSTRAP-20261021`.
> **0.6.193 the fleet pull channel: a Mac the controller cannot dial still works — 2026-10-20. PREPARED AND GATED ON `release/0.6.193`; main integration and rollout REMAIN WITH THE COORDINATOR.** One tested feature branch was merged over verified main `bc84fe2` (0.6.192) in worktree `_worktrees/studio-hub-kh/release-193`: `pull-fleet` (`efbf399`), auto-merging cleanly with no conflict. `native/StudioAPI.swift` moves, so the app binary is rebuilt. Design note: `docs/fleet-pull-channel.md` — **read its rollout order: controller first, then nodes, then the Gateway.** What is in it: three Shops Macs reach their controller through Tailscale **node sharing**, and sharing works one way (the shared Mac can dial out, the sharer cannot dial back), so `fleet_nodes.probe()` — which accepted only a dial as evidence of life, with `note_seen` stamping `last_seen_at` only from one — left all three reading `never_connected` for ever, **measured 51 hours**, while they were on and calling home every 60 s; the roster address was written once at join and never again, so a Mac that moved could not correct it. The node's own authenticated call home is now the channel: **`POST /api/hub/fleet/nodes/{id}/checkin`** (Fleet token **or** the node's own credential, which may check in only for its own id) stamps `last_seen_at` with the reason kept distinct (`last_seen_via` `probe` | `check-in`), carries the Mac's current address, version, Lite Mode, startup and its own verbatim answer to `GET /api/hub/resources?local_only=true` — the exact route the probe dials — clears `never_connected`, and hands back queued requests; a node is check-in alive while `last_checkin_at` is inside `CHECKIN_FRESH_S` (180 s). Every command carries an idempotent id the node acks (a command delivered twice runs once) and is **exactly the HTTP request the dial would have sent**, replayed on the node's own Hub with its **own** token — the controller's Fleet token is stripped and never reaches the node; a command retires on ack or `expires_at` (900 s). **Dialling stays the first choice:** `fleet_pull.PullTransport` is installed on the controller's shared outbound client (`monitor._client`), so job dispatch, job batches, shared-voice fleet-sync, fleet-token rotation and model-memory calls reach the queue **with no broker, Chat or Subtitle call site changed** — it dials first and only a transport error against a check-in-alive node reaches the queue, `dial_is_failing()` (from `last_seen_via == check-in`) skips a dial already known to be failing rather than giving it a job's whole timeout, and a successful dial puts everything back on `probe`. Settings changes answer **`202`** with `queued: true` and the command id (the Mac applies it on its next call home); work the caller expects an answer for is waited for. `peers.one` takes the check-in snapshot when the dial is known to fail, so the scheduler sees a Mac that is up rather than one that reads online and is never given work. **Either side may ship first:** a controller older than this build answers **404** and the node does nothing at all, keeping `GET …/status`. The Gateway (0.2.194) carries `node_via` on the flat machine row and recognises `202 + queued` in `_node_post`. Left dial-only deliberately, with reasons in the note: `peers.py`'s dead relay helpers, and `delivery.py`'s artifact receipt ack and `transcription_jobs.py`'s ack. REMAINING (coordinator): main integration and paced fleet rollout (controllers first, including the Shops controller, then nodes). The full Python gates (unittest/regressions/voice), the fresh native gate on the clean release commit and packaging/DMG are run by the 0.6.193 release worker and recorded in the receipts directory below. Receipts directory: `~/Developer/_orchestra/codex-takeover-20261012/hub-0193-release/`. VERSION `0.6.193`.
> **0.6.192 the pack lane's deadline stop is named and terminal, and an offline Mac holding a job reads offline — 2026-10-20. PREPARED AND GATED ON `release/0.6.192`; main integration and rollout REMAIN WITH THE COORDINATOR.** One tested fix branch was merged over verified main `fd27422` (0.6.191) in worktree `_worktrees/studio-hub-kh/release-192`: `fix/pack-deadline-named` (`884553c`, on `5a105ab`). No native source moves; the app binary must reproduce 0.6.191's. (1) `_run_pack` had a terminal branch for `ChatStalled` but none for a read timeout, so the pack's own deadline fell into the transient retry — "retrying in 5s: " with an empty reason (httpx's read-timeout message is `''`) — and re-ran the full deadline up to `MAX_TRIES` times; a `ReadTimeout` once the pack's computed deadline has elapsed is now terminal **`CHAT_DEADLINE_REACHED`** with `chat_deadline.deadline_reason`, as the proxy lane already does; `ConnectTimeout` and other transients still retry, and no retry/terminal line can print an empty reason (falls back to the class name). (2) `activity._state_at` checked for work before checking for every studio being down, so a Mac with all workers gone but an open broker item read "working"; the existing all-studios-down rule now comes first, the held job stays named as `current_job` and the fleet row carries `job_waiting`. REMAINING (coordinator): main integration and paced fleet rollout. Receipts directory: `~/Developer/_orchestra/codex-takeover-20261012/hub-0192-release/`. VERSION `0.6.192`.
> **0.6.191 the local stall stop the Hub never called, and the flaky gate tests fixed at the source — 2026-10-20. PREPARED AND GATED ON `release/0.6.191`; main integration and rollout REMAIN WITH THE COORDINATOR.** One tested fix branch was merged over verified main `2868be4` (0.6.190) in worktree `_worktrees/studio-hub-kh/release-191`: `fix/flaky-gate-tests` (`25c9299`), carrying the merged `fix/fault-injection` (`0a09916`). `native/StudioHub.swift` moves, so the app binary is rebuilt. What is in it: `automatic_recovery.py` was adopted with the source Hub but its caller lived in `main.py` (an excluded entry point), so a local Image/Voice job whose progress froze was only flagged and never stopped — `broker._stop_a_stalled_local_job` is now that caller, observing the job's own counters on the existing poll and ending it with a plain reason and error code **`STALLED`**, on the module's measured budgets (**1 h Image, 2 h Voice**; Voice moves only at long-form section boundaries, one measured Breeze section took 1,747 s on an M1), leaving a peer's job to the peer; the observer had been reading `current_step`/`total_steps`, which no worker publishes (Image writes **`denoise_step`/`denoise_steps`**), so Image stage evidence was false for every job — the stage fields now live in one table named after the workers that publish them; a stalled item is only ended **after the worker took the stop**, so an unlanded signal cannot report a dead job while a live one holds the Mac (and cannot leave the durable phase as `cancel_requested`, which the policy never retries); the activity timestamp is the item's own last progress (`fix/fault-injection`); and the flaky gate tests are fixed at the source — the Voice child's teardown crash isolated, the PID file written aside and renamed, **half-second ticks in the native quit wait** (a real product bug: `SECONDS` arithmetic could make a 2 s wait 1.0 s and SIGTERM a Hub still quitting normally), and environment leaks between test modules fixed rather than worked around. REMAINING (coordinator): main integration and paced fleet rollout. The full Python gates (unittest/regressions/voice), the fresh native gate on the clean release commit and packaging/DMG are run by the 0.6.191 release worker and recorded in the receipts directory below. Receipts directory: `~/Developer/_orchestra/codex-takeover-20261012/hub-0191-release/`. VERSION `0.6.191`.
> **0.6.190 a chat deadline sized to the job, and a stall stop — 2026-10-20. PREPARED AND GATED ON `release/0.6.190`; main integration and rollout REMAIN WITH THE COORDINATOR.** One tested feature branch was merged over verified main `5aab40e` (0.6.189) in worktree `_worktrees/studio-hub-kh/release-190`: `feat/dynamic-chat-deadline` (`3968341`, based on `ac15d69`). No native source moves, so the app binary is unchanged. What is in it: the lane's flat 1,800 s (derived from one M1 measurement, 30 scenes at ~403 tokens) cut the longest planned job — a 100-prompt scene pack, ~4,072 s — roughly in half while holding a Mac thirty minutes for a one-line question; `components/hub/backend/chat_deadline.py` (new) computes model load + prompt at this Mac's prefill rate + the expected answer at this Mac's measured decode rate (`generation_tps`, already polled every 5 s), × 1.5, clamped to **[1,800, 14,400] s**, using the stated per-chip figure where a Mac has never run a chat job and naming which of the two in the note; the expectation is the caller's `expected_output_tokens`, else a pack's scene count, else the p90 of the fleet's 1,122 measured answers — **never `max_tokens`**; a job that writes no token for **5 minutes** is stopped with "stopped: no new text for 5 minutes", distinct from the deadline stop, and never fires for a worker that publishes no activity; the pack lane's flat 600 s read timeout becomes the pack's own computed limit; and `deadline_s` / `deadline_note` are recorded on the job row and the pack row. REMAINING (coordinator): main integration and paced fleet rollout. The full Python gates (unittest/regressions/voice), the fresh native gate on the clean release commit and packaging/DMG are run by the 0.6.190 release worker and recorded in the receipts directory below. Receipts directory: `~/Developer/_orchestra/codex-takeover-20261012/hub-0190-release/`. VERSION `0.6.190`.
> **0.6.189 one admission floor per model, and a Mac that can never meet one is skipped — 2026-10-19. PREPARED AND GATED ON `release/0.6.189`; main integration and rollout REMAIN WITH THE COORDINATOR.** Two tested fix commits were merged over verified main `7c4b233` (0.6.188) in worktree `_worktrees/studio-hub-kh/release-189`: `fix/hunt-fixed-numbers` (`2c7b3bb`, on top of `de4e736`). `native/StudioAPI.swift` and `native/StudioViews.swift` move, so the app binary is rebuilt. What is in it: the Voice worker asked for the stricter of its row's measured floor and a formula (`size_gb + 1.25`) — 3.55 GB for VoxCPM2 4-bit against its row's 2.8 — so the Hub admitted 8 GB Macs the worker then refused (live: 126 attempts, 3,068 `INSUFFICIENT_MEMORY` hand-backs); `_required_free_memory_gb` now prefers the published floor and the estimate survives only for an unmeasured row; `broker._memory_gate` no longer answers `wait` for a floor at or above the machine's whole RAM (judged by `broker.memory_class_gb`, the shipped size) while still waiting when the Mac is merely short now; `memory_admission._FLEET_DEFAULTS` keeps only the total-memory measurements, `describe` takes the live-free default from the row alone and reports `min_free_source` (`operator_override` / `model_default` / `default`), and `monitor._assemble_aggregate_catalog` publishes the enforced floor and owner as `hub_free_memory_gb` / `hub_free_memory_source`; and the Models pane gains "Memory to start a job" with Free before start, Source, and **Reset to default** (a `PUT /api/hub/memory/admission` with no `min_free_memory_gb`). REMAINING (coordinator): main integration and paced fleet rollout. The full Python gates (unittest/regressions/voice), the **fresh** native gate on the clean release commit and packaging/DMG are run by the 0.6.189 release worker and recorded in the receipts directory below. Receipts directory: `~/Developer/_orchestra/codex-takeover-20261012/hub-0189-release/`. VERSION `0.6.189`.
> **0.6.188 chat output ceilings + vision caption budget — 2026-10-19. PREPARED AND GATED ON `release/0.6.188`; main integration and rollout REMAIN WITH THE COORDINATOR.** Two tested fix commits were merged over verified main `f616698` (0.6.187) in worktree `_worktrees/studio-hub-kh/release-188`: `fix/output-limit-audit` (`ac15d69`, on top of `7eea4ed`). No native source moves, so the app binary is unchanged. Three changes: (1) **`FALLBACK_MAX_OUTPUT_TOKENS` 16,384 → 128,000** in `components/chat/app/backend/llm_engine.py` — the last resort only, since a figure the model states wins and a caller's number wins over both, and it is a maximum rather than a target (an answer still stops at EOS). **The 1,800 s chat deadline is deliberately NOT changed** (`standalone.MODALITY_DEADLINE_FLOOR_S["chat"]` stays `1800.0`; owner decision pending): at the measured rates 1,800 s buys ~21,500 / 25,200 / 32,100 tokens on M1 / M2 / M4, so a genuinely 128,000-token answer is cut by the deadline rather than the ceiling, and the comment records that whoever raises that floor next must re-derive it from the same arithmetic in the same build. (2) **An unsized request is shrunk to fit a smaller model's context instead of refused** — the route resolves an omitted `max_tokens` to the lane's published default, so `_named_a_figure_of_its_own` treats that figure as the lane's own and `_refuse_over_context(asked=…)` returns the room the context leaves (gemma4-26b-a4b-qat-4bit states 32,768 and would otherwise refuse every caller that named nothing); a figure the request named itself is still refused with the measured counts. (3) **The dispatch note names the largest ceiling on offer** — `chat_jobs._eligible_studios` also returns the largest output ceiling any ready Studio serving the model publishes, and a batch above every one of them says so and says it is not waiting for a free Mac, instead of the old "Waiting for a free online Chat Studio with this model cached". Plus the vision fix: **vision captions are no longer cut short at 256 tokens, so region detection keeps all regions** — `<DETAILED_CAPTION>` was pinned to `max_new_tokens=256` while every sibling call uses `limit` (the manifest's `defaults.max_new_tokens`, 1,024 on Florence-2 base and large), and the caption is what `segments` matches region words against, so a truncated caption read as "the caption does not name this region"; `tests/test_vision_worker.py` gains `test_every_task_runs_with_the_models_own_stated_budget`. REMAINING (coordinator): main integration and paced fleet rollout. The full Python gates (unittest/regressions/voice), the fresh native gate on the clean release commit and packaging/DMG are run by the 0.6.188 release worker and recorded in the receipts directory below. Receipts directory: `~/Developer/_orchestra/codex-takeover-20261012/hub-0188-release/`. VERSION `0.6.188`.
> **0.6.187 owner retention rule (7 days / 20 GB) + Finder Trash auto-empty + Models page tabs — 2026-10-19. PREPARED AND GATED ON `release/0.6.187`; main integration and rollout REMAIN WITH THE COORDINATOR.** Three tested feature branches were merged over verified main `32c7abf` (0.6.186) in worktree `_worktrees/studio-hub-kh/release-187`: `feat/retention-7d-20gb` (`55841d7`), `feat/macos-trash-auto-empty` (`20dbf9a`) and `feat/models-page-tabs` (`0cf5c7a`). All three touched `native/StudioViews.swift` and the changelog's `## Unreleased`; the retention and Trash branches also both touched `native/StudioAPI.swift` and `components/hub/output_storage.py`. The only conflicting file was `CHANGELOG.md` (three `## Unreleased` sections); every code hunk auto-merged, and every added line of all three branches is present in the merge — the changelog's three sections are folded into one `## 0.6.187 — 2026-10-19` entry. What is in it: the owner's 2026-10-19 retention rule at both stages — generated output moves to the Hub Trash Folder after **7 days or 20 GB** (`DEFAULT_RETENTION_DAYS` 7, 20 GB live budget unchanged), the Trash Folder hands itself to macOS Trash after **7 days or 20 GB** through the new `trash_handoff(days, max_bytes)` with the over-cap items going **oldest first** and the cap measured across every root `_trash_bases()` names, the same 7-day rule for transcription/job-storage (`POLICY_VERSION` 4) and subtitle inputs, and `standalone_config._DEFAULTS_VERSION` **3** with `_LEGACY_DEFAULTS` so a Mac that never chose a number migrates and a chosen one is kept; macOS's own `com.apple.finder FXRemoveOldTrashItems` is read as the logged-in user and published as `macos_trash_auto_empty` on `startup` / `/api/hub/resources` / fleet rows (absent key is **off**, an unreadable one is `unknown`), with **one-way** enable routes `POST /api/hub/macos-trash-auto-empty/enable` and `POST /api/hub/fleet/nodes/{node_id}/macos-trash-auto-empty/enable` that read the value back and refuse `NOT_APPLIED` when the write did not stick, never restart Finder, and answer a node older than `standalone.MACOS_TRASH_HUB` (`"0.6.187"`, which must equal `VERSION`) with `HUB_TOO_OLD`; and the Models page becomes function tabs (`All · Image · Audio · Subtitle · Music · Chat` with live counts, no tab for a function the library has no model for) with a two-way resizable details split whose width is remembered (`studioHub.modelsListWidth`) and no details pane at all when nothing is selected. REMAINING (coordinator): main integration and paced fleet rollout. The full Python gates (unittest/regressions/voice), the fresh native gate on the clean release commit and packaging/DMG are run by the 0.6.187 release worker and recorded in the receipts directory below. Receipts directory: `~/Developer/_orchestra/codex-takeover-20261012/hub-0187-release/`. VERSION `0.6.187`.
> **0.6.186 Trash controls + node history + preferred-work hold + App Update Key — 2026-10-19. PREPARED ON `release/0.6.186`; gates, DMG and integration RUN BY THE COORDINATOR.** Three tested feature branches were merged over verified main `075fc18` (0.6.185) in worktree `_worktrees/studio-hub-kh/release-186`: `feat/unified-trash-controls` (`80f72dc`), `fix/assets-own-vs-nodes` (`1ba4092`) and `feat/preferred-work-idle-hold` (`0d0a437`) — all three touched `native/StudioAPI.swift` / `native/StudioViews.swift`, and the merge is a superset of every branch's added lines with no conflicting hunk. What is in it: the Trash Folder's own local routes (`GET /api/hub/trash`, `POST /api/hub/trash/move-to-macos-trash`), the `trash_handoff_days` timer (default 7), legacy-Trash adoption at start-up, and retirements instead of deletes everywhere the Hub used to delete; a node lists the work it ran for its controller, the controller's Jobs page gains a "Made on" All/This Mac/Nodes filter, "Show in Finder" creates a missing model folder, and a failed `/api/hub/model-packages` read keeps the last good rows; a per-Mac preferred-work idle hold (OFF by default, 1–3600 s, default 300); and the App Update Key fix for Mac 1607 — a pasted **fingerprint** is refused with what to paste instead (it is 64 hex too, so it parsed as a valid 32-byte key and then refused every release silently), `GET /api/hub/release` publishes `last_sync`, and the Connections screen shows the stored key's first 8 characters and any refusal — plus the 8 GB Chat fix for Mac 1605 (a host below its model's own published tier now reports itself not eligible, so an 8 GB Mac stops advertising the 16 GB chat model as runnable; `_host_eligibility` sets `eligible: False` with the warning as the reason, and `_assemble_aggregate_catalog` falls back to the machine's own hardware profile when a worker states no `memory.total_gb`, which the Chat worker never does — **this supersedes the 2026-09-30 "the Hub is the only wall, the worker informs" split, recorded in `tests/test_below_tier_warning.py`**). **Five expectations the branches' own runs did not cover were red on the branches themselves** (a failed policy write now retires to the Trash Folder; the empty Jobs section names the Macs the filter shows; the reveal creates rather than being disabled; and the native API fixture still required every model to read as unsupported after a failed package read — the assets branch was Swift syntax-parsed only and its native suite was never run, so `tests/native/api_main.swift` trapped the fresh native build). They were brought up to their own branches in `ffb57e0` (Python) and `6b07235` (native); none was a merge conflict. REMAINING (coordinator): full Python gates (unittest/regressions/voice), fresh native gate on the clean release commit, packaging/DMG, main integration, paced rollout. Receipts directory: `~/Developer/_orchestra/codex-takeover-20261012/hub-0186-release/`. VERSION `0.6.186`.

> **0.6.185 SSD/app model-location unify — 2026-10-18. SOURCE PREPARED IN A WORKTREE; GATES UNRUN; NOT INTEGRATED.** Owner request (`E/hub-ssd-model-location-20261018.md`): the SSD installer saved models somewhere other than the folder the app looked in on a new Mac, so the folder had to be repointed by hand; unify the two locations and add a "Show in Finder" control for the folder the app is using. Seven source paths are prepared on branch `release/hub-0.6.185` (worktree `_worktrees/studio-hub-kh/release-185`, cut from verified main `7d556ea`): the new resolver `components/model_library.py` and `tests/test_model_library_root.py` (new), plus tracked edits to `native/ShellCore.swift`, `native/StudioHub.swift`, `native/StudioViews.swift`, `qualification/copy_models_for_this_mac.py` and `tests/test_copy_models_for_this_mac.py` — `git diff` `+302/−11` over the five tracked files — together with metadata (CHANGELOG/release-notes/VERSION `0.6.185`/HANDOFF/BOARD). The candidate source is independently accepted SOURCE-only (`E/hub-ssd-model-location-20261018/independent-f1f5-review-20261018.md`) and frozen at `FREEZE-F1F5-corrections-20261018.md` (`a40b0c94…`). **Nothing is committed, staged, built, tested, installed or fleet-published, and no gate has been run** — the Python suites, the native gate and packaging/DMG are all UNRUN, and the 185 runner is current at `01b3adc2…`, whose lifecycle residuals are independently closed — the final #7 source-identity delta reviewed and PASSED SOURCE-only in `hub-0185-release/runner-fix-20261018/independent-third-review.md` — with the gates themselves UNRUN. REMAINING before release: explicit execution grant → full Python gates (unittest/regressions/voice) on the current final 0.6.185 source with the matching VERSION `0.6.185` and headings (the 0.6.183 known environment-only errors reproduce on any worktree base and are never waved green) → two explicit-path commits (the seven source paths, then the five metadata paths) with all bytes unchanged → fresh native gate on the clean final 0.6.185 commit → packaging/DMG → main integration → paced rollout. Hold the 0.6.184 candidate and the partial 0.6.184 fleet rollout (10 of 11 required Macs at 1100) unchanged. Plan: `E/hub-ssd-model-location-20261018/ADDENDUM-2-185-gate-plan-f1f5-20261018.md`.
>
> **0.6.184 native Chat busy-Mac wait — 2026-10-18. CANDIDATE, NOT RELEASED.** The accepted, independently reviewed native Chat queue-opt-in candidate is integrated on `release/hub-0.6.184` (`f51e446`, exact tree of reviewed commit `8db7c88`, blobs verified identical: `native/StudioAPI.swift e1d9e99`, `tests/test_chat_priority.py e09f39c`, `tests/native/api_main.swift 06545d4`); metadata (CHANGELOG/release-notes/VERSION/HANDOFF/BOARD) prepared on top. Truthful scope: the app's own Chat page now opts a named-model completion into the Hub's 0.6.157 bounded queue (wait 1800 s + 1800 s generation + 10 s client margin), one fresh 32-hex wire id per send across header/body/activity poll, no automatic retry, unnamed sends byte-for-byte unchanged. Nothing is installed or fleet-published. Provenance: native gate on the exact candidate `bash native/build.sh` exit 0 all four stages (receipts `~/Developer/_orchestra/codex-takeover-20261012/hub-chat-queue-gate/09-12`, release binary `d8b69cde…` retained), Python focused suites 71 passed on the same candidate bytes (logs 01/02; unchanged since — not rerun). REMAINING before release: independent final delta review, explicit execution grant, then full Python gates (unittest/regressions/voice; the 0.6.183 known environment-only errors reproduce on any worktree base and are never waved green), native gate rerun (the candidate's native inputs changed only by integration commit — the builder must decide binary reuse per its own provenance checks; do not assume `d8b69cde…` reuse), packaging, DMG, integration, paced rollout. Plan: `agent-reports-20261016/hub-0184-release.md`. Receipts directory for this release: `~/Developer/_orchestra/codex-takeover-20261012/hub-0184-release/`.

> **0.6.183 delivered to the reachable fleet — 2026-10-17.** Product main `3700c4fe65e2c420e75d529d0fe545372bd4ffa9` (reviewed `98d4fa3` + blocker fix `12db653`, independent re-review ACCEPT) is pushed. Primary `dist/StudioHub-0.6.183-3700c4f.dmg` is 1,321,430,528 bytes, SHA-256 `f1346709d53bf625df19d47abac295031220cca5c62fc6ce9e410eb0bcb50eca`; the 0.6.182 native binary is reused byte-for-byte (`native/` unchanged, enforced by the builder). Gates: unittest 2419 run/36 skipped/2 env errors reproduced on `5b60256`, regressions 416, voice 1014; no native gate needed. Paced rollout: all eight controllers accepted the publication (This Mac, 1100, 1200, 1300, 1400, 1500, 1600, Shops) and **59 Macs report 0.6.183**. Verified on two consecutive reads: This Mac 1/1, 1100 8/9, 1200 11/11, 1400 11/11, 1500 5/5, Shops 4/4. Open exceptions: `studio-mini` (1600) is reachable but still 0.6.181 with `version_status behind` and no update error — its own updater has not applied the release; `studio-mac-0106` (1100) and `terranes-1606` (1600) are offline and defer naturally. Note the controller-restart pattern: each site's controller reads as transiently offline during its own update, so the driver defers it and the site is re-verified read-only. Receipts: `~/Developer/_orchestra/codex-takeover-20261012/hub-0183-release/` and `hub-0183-rollout/`.

> **0.6.183 fleet Chat relay — 2026-10-17.** Reviewed `98d4fa3` and its blocker fix `12db653` (independent re-review ACCEPT) fast-forwarded over main `5b60256`: a site's controller relays a Chat completion for a model only its nodes carry to one node (reusing the pack dispatcher's eligibility, memory readiness, ranked arbitration and lease), and a candidate whose catalog read goes stale in that window is skipped rather than dispatched, so an unprovable Mac is passed over instead of crashing or being assumed empty. Python only; `native/` unchanged, so the 0.6.182 native binary is reused byte-for-byte. Pairs with Gateway 0.2.183. Known limitations are listed in `agent-reports-20261016/fleet-chat-relay-release.md`. Receipts: orchestra `hub-0183-release/`.

> **0.6.182 Trash Folder, no automatic hard deletes — 2026-10-16.** Reviewed `ae28ffc` fast-forwarded over main `efb04dc`: everything the Hub retires moves to its Trash Folder (or macOS Trash in the native app) instead of being deleted; Gateway 0.2.181 lists, moves to macOS Trash or (owner app only, confirmed and re-measured) permanently deletes each Mac's Trash Folder through new fleet routes; Show Trash Folder in Finder. Native source changed, so the native binary comes from a fresh native source gate. Gates, artifact and rollout receipts: orchestra `hub-0182-release/`.

> **0.6.181 source, installer, This Mac and 1100 verified; paced fleet delivery running — 2026-10-16.** Reviewed `5970ea0` (independent Opus review ACCEPT) integrated over main `a655fe1`; product main `c32b16bbf19f10c8799912bef8eca6f43dcd7d73`. A Voice section's final quality-gate-rejected take is kept as an inactive review-only candidate (exact text, measured reason, settings, quality evidence); Hub binds it to the failed item and serves it through authenticated, byte-verified GETs. Failed outcomes unchanged; no automatic deletion. Gates: unittest 2390 run / 36 skipped / 0 failures / 2 errors, both environment-only (`.build/voice-runtime`, `.build/prepare-kit.sh` absent from worktrees) and reproduced identically on `a655fe1`; regressions 416 passed; Voice 1014 passed / 28 skipped. Python-only: the 0.6.180 native binary (`ef07f1a5…`) is reused byte-for-byte; staged six zero-skip checks, four packaged Image imports and `hdiutil verify` passed. Primary `dist/StudioHub-0.6.181-c32b16b.dmg`: 1,320,062,976 bytes, SHA-256 `5b28f596a3ece759b03be357d1526c884a47fe2d2b5cf31bca61c04e82ff81b4`; 0.6.180 DMG retained under `_trash/dist/20261016-121350-*`. Normal publications accepted for This Mac (native binary/manifest proof, 7/7 services), 1100 (10/10 reachable on 0.6.181 twice, 0106 offline deferred, pauses unchanged) and 1200; the one paced driver continues 1300→1600→Shops; offline Macs catch up naturally. Receipts: orchestra `hub-0181-release/`, `hub-0181-rollout/`.

> **0.6.180 reachable-fleet delivery complete — 2026-10-15.** Reviewed typed-array catalog decoder correction and focused proof shipped independently of unfinished private rendering. Delivered product main `b6fbab4ae8c433512fb84de2e58c3541b5d12450`; primary `dist/StudioHub-0.6.180-b6fbab4.dmg` is 1,318,160,384 bytes, SHA-256 `cc138521254962a18c23ee6cdc2f8997249489a7930b27c30d521810a25e80be`. Required native/source, staged six zero-skip checks, packaged imports and DMG verification completed; reuse retained gates. The sole normal paced driver session89004/PID67868 exited0 after all eight accepted site publications. `hub-0180-rollout-complete.json` proves the same58 reachable Macs healthy/on180 at10:47:52Z and10:50:02Z, all eight groups; fresh native startup reports180 on58. Exact native binary/manifest provenance is This Mac only. Two offline Macs(1106 and kt-pdt-imac) defer naturally;50 known reachable pause values match and8 baseline controller values remain unknown. Never republish accepted receipts or restart the completed driver. The exact historic1107body was not captured; broader read/readiness coupling remains separate. Story305/Gateway179 and renderer activation are unchanged. This closing documentation does not change or rebuild the verified product artifact; living iCloud/orchestra receipts govern later availability.

> **0.6.179 delivery complete — 2026-10-15.** Main `e99a1b789d3044ded6659fcb9683d2b213973bc0` is the delivered product source. Primary `dist/StudioHub-0.6.179-e99a1b7.dmg` is verified at `1,319,457,792` bytes with SHA-256 `df09d3abda29264828a830aa675617faec39ab9555a3a5d2624da5251b8fc3cf`. The normal rollout receipt `hub-0179-rollout-complete.json` records two fresh reads with 59 reachable healthy Macs on 0.6.179, all eight existing publication receipts accepted, and one unreachable 1106 deferred. This Mac's native proof is `hub-0179-this-mac-native-first-proof.json`; 51 known reachable pause values were compared and 8 baseline controller pause values remain unknown. Native fleet-controls behavior is delivered; the separate private renderer remains disabled and unqualified.

> **0.6.178 source and primary installer verified; deployment pending — 2026-10-14.** Product `0de617818ac218172837e8bac99d1cbe321b2ed3` is pushed/remote-verified main. Normal startup login-agent repair and Settings outcome handling are independently reviewed. Exact frozen complete native API/lifecycle/secret/view/optimized compile, root boundary, affected Python132/3initial-artifact-skips and new-metadata17/3initial-artifact-skips passed. All six staged-bundle checks subsequently passed withzero skips, closing the artifact gaps; fourpackagedimage imports and DMGverification passed. Primary `dist/StudioHub-0.6.178-0de6178.dmg`:1,319,026,176bytes/SHA256 `e5a9411a57eed157ea16935d09d230e99558d766379e8a14d0dc43f151ac648e`. Correctproven nativebinary is byte-identical in the stage. Code-onlyGraphify atproduct595files/17055nodes/39054links/paid0; one inherited partial-parser warning for unchangedStudioAPI.swift, direct source authoritative. No Pythonbackend/worker/runtime change; previous177backend/Voice/regression evidence retained, not rerun. Graph/builder finished; no publisher/installer yet. Keep accepted1503copy/sourceleases until naturalsettlement before normal paceddeployment. Exacthistorical update-helper missunknown; operationalfour-Macrecoveryseparate. Thisclosingdocumentation doesnotchange the verified product artifact.

> **0.6.178 reviewed source candidate; installer/deployment pending — 2026-10-14.** Normal non-agent/default-data startup with actual instance-lock ownership refreshes the installed login registration using exact bootout then unchanged-plist bootstrap. Refresh and Settings toggles serialize; unsuccessful unload keeps the plist and reports failure, while timeout/signal uncertainty blocks later mutations in this app process. This bounded process-local quarantine does not establish durable launchd health. Independent corrected actual-diff review clean, source hashes/report bound in orchestra hub-loginagent-refresh-fix-wave-independent-review.md. Complete frozen native API/lifecycle/secret/view and optimized compile passed; root native boundary and affected Python132/3artifact-skips passed. Unchanged177 backend2383/15skip, regressions416 andVoice998/28skip evidence is retained, not rerun; no Python backend/worker runtime source changed. Stage/packaged checks/primary DMG/main integration and normal deployment remain pending. Operational four-Mac recovery is separate verified evidence; exact old update-helper reopen miss remains unknown. Deployment waits affected accepted model transfers and active source leases naturally. Living iCloud handoff/orchestra own current operation identities.

> **Current installed177 receipt correction.** Hub177 was subsequently pushed, built and normally rolled out: hub-0177-rollout-complete.json proved all53then-reachable Macs across8groups twice; after four operational recoveries59reachable/all177 was verified. Latest10:15Z normal read58online/all177, oneoffline/oneoffLite. Historical pending statements below describe their original checkpoints, not current installation. Preserve owner pauses/model-off choices;1107 management access remains unavailable.

> **0.6.177 source verified, installer/deployment pending.** Narrow Fleet idle-worker memory-release permission fixes the reproduced403 admission error without changing presets or free-memory floors. Independent source and gate-correction reviews are clean; full unit2383/15skip, regression416, voice998/28skip and native build/boundaries passed. Initial two stale negative assertions and passing corrected gate are retained in orchestra hub-0177-gate andhub-0177-gate-retry. No extra YuE2 jobs generated. Main/push and primary DMG follow; do not claim installed yet.

> **0.6.176 source and installer verified; fleet deployment pending — 2026-10-13.** Product `a5bd27f4aea8508e0ad401dc8b8abb303739d743` is on pushed main. Embeddings and Vision publish authenticated scalar-only activity with actual preparation/queued/inference ownership; async health/activity reads remain responsive when the request pool is occupied. Build identity is cached per launched factory, service state stays fresh, and snapshot timestamps are captured under the state lock. Independent actual-diff and scoped review clean after two reproduced P2 fixes. Required checks:2381unit/15skips,416regressions,Voice998/28skips, allnative checks/optimizedbuild, sixbundlechecks/fourpackagedimports/DMGverify passed. Staged realfactories/auth/Hub-validation proof passed; sharedhelper is in the verified delta manifest. Primary `dist/StudioHub-0.6.176-a5bd27f.dmg`: 1318415872bytes, SHA256 `57724efe0178e0018f576b7053dd34fba8c16726a031fe6852eb5fdfa0a31e57`. **NOT published/installed yet.**175installer remains in primarydist;175publication stays unsubmitted. Deliver one176pacedupdate containing both repairs after current accepted copies and cross-site sourceleases naturallysettle. The living iCloud handoff and orchestra checkpoints own exact liveidentities. Keepstrictall-reportersguard and storedYuE2probe unsubmitted until updatedfleet/fresh completeidle proof. These closingnotes don'tchange the exact verified product artifact.

> **0.6.175 source and installer verified; deployment pending — 2026-10-13; Astra/root owns integration.** Product `f77fc7d4e79068f373e582e66188fb6f46df444c` is on pushed main. Continuous LAN file-body delivery renews only a still-valid scoped model grant; unknown/expired grants, HEAD/empty sends, exact revision/file boundaries, range/multipart handling, failed-send cleanup and transfer caps retain their rules. Independent actual-diff review clean; root focused checks21 passed. Required gates passed: unit2369/15skips, regressions416, Voice998/28skips, all native boundary/API/secret/view/lifecycle checks and optimized build. Six staged-bundle checks, four packaged imports and DMG verification passed. Primary `dist/StudioHub-0.6.175-f77fc7d.dmg`: 1,318,295,040 bytes, SHA-256 `2b7a6294aba1abaac31bea7b02ab73fbb85ca65909e7a3c13bddf3d009d7d6e9`. **Not published or installed yet.** Current Turbo plan `c3350d9e0801` owns accepted copies; wait latest affected target runs and cross-site source leases naturally terminal before normal deployment. No cancellation, forced restart, generation, paid retry or pause/preset change. These closing notes do not alter the exact product artifact. Live identities and any later publication belong to iCloud HANDOFF and orchestra CONTINUATION/ACTIVE-STATE.

> **0.6.174 release source and installer verified — 2026-10-13; normal fleet delivery in progress.** Product commit `f5c9794bccca881cdf56c5ccbbce11fb037ca11f` is on pushed main. Required source checks, six staged-bundle checks, four packaged imports and DMG verification passed. Primary `dist/StudioHub-0.6.174-f5c9794.dmg` has SHA-256 `d8e66028ddd674b4199c5a574393a5b9071793813c1910581ee94aca3af6e135`. Local release was published through the normal updater; the single paced driver and accepted-transfer barrier are recorded in the living iCloud handoff and orchestra CONTINUATION/ACTIVE-STATE. Do not infer full deployment from VERSION, restart a completed driver, cancel accepted downloads or start another spread. These notes update delivery provenance only; the shipped product remains the exact verified commit above.

> **0.6.173 source verified — 2026-10-13, ChatGPT/Astra owns integration.** Latest owner decision: adopt Qwen Image 2.1 Turbo Low-RAM MLX 4-bit through normal Hub distribution and production on 16 GB+ Macs, without a separate isolated memory-fit qualification. This supersedes the older physical-access gate below. Preserve the existing staged loading, zero cache, tiled decode, release after each job, eight-step/CFG-off/size contract and one-job-per-Mac scheduling. Use the regular Qwen 4-bit 8.0 GB start floor as an explicitly inherited provisional operating assumption; no invented Turbo speed or peak. Independent review passed (spec/quality, no findings). Focused checks135plus181subtests passed; full gate2363tests/15skips/zero failures,410fleetregressions,998Voice/28skips, native boundary/API/secret/view/lifecycle checks and optimized build passed. Installer staging/import checks, primary dist DMG and paced rollout are next, then matched normal-production speed/quality runs. Fleet deployment and benchmark are not yet complete. Shared iCloud HANDOFF.md and `~/Developer/_orchestra/codex-takeover-20261012/CONTINUATION.md` own live state.
>
> Prior0.6.172 driver exited at site1500 with1503 pending; do not duplicate it. This Mac and1100–1400 were verified. Keep offline nodes pending, pauses unchanged, no forced install. Existing Story1.4.302/Gateway0.2.176 gates and installers remain complete.

## Historical release receipts — newer owner decision and live handoff above take precedence

> **0.6.172 published; This Mac verified — 2026-10-13. Its driver later stopped at site1500; see the active shared handoff.** Product source `675e218e0ba1349b6b2f07542e52041d5b0366ac` is on main and pushed. Primary `dist/StudioHub-0.6.172-675e218.dmg` is verified: 1,323,565,056 bytes, SHA-256 `ff2ceeefd1be1342ea2efdb4b6567791cee0bcc2942f8f54729f542d8d5c99b8`. Native unattended delta update completed normally on This Mac; two fresh Gateway reads confirmed 0.6.172 at 19:34Z, with no update error. The sole paced driver then published site 1100. It waits for every reachable Mac in each site before continuing 1100 → 1200 → 1300 → 1400 → 1500 → 1600 → Shops; pauses are preserved and offline Macs tracked separately. Consult the live receipt before acting: `~/Developer/_orchestra/codex-takeover-20261012/hub-0172-rollout/status.json`. Do not start another rollout or move/build the old primary checkout branch.
>
> Bounded broker dispatch traces record actual first refusal gates and eventual selection without changing placement. Gateway 0.2.176's authenticated, sanitized read is installed and verified. After site 1600 is updated and eligible Macs are idle, one bounded three-job, 60-second reproduction through normal Gateway routing will establish the current M2 gate; the historical cause is not proven.
>
> Fresh gates passed: unit 2,363 run / 15 skipped / zero failures or errors; regressions 410 passed; voice 998 passed / 28 skipped; native boundary/API/secret/view/lifecycle checks and optimized build passed. Focused dispatch checks 14 passed; callback-origin cancellation and monotonic-progress checks 51 passed. Staged-bundle checks six passed; packaged Image imports passed, including Qwen Image 2.1 Turbo 4-bit. No real-model inference or physical memory claim is implied.
>
> **Turbo 4-bit isolated kit is refreshed:** `.build/Turbo-675e218`, current app and qualification tools, all 19 files / 9,963,443,598 bytes independently hashed again at pinned revision `bda8951ec6b8cc288a961432fe15c22433521a1d`. The old `Turbo-0802605` kit is retained and marked superseded. Production stays disabled. Qualify physical 16 GB first, then 24/32 GB, including actual callback-origin cancellation, quality, peak/free memory, swap/pressure and per-chip timing; no 8 GB support claim. An access route to launch the isolated kit on a physical 16 GB Mac remains missing. Owner local-use scope is settled.
>
> Evidence: `~/Developer/_orchestra/codex-takeover-20261012/hub-0172-artifact.json`, `hub-0172-update-observation.md`, `turbo-kit-675e218.json`, and `turbo-qualification-next.md`. This closing documentation commit retains the verified product source and installer.

> **0.6.171 — 2026-10-12; YuE2 tolerates "warning" pressure.** Owner: "let them continue". `yue2_runner.tolerate_warning_pressure(lyra.measure.GPUExecution)` wraps the port's `_check_sample`: level 2 is passed on as 1; critical (4), < 2 GiB available, budget and swap stops stay the port's. Pre-load gate refuses only critical. Test `WarningPressureTests`. Measurements: `_orchestra/audit-20261012/lyrics-flow/SPEED-fleet.md`.

> **0.6.170 — 2026-10-12; YuE2 per-song deadline, enforced; no start on an uncleared Mac.** `yue2.job_deadline_s(d)` = `2 × (SLOWEST_S_PER_SONG_S 7.1 × d + SLOWEST_LOAD_S 120)` (owner: slowest measured Mac for every Mac, × 2; table in `_orchestra/audit-20261012/lyrics-flow/SPEED-fleet.md`); `generation.submit` puts it on YuE2 jobs as `deadline_s` (broker copies onto the item; Gateway 0.2.175 has the same constants) and passes it as the bridge timeout. `yue2.generate` watcher thread (2 s): cancel or deadline → terminate. `prepare_machine_memory`: handoff `failed` → `skip`. `yue2_runner`: pressure still not normal after the wait → fail before loading. Open: M1/M2 16 GB still trip the port's level-2 guard mid-song in 2 of 4 runs; proposal (b) (tolerate level 2, keep critical/available/swap stops) awaits the owner.

> **0.6.169 — 2026-10-12; YuE2 clears the Mac first.** `broker.CLEAR_MACHINE_MODELS` = {`vanch007/mlx-Yue2-3B`}: `prepare_machine_memory` calls `release_idle_siblings` before the free-memory gate (busy → `wait`, never preempt; released on a node → peer refresh). `yue2_runner.wait_for_normal_pressure` (≤ 120 s, sysctl `kern.memorystatus_vm_pressure_level`, unknown = go) before load. Why: on 0.6.168, 16 GB Macs failed mid-song on the port's pressure guard (level 2); the port's guard is not weakened. Test `tests/test_yue2_clear_machine.py`. Receipt `/build/Developer/_orchestra/audit-20261012/lyrics-flow/hub/`.

> **0.6.168 — 2026-10-12; YuE2 package carries `model/LICENSE`; same-revision installs reuse their files.**
> Every fleet YuE2 run failed at load (`missing=['LICENSE']`, Gateway probe `f97b7ba8` on 1300/1100/1200): the capture's furniture rule dropped the publisher `LICENSE` that `lyra/conversion.py` requires. Manifest `yue2-3b-mlx` + `model/LICENSE` (20,309 B, the port's pinned sha256), capture entry `keep: ("LICENSE",)`. `model_packages._seed_from_installed`: before `_download_one` fetches, files of an install at the same revision are hard-linked into staging (hash-checked as usual), so the reinstall fetches 20 KB. Needs Gateway "Install to fleet" for `yue2-3b-mlx` after rollout. Part of Story's songs-with-lyrics flow (plan `/build/Developer/_orchestra/audit-20261012/lyrics-flow/PLAN.md`; Gateway 0.2.170 caps YuE2 at 2 fleet-wide, background only). Receipt: `/build/Developer/_orchestra/audit-20261012/lyrics-flow/hub/`.

> **0.6.167 — 2026-10-12; staged voice copies kept up to 96 h (was 48 h).**
> `execution_assets.MAX_TTL_SECONDS` = 96 h so Gateway 0.2.168's 3-day voice reuse (it asks for 84 h: `_STAGED_TTL_S` in `gateway/adapters/studio_hub.py`) is honoured; Gateway cut reuse at Hub `expires_at` − 12 h, i.e. ~36 h before. Default TTL 24 h and the expiry sweep (`cleanup_expired` via `job_storage`) unchanged. Receipt: `/build/Developer/_orchestra/audit-20261012/voice-ttl-0167/DELIVERY.md`.

> **0.6.166 — 2026-10-11; Vision Studio (depth, subject, points and outlines for Story's effects E2–E4).**
> New worker `vision` (engine `vision-mlx`, modality `vision`) on the shared Voice runtime: plain-MLX Depth Anything V2 Small (`depth.py`) and Florence-2 (`florence.py`, base default, large switchable by installing its package). Peer route `POST /studio/vision/v1/vision/analyze` and node relay `POST /api/hub/fleet/nodes/{id}/vision/analyze`, admitted at the door but never leased. Gateway 0.2.167 `POST /v1/vision/analyze` routes to it (source Mac, then caller's site, then any). Delivery receipt: `/build/Developer/_orchestra/audit-20261011/vision/DELIVERY.md`.

> **0.6.165 — 2026-10-11; Embeddings Studio (local text embeddings for Story's meaning check).**
> New worker `embeddings` (engine `embed-mlx`, modality `embeddings`) on the shared Voice runtime, plain-MLX BERT (`components/embeddings/app/backend/encoder.py`). Model BGE small en v1.5 MLX bf16 (`mlx-community/bge-small-en-v1.5-bf16`, 67 MB, MIT), installed on This Mac only. Peer route `POST /studio/embeddings/v1/embeddings` is admitted at the door but never leased (`standalone._PEER_UNLEASED_PATHS`). Gateway `POST /v1/embeddings` routes to it. Delivery receipt: `/build/Developer/_orchestra/audit-20261011/embeddings/DELIVERY.md`.

> **0.6.160 — 2026-10-08; every model-download run listed in the app, in its own Jobs tab, with Cancel.**
> Root cause: the app only polled run ids it had saved itself (`hubDownloadRuns`, `native-model-downloads.json`, filled only by `startDownload`), so a run started by the Gateway fleet install was never shown and `cancelDownload` refused it. The Hub now lists every run (`model_downloads.list_runs`, `GET /api/hub/model-downloads`, owner token only); the app's refresh adopts running ones into `hubDownloadRuns`, so they are polled, shown (Jobs > Downloads tab, Models strip, Overview) and cancelled like its own. This Mac only; a controller does not list its nodes' runs. `HistoryTab.downloads` replaces the bottom-of-All section. Delivery receipt: `/build/Developer/_orchestra/audit-20261008/hub-0160/DELIVERY.md`.

> **0.6.159 — 2026-10-08; per-job Image limits that scale with steps and Mac speed; Low-RAM Qwen 16 GB receipt.**
> `generation.job_deadline_s` (`components/image/app/backend/generation.py`): `120 + 1.5 x steps x seconds_per_step`, clamped [900, 7,200] s. Seconds per step: this Mac's median over its last 20 finished jobs of that model (>= 3, from `jobs.json`), else `ImageModel.seconds_per_step[chip]` (wall/steps of stored runs; chip from `sysctl machdep.cpu.brand_string`), else 2 x the model's slowest reading. Steps only: presets span 0.4-1.3x the measured 1K pixels, inside the 1.5x margin. The worker writes `deadline_s` on the job at submit; `run_image` (mlx-serve leaf) stops at it; `broker._run_item` copies it onto the batch item (cleared on retry via `_ATTEMPT_EVIDENCE_FIELDS`); Gateway 0.2.147 polls to max(lane `deadline_seconds`, item `deadline_s`). The mflux path (Low-RAM Qwen, FLUX, Z-Image) has no leaf; its in-process run is stopped by the Gateway's cancel at the same number (Low-RAM Qwen checks cancel every step). The image lane floor stays 1,800 s. `mlx_serve_runner.job_timeout_s` is gone. Low-RAM Qwen's 16 GB tier now cites `qualification/evidence/qwen-image-21-20261008-16gb/` (manifest, capture row, readings, benchmarks regenerated; 8.0 GB floor kept). `docs/model-benchmarks.md` no longer names the retired SSD runners. Delivery receipt: `/build/Developer/_orchestra/audit-20261008/hub-0159/DELIVERY.md`.

> **0.6.158 — 2026-10-08; Qwen Image 2.1 tiers and stocking, plus the first fleet rollout of 0.6.155–0.6.157.**
> Native `qwen-image-2-1-mlx-serve-4bit` qualified on 24 GB (owner's physical 24 GiB M4 run, `qualification/evidence/qwen-image-21-20261008-24gb/`); low-RAM `qwen-image-2-1-mflux-q4` qualified on 16 GB+ by owner decision (evidence: 32 GiB run 2026-10-06, no 16 GB receipt; 8 GB unqualified). Free floors are peak demand + 2 GB (12.5 / 8.0 GB, `ponytail:` comments in `generation.py` and the capture rows). Native is offered but not stocked (`NOT_STOCKED` in `qualification/copy_models_for_this_mac.py`); low-RAM is the stocked Qwen. Image lane deadline 900 → 1,800 s (`standalone.MODALITY_DEADLINE_FLOOR_S`); native leaf limit scales 34.4 s/step (`mlx_serve_runner.job_timeout_s`). Delivery receipt: `/build/Developer/_orchestra/audit-20261008/hub-0158/DELIVERY.md`.

> **0.6.157 — 2026-10-08; ranked functions, source verified. Delivery identities are receipt-owned.**
> Settings > Work on this Mac now offers Job priority: No preference or Custom order, with accessible arrows. Each Mac independently ranks registered functions. Current work finishes; the highest-ranked eligible queued function runs next, FIFO at the same rank. Other enabled functions borrow idle capacity. Strict ranking can leave lower-ranked work waiting under continuous preferred demand. Legacy LLM-first remains the partial order `["chat"]`; disabled saved ranks and all pause/function choices are preserved. Gateway 0.2.145 exposes the same per-Mac control; older Hubs clearly require an update.
> Canonical `priority_order` and producer-owned `priority_functions` extend the existing work-policy API and authenticated controller/node relay. Policy writes are validated and atomic. Actual dispatch eligibility, routing pins, immutable model revisions, memory, qualification, maintenance and final post-await cancellation/pause checks remain authoritative. Explicit `X-StudioHub-Queue-Wait-Seconds` requests use bounded model-specific synchronous Chat waiting regardless of rank; ordinary streaming/default-model requests keep their existing immediate path.
> Proof: 267 selected Hub integration tests passed (`hub-integration-core-final.log`); all 374 backend regression cases passed (`hub-regressions-final.log`). The first regression run stalled because its synthetic Voice worker was forced eligible while still marked unknown; one fixture status correction restored the durability test without weakening the production health fence. Native boundary/API/secret-masking/lifecycle checks and optimized compilation passed. Actual controller/node Settings views produced 24 light/dark, narrow/wide captures covering partial/full/no preference; six were visually reviewed. Independent review findings were repaired and proved. These are scoped integration checks, not a fresh full-repository test run or physical model qualification.
> Authoritative commit/push, DMG, local install, live policy/pause preservation, screenshots and graph evidence: `/build/Developer/_orchestra/function-priority-20261008/DELIVERY.md`. Check that receipt for running identities; VERSION alone is not installation proof. Local delivery only: no fleet/SSD rollout. Qwen Image 2.1 qualification worktree and model assets remain untouched; the existing qualified Image runtime is only read for normal app staging. The unrelated primary checkout branch stays in place.

> **0.6.156 — 2026-10-08; Hub source verified, delivery identities are receipt-owned.**
> Integrated over the 0.6.155 qualification base `1bb472a`. Work on this Mac offers registered local function choices and optional Prefer LLM jobs: current work finishes, eligible queued LLM work precedes new TTS/image jobs, and other enabled work uses idle capacity. Function/priority changes never resume local or Gateway/fleet pauses. Keep the owner's ordinary Gateway This Mac pause set.
> Full Hub discovery ran 2,270 tests with 15 skips, two fixture failures and zero errors (`hub-unittest-0156.log`). The affected combined replay ran 71 tests: 70 passed and one synthetic library-directory fixture still failed (`hub-0156-fixture-replay.log`). After fixing that fixture, the targeted Chat case passed under full-discovery context (`hub-chat-priority-fixture-final-replay.log`, one test). Gate failures required fixture repairs, not production behavior changes; no unchanged full discovery is claimed as freshly green.
> Native boundary and API contract checks passed in `hub-native-0156.log`, including the nil-MIME JPEG fixture. The subsequent compilation failure was three view references to a private API error type; only those view guards changed. The retained tail verified unchanged API/source fixture hashes and passed secret masking, lifecycle and optimized release compilation (`hub-native-tail-0156.log`, exit 0).
> Actual-view rendering exited 0 and produced 24 controller/node Settings/Image/Video captures in light/dark at 1440x1200 and 980x1000 (`hub-work-policy-ui/fixture-build.1X6LVA/png/`). The coordinator visually inspected seven captures spanning the three pages, widths and themes; it did not claim inspection of all 24. The Image warning stays local. The existing offscreen selected-sidebar black appearance in light mode remains a capture limitation, not a new UI finding. Independent review found no high-impact issue.
> The installed 0.6.155 Gemma 4 26B-A4B QAT MLX 4-bit qualification and its receipts below remain intact: measured 32 GiB only, owner acceptance, curated 32k combined budget, 24/16 GiB excluded. This release adds no physical model qualification claim.
> **Running identity must be checked against the authoritative delivery receipt:** `/build/Developer/_orchestra/codex-remediation-20261007/WORK-POLICY-DELIVERY.md`. The coordinator writes/updates it during commit, push, stage, DMG and controlled installation. This source record establishes none of those installed identities; do not infer live 0.6.156 from VERSION or source checks. All named logs/captures are retained in the same external evidence directory. Gateway's final gate and delivery are recorded there by the coordinator.

> **0.6.155 installed on This Mac — Gemma 4 32 GiB owner-accepted qualification.**
> Code/payload commit3ac4a48; exact package imported and fully SHA256-verified in the existing local library. Live authenticated catalog reports Gemma execution_ready=true,32GB requirement,curated32k combinedcontext and16k workeroutputallowance. A tiny direct packaged-worker request returned READY; pressure stayed normal and modelmemory was explicitly released. Ordinary Gateway This Mac fleetpause remains set. No remote install/fleet release publication.
> Repository qualification/evidence/gemma4-20261007-32gb retains exact publisher identity, both owner-run profiles, original strictFAIL, accepted985/800/805word output, Story8/08 parser equivalence,50scene success and separate packaged-serving-smoke proof. Physical24/16 remain excluded.
> Verified installable artifact: dist/Studio Hub KH 0.6.155-arm64.dmg. Independent102 focusedchecks passed. All4 SSDs retain full models and updatedGemma32qualification records; their apppayloads were not replaced. Authoritative localrelease receipt is coordinator audit/GEMMA_QUALIFICATION_RELEASE.json.


> **0.6.154 candidate — 2026-10-07, audit remediation merged over 0.6.153. Not yet installed or published.**
> `broker._adopt_abandoned_attempt` finalizes a private original candidate, rechecks winner/cancellation,
> durably writes its batch, then commits supersession and cancels the replacement. Metadata, asset-ledger
> or batch-ledger failure leaves the replacement execution intact. `_record_worker_success` keeps
> metadata private across awaits and checks ownership before asset recording; a late replacement does
> not overwrite a completed original. Receipt/hash checks, recovery holds and callback reservation remain.
> Native controller trust and App updates say **App Update Key (fingerprint)**; identifiers and trust
> semantics are unchanged. Release notes carry the owner's Gateway API Key / App Update Key wording.
> Evidence scoped to the pre-merge audit source: five fault/race cases failed on the 0.6.152 base;
> the repaired free-work reroute suite passed 30/30 and its expanded acceptance suite passed 34/34
> (`_orchestra/codex-remediation-20261007/hub-acceptance-green.log`). The isolated replay of 28
> previously failing tests also passed on that old base (`hub-focused-final.log`). A full old-base run
> stopped when 0.6.153 arrived and is invalid. The complete combined 0.6.154 discovery then ran 2,245
> tests with 15 skips, 8 failures and 3 errors (`hub-unittest-0154.log`): one seed mock targeted a
> preloaded real MLX submodule instead of the fake the product imported, seven assertions had stale
> 31-package inventory/generated-record text, and three synthetic staging trees omitted the newly
> required shared MLX helper. Its nested Hub regression suite passed 374/374 and Voice passed 994 with
> 28 skips. After correcting those test fixtures, adding the generator's manifest `display_name`
> fallback, and refreshing manifest-derived documents, the whole affected
> neighborhood passed 35/35 with no skips (`hub-0154-repairs-focused-green.log`); the coordinator chose
> this bounded replay instead of repeating the unchanged 2,245-test discovery. The isolated native gate
> passed boundary, API contract, secret masking, lifecycle and release-build checks (`hub-native-0154.log`,
> exit 0). The retained actual-view render produced and visually inspected all 24 controller/node,
> light/dark, 1440/980 px fixtures (`hub-ui/fixture-build.xPNotG/png/`); the wide controller light and
> dark Connections views show the renamed App Update Key labels and raw key input. Narrow Connections is
> ordinary scroll content, so no below-the-fold key-visibility claim is made. The authoritative record is
> `/build/Developer/_orchestra/codex-remediation-20261007/hub-delivery-receipt.md`.
> Remaining: coordinator commit/stage/DMG, push and controlled installation. Build: `bash native/build.sh`;
> tracked stage: `packaging/stage_voice.py`; tracked DMG: `packaging/make_dmg.py`; install:
> `bash packaging/install_local.sh <staged .app>`. The old untracked `.build/make-dmg.sh` flow is superseded.
> The 0.6.153 Chat model-selection, progress, pending-admission and shared-cache work remains intact;
> its candidates remain physically unqualified and `production_allowed:false`. Root owns release integration.

> 2026-10-07 0.6.153 preparation: source and offline wiring for manifest-driven Chat selection, one resident model, pending-production gates, request-scoped phases, and worker-stated prefill/decode telemetry. New pinned Nemotron 3.5 Lightning30B-A3B MLX4-bit, Qwen3.6 35B-A3B MLX4-bit and Gemma4 26B-A4B QAT MLX4-bit all remain production_allowed:false and physically unqualified. Shared configured512MB cache limit matches the held standalone probe. No model inference or RAM/TPS/quality qualification has run. This work must not be described as installed or fleet-published; distribution state is the receipt under `_orchestra/audit-20261007/moe-qualification/`. Keep This Mac manually paused, all four SSDs mounted, and wait for owner restart/explicit go before testing one model at a time.

> 2026-10-07 0.6.152: the controller update race from the 0.6.151 rollout is closed. A controller's stage waits (<=15 min,
> `phase: downloading`) for blobs a Gateway is still pushing; a node keeps asking its controller for a blob as long; either
> ends `not_ready` (retryable), never the disk-image fallback that 404s. The app (both roles) treats `not_ready` as no
> failure, keys an automatic failure by descriptor signature (a re-push heals it) and retries after 30 min, and reports
> `startup.last_update_error {phase, reason, build, at}` up the fleet. Gateway follow-up: add `last_update_error` to
> `gateway/fleet.py _STARTUP_KEYS` (sanitised) to show it on the fleet row. Not published (0.6.151 rollout in progress).

> 2026-10-07 0.6.151: Breeze reads ONE SENTENCE PER SECTION (owner approved the A/B by ear; `long_form_policy`
> `max_sentences_per_section`, Breeze 1, others unchanged). A sentence under 30 spoken characters joins a neighbour in its
> paragraph; joins stay 1.0 s sentence / 1.5 s paragraph. Section Whisper reuses the language detected on an accepted section
> (one encoder pass instead of two). Not published: the coordinator runs the paced rollout and measures with
> `_orchestra/audit-20261001/breeze-ab/measure_rollout.py` (baseline 7 d: 13.7% section rejections, 4.58 fleet-s per audio-s).

> 2026-10-06 Qwen integration 0.6.150: ordinary selectable native Qwen Image 2.1 affine 4-bit and
> separate mflux q4 low-memory package. RAM floor 32 GB until physical lower-tier receipts; native
> package macOS 26.2+, base/low mflux macOS 14+. Four packaged low-memory 1K cases passed on physical
> 32 GiB M2 Max (5.51–5.85 GB, normal pressure, zero swap). Native engine's clean 32 GiB receipts are
> retained; current-host packaged startup needed 16.7 GB free and refused 14.9 GB, so no packaged native
> success or 24 GB claim is made. Owner will run SSD commands on physical 24 GB normal / 16 GB low.
> Both packs are SHA-verified on all four SSDs. Keep This Mac manually paused: owner's explicit reply.
> Tests full gate 2169 / 13 skips; focused checks after measurement corrections pass. Composite Image
> runtime c5ac478f85d5687e0bd6df715eec5d55a3a59e36aae4f14da352f45ea546923a; model assets outside app.

# Studio Hub — session handoff

**Read this first in a new session.** It is kept current with every release; see the rule in
`AGENTS.md`. If it disagrees with the code, the code wins — and then fix this file.

Last updated: **0.6.156 source verified; delivery receipt authoritative** (newest block first; the rest of this line and the blocks under it are older).

> **0.6.149 - a Hub restart re-runs the work it interrupted (`fix/restart-lost-free-work` off 0.6.148 a7fd296, 2026-10-05).**
> `broker._rerun_after_restart` (from `restore_batches`) requeues running Image/Music/Video/Voice items instead of fencing them
> `uncertain`; `_watch_restored_attempts` (dispatch loop) hands each original to `_watch_abandoned_attempt` to adopt or stop it.
> Last try -> `error`/`HUB_RESTARTED`. Transcription's `restore_batches` requeues likewise. A cancel in flight keeps the fence.

> **0.6.148 - why a LAN copy fell back is recorded; finished downloads survive an update (`fix/lan-share-followups` off 0.6.147 85a1344, 2026-10-05).**
> Plain: "Copying a model between Macs now records why a copy fell back to the internet; finished downloads are no longer marked interrupted by an update".
> The downloader prints `LAN copy failed, using the internet (internet|mixed): <reason>` when it drops the peer; a LAN run's status carries `lan_error` and
> `source_used` from that moment (`model_downloads._lan_fallback`). `shutdown()` records an already-exited child as it ended instead of `interrupted`.
> Gateway 0.2.126 relabels the row and counts it as the network's internet download. Per-release script
> `_orchestra/audit-20261001/release-0148/prepare-kit-0148.sh`; gate numbers, DMG and checksum in `_orchestra/audit-20261001/release-0148/`.
> Not installed / not on the drives / not published until the coordinator does it.

> **0.6.147 - Phonon-2 loads its real weights; admin test transcription on one chosen Mac (`fix/phonon2-nzrow` off 0.6.146 a58475d, 2026-10-05).**
> Plain: "Phonon-2 (optional English transcription) now loads its real weights correctly; still in testing, Whisper stays the default".
> `_phonon2_dense` now reads `nz_row` as the exclusive prefix sum of per-row nonzero counts (the real file's form; G0 found 0.6.146 refused every
> real block). New `POST /api/hub/fleet/nodes/{id|local}/transcribe` (Fleet token; local = `/studio/transcription/api/transcribe`, node = relayed to
> its own `local` route via `relay_model`, 960 s), and Subtitle's sync `/api/transcribe` keeps segment `words` when asked. Contract
> `_orchestra/audit-20261001/INTERFACES-0147.md` (Gateway 0.2.125 `POST /admin/api/controllers/{loc}/machines/{mid}/transcribe`). Per-release
> script `_orchestra/audit-20261001/release-0147/prepare-kit-0147.sh`; gate numbers, DMG and checksum in `_orchestra/audit-20261001/release-0147/`.
> Not installed / not on the drives / not published until the coordinator does it.

> **0.6.146 - Macs copy models from each other; Phonon-2 to try; narration timeline, text check and deadline (`release/0.6.146` = 0.6.145 76a935b + `--no-ff` merges of 5aac7dd `feat/lan-model-share`, b1abff5 `feat/phonon2-asr`, 6bf5646 `fix/breeze-failures` and 8b9b0a6 `feat/section-timeline`, 2026-10-05).**
> Plain: "Macs on the same network can copy a model from each other instead of each downloading it from the internet (the Gateway picks a nearby Mac; every
> file is checked)"; "Phonon-2, a small fast English transcription model, is available to try next to Whisper"; "Narration's text check allows a little more
> mishearing in short sections (a skipped phrase is still caught)"; "Long narration pieces get 45 minutes before they're stopped"; "Narration now reports where
> each piece of text sits in the audio, so subtitles can be timed exactly from the script".
> LAN share: `components/hub/model_share.py` (in-memory tokens, 30 min renewed on use, gone on restart, <= 2 transfers per Mac), share route
> `.../fleet/nodes/{id|local}/model-packages/{pkg}/share`, serve `GET/HEAD /api/hub/model-share/{token}/files/{path}`, download route `{"source": share}`
> with per-file internet fallback, run `source_used`; contract `_orchestra/audit-20261001/INTERFACES-0146.md` (Gateway 0.2.122). Phonon-2: package
> `phonon-2-mlx` (unqualified, 8 GB tier), `transcription.load_phonon2`, Voice/Subtitle `phonon2`; Whisper turbo stays default; catalog count 29.
> Breeze failures: `tts_quality` unheard share 0.15 (was 0.10), voice deadline floor 2,700 s (was 1,800 s). Timeline: `section_quality.timeline` =
> `[{section, start_s, end_s, text_start, text_end}]` (speech start/end per section in the joined file, tempo-adjusted; broker passes numbers only).
> `docs/model-benchmarks.md` regenerated for Phonon-2 (the branch had not). Per-release script `_orchestra/audit-20261001/release-0146/prepare-kit-0146.sh`. Gate numbers, DMG and checksum:
> `_orchestra/audit-20261001/release-0146/`. Not installed / not on the drives / not published until the coordinator does it.

> **0.6.145 - Breeze TTS 2 4-bit runs on 8 GB Macs (`release/0.6.145` = 0.6.144 d14ad78 + the owner's Breeze decision, 2026-10-04).**
> Floor 8 GB / 3.2 GB free in `memory_admission._FLEET_DEFAULTS` (measured 2026-10-04 on an 8 GB M1: started from 3.31-3.46 GB free, peak
> 4.69 GB, never critical). `worker_free_floor_gb` (was `override_free_gb`) forwards override-or-measurement to the Voice worker as
> `memory_floor_gb`. Kit: `PROVEN_SMALLER` carries it to 8 GB. Not Lite Mode. Per-release script
> `_orchestra/audit-20261001/release-0145/prepare-kit-0145.sh`. Gate numbers, DMG and checksum: `_orchestra/audit-20261001/release-0145/`.
> Not installed / not on the drives / not published until the coordinator does it.

> **0.6.144 - narration joins 1.0 s after a sentence / 1.5 s at a paragraph; a standing-by Lite Mode Mac is not group capacity (`release/0.6.144` = 0.6.143 a0f8263 + `--no-ff` merges of 9d13187 `feat/lite-capacity` d41f1dc `feat/kit-breeze-8gb-test` and 38402e5 `fix/install-model-visible`, 2026-10-04).**
> Pauses: owner decision 2026-10-04, `long_form_policy.UNIFORM_SENTENCE/PARAGRAPH_JOIN_PAUSE_SECONDS` = 1.00 / 1.50 (soft 0.15 unchanged) - the one source for the
> joiner, published `join_pause_milliseconds` (1000), the lane contract's `sentence_pause_sec` / `paragraph_pause_sec` defaults (bounds 0-3 s unchanged) and
> `tts_quality.max_pause_s` (+3.5 s margin). Requests sending their own pauses keep them. Lite capacity: `capabilities._model_capability` excludes a worker with
> `standing_by` (`peers.standing_by`) from `capacity_eligible`. Install a model: a status read reporting `done` re-reads that Mac's catalogs once per run (`StudioMonitor.refresh_machine_catalogs`). Kit: `qualification/RUN-8GB-BREEZE.command` (8 GB Breeze test, imports via `import_kit_packages.py`). Per-release script `_orchestra/audit-20261001/release-0144/prepare-kit-0144.sh`. Gate numbers,
> DMG and checksum: `_orchestra/audit-20261001/release-0144/`. Not installed / not on the drives / not published until the coordinator does it.

> **0.6.143 - calibrated TTS sampling defaults, gzip fleet JSON, no tiny sections, any-Mac group readiness, fleet model install, Khmer transcribe-back skipped (`release/0.6.143` = 0.6.142 aeb89a5 + `--no-ff` merges of fba7d3c `perf/controller-read`, a36ca55 `feat/kit-lite-models-copy`, 9231ee5 `fix/tiny-section`, 3543f56 `fix/0143-small`, 37eb4cd `fix/machine-counters`, fe486fa `feat/node-model-install`, 65e49f7 `fix/khmer-gate`, 2026-10-04).**
> Calibrated defaults live only in `catalog.SAMPLING_DEFAULTS_BY_REPO` (from `_orchestra/audit-20261001/tts-sampling/REPORT.md` + `recommended.json`), pinned by
> `test_sampling_levers.CALIBRATED_0_6_143`; family baselines stay `EFFECTIVE_IN_0_6_141`. `standalone.create_app` gzips JSON (not `application/octet-stream`).
> Splitter drops unspeakable paragraphs and merges sections under 12 spoken chars; text-caused no-audio is a ValueError; `broker._counts_against_model` lifts stored
> text-caused pauses on start. `memory_admission.publish_floor` makes the published tier the admission floor. `_a_joined_mac_can_run` accepts a job any joined Mac can
> run. `activity.REFUSAL_CODES` (`INSUFFICIENT_MEMORY`) are not failures; `/api/hub/stats?activity_since=`. Fleet install routes
> `.../fleet/nodes/{id}/model-packages/{pkg}/download` + `.../model-downloads/{run}` (contract `_orchestra/audit-20261001/INTERFACES-0143.md`). Khmer: score advisory
> (`text_check`), `transcribe_back_applies` False (Whisper not loaded). Kit: `copy-lite-models.command` -> "2 - Copy the Lite Mode models"; shop commands retired by
> `prepare-kit-extras.sh`; per-release script `_orchestra/audit-20261001/release-0143/prepare-kit-0143.sh`. Gate numbers, DMG and checksum:
> `_orchestra/audit-20261001/release-0143/`. Not installed / not on the drives / not published until the coordinator does it.

> **0.6.142 - every TTS model's sampling levers reachable (defaults unchanged), Khmer narration fixes, Lite Mode in Settings, paused-group rule for transcription (`release/0.6.142` = 0.6.141 + a599402 + 37c7bc7 + merges of 10e60cb `feat/lite-mode-every-role` (on 2e0edf2 `feat/workstation-app-settings`) and a2b6284 `fix/paused-group-chat-transcription`, 2026-10-04).**
> `catalog.SAMPLING_LEVERS` is the one table (family -> request field -> engine kwarg, bound, default; `SAMPLING_DEFAULTS_BY_REPO` per checkpoint);
> `catalog.sampling_kwargs` is applied once in `_generate_mlx_audio` after the mode resolver (and in `_generate_khmer_vits` as VitsModel attributes);
> `lane_contract.parameters_for` publishes them. Defaults = 0.6.141's effective values (mlx-audio `generate_audio` passes temperature 0.7 to all). To adopt
> calibrated defaults, edit only that table and the pin in `components/voice/app/tests/test_sampling_levers.py`. Khmer MMS seeds like MLX now.
> Khmer: ។/៕ in `_QWEN_SENTENCE_ENDINGS`; `_split_long_text_unit` prefers ។/៕, then space/U+200B/`,;:`, then `_cluster_boundary`; gate tokens include Khmer
> syllables (`qwen_quality._KHMER_SYLLABLE`), Khmer thresholds `tts_quality.KHMER_*` are a loose unmeasured guess; Whisper reads Khmer sections as `km`.
> Audit + calibration plan: `_orchestra/audit-20261001/tts-sampling/`. Not installed / not on the drives / not published until the coordinator does it.

> **0.6.141 - a paused group hands its work back; TTS admitted once per job on one reading (`release/0.6.141` = 0.6.140 + the TTS-comparison fixes, 2026-10-04).**
> 0.6.140 was never published; 0.6.141 carries workstation mode too. `broker.every_capable_mac_paused(modality, model, routing)`: known capable Macs
> (`_model_rows` + `_can_run`) all `_paused` (machine/Studio paused or drained) -> `POST /api/hub/jobs` 503 `{"error","code":"NO_AVAILABLE_MACHINE","detail"}`
> (not for a replay) and `_dispatch_loop` -> `_hand_back_queued` (queued items `error` / `NO_AVAILABLE_MACHINE`, no try, durable write then webhooks).
> Offline/busy Macs still count. Broker lanes only (chat/transcription: open). Voice `_memory_preflight`: skipped for `_memory_admitted` sections
> (index > 1 or a re-render; the guard re-measured the job against its own resident model: "2.8 GB needed, 2.0-2.5 available" on idle 8 GB Macs), and frees a
> different idle model before refusing; `release_idle_siblings` also releases the idle target. `_insufficient_memory_text(b, studio, decision, ...)` parses the
> refusal's own numbers. Voice `memory_eligible` = total >= tier only. Contract: `_orchestra/audit-20261001/INTERFACES-0141.md`. Gate numbers, DMG and
> checksum: `_orchestra/audit-20261001/release-0141/`. Not installed / not on the drives / not published until the coordinator does it.

> **0.6.140 - workstation mode for shop Macs (`release/0.6.140` = 0.6.139 + 5e5ad77 `feat/workstation-mode` incl. 9e44011, 2026-10-03).**
> `components/hub/workstation.py`: `<hub data>/workstation.json` (default off), set via controller relay `POST /api/hub/fleet/nodes/{id}/workstation` ->
> node `/api/hub/workstation`; `workstation` block on `/api/hub/resources` + fleet rows. On: workers at `PRIO_DARWIN_BG`, only `components/workstation_models.py`
> (6 models, package + repo ids; checked against `model-manifests/` after 0.6.139, all ship, none retired/shelved), only-when-idle / step-aside / quiet hours,
> no pop-ups. Critical memory pressure 20 s -> job handed back as `WORKSTATION_YIELDED` -> broker re-queues on another Mac of the group or returns it to the
> Gateway, no try spent; `WORKSTATION_STANDING_BY` / `WORKSTATION_MODEL_NOT_ALLOWED` refusals are waits (30 s / 600 s). Kit Extras: `Set Up a Shop Mac.command`,
> `Set Up the Shops Controller.command` (copied by `prepare-kit-extras.sh` beside Yam Display); `NODE-SETUP-CHECKLIST.md` "Shops". Merged with no textual
> conflicts against 0.6.139's uniform narration (broker hunks are separate). Gate numbers, DMG and checksum: `_orchestra/audit-20261001/release-0140/`.
> Not installed / not on the drives / not published until the coordinator does it. Previously **0.6.126** (merged into `implementation/voice-runtime` + `main`; DMG built; **NOT installed anywhere, not on the drives, nothing published**) over **0.6.125** (macOS free memory; installed on this Mac; 2026-10-01).

> **0.6.139 - one pause rule, 250-character sections and 1,500-character requests for every voice; the kit installs Yam Display (`release/0.6.139` = 0.6.138 + 3f59771 `feat/kit-yamdisplay` + 52eb9a7 `feat/uniform-narration` incl. 37b0971, 2026-10-03).**
> `long_form_policy` uniform joins 0.6 s sentence / 1.0 s paragraph / 0.15 s soft for every family (request `sentence_pause_sec` / `paragraph_pause_sec` override);
> `policy_for(uniform=True)` caps sections at 250 for every model (audit records unchanged, checked against the measured policy); `lane_contract.maximum_input_characters`
> 1,500 for every TTS model (smaller if a timed slowest Mac needs it); gate silence allowance = pause + 3.5 s (4.1 / 4.5 s). Kit: `packaging/kit-extras/` Yam Display
> installer command, run by the install/update launchers. Write-up: `_orchestra/audit-20261001/uniform-narration.md`. Gate numbers, DMG and checksum:
> `_orchestra/audit-20261001/release-0139/`. Not installed / not on the drives / not published until the coordinator does it.

> **0.6.138 - section gate catches internal silence and dropped clauses (`release/0.6.138` = 0.6.137 + f5d7946 `fix/gate-internal-silence`, 2026-10-03).**
> `tts_quality.audio_metrics` -> `longest_pause_s` (longest unvoiced run between first and last voiced frame); `TTS_SECTION_INTERNAL_SILENCE` when it exceeds the
> text's longest pause (paragraph 0.6 s / sentence 0.3 s) + 3.5 s, / native speed; no model, so 8 GB Macs run it. Transcribe-back also fails on > 3 consecutive
> unheard words (`longest_missing_run`). Evidence revision v2; `broker._SECTION_QUALITY_*` whitelist carries the new code and numbers. Write-up:
> `_orchestra/audit-20261001/tts-gate-internal-silence.md`. Gate numbers, DMG and checksum: `_orchestra/audit-20261001/release-0138/`. Not installed / not on the
> drives / not published until the coordinator does it.

> **0.6.137 - per-section TTS quality gate with re-render, Breeze `max_tokens` bounded by text, `maximum_input_characters` from the slowest Mac (`release/0.6.137` = 0.6.136 + c6ea53b `fix/whisper-gaps-tts-deadline` + f115fb5 `fix/tts-quality-gate`, 2026-10-03).**
> `catalog.SlowestMacSpeed` on a TTS row -> `lane_contract.maximum_input_characters` (+ `_source`) = floor(900 s / RTF x chars/audio-s, to 100); Breeze 1,500 (M1 16 GB,
> RTF 5.13, 8.58 chars/s); Gateway deadline scaling and Story splitting read it. `tts_quality.py`: per-section DC/silence/duration checks + Whisper-turbo transcribe-back
> (only when turbo + 2 GB free; skip reason recorded); every mlx-audio family goes through the parent section loop; a rejected section retries with seed +7919 up to 3x,
> then `TTS_SECTION_*` "Section N of M"; Breeze `max_tokens` bounded by the duration ceiling. `section_quality` on the job, copied to the batch item by
> `broker._record_worker_section_quality`. Write-ups: `_orchestra/audit-20261001/whisper-gaps-tts-deadline.md`, `tts-quality-gate.md`. Gate numbers, DMG and checksum:
> `_orchestra/audit-20261001/release-0137/`. Not installed / not on the drives / not published until the coordinator does it.

> **0.6.136 - drain never starves, relaunch only after a real crash + `last_stop`, free work re-run when its Mac or worker dies (`release/0.6.136` = 0.6.135 + e505ad9 `fix/drain-never-starves` + b65a9ad `fix/crash-only-and-reroute`, 2026-10-03).**
> `broker.drain_holds_group()`: once a draining controller's own Mac runs nothing, `busy_machines` names every machine in the group and
> `_submission_readiness` returns `MACHINE_BUSY`, so the nodes finish and the controller installs (re-runs dispatch through the same gate). Native
> `StopRecord` (`ShellCore.swift`) classifies the last stop from heartbeat/signal/memory/clean-quit/updating/power-off markers + `.ips` reports; standby exits 1
> only for a crash (`AgentStandby.shouldStayStopped`), no plist change. `<data>/last-stop.json` -> `last_stop {reason, at, label, relaunched}` on
> `/api/hub/resources`, fleet overview and `/api/hub/fleet/nodes` rows (stale relay for unreachable nodes). `_run_item`: a worker that went silent, lost the job
> or restarted (`WORKER_RESTART_UNCERTAIN`, Voice `EXECUTION_IDENTITY_LOST`) -> `_abandon_attempt` re-runs it in every lane; `_watch_abandoned_attempt`
> cancels the original or adopts its finished result (first result wins); voice re-runs get `...:rerun-N` request ids. Gate numbers, DMG and checksum:
> `_orchestra/audit-20261001/release-0136/`. Not installed / not on the drives / not published until the coordinator does it.

> **0.6.135 - worker exits restart only that worker, the launch agent supervises the app, unmanaged voices stay local, pause a node (`release/0.6.135` = 0.6.134 + 214ad11 `fix/worker-exit-resilience` + 1194f1b `feat/pause-node`, 2026-10-03).**
> `packaging/hub_worker.py` restarts a private worker that exits after start-up on its own port (backoff 1/2/5/10/30/60 s, reset after 5 min up; fatal only before
> the Hub is ready; `run_server(shutdown_started=...)` stops restarts once shutdown begins). Native (`ShellCore.swift`, `StudioHub.swift`): a hand/update launch
> loads+kickstarts the launch agent; the agent's copy waits on the data-folder lock before AppKit, exits 0 after a clean Quit (marker) else 1 so launchd relaunches
> (default data folder only). `broker.py`: an unmanaged `voice_library_id` routes only to the studio holding it. Fleet-token `POST /api/hub/fleet/nodes/{id}/pause`
> `{paused}` -> `registry.machine_enabled`, persisted in `machine_flags.json`, roster publishes `paused`. Gate numbers, DMG and checksum:
> `_orchestra/audit-20261001/release-0135/`. Not installed / not on the drives / not published until the coordinator does it.

> **0.6.134 - peer jobs polled to their own answer, leases end with the job, MACHINE_BUSY is a wait everywhere, abandoned peer work cancelled (`release/0.6.134` = 0.6.133 + 2e29bea + ec170ac + 59b6ec6, branch `fix/image-machine-id-reserved`).**
> `broker.py` dispatch poll + `_reconcile_async_item` pass `remote=not peers.is_local_studio(studio)` to `_validate_durable_worker_job` (was: every peer
> image/music/video job failed its first poll; only jobs under `RECOVERY_WINDOW_S` survived). `standalone._release_finished_peer_leases` frees a node's lease once
> its own worker says the job is over/gone. `transcription_jobs._run_item` and `chat_jobs._run_pack` treat `broker._is_machine_reservation_refusal` as a wait.
> `broker._cancel_abandoned_peer_job` (background task, set `_abandoned_peer_cancels`) DELETEs the abandoned remote Image/Voice job on its node, logged on
> `studiohub.broker`. Gate numbers, DMG and checksum: `_orchestra/audit-20261001/release-0134/`. Not installed / not on the drives / not published until the coordinator does it.

> **0.6.133 - fleet health follows each node, renamed Macs keep their new name, rename from the Gateway (`release/0.6.133` = 0.6.132 + 8cff7b1 + 9a7e786 on main).**
> `fleet_nodes.sync_roster_from_snapshots` (monitor poll) makes a node's roster studios and name follow its authenticated peer snapshot (`machine: {id, name}` now in
> `/api/hub/resources`); silence/errors/older Hubs change nothing. `activity._state_at`: an error holds `needs_attention` only 15 min. Gateway rename: Fleet-token
> `POST /api/hub/fleet/nodes/{id}/name` -> `relay_name` -> node `POST /api/hub/machine-label`, then the roster (a Mac's name lives on the Mac). Handover join now sends
> `public_settings()` machine name. Write-ups: `_orchestra/audit-20261001/node-rename.md`, `fleet-health/`. Gate numbers, DMG and checksum:
> `_orchestra/audit-20261001/release-0133/`. Not installed / not on the drives / not published until the coordinator does it.

> **0.6.132 - Wave Frame icon, fleet-token rotation grace, unregistered voice outputs to the Trash Folder, 16 GB Fish test (`integrate/0.6.132` = 0.6.131 + `fix/small-batch-0132` d9e3534 + `feat/fish-16gb-test` 4fc4694 + the logo commit).**
> New app icon (owner's pick, concept 2 "Wave Frame"): sources `assets/logo/app-icon.svg` + `menubar-icon.svg`; **`python3 packaging/make_app_icon.py`** renders `assets/app-icon.icns`
> (every size drawn at its own pixel size), `app-icon-1024.png`, `menubar-icon.png` (18 px) and `menubar-icon@2x.png` (36 px). The menu-bar item loads the two PNGs from
> `Contents/Resources` as one template image (`AppDelegate.menuBarMark()`); `stage_voice.py` refuses a build without them. The Voice Studio web UI keeps its own
> `components/voice/app/frontend/icon.png` (a separate product surface, not changed). Fleet-token rotation: nodes that missed it keep the old token 30 days and are handed the
> new one when they answer (`components/hub/backend/peers.py`, `auth.py`); the disk governor moves never-registered Voice audio to the Trash Folder after the 7-day window.
> 16 GB Fish test: `qualification/RUN-16GB-FISH.command` + `import_kit_packages.py` (kit script copies both; they are in `prepare-kit`'s MANAGED list).
> Gate numbers, DMG and checksum: `_orchestra/audit-20261001/release-0132/`. Not installed / not on the drives / not published until the coordinator does it.

> **0.6.130 - text-led 0.3/0.6 s pauses for Fish/VoxCPM2/Chatterbox/Pocket, Fish 4-bit on the 16 GB tier, Breeze back to 16 GB only (`integrate/0.6.130` = 0.6.129 + `fix/pauses-tiers-0130` 645d542).**
> Supersedes the 0.6.129 Breeze 8 GB carry and the 24 GB Fish 4-bit tier. Gate numbers, DMG and checksum: `_orchestra/audit-20261001/release-0130/`. Not installed / not published until the coordinator does it.

> **0.6.129 - library ownership + Fish S2 Pro 4-bit + Fish bounded decode + Breeze 8 GB carry (`integrate/0.6.129`; merges `fix/library-ownership`, `feat/breeze-8gb-carry`, `feat/fish-4bit`, no conflicts).**
> A Hub only moves packages out of a model library it owns (`components/library_ownership.py`, `<library>/.studiohub/owner.json`; a temp data dir never owns one);
> the launch tests and `verify-launch.sh` use a temporary library (`tests/test_no_real_library_launch.py` scans for regressions). Fish S2 Pro 4-bit
> (`majentik/fishaudio-s2-pro-MLX-4bit`, 24 GB tier, floor 8.4 GB measured) carries the 8-bit release's `codec.safetensors`; Fish decoding is `bounded`
> (S2 peak 14.6 -> 7.4-8.1 GB). Breeze TTS 2 4-bit is copied to 8 GB Macs. Gate numbers, DMG and checksum: `_orchestra/audit-20261001/release-0129/`.
> Not installed, not on the drives, nothing published until the coordinator does it.

> **0.6.126 - even TTS pauses + short-line level match (from `fix/tts-join-pauses`, code commit `a61c2f0`; NOT installed, NOT published).**
> Owner: "it's glaring". Cause and fix are in `CHANGELOG.md` 0.6.126: `generation._join_long_form_wavs` (every engine) wrote each
> section whole then added the designed pause, and every engine returns up to ~1.2 s of near-silence at section edges, so joins
> were median ~1.0 s vs 0.12-0.6 s designed. Now each section is cut to its speech + pad (`_analyse_join_section`, `_join_cut`,
> `_JOIN_*` constants), the zeros are designed pause minus the pads, and sections with < 4 s voiced audio are level-matched
> (`_join_match_gains_db`, +/-6 dB, soft-knee peak guard). Pause values (`join_pause_seconds`, OmniVoice/Qwen 1.7B paragraph/sentence/soft)
> are unchanged. The Qwen3 1.7B Base audit flag `private_edge_destructive_trim` stays False (only near-silence is cut). Proof on the
> audit's real clips: `_orchestra/audit-20260930/join-fix/` (`results.txt`, `rejoin_demo.py`): median gap 0.85 s -> 0.14 s, worst 2.92 -> 0.60 s,
> after-minus-designed p95 30 ms / worst 60 ms; worst short-section step 7.3 -> 2.0 dB. No live Hub generation was run for the proof.
> **Gate:** `unittest discover -s tests` -> **1633 tests, OK (13 skipped), 662 s, exit 0**; `components/voice` pytest (`PYTHONPATH=../../.build/test-deps:../..`,
> hub-gate python) -> **777 passed, 2 skipped**, incl. 11 new `test_long_form_join_edges.py` (7 fail on 0.6.125's joiner). `native/build.sh` NOT re-run: no
> native/Swift change, the staged native binary is the same as 0.6.125's (the owner can re-run it). **DMG** `dist/StudioHub-0.6.126-a61c2f0.dmg`
> (1,567,211,662 B, SHA-256 `392d99d6c3388cd11063c3be8dda37e1ed9018bcf85a5821a731f8d5c021309e`, `hdiutil verify` valid, `bytes-wasted: 0`; the first output with
> 394 MB dead space is `_trash/dist/StudioHub-0.6.126-a61c2f0-wasted394MB.dmg`; built with `.build/make-dmg-noprune.sh` so no kit drive was touched, the 0.6.125 DMG
> moved to `_trash/dist/`). Stage `.build/StudioHub-0126/Studio Hub.app` (manifest `0.6.126` / `a61c2f0...`); delta vs 0.6.125 is 12 small files
> (voice `generation.py` / `CHANGELOG.md` / `VERSION`, `Info.plist`, `release-manifest.json`, `release-notes.md`, file-manifest); runtimes and native binary identical.
> **Rollout (owner OK needed first):** this Mac -> one controller -> rest; `_orchestra/audit-20260930/update-0120/diag/publish-local-0126.py` is prepared, NOT run.

> **0.6.125 - macOS free memory + measure-anyway runner (merged from `fix/macos-free-memory`, no squash; the 0.6.121 video bridge label is unchanged).**
> `components/host_memory.py` is now the ONE answer to "what can a new job use?" (Hub `resources.host_stats`, Voice `_memory_snapshot`
> + resource telemetry, the TTS runner): max(psutil available, free + file-backed + purgeable pages) - `kern.vm_page_free_target`,
> capped at total - wired - compressor, via sysctl counters. Measured on this 32 GB Mac: `memory_pressure -Q` / `kern.memorystatus_level`
> = (active + inactive + free + speculative) / total (87% constant while free pages fell to ~0 under a 12 GB allocation), so it is a
> pressure signal, NOT headroom - never gate on it. 1101's run.log has no vm_stat, so its "would have yielded" figure is an estimate:
> psutil said 3.0 GB; the new figure adds the active file cache + purgeable pages and is >= 3.0 (admitted if file cache + purgeable +
> free >= ~3.4 GB). New `PUT/GET /api/hub/memory/admission` exposes the (previously route-less) operator free-memory override; the Hub
> forwards it to the Voice worker as `memory_floor_gb` and `_memory_preflight` honours it. The 8 GB TTS runner measures a held model with
> the guard lowered (0.5 GB, safety net: swap > 1.5 GB or urgent pressure), always restores it (`tts8-guard-restore.json` beside
> `hub-ready.json` for a dead run), and recommends measured peak + 0.25 GB. The 3.2 GB floor is unchanged. The chat-tier and z-image
> runners still use their own (stricter) figure; harmonise if their floors start refusing healthy Macs.

> **Gate / DMG / delta / proof (2026-10-01):** `unittest discover` -> **1633 tests, OK (13 skipped), 590 s, exit 0**; `native/build.sh` green
> (boundary, API contract, lifecycle). **Delta 0.6.124 -> 0.6.125: 13 files, 13 blobs, 1,179,980 B to send** (`host_memory.py` new, hub
> `broker.py` / `memory_admission.py` / `resources.py` / `standalone.py`, voice `generation.py` / `main.py` / `resource_telemetry.py`,
> `Info.plist`, `release-manifest.json`, `release-notes.md`, `voice/CHANGELOG.md`, `voice/VERSION`); 0 removed; totals 77,468 files,
> 4,202,376,666 B (the native binary is byte-identical to 0.6.124's). **DMG** `dist/StudioHub-0.6.125-468713e.dmg` (1,570,469,646 B,
> SHA-256 `5090e8fd0151204b0047f92fd4d53cb12d7a2351a0a84b23478d751f4726dfe7`, `hdiutil verify` valid, `bytes-wasted: 0`); the first output
> carried 519 MB of dead space (kept as `_trash/dist/StudioHub-0.6.125-468713e-wasted519MB.dmg`), re-compacted with `hdiutil convert -format
> UDZO`. Installable app: `.build/StudioHub-0125/Studio Hub.app` (manifest `0.6.125` / `468713e2c87cf152892824593a831fa25c5f82a6`).
> **Proved on this Mac (live fleet location, `keep_all_models` true):** published to the LOCAL Hub only (delta push, 13 blobs); the app
> updated itself with no click and no kill: published 16:19:35, Hub down 16:21:56, back on 0.6.125 at 16:22:11 (outage ~15 s, 2 min 36 s
> from publish). `GET /api/hub/memory/admission?model=<repo>` answers (the `model` query is required); the Hub's `host.available_gb` read
> 24.1 GB against psutil's 19.5 GB on the same instant (Mac idle, +4.6 GB of file cache psutil leaves out); the 8 GB runner `--quick`
> (labelled "not 8 GB evidence") ran all three models (S2 55.9 / 95.6 / 71.4 s) and wrote the new columns; no shelved folder moved. The
> four SSDs carry 0.6.125 (kit refresh + `kit_stamp_index.py`, rsync -acn clean, runner files identical, Story/Gateway checksums OK).
> Nothing was published to any fleet controller.

> **8 GB TTS runner - memory stall handling (kit-only, after 0.6.124, 2026-10-01; no app change, no version bump).** The owner's
> real 8 GB Mac (node 1101, Hub 0.6.122) sat forever on Qwen3-TTS 0.6B 8-bit: the Hub holds a job back until
> `host_stats()["available_gb"]` (until 0.6.125: psutil macOS = free + speculative + inactive pages; see the block below) is at least
> the model's floor, here **3.2 GB** (`components/voice/app/backend/catalog.py` `min_free_memory_gb=3.2` -> `workload_policy` ->
> `memory_admission.describe` -> `broker._memory_gate`, `broker.py` ~296), and the Mac had 3.0. The 3.2 is the Voice worker's own
> formula (`generation.py` `_required_free_memory_gb`: catalog `size_gb` 1.9 + `MEMORY_HEADROOM_GB` 1.25 = 3.15, rounded up) -
> labelled "measured" in the catalog comment but **not a measurement**; the worker's own `_memory_preflight` refuses at the same
> 3.15, so lowering only the Hub number would not start the job. The Hub already asks idle sibling Studios to release before it
> queues a job (`broker.prepare_machine_memory` -> `release_idle_siblings`); what stays is macOS + apps, which no release frees.
> The reason is on the job (`GET /api/hub/jobs/<id>` -> `governor_note`, e.g. `voice: waiting for memory: needs ~3.2GB, ~3.0GB free`).
> `qualification/eight-gb-tts-run.sh` now (a) calls `POST /api/hub/memory/release` before each model and records free memory +
> what holds it (`free_gb_before`, `memory_released`, `memory_holders`), (b) reads `governor_note` while a clip is queued and, after
> `TTS8_MEM_WAIT` (default 300 s) of memory waiting, records `did not start - needs X GB free, had Y GB`, cancels the job
> (`DELETE /api/hub/jobs/<id>`), skips that model's other clips and continues; `summary.txt` says so. Pinned by
> `tests/test_tts_8gb_runner.py::MemoryStallTests` (fake Hub; mutation-proved). Copy the five TTS files to the drives as before.

> **0.6.124 - Chatterbox shelved + the 8 GB TTS test** (owner, 2026-10-01; **supersedes 0.6.123, which was never published or
> installed - roll out 0.6.124**). Read `CHANGELOG.md` 0.6.124; what to know:
>
> - **Shelved, added:** `chatterbox-4bit`, `chatterbox-8bit` and `s3tokenizerv2` (+ repo ids) in `SHELVED_MODELS`. S3TokenizerV2
>   is required only by the two Chatterboxes (checked in every manifest's `requires_packages`), so it goes with them; the
>   existing "no kept model needs a shelved one" test guards that. Same semantics as the others (catalog yes, kit copy step
>   skips, non-keep-all Hub moves installed copies to `hub/trash/<date>/shelved-models/`, 15 days). **Mark the owner's own Mac
>   keep-all BEFORE it starts 0.6.124** or its Chatterbox folders move at first start. Weights stay on the SSD model library.
> - **8 GB TTS test:** `qualification/RUN-8GB-TTS.command` (double-click; in the kit's `Technical` folder) -> `eight-gb-tts-run.sh`
>   (shell shim + embedded Python run by the installed app's own `runtimes/voice/bin/python3`) + `README-8gb-tts.md` +
>   `tts-script-S2.txt` / `tts-script-S4.txt` (the 32 GB test's texts). Qwen3-TTS 0.6B 8-bit -> VoxCPM2 4-bit -> OmniVoice bf16
>   x Chris (`cd07b21a2ccf`) / Christian (`0616aec33b41`) x S2/S4 through the local Hub `POST /api/hub/jobs` (modality `voice`;
>   token from `hub-ready.json`, never printed). Pre-checks (RAM, Hub >= 0.6.122, three packages `files_verified`+`eligible`,
>   two voices, no active Hub jobs) refuse in plain words and write nothing. Results folder `TTS 8GB Run <stamp>` beside the
>   script (Desktop fallback): WAVs, `results.csv` (32 GB `summary.csv` columns + status/error/memory_pressure/swap/RAM/macOS/
>   app version/...), `summary.txt`. `--quick` (S2, Chris, per model) is the self-test; `--models`, `--allow-non-target`,
>   `TTS8_TIMEOUT`, `TTS8_OUT`, `TTS8_READY_FILE`. Verified on this 32 GB Mac against the live 0.6.122 Hub (quick, all three
>   models, S2 audio 68.4 / 78.8 / 68.08 s = the 32 GB test's; 38.9 / 91.4 / 63.4 s vs 42.5 / 97.0 / 80.3 s). **Nothing has been run on
>   an 8 GB Mac yet.** The kit step lives in the gitignored `.build/prepare-kit.sh` (copies the five files, `chmod`, and lists
>   them in the Technical folder's MANAGED set so the prune keeps them) - **a fresh checkout loses it**; `tests/test_tts_8gb_runner.py`
>   pins it where the file exists. The drives are NOT refreshed - the coordinator does that.
> - **Gate / DMG / delta:** `unittest discover` -> **1615 tests, OK (13 skipped), 565 s, exit 0**; `native/build.sh` green (boundary,
>   API contract, lifecycle). **Delta 0.6.122 -> 0.6.124: 9 files, 9 blobs, 13,692,433 B to send** (native binary, `Info.plist`,
>   `hub/standalone.py`, `hub/standalone_config.py`, `shelved_models.py`, `release-manifest.json`, `release-notes.md`,
>   `voice/CHANGELOG.md`, `voice/VERSION`); 0 removed; totals 77,467 files, 4,202,366,624 B. **DMG**
>   `dist/StudioHub-0.6.124-87e1ebc.dmg` (1,568,108,537 B, SHA-256
>   `9064c8c45d1f865cb9708a17f229b1a8ae1f846e0659fda81d1d9548dafff4b8`, `hdiutil verify` valid, `bytes-wasted: 0`); the first
>   output carried 403 MB of dead space (kept as `_trash/dist/StudioHub-0.6.124-87e1ebc-wasted403MB.dmg`), re-compacted with
>   `hdiutil convert -format UDZO`. The 0.6.123 DMG is in `_trash/dist/`. Installable app:
>   `.build/StudioHub-0124/Studio Hub.app` (manifest `0.6.124` / `87e1ebc73630bbc6f1f7fc14c7738905cb936c5c`). **Not installed, not
>   on the drives, nothing pushed to controllers.** (The 0.6.123 stage `.build/StudioHub-0123` is superseded; do not install it.)

> **0.6.123 - Relaunch safety net + SHELVED MODELS** (owner-approved 2026-09-30/10-01). Read `CHANGELOG.md` 0.6.123; what to know:
>
> - **Relaunch safety net** (`native/StudioHub.swift` `RelaunchHelper`): the helper `relaunchAfterUpdate` starts waits for the old
>   app pid 120 s, then SIGTERM, 10 s, SIGKILL, then `open`s the new app (normal path unchanged). It is spawned with `posix_spawn`
>   in its own session (`POSIX_SPAWN_SETSID`) and no inherited descriptors (`POSIX_SPAWN_CLOEXEC_DEFAULT`): the login launch agent
>   has no `AbandonProcessGroup`, so launchd kills the app's leftover process group at exit and would have taken a same-group
>   helper with it. Seam `spawnRelaunchHelper: (pid, appPath)`. `lifecycle_main.swift` `relaunch-helper` runs the real helper
>   against stand-ins (exits by itself / never exits / ignores SIGTERM); mutation-proved (no escalation, no SIGKILL, no SETSID,
>   kill-at-once each fail). A Mac below 0.6.123 relaunches with its own old helper when it takes this update.
> - **Shelved models** (`components/shelved_models.py`, `SHELVED_MODELS`): `whisper-base`, `whisper-small`, `voxcpm2-bf16`,
>   `fish-audio-s2-pro-bf16` (+ their `mlx-community/...` repo ids). Stay in the catalog and usable where present; weights stay in
>   the kit's SSD model library; `copy_models_for_this_mac.py` skips them ("Shelved, not copied") unless keep-all; the Hub, at
>   start-up on a Mac not marked keep-all, moves an installed one to `hub/trash/<UTC date>/shelved-models/` (purged after 15
>   days) and refuses download/import (409, "Not on this Mac: ..."); the row is `not_installed` + `shelved: true`, never `invalid`.
>   Whisper turbo is the Subtitle default already (`"default": true`, only manifest) and is not shelved.
> - **KEEP-ALL (device setting `keep_all_models`, default false):** set it with `PUT /api/hub/settings {"keep_all_models": true}`
>   (Hub token) or by adding `"keep_all_models": true` to `~/Library/Application Support/StudioHub Standalone/hub/device_settings.json`.
>   Restore-defaults keeps it. **Mark THIS Mac keep-all BEFORE it starts 0.6.123** or its four shelved folders move to the Trash
>   Folder at that first start (recoverable from `hub/trash/<date>/shelved-models/` for 15 days). The Gateway/Story subtitle model
>   selection (still Whisper base, 42 jobs) is the coordinator's switch to turbo.
> - **Limit:** the move is a same-volume rename; a model library on another volume than the Hub data folder is skipped, not copied.
> - **BRIDGE KEPT** (video `runtime: 'voice'`), unchanged.
> - **Gate / DMG / delta:** `unittest discover` -> **1610 tests, OK (13 skipped), 649 s, exit 0**
>   (a first run had 1 failure, the device-settings dict shape pinned in `test_hub_standalone`, updated); `native/build.sh`
>   green (boundary, API contract, lifecycle incl. `relaunch-helper`). **Delta 0.6.122 -> 0.6.123: 9 files, 9 blobs, 13,687,418 B
>   to send** (native binary, `Info.plist`, `hub/standalone.py`, `hub/standalone_config.py`, new `shelved_models.py`,
>   `release-manifest.json`, `release-notes.md`, `voice/CHANGELOG.md`, `voice/VERSION`); 0 removed; totals 77,467 files,
>   4,202,361,609 B. **DMG** `dist/StudioHub-0.6.123-6af8ae9.dmg` (1,567,659,850 B, SHA-256
>   `e7799910ca05aae54230193416ed2c4d6608b3f12cc157ce3170959e0eb2a5d9`, `hdiutil verify` valid, `bytes-wasted: 0`); its first
>   output carried 376 MB of dead space (kept as `_trash/dist/StudioHub-0.6.123-6af8ae9-wasted376MB.dmg`), re-compacted with
>   `hdiutil convert -format UDZO`. The 0.6.122 DMG moved to `_trash/dist/`. Installable app:
>   `.build/StudioHub-0123/Studio Hub.app` (manifest `0.6.123` / `6af8ae9295bac17e4c14224ebbd0d227f080b239`). **Not installed, not
>   on the drives, nothing pushed to controllers.**

> **0.6.122 - Updates restart the app by themselves (relaunch hang fixed; bridge KEPT)** (0.6.119-0.6.121 installs hung
> half-quit on four controllers, 2026-10-01). Read `CHANGELOG.md` 0.6.122 and
> `_orchestra/audit-20260930/update-0120/diag/relaunch-*` (incident) and `relaunch-fixed-0122-*` (proof); what to know:
>
> - **Cause:** `AppDelegate.relaunchAfterUpdate` called `NSApp.terminate` from the release tick, a main-actor task. With the
>   engine running `applicationShouldTerminate` answers `.terminateLater`, AppKit waits in a nested loop, and the engine's
>   exit (a main-actor task) can never run until the tick returns -> never replied, never quit, the reopen helper waited for
>   ever. **Fix** (`554ed1f`, `native/StudioHub.swift`): stop the engine first, quit from `onStopped` (`engineStopped`), so
>   `terminate` runs with nothing running and answers `.terminateNow`. Seams `spawnRelaunchHelper` / `terminateApp`;
>   `lifecycle_main.swift` `relaunch-after-update` (mutation-proved: quit straight away -> "must not quit while the engine is
>   still running" fails); `test_update_unattended` pins the source shape.
> - **Other paths audited, none changed:** no other `NSApp.terminate` runs inside a main-actor task. User Quit goes
>   `terminate` (menu/Dock/Apple event on the run loop) -> `.terminateLater` -> a task that only `reply`s: safe, and proved
>   live below. The one other `terminate` is the launch-error alert (no controller -> `.terminateNow`). Known, not a hang:
>   `confirmForceStop`/`confirmQuitWhileBusy` run `runModal` inside a task, so while such a dialog is up other main-actor
>   tasks wait for the answer.
> - **BRIDGE KEPT** (`runtime: 'voice'` for video in `_stage_video`): Macs on 0.6.119/0.6.120 must still accept this
>   release. Revert it (and delete `test_bridge_release_video_is_verifiable_by_the_apps_already_installed`) only once no Mac
>   runs below 0.6.121.
> - **A Mac on 0.6.119-0.6.121 still hangs once, taking THIS update** (its own old app does that relaunch): after the install
>   the new app is on disk; `kill <pid>` (Force Quit) the old process, then open Studio Hub. From 0.6.122 on it is automatic.
> - **End-to-end proof on this Mac (no manual step):** real 0.6.122 installed with `install_local.sh`; a throwaway TEST release
>   (`0.6.122.1`, build `ca4380f`, scratch commit on a local branch never pushed, stage moved to `_trash/test-releases/`) was
>   pushed ONLY to 127.0.0.1:47873 (delta, 3 blobs, 3.4 KB, signed with the fleet publisher key; version comparator:
>   `ReleaseVersion` accepts 4 components, `0.6.122.1 > 0.6.122`). Published 04:25:05; the app's 600 s release check offered
>   it at 04:32:10; pin/verify -> delta stage -> swap done by 04:32:17.9; engine stop begun ~04:32:17.9; `terminate:` ->
>   `applicationShouldTerminate: NSTerminateNow` at 04:32:19.037 (exit status 0 at 19.05); the helper opened the new app
>   (pid 72734) at 04:32:19.4; `/api/version` 0.6.122.1 again at 04:32:32.9. **Outage 14.9 s, no kill, no click.** (The
>   window was not hidden: no Automation permission for System Events; the release tick does not depend on it.) Then the
>   local Hub's held release was replaced by real 0.6.122 (push_delta replaces `release.json`) and `install_local.sh`
>   put real 0.6.122 back; the Apple-event Quit of the 0.6.122.1 app took the real user-Quit path:
>   `NSTerminateLater` 04:33:39.77 -> reply YES 04:33:41.21 -> exit 04:33:41.24 by itself. Final: /Applications
>   0.6.122 / `f710e41`, serving, held release 0.6.122 / `f710e41` (not an offer), app pid stable.
> - **Gate:** `unittest discover` -> **1597 tests, OK (13 skipped), 589 s, exit 0** (a first run had 1 failure: the source pin
>   `test_a_relaunch_after_an_update_force_stops_a_hung_shutdown_without_asking` still expected the old shape; updated);
>   `native/build.sh` green (boundary, API contract, lifecycle).
> - **Delta (stage file manifests):** **0.6.121 -> 0.6.122: 6 files, 6 blobs, 13,495,854 B to send** (native binary,
>   `Info.plist`, `release-manifest.json`, `release-notes.md`, `voice/CHANGELOG.md`, `voice/VERSION`). **0.6.119 -> 0.6.122:
>   12 files, 12 blobs, 13,876,425 B to send**, 0 added, ~15.6k removed (the Lance tree; removals send nothing). Totals
>   77,466 files, 4,202,330,364 B.
> - **DMG** `dist/StudioHub-0.6.122-f710e41.dmg` (1,570,244,746 B, SHA-256
>   `d3276c58e2e1c09a7d6f9d6fdc1671f707920411abd2ce3430a112c49c0b582a`, `hdiutil verify` valid, `bytes-wasted: 0`), built with
>   `.build/make-dmg-noprune.sh`; its first output carried 557 MB of dead space (kept as
>   `_trash/dist/StudioHub-0.6.122-f710e41-wasted557MB.dmg`), re-compacted with `hdiutil convert -format UDZO`. The
>   0.6.121 DMG moved to `_trash/dist/`. Installable app: `.build/StudioHub-0122/Studio Hub.app` (manifest `0.6.122` /
>   `f710e41292c7a4752fa406867998a07a6b08bb90`). **Installed on this Mac only; not on the drives, nothing pushed to
>   controllers.**

> **0.6.121 - Macs can install updates by themselves again (installer fix + ONE-RELEASE BRIDGE)** (0.6.120 rollout,
> 2026-10-01: no controller or node updated). Read `CHANGELOG.md` 0.6.121 and
> `_orchestra/audit-20260930/update-0120/diag/`; what to know:
>
> - **Cause:** `ReleaseInstaller.runtimePaths` (`native/UpdateClient.swift`) had no `video`, so the app's own pre-swap
>   check (`verifyDeclaredComponents`) refused, AFTER staging, every release whose manifest says
>   `components.video.runtime == "video"` (0.6.120 did). `autoUpdateFailedBuild` then stopped retries; nothing showed.
>   **Fix** (`7031035`): `video` is in the table; tests use the real 0.6.120 manifest as a fixture, the fake-app helper
>   builds the real layout, `tests/test_installer_knows_shipped_runtimes.py` pins "every runtime `stage_voice.py`
>   writes is verifiable".
> - **BRIDGE (`28ad677`) - kept in 0.6.122, revert only when no Mac runs < 0.6.121:** installed 0.6.119/0.6.120 apps verify with THEIR table (no `video`),
>   so this release labels `components.video.runtime` **`'voice'`** in `packaging/stage_voice.py` `_stage_video`.
>   Nothing else reads that label (`_video_spec` reads `runtime_sha256`). **To revert: set it back to `'video'`,
>   delete the BRIDGE comment and DELETE `test_bridge_release_video_is_verifiable_by_the_apps_already_installed`**
>   (only once the fleet is on >= 0.6.121). Until then the staged manifest says `voice` for video on purpose.
> - **Verified (the point of the release):** the REAL 0.6.119 installer (`git show 5417087:native/UpdateClient.swift`
>   compiled with `diag/harness-swap-verify.swift`, version/build edited to 0.6.121/451b613) ran `swapStagedApp` on an
>   APFS clone of the staged 0.6.121 app over a clone of the 0.6.120 app: **`SWAP OK, landed: 451b613`**. Control, same
>   clone with video relabelled `video`: `SWAP FAILED ... component 'video' with runtime 'video', which this installer
>   does not know`. Staged manifest: hub/subtitle/chat/music/video -> `voice`, image -> `image` (all in the 0.6.119
>   table: voice, image, voice+music-overlay). The staged native binary contains `runtimes/video/bin/python3` (0.6.120's
>   does not). Harness scratch: the session scratchpad `v119/`.
> - **Gate:** `unittest discover` -> **1597 tests, OK (13 skipped), 607 s, exit 0**; `native/build.sh` green
>   (boundary, API contract, lifecycle).
> - **Delta (stage file manifests, `release_delta.Plan`/`fetch_bytes`):** **0.6.120 -> 0.6.121: 6 files, 6 blobs,
>   13,492,018 B to send** (native binary 12.99 MB, `Info.plist`, `release-manifest.json`, `release-notes.md`,
>   `voice/CHANGELOG.md`, `voice/VERSION`; 0 added, 0 removed). **0.6.119 -> 0.6.121: 12 files, 12 blobs, 13,872,589 B
>   to send**, 0 added, 15,577 removed (983,228,606 B, the Lance tree; removals send nothing). Totals 77,466 files,
>   4,202,326,528 B (app 4,286,596 KB on disk).
> - **DMG** `dist/StudioHub-0.6.121-451b613.dmg` (1,567,432,437 B, SHA-256
>   `bd7ba0da204e1d597273cfe6a753a2fabe33b5c3d0dc4fe60a97263996f51e6b`, `hdiutil verify` valid, `bytes-wasted: 0`),
>   built with `.build/make-dmg-noprune.sh`. **Its first output again carried 587 MB of dead space** (2.15 GB);
>   re-compacted with `hdiutil convert -format UDZO`, the wasted copy is `_trash/dist/StudioHub-0.6.121-451b613-wasted587MB.dmg`.
>   Do this every time. The 0.6.120 DMG moved to `_trash/dist/`. Installable app:
>   `.build/StudioHub-0121/Studio Hub.app` (manifest `0.6.121` / `451b61398715683844cea52dc831a78e60c94423`).
>   **Not installed, not on the drives, nothing pushed to controllers.** `.build/StudioHub-0119`/`-0120` untouched.
> - **Rollout note:** a Mac still on 0.6.119/0.6.120 takes this release through its own (unfixed) check because of
>   the bridge label; once on 0.6.121 its table knows `video`, so 0.6.122 (label back to `video`) also installs.

> **0.6.120 - Lance video is retired; video is LTX-2.3** (owner, 2026-09-30: "the Lance one sucks and is
> terrible ... remove it like the ace 1.5"). Read `CHANGELOG.md` 0.6.120; what to know:
>
> - `lance-3b-video-bf16`, `mlx-community/Lance-3B-Video-bf16`, `lance-3b-processor-qwen2-5-vl` and
>   `Qwen/Qwen2.5-VL-3B-Instruct` are in `components/retired_models.py` (the one list): never offered, never
>   staged, refused by name. Both manifests, `lance_runner.py`, `packaging/lance-runtime/`,
>   `qualification/lance-video-worker-run.py` are in `_trash/0.6.120-lance-retire/` (symlink target
>   `~/Developer/_trash/studio-hub-kh/`). `LANCE_3B`, `run_lance`, `lance_runtime_python`, the `video-lance`
>   engine, `--lance-runtime` / `--unqualified-lance-runtime` and `runtimes/video-lance` no longer exist
>   (nothing else used that tree). **The stage command no longer takes `--lance-runtime`** (loop step 3 fixed).
>   The default video model was already LTX (`MODELS` lists it first; `MODEL_ID`/`DEFAULTS` read it).
> - Kit / listing: **28 packages, ~103.5 GB** (was 30 / 119.1). `.build/lance-runtime`, `.build/lance-revision.txt`,
>   `.build/fetch_lance.py`, `.build/kit-add-lance.sh` are unused scratch now. Drives still hold Lance in
>   `model-library/` until the next kit refresh moves it to each drive's `_trash`.
> - **Tests:** the Lance pins became "not offered; LTX is the one video model and the default; a Lance manifest left
>   on a drive is not a video manifest" (`test_video_worker.EngineRowTests`); `test_retired_models` covers all four
>   ids with old manifests planted. Mutation-proved: re-adding `lance-3b-video-bf16.json` failed 4 tests; dropping the
>   ids from the list failed the retired test. Doc-count tests, `model-readings.json` (Lance row removed, record
>   regenerated), `research/st01-runtime-contracts.json` updated.
> - **Gate:** `unittest discover` -> **1594 tests, OK (13 skipped), 559 s, exit 0**; `native/build.sh` green.
>   (A first run had 4 doc-count/contract failures, fixed.)
> - **Delta 0.6.119 -> 0.6.120** (stage file manifests): 11 files changed (879,166 B: `retired_models.py`,
>   `standalone.py`, `model_packages.py`, video `generation.py`, music `generation.py`/`yue2.py`, `release-notes.md`,
>   `voice/CHANGELOG.md`, `voice/VERSION`, `Info.plist`, `release-manifest.json`; the native binary is byte-identical),
>   0 added, **15,577 removed** (983,228,606 B: 15,574 under `runtimes/video-lance`, `lance_runner.py`, two manifests).
>   **Bytes to send: 879,166 (11 blobs)**; removals send nothing. Totals 93,043 -> 77,466 files;
>   5,185,566,996 -> 4,202,323,961 B (app 5,282,264 KB -> 4,286,592 KB on disk, -0.95 GiB).
> - **DMG** `dist/StudioHub-0.6.120-c439bbf.dmg` (1,568,998,085 B, SHA-256
>   `0cb073f410ec4b078169a37765ba87c7b04cc2f4e720f955ce39ee2bbe87908f`, `hdiutil verify` valid), built with
>   `.build/make-dmg-noprune.sh`. **That script's first output carried 573 MB of dead space**
>   (`CUDIFEncoding-bytes-wasted`, 2.14 GB file); it was re-compacted with `hdiutil convert -format UDZO` and the
>   wasted copy moved to `_trash/dist/`. Check `hdiutil imageinfo` for `bytes-wasted: 0` after a DMG build.
>   Installable app: `.build/StudioHub-0120/Studio Hub.app` (manifest `0.6.120` /
>   `c439bbf9a94ff01b66af45958290db38058ffb6a`). **Not installed, not on the drives, nothing pushed to controllers.**
>   `.build/StudioHub-0119` (SSD/fleet source) untouched.

> **0.6.119 — Quit asks only when a job is running** (owner, 2026-09-30: "if no job is running. quit is quit.
> if have job, then we should ask."). Read `CHANGELOG.md` 0.6.119; what to know:
>
> - `AppDelegate.applicationShouldTerminate` (`native/StudioHub.swift`) no longer asks whenever the engine
>   runs. It reads **fresh** work (`NativeWorkspaceStore.activeWorkNow()` = `refresh` + `refreshMemoryPolicy`
>   + the same `activeWorkCount` the updater's drain uses; a hidden window does not poll, so the stored lists
>   can be stale; bounded to 3 s so a hung Hub cannot hold Quit). Zero -> stop the engine and quit, no alert.
>   Above zero -> the old "Keep Running / Quit and Stop" question. `relaunching` (update) never asks.
> - After Quit is decided, `forceStopWithoutAsking = true` (`stopThenQuit`), so a stop that hangs 15 s is
>   force-stopped by itself - no second "Force Stop?" dialog. The in-app Stop Engine / Force Stop buttons
>   (`confirmStop`, `confirmForceStop`) are unchanged and not reachable from Quit.
> - Seams on `AppDelegate`: `activeWork`, `confirmQuitWhileBusy`, `relaunching` (internal, for the test).
> - **Tests:** `tests/native/lifecycle_main.swift` `quit` (idle / busy-keep / busy-quit / relaunch) and
>   `api_main.swift` (`activeWorkNow` reads fresh); Python `test_update_unattended`,
>   `test_native_operator_surfaces` pin the source. Mutation-proved, each failing on an assertion: idle asks,
>   busy never asks, relaunch asks, no force flag, `activeWorkNow` stale.
> - **Gate:** `unittest discover` -> **1597 tests, OK (13 skipped), 594 s, exit 0** (an earlier run had one
>   load-related failure, `FleetCancelTests.test_cancelling_on_the_controller_stops_the_node`, green 3/3 alone
>   and on the old tree); `native/build.sh` green.
> - **Delta 0.6.118 -> 0.6.119** (stage file manifests): 6 files changed (13,487,044 B: native binary
>   12,990,856 B, `release-notes.md`, `voice/CHANGELOG.md`, `voice/VERSION`, `Info.plist`, `release-manifest.json`),
>   0 added, 0 removed. Totals 93,043 files; 5,185,558,591 -> 5,185,566,996 B.
> - **DMG** `dist/StudioHub-0.6.119-96fef8c.dmg` (1,941,922,463 B, SHA-256
>   `749097b3eedf93de4183dab6683fcdf5d88959e43546e9830605083a5a680f61`, `hdiutil verify` valid), built with
>   `.build/make-dmg-noprune.sh`; the 0.6.118 DMG moved to `_trash/dist/`. Installable app:
>   `.build/StudioHub-0119/Studio Hub.app` (manifest `0.6.119` / `96fef8c9e300a29998e68c16cb460e289a084d8b`).
>   **Not installed, not on the drives.** `.build/StudioHub-0116`/`-0117`/`-0118` untouched.

> **0.6.118 — a Mac that never goes idle still updates itself (drain for update); the unattended tick pins
> its own key; a blob push no longer stalls the controller** (live pilot findings, 2026-09-30). Read
> `CHANGELOG.md` 0.6.118; what to know:
>
> - **Drain for update** (`releaseTick` in `native/StudioAPI.swift`, `components/hub/backend/update_drain.py`).
>   A verified offer waits `updateDrainGrace` (600 s; `defaults write <bundle id> UpdateDrainGraceSeconds -int N`)
>   for `activeWorkCount == 0`; after that the app asks its Hub (`POST /api/hub/update/drain {"want":true}`,
>   Hub token) to stop taking new work, keeps the lease alive every 30 s, and installs when running work
>   finishes. **Refusal code `MACHINE_BUSY` (409)**: the gateway's `_LOCATION_REFUSAL_CODES` re-routes it
>   (`LOCATION_NOT_READY`) and the broker's `_is_machine_reservation_refusal` treats it as "wait", not failure.
>   One flag feeds `broker.busy_machines()` (every machine taken: scheduler, peer admission
>   `_peer_admission_refusal`, chat/transcription lanes) and `_submission_readiness`.
> - **Stagger rule:** one Mac per controller group drains/installs at a time, controller included: the
>   controller holds one in-memory slot (`update_drain.slot`); a node asks it via `POST /api/hub/fleet/update-slot`
>   (added to `FLEET_ALLOWED` and `NODE_ALLOWED`); an old/unreachable controller does not block a node.
> - **Cannot wedge:** the drain and the slot are 120 s leases; failed install -> `endUpdateDrain()` at once
>   and that build is not retried (`autoUpdateFailedBuild`); app/Hub/Mac restart -> the lease lapses.
>   Only queued jobs are excluded from the count while draining (they persist through the relaunch);
>   running work still counts. Visible: sidebar "Updating after current work finishes...",
>   `GET /api/health` `update_draining`.
> - **Key pin:** `releaseTick` no longer needs a pinned key. First unattended read pins the local Hub's
>   key via `refreshRelease` (TOFU), `lastReleaseCheck` is reset so the verify happens on the next tick
>   (10 s), a different key later refuses (`releaseRefusal` names it). `rememberReleasePublisherKey`
>   persists it as before.
> - **Human blockers found in the unattended path:** (1) fixed - after the install the relaunch stops
>   the services and, if that hangs 15 s, `confirmForceStop()` raised an `NSAlert` nobody answers;
>   `relaunchAfterUpdate` now sets `forceStopWithoutAsking` and `scheduleForceStop` force-stops itself.
>   (2) verified fine - the 0.6.114 "jobs are running, install anyway?" dialog is `confirmingInstall` in
>   `StudioViews` (manual button only); `installOfferedRelease(automatic:)` has none; the quit prompt is
>   skipped while `relaunching`; no window/Connections/connection notice is needed. (3) left, deliberate:
>   `memoryInventory != nil` (only a Hub older than the app lacks that route).
>   `tests/test_update_unattended.py` pins these by source.
> - **Blob push:** `PUT /api/hub/release/blob/<sha>` no longer recomputes `missing_blobs()` (dropped `need`
>   from its reply; gateway reads `GET /need` once), runs `BlobUpload` in a thread, and
>   `release_delta.installed_by_sha` is memoised per installed app. `POST /api/hub/release` adds `basis`
>   `{installed_app, wanted_blobs, needed_blobs}`. **Measured on this Mac** (0.6.117 stage manifest vs
>   the 0.6.116 stage as installed app): `need` = 12 blobs, 13.7 MB; per-`missing_blobs()` cost ~20-30 ms
>   warm (0.23 s cold), so with 12 blobs the per-PUT rescan cannot explain 550 s. **It only explains it if
>   `need` was thousands of blobs, i.e. the controller could not find its own installed app** (or had to hash
>   it cold, ~20 s+ on a loaded Mac). The next push's `basis.installed_app` says which; not verifiable here
>   (the SSD-installed 0.6.116 on 1500 is not reachable).
> - **Gate:** `unittest discover -s tests -p 'test_*.py'` -> **1597 tests, OK (13 skipped), 599 s, exit 0**;
>   `native/build.sh` green. Mutation-proved (each fails on an assertion): Swift 6 (no grace, queued counts,
>   failed install keeps drain, needs pinned key first, no next-tick verify, ignores slot refusal) and
>   Python 9 (busy_machines ignores drain, submission ignores drain, slot not exclusive, node fail-closed,
>   lease never lapses, node route missing, force-stop flag missing, PUT rescans, PUT commit on loop, sha
>   index not memoised).
> - **Delta 0.6.117 -> 0.6.118** (stage manifests): 11 files changed (13,912,888 B) + 1 added
>   (`update_drain.py`, 3,166 B) + 0 removed = **12 blobs, 13,916,054 B** (native binary 12,984,024 B is
>   most of it). Totals 93,042 -> 93,043 files; 5,185,502,300 -> 5,185,558,591 B.
> - **DMG** `dist/StudioHub-0.6.118-9781819.dmg` (1,946,012,769 B, SHA-256
>   `91221238d8ca6ee8bfac0e2ed91ab946eb40dedecf0e507b5b2e1ac0d6622a34`, `hdiutil verify` valid), built with
>   `.build/make-dmg-noprune.sh` from the `9781819` stage; the 0.6.117 DMG moved to `_trash/dist/`.
>   Installable app: `.build/StudioHub-0118/Studio Hub.app` (manifest `0.6.118` / `9781819246dc344f9ec56fbecb260f2e0713a9a9`).
>   **Not installed, not on the drives.** `.build/StudioHub-0116`/`-0117` untouched.
> - **Not done:** grace is a `defaults` key, not a Settings control; a real drain/install across two Macs
>   was not exercised (no installs, no drives); the SSD-app manifest question above.

> **0.6.117 — output cleanup uses a Trash Folder; ONE list retires a model; updates stop piling up**
> (two owner decisions, 2026-09-30). Read `CHANGELOG.md` 0.6.117; what to know:
>
> - **`output_storage.enforce` no longer unlinks.** It renames acknowledged, unleased output to
>   `<hub data>/trash/<UTC date>/<batch>/<file id>-<name>` (same volume; a failed rename skips the file,
>   there is **no** delete fallback). `purge_trash` removes dated folders older than `TRASH_DAYS = 15`
>   at the start of every sweep - **the only hard delete, owner-approved**. `max_bytes` counts live output
>   only. `admission()` empties Trash **oldest day first** before refusing for the free-space floor (my
>   choice, so a full Trash cannot wedge work for 15 days). `hub data` = `~/Library/Application
>   Support/StudioHub Standalone/hub`, so the native side files under `.../hub/trash` too.
> - **New defaults 20 GB / 3 days / 5 GB free** (`standalone_config.py`). Macs that already saved the
>   old defaults are migrated at read time (a file without `defaults_version` whose value equals the old
>   default reads as unset; saving stamps the version). A hand-edited file keeps its numbers.
> - **`components/retired_models.py` is the one retired list** (`RETIRED_MODELS`, seeded with
>   `ace-step-1-5-turbo` and `ACE-Step/Ace-Step1.5`). Readers: `standalone._model_catalog`,
>   `model_ids`, `monitor._assemble_aggregate_catalog`, `release_kit.build`,
>   `stage_voice.copy_model_manifests`, `copy_models_for_this_mac.py` (via the app's own copy; an app
>   that predates the module retires nothing). Refusal: `MODEL_RETIRED` in `_submission_readiness` and the
>   peer path. **Do not put it in `model-manifests/`** - every reader globs `*.json` there.
> - **Swift:** `HubTrash.move` (UpdateClient.swift) is the one same-volume mover. `swapStagedApp` sends the
>   replaced app to the Trash Folder (falls back to removal if `trashFolder` is nil or on another volume);
>   `retireReleaseArtifacts()` moves everything under `Updates/` after any completed install. Tests build
>   `ReleaseInstaller` with no `trashFolder`, so they never touch the real Hub data.
> - **Delta rollout 0.6.116 -> 0.6.117**, from the two stages' file manifests: **11 files changed
>   (13,691,402 bytes), 1 added (`components/retired_models.py`, 1,199 bytes), 0 removed.** The native binary
>   changed (12,939,800 bytes), so this is the delta that carries new Swift; the runtimes are byte-identical.
>   Total files 93,041 -> 93,042; 5,185,460,742 -> 5,185,502,300 bytes.
> - **Gate: `unittest discover -s tests -p 'test_*.py'` -> 1584 tests, OK (13 skipped), 725 s, exit 0**
>   (`.build/hub-gate/bin/python`, `PYTHONPATH=$PWD/.build/test-deps`; +3 files of new tests). `native/build.sh`
>   green (boundary, API contract, lifecycle, release build). Mutation-proved, each failing on an assertion:
>   15 Python mutants (prune-deletes, delete-fallback, purge `<=`, purge never, 10-day purge, no admission
>   purge, old default, no migration, six retired-list readers) and 4 Swift mutants (swap removes, downloads
>   not moved, failed install moves, only installed one moved).
> - **Built, NOT installed, NOT on the drives.** `dist/StudioHub-0.6.117-ae40e35.dmg` (1,942,969,730 bytes,
>   SHA-256 `968b7d46f647a979fac2e8606a184cfe5789f170163deb24fd33b6066c369e64`, `hdiutil verify` valid), from
>   the `ae40e35` stage. Installable app: `.build/StudioHub-0117/Studio Hub.app` (manifest `0.6.117` /
>   `ae40e35aef1777be4a070e86be702856602937aa`). Built with `.build/make-dmg-noprune.sh`; the 0.6.116 DMG
>   moved to `_trash/dist/`; `.build/StudioHub-0116` and `-0115b` untouched.
> - **Not done / known:** the Trash Folder is per Hub data dir, not per managed root; an output root on
>   another volume than the data dir never moves (skipped, stays live) - today every root is under the data dir.
>   A rolled-back app is a manual drag from the Trash Folder; there is no UI for it.

> **0.6.116 — ACE-Step 1.5 is retired. Music is YuE2 and Stable Audio 3, nothing else** (owner
> decision 2026-09-30, confirmed in chat: *"i wont' want ace 1.5 either… we only offer 2 music model,
> the yue2 and the stable audio3"*). Read `CHANGELOG.md` 0.6.116 for the list; what to know:
>
> - **Stable Audio 3 Medium (1.4B) is the default music model** (`catalog.DEFAULT_REPO`, was `MAIN_REPO`
>   = ACE). It is first in `/api/catalog`, so the app's music picker (first supported music model)
>   opens on it. YuE2 3B (8-bit AR + bf16 NAR) and its 0.6.115 decoder fix are untouched.
> - **Deleted, not hidden:** `model-manifests/ace-step-1-5-turbo.json`, `engine_child.py`,
>   `EngineExecutor`, `_validate_ace_step`, `_run`, the ACE source-tree verification, the ACE catalog
>   entry/tiers/family, `packaging/music-runtime/` (the ACE-only overlay; **verified SA3 imports without
>   it** — every `models.defs.*` module and `sa3_mlx.py --help` load on the bare Voice runtime; a real
>   SA3 render was NOT run here, no SA3 weights are on this Mac), and the staged
>   `engines/ace-step-1-5`. All moved to `_trash/ace-step-retire-0.6.116/` (removed files) — nothing `rm`ed.
> - **The stage command lost `--music-runtime` / `--music-engine`** (the flags no longer exist). Music is
>   staged with every Hub bundle and its release record is `runtime: "voice"` + the Voice runtime's
>   digest. That name is in every deployed update client's `runtimePaths` table, so a 0.6.115 node
>   accepts this release. `native/UpdateClient.swift` still lists `voice+music-overlay` (dead entry,
>   deliberately left: changing Swift was out of scope).
> - **Nothing is resident in Music now**, so the memory policy has nothing to release
>   (`loaded_models` always `[]`, release answers `released: false`); the fleet routes still exist.
>   `/api/health` dropped `engine_loaded`/`engine_child_pid`. `GenerationManager` no longer takes
>   `engine_root`; `STUDIOHUB_MUSIC_ENGINE_ROOT` is ignored.
> - **Behaviour kept honest:** an SA3 job that fails after being marked done now clears its artifact
>   fields (the ACE path used to; SA3 did not). The ACE-only `JOB_PERSISTENCE_UNCERTAIN` path (discard
>   itself failing) went with `_run` — SA3/YuE2 never had it.
> - **Not done / for the coordinator:** the drive kits still carry the ACE package in their model
>   libraries until the next kit refresh (the copy step moves a model the kit no longer offers into
>   `_trash`, never deletes); `.build/prepare-ssds.sh` (old one-off) still names `ace-step-1-5-turbo`;
>   `catalog.FAMILIES` has no `yue2` entry (pre-existing: YuE2's row names family `yue2`).
>   `.build/music-runtime` and `.build/music-engine-source` are now unused scratch.
> - **Delta rollout (the first one).** 0.6.115 -> 0.6.116 measured from the two stages' file manifests:
>   **20 files changed (784,972 bytes), 0 added, 798 removed (11,527,708 bytes)**; the native binary and
>   all six runtimes are byte-identical. Total files 93,839 -> 93,041; 5,197,040,792 -> 5,185,460,742
>   bytes. The 798 removed are `engines/ace-step-1-5` (619), `runtimes/music` (177), the manifest (1)
>   and `components/music/app/backend/engine_child.py` (1).
> - **Gate: `unittest discover -s tests -p 'test_*.py'` -> 1581 tests, OK (13 skipped), 612 s, exit 0**
>   (`.build/hub-gate/bin/python`, `PYTHONPATH=$PWD/.build/test-deps`; 15 fewer than 0.6.115's 1596 —
>   the ACE-only tests went with their code). `native/build.sh` green (boundary, API contract,
>   lifecycle), exit 0. Mutation-proved: re-adding the ACE manifest, flipping `DEFAULT_REPO` to YuE2, and
>   dropping the SA3 `clear_artifacts` each fail on an assertion.
> - **Built, NOT installed, NOT on the drives.** `dist/StudioHub-0.6.116-5af759a.dmg` (1,948,134,094
>   bytes, SHA-256 `d74c70a8b2edca8cba112a75c0c126c860ced3b81736cbc1a32d9d0a0b78ca44`, `hdiutil verify`
>   valid), built from the `5af759a` stage. Installable app: `.build/StudioHub-0116/Studio Hub.app`
>   (manifest `0.6.116` / `5af759a5435ce6ab42e525a14869f02832b97435`, `file-manifest/` present). Later
>   commits are docs-only. `make-dmg.sh` was run from a copy **without its prune step**
>   (`.build/make-dmg-noprune.sh`), so nothing moved to a drive and `.build/StudioHub-0115b` is still
>   there. Two earlier 0.6.116 stages (from pre-amend commits) are in `_trash/build-stages/` and their
>   DMG is in `_trash/dist/`.


> **0.6.115 — three reviewed branches merged (`wave2-sched`, `wave2-fleet`, `wave2-delta`), kept as
> merge commits. Read `CHANGELOG.md` for the list; this is what you must know before touching them.**
>
> **⚠ ROLLOUT: 0.6.114 -> 0.6.115 is a FULL (disk image) update** — 0.6.114 apps have no delta path.
> The first delta update is **0.6.115 -> 0.6.116**; that is where to watch the APFS-clone stage, the
> "cannot prove it, fall back to the DMG" path and the controller's `.../release/need` exchange for
> the first time in the field. The gateway side (`release_publisher.py`: manifest push, blob PUT,
> fall back on 400/404/405 or a missing `delta` key) is a separate task; the wire contract is
> `_orchestra/audit-20260930/CONTRACT-hub-release-delta.md`.
>
> **⚠ SUPERSEDES the 0.6.114 owner decision below: a CONTROLLER now auto-installs an offered release
> when drained**, exactly like a node (delta removed the role gate in `StudioAPI.releaseTick`; it still
> needs a pinned publisher key and `activeWorkCount == 0` after a fresh refresh). The 0.6.114 note
> "a CONTROLLER stays manual" is no longer true; the sidebar Update badge remains.
>
> **Scheduler.** `memory_admission.requirement_gb` (measured -> manifest -> catalog) is a **hard
> eligibility floor**; a freed Mac takes the waiting job the fewest Macs can run (ties: loaded model,
> then oldest; "Send here" wins). No more 30 s `MACHINE_BUSY` park or 90 s avoid. This replaces the
> 0.6.112 note about `MACHINE_BUSY` deferral. BOARD.md's "8 GB tier stays unclaimed rather than refused"
> is annotated as superseded.
>
> **Fleet.** Removal from either side (`GET /fleet/nodes/{id}/status`, a 403 with the removal date for a
> revoked credential, 14-day retry of a node's leave notice). Labels everywhere; node id persisted.
>
> **YuE2 decoder (P0, found by the coordinator through the gateway).** Every YuE2 job failed with
> *"the YuE2 decoder (yue2-vae) is not installed"* on every Mac although the files were on disk:
> `_run_yue2` read `files["requires"]`, a key `model_packages.preflight` never returns. Now
> `MusicCache.companion_root("yue2-vae")` resolves it from the decoder package's own receipt +
> manifest layout destination. `DecoderResolvesFromARealLayoutTests` (tests/test_music_yue2.py)
> builds the library from the shipped manifests; reverting the fix reproduces the owner's sentence.
> ⚠ **(Done in 0.6.116 — see the block above.) Was: NOT done: retiring ACE-Step 1.5** (owner decision relayed by the coordinator: the Hub offers
> only YuE2 and Stable Audio 3). The removal was started and **blocked by the tool's permission
> classifier**, so nothing of it is in this release: `model-manifests/ace-step-1-5-turbo.json`,
> the ACE paths in `components/music/app/backend/{catalog,generation,cache,main,memory_policy,
> engine_child}.py`, `packaging/{music_worker,hub_worker,stage_voice,capture_model_manifests}.py`
> and ~14 tests still ship ACE-Step. `generation.py` makes ACE the default model
> (`DEFAULT_MODEL = require_model(MAIN_REPO)`), so it needs SA3 as the new default.
>
> **Built, NOT installed, NOT on the drives.** `dist/StudioHub-0.6.115-c317f3b.dmg` (1.8 GB, SHA-256
> `711aeaa748b92b05586ce5d354ce6121d66a8f0db491d58339859109a8d48902`, `hdiutil verify` valid; built
> from the `c317f3b` stage, later commits are docs-only). The installable app is
> `.build/StudioHub-0115b/Studio Hub.app` (manifest `0.6.115` / `c317f3b`, `file-manifest/` present).
> `.build/StudioHub-0115/` is a superseded stage of 2887747 (before the YuE2 fix) - do not install it.
> `make-dmg.sh` was run from a copy **without its prune step**, so nothing was moved to a drive and both
> 0.6.115 stages plus 0113 are still in `.build/`.
>
> **Merge notes.** One conflict, `components/hub/backend/auth.py`: fleet and delta both extended
> `FLEET_ALLOWED` and `NODE_ALLOWED`; every entry from both is kept. `standalone.py`, `StudioAPI.swift`
> (`pollOnce` runs `membershipTick` then `releaseTick`), `tests/native/api_main.swift` and `broker.py`
> auto-merged and were exercised by the gate; no feature-versus-feature conflict was found.
>
> **Gate: `unittest discover -s tests -p 'test_*.py'` -> 1596 tests, OK (13 skipped), 636 s, exit 0** (run on the tree with the YuE2 fix, c317f3b; the first merged-tree run was 1593 OK, 582 s)
> (`.build/hub-gate/bin/python`, `PYTHONPATH=$PWD/.build/test-deps`; includes the isolated-child
> `tests/hub_regression/cases` run); `native/build.sh` green (boundary, API contract, lifecycle).
> Not exercised here: a real delta install, a real node removal across two Macs, or the relaunch after an
> unattended install — no Mac was installed over.

> **0.6.114 — twelve findings from the native-app audit (`_orchestra/audit-20260930/ui-hub.md`), all
> "the Hub published it, the app drew a fixed sentence". Read `CHANGELOG.md` for the list; this is
> what you must know before touching any of it.**
>
> **⚠ OWNER DECISION: a joined NODE updates itself when idle; a CONTROLLER stays manual (sidebar
> badge only).** `NativeWorkspaceStore.releaseTick` (10-minute offer check, 1-minute idle retry) is
> the whole mechanism. "Idle" is `activeWorkCount == 0` with a **fresh** `refresh()` +
> `refreshMemoryPolicy()` — a job a controller sent to a node is *not* one of that node's batches, so
> the Studios' own `busy` flags are the only place it shows. It requires a **pinned** controller key
> (`releasePublisherKey != nil`) so it can never be the read that pins one, re-checks idleness after
> the download (work that arrived defers the swap, file kept), and never retries a failed build until
> the app restarts. After a landed install the app **relaunches**: `AppDelegate.relaunchAfterUpdate`
> starts a `sh` helper that waits for this PID to exit (the data-folder lock is held until then) and
> `open`s the bundle; `relaunching` skips the "Quit and stop its engine?" alert.
> ⚠ **The relaunch helper and the unattended install were exercised by the native contract suite
> (installer stubbed) and by reading the code, NOT by installing over a live app** — the fix agent
> was forbidden to launch or install. First real node update: watch it.
>
> **Polling.** `nextPollDelay` = 2 s only while the window is visible *and* work is active, else 10 s;
> a hidden/minimised/occluded window skips the screen refresh (`isWindowVisible`, set by the app
> delegate) but **not** the update check, because a node's window is normally closed. `/api/hub/jobs`
> has **no `since`/limit parameter**: it still re-reads every finished batch from SQLite on each call.
> A cheap Hub-side page/limit is the next lever and was not invented here.
>
> **Job cache.** Failed batches are cached only when the summary's own counts (`queued`, `retrying`,
> `running`, `cancel_requested`, `uncertain`) are all zero — a Retry makes it active again.
> `tests/test_job_detail_cache_is_safe.py` says why; its comment had already named this invalidation as
> owed once the app could retry.
>
> **Downloads.** The downloader prints nothing while it works, so `model_downloads._public` measures
> `bytes_done` from `<library>/.studiohub/staging/<package>-<digest>` (the record stores that path as
> `staging`; it is never published). Companion packages are not in `total_bytes`. Finished runs leave
> `native-model-downloads.json` but stay in the session's Jobs list; a fixture reusing one run id
> across downloads needs `dismissDownload` between them.
>
> **Storage.** `output_storage.preview()` now returns `would_delete_bytes` (the sweep's rule replayed
> on the index; hard links counted per file, so it can overstate). Proved equal to what `enforce`
> frees in `tests/test_hub_ops_visibility.py`.
>
> **Not done, deliberately:** a Cancel for the update download (Task not held), a remote "download
> this model on node X" action (a product choice — the queued note now says *"download it on that
> Mac, or pin another"*), auto-refresh of the Fleet page, a search field for ~50 nodes, chat-row Retry.
> `finishedHubDownloads` and the two mutable release fields are in-memory by design.
>
> **Gate: `unittest discover -s tests -p 'test_*.py'` → 1549 tests, OK (12 skipped), 611 s, exit 0;
> `native/build.sh` green (boundary, API contract, lifecycle).** New: `tests/test_hub_ops_visibility.py`
> (6), `tests/test_native_operator_surfaces.py` (19), ~40 checks in `tests/native/api_main.swift`.
> **Mutation-proved** (each failed on an assertion, restored): governor note dropped, unknown counted as
> blocked, drop-on-any-failure, no prune, controller auto-updating, retry on the wrong route, reversed
> sort, no dry-run figure, the broker string, download progress/error tail, both storage-preview rules,
> and six view wirings. ⚠ **Two of these have two independent guards** (idle check before *and* after the
> download; error-cache settled check on write *and* read), so mutating one alone survives by design —
> the test only bites when both go, which is what `e2`/`f2` did.
>
> **Built, NOT installed, NOT on the drives.** `dist/StudioHub-0.6.114-4ec6832.dmg` (1.8 GB, SHA-256
> `8a4da97e097e4d041d534d6abb486e88a6b4042f67afe30e709220a68bf68b42`, `hdiutil verify` valid); the
> staged bundle's manifest reads `0.6.114` / `4ec6832…`. ⚠ **`make-dmg.sh`'s automatic scratch prune
> moved the fresh stage** `StudioHub-0114` to
> `/Volumes/KH-STUDIO-ATLAS/_build-archive/studio-hub-kh/StudioHub-0114` (and kept the *0.6.113* stage
> in `.build/`, because it keeps the stage named in `VERSION` at the time it ran). Install and the
> kit refresh need the 0.6.114 stage: copy it back, or restage from `4ec6832`. This is the documented
> prune, not a manual write, but it did put a 5 GB directory on ATLAS.

> **0.6.113 — the reason a node did not answer was only ever in a sentence, spelled two ways.**
>
> **Found by checking an owner question, not by a failure.** He noticed one controller reporting
> eight of nine Macs unreachable and asked whether it was a real outage or a stale roster. It was a
> **real outage** — the Macs are switched off — but the check turned up a defect in what the
> Gateway told him to do about it: *"Studio Hub on that Mac"* for every unreachable machine.
>
> **The Hub has always drawn the distinction; nothing could read it.** `peers.py` separates:
>
> - **`no_hub`** — TCP refused. The Mac is up and nothing is listening on `:47873`.
> - **`unreachable`** — packets dropped. A firewall is blocking, **or the Mac is asleep or off.**
>
> But the two paths that produce an unreachable node wrote it differently —
> `_from_snapshot` wrote `could not reach the node (no_hub)` with the token present, and `probe`
> wrote `could not reach http://…: ConnectTimeout` with **no token at all** — so the only way to
> learn the reason was to parse a sentence written two different ways. With no field to read, the
> Gateway gave one answer to both, and picked the wrong one for eight powered-off Macs.
>
> `reason` is now published by both paths, in the poll's own vocabulary; `detail` is unchanged. The
> direct-dial path classifies with the same rule the poll uses (`httpx.ConnectError` is a refusal,
> everything else a drop), so the two agree **by construction rather than coincidence**.
>
> ⚠ **The Gateway reads this field, but it also has a fallback, and that is deliberate.** Every
> controller in the fleet is on **0.6.110** and predates the field, so `gateway/fleet.py`'s
> `_node_reach_reason` falls back to the machine's `health` and then to the token in the sentence.
> That fallback is a second copy of a vocabulary this Hub owns and will drift; it is confined to one
> function so deleting it, once every controller is updated, is a two-line change.
>
> **Gate: `unittest discover -s tests -p 'test_*.py'` → 1523 tests, OK (12 skipped), 611 s, exit 0.**

> **0.6.112 — 0.6.111 was audited independently, and the audit found two defects in 0.6.111's own
> footprint.** Neither changes shipped behaviour; one closes a hole in a guard, the other stops a
> comment claiming a guarantee the code does not provide. **Read this before trusting either.**
>
> **1. The repaired kit test still had the hole it was written to close.** 0.6.111 rightly stopped
> pinning the literal `chmod +x`, but the repair accepted *any* mode beginning with `+` —
> `chmod +r "Update this Mac.command"` passed a test whose message says the operator must be able to
> run it. The mode is now **parsed**: numeric by the owner-execute bit, symbolic by resolving who the
> clause addresses, its `+`/`-`/`=` operator, and whether `x`/`X` is in the permissions. **`a`
> addresses the owner; `g` and `o` do not** — my first cut got that wrong and returned `False` for
> `a+x`, which is why the helper is pinned by a 21-spelling table rather than one example.
> ⚠ **Write a helper and you must table-test it; a single example would have shipped that bug.**
>
> **2. The `MACHINE_BUSY` comment named a bound that is not one.** It closed with *"the batch's own
> lease, cancellation and the stall detector stay the bounds on how long this waits."* The stall
> detector is **explicitly non-destructive** — `broker.py:1641`, *"Visible, never destructive:
> nothing is cancelled on the strength of this"* — and `capacity_wait_started_at`, written by the
> capacity branch directly above, is **read nowhere in the tree**.
>
> ⚠ **The honest fact, now in the comment:** nothing in the broker bounds a `MACHINE_BUSY` deferral.
> `tries` is restored on every refusal, so the `MAX_TRIES` ladder cannot end it; cancellation is the
> operator's. It is a **queue with no clock, not a wait with a deadline** — the same unbounded
> semantics the capacity wait has always had, and what the owner asked for (*"should the work get
> queued instead of failing outright?"*). Do not "fix" it with a timeout without asking: a bound
> would turn a long reservation back into the failure this release exists to remove.
>
> **Both were mutant-proved:** the script mutated to `644` and to `+r` both fail the guard with the
> mode named; `a+x`/`g+x`/`o+x`/`u=rw` are all pinned by the table.
>
> **Gate: `unittest discover -s tests -p 'test_*.py'` → 1519 tests, OK (12 skipped), 613 s, exit 0.**

> **0.6.111 — a peer that says "I'm busy" was a verdict, not a wait.**
>
> **Reported from the Gateway**, a `routing: "pool"` batch failing its *first* item terminally while
> the rest of the pool sat idle: `{"item_index": 0, "code": "WORKER_TERMINAL_ERROR", "message":
> "HTTP 409: local is reserved for other work on this Mac"}`. The owner asked the right question —
> *"should the work get queued instead of failing outright?"* — and the answer was yes.
>
> **This is a classification bug, and the distinction matters for anyone reading the diff later.**
> `_eligible_studios` already filters `busy_machines()`; that filter was never the problem. But
> `busy_machines()` is **this Hub's own lease table only**, and a peer's reservation cannot appear in
> it — the controller's row for a node uses `machine` = **node id** (`fleet_nodes.py:498`), the
> serving node's worker row uses `machine="local"` (`packaging/hub_worker.py:344`), and
> `/api/hub/resources` publishes host + per-studio up/down with **no busy fact at all**. So the 409
> at the door is the *first* place the controller can learn it, and a pre-selection filter is
> impossible today without publishing a new field. **Do not go looking for the missing filter; it is
> not missing, it is unreachable.**
>
> The same condition was a **skip** inside one Hub and a **terminal failure** over the peer surface.
> A transient lease was reported as a verdict.
>
> **Two defects, and the second hid the first.** `_worker_http_error` read the code only from
> *inside* `detail` (the worker nests it: `{"detail": {"code": …}}`), while `standalone._peer_refusal_response`
> puts it *beside* it — so **every peer refusal arrived untyped** and published as
> `WORKER_TERMINAL_ERROR` with no nameable cause. Then `MACHINE_BUSY` got a deferral: `state="queued"`,
> `retry_at` +30 s (`MACHINE_RESERVED_RETRY_S`, its own constant so a memory-wait retune cannot
> silently retune it), machine avoided 90 s, `tries -= 1` — **safe because the refusal arrives before
> this item's worker saw the request**, so no attempt is spent and no duplicate is possible.
>
> ⚠ **`MODEL_PAUSED` / `MACHINE_DISABLED` / `STUDIO_DISABLED` stay terminal on purpose.** A 409 is
> also how a peer names an operator decision no other Mac can fix. Deferring those would launder a
> decision into a wait and retry it forever. The **code** is required, not the status.
>
> **The peer side is deliberately untouched** — `409 MACHINE_BUSY` is the pinned wire contract
> (`tests/test_studio_proxy.py:366-403`).
>
> **Shipped alongside it:** the avoid-window note. When every eligible machine is avoided, the
> dispatcher wrote *"Waiting for a Voice Studio where the selected shared voice is synchronized"*
> **for any modality** — `_shared_voice_allows_studio` returns `True` for every non-voice modality,
> so for an image batch the avoid window is the *only* thing that can reach that line. This was
> flagged as a wart to leave alone; **it was fixed instead, because shipping the deferral makes that
> branch the ordinary outcome of every pool dispatch** and a wrong sentence in the common path is a
> defect, not a cosmetic one. `_no_candidate_note` now separates the two waits.
>
> **Gate: `unittest discover -s tests -p 'test_*.py'` → 1519 tests, OK (12 skipped), 606 s, exit 0.**
> The **first** run was red on `test_the_kit_ships_it`, and it was **not** this change — it was
> **left red by 0.6.110**. The test pinned the literal `chmod +x` in `.build/prepare-kit.sh`, which
> that release deliberately changed to `chmod 755` (with a comment giving the reason: `+x` adds the
> execute bit to whatever mode the file arrived with, so a source written 600 landed on a drive as
> 711 beside its 755 neighbours). **The script was right; the test was stale.** It now asserts the
> property it was written for — that the mode grants its owner execute — and was **proved to fail**
> with the mode mutated to 644. ⚠ If a release ever leaves the gate red, read *which* test before
> assuming the working tree caused it; `prepare-kit.sh` is gitignored, so `git status` cannot show
> you that file.
>
> `tests/hub_regression/cases/test_pool_peer_machine_busy.py` is **mutant-proved four ways**, every
> one failing on a clean assertion rather than an error: revert to `HEAD` (2 failed, `error` vs
> `queued` and `WORKER_TERMINAL_ERROR` vs `MODEL_PAUSED`), code-read disabled (2 failed), deferral
> removed (1 failed, reproducing the reported message verbatim), old voice-only note restored
> (1 failed, reproducing the misleading sentence verbatim).
>
> **The "87 s" is settled — and the standing hypothesis was wrong.** Read from the **controller's
> own record**, not from the Gateway's summary: gateway job `a96453ad-feb6-4da3-b925-4f3863afa3f2` →
> Hub batch **`1cedcc2431`**, on provider `studio-hub-9701220bf30f` = **`studio-mac-1200-m4-24-256`**
> (`100.114.4.52:47873`). ⚠ **That is not the 1500 controller** — check the roster before naming a
> machine; the recovered thumbnail in `_orchestra` came from a *different* Hub and a different job.
> Measured: `created_at` 18:40:36.712 → `finished_at` 18:40:48.184 = **11.47 s**, and the item's own
> `execution_started_at` 18:40:36.922 → terminal 18:40:48.184 = **11.26 s**. **There is no 87 s in
> this batch**; `duration_s` and `runtime_s` are both **0.01**, because the work was refused at the
> door and nothing was generated.
>
> The item finished on **`studio-mac-1202-m2-8-256-hub:image`** — a *joined peer*, which is exactly the
> case this fix covers, and the message says *"local is reserved"* because the **serving** node names
> itself `local` (`hub_worker.py:344`), confirming the root cause above from the far side.
>
> **`tries = 2`, and the retryable path is why — which kills the old hypothesis.** The item was
> dispatched twice (`tries` increments per dispatch, `broker.py:3238`); attempt 1 failed retryably and
> attempt 2 met the 409. The previous note guessed *"a prior retryable failure set a 90 s avoid-window
> and the item retried into the reservation."* **It cannot have: the retryable branch
> (`broker.py:4042-4046`) re-queues after `RETRY_DELAYS_S[0]` = 3 s and never touches
> `avoid_machines` at all** — that key is absent from the item in the stored record. Only the
> failed-worker branch sets an avoid window. Do not re-chase that lead.
>
> **What is still not explained:** why ~11 s elapsed between the first dispatch and the retry, when
> the retry delay is 3 s and the worker's own measured duration is 0.01 s. That gap is the
> dispatcher/peer round trip, not a reservation, and it is not a defect on its own.

> **0.6.109 — a controller that booted before its network was unreachable, and looked fine.**
>
> **The owner, after a power cut on a headless controller:** *"I tried to connect to it from the node.
> It doesn't work I think the ip was blocked… I just stop server and then start server again. Only
> then the node can be connect… I have to restart it every time there's a new node. this is a
> terrible bug."*
>
> **Nothing was blocked.** There is **no IP blocking, no lockout and no rate limit anywhere in the
> Hub** — no per-IP state exists at all (checked `auth.py`, `fleet_nodes.py`, and every
> `request.client` use in `standalone.py`). The cause is worse: `bindableListenHost()` reads this
> Mac's addresses **once, at the instant the services start**, and treats a chosen address that is
> not there as one this Mac *no longer has* — dropping it and binding whatever remains, which is
> **loopback** when nothing else is left.
>
> **The boot instant is the worst possible one.** The app is a login item, so on a headless Mac after
> a power cut it starts while Ethernet is negotiating, DHCP is unanswered and Tailscale is not up.
> The Hub then comes up on `127.0.0.1`, **answers its own health check, writes its ready file and
> reports "Local services ready"** — healthy by every measure it publishes, and reachable by no node
> at all. His manual stop/start was the same read taken a minute later.
>
> ⚠ **The second symptom is the same cause, not a second bug:** a controller bound to loopback cannot
> probe its nodes, so they showed offline until it was restarted — *"after I've connected the
> machines, it's still shown offline, until I click restart or sth."*
>
> Fixed by remembering the missing set (`addressesMissingAtStart`) and letting the **three-second
> supervisor loop restart the services when one appears**; the spawn clears the set, so a
> deliberately narrowed Hub restarts at most once rather than looping. The note also stopped stating
> the wrong fact — it said *"no longer has 192.168.1.21"* from a single reading; it now says the Mac
> does not have it **yet**, that this is usual for a minute after a restart, and that the services
> restart by themselves.
>
> `ShellConfiguration.arrivedAddresses` is the decision, extracted **pure with injectable addresses**
> so a headless boot is testable without waiting for a network. Five assertions in
> `tests/native/main.swift`, one **proved to fail** with the comparison stubbed out.
>
> **Not fixed, and worth knowing:** the Hub still decides its bind list only at start. A Mac that
> loses and regains the *same* address while running does not rebind — that was always true and is
> not what he hit. If it ever matters, the supervisor loop is now the place to notice it.
>
> **0.6.108 — a node that had joined came up saying it had not.**
>
> **The owner, on a second Mac:** *"it shows not joined a controller. but at the bottom it shows i
> already joined as a node."* Two lines on one screen, each reading a **different property** for the
> same fact. The standing line reads the credential (`fleetCredentialOrigin == "adopted"`) and
> correctly said "Joined to a controller"; the health note reads the **address**, found it empty, and
> said "Not joined to a controller." The dot went grey and the Connections page had no address to
> check, which is why a fresh look showed nothing about the controller at all.
>
> **The omission is one line.** `adoptStartupMembership` copies the saved address into
> `storedMembership` — what the **re-join loop** reads, and the re-join was working the whole time —
> but never published it to `controllerAddress`, the property the **screen** reads. The app saves the
> address on join (`onControllerJoined` → `configuration.controllerAddress`, persisted in
> `ShellCore`), loads it at launch and passes it in; only the last step was missing, on every
> restart.
>
> **Second time this function has hidden state the UI needed.** Its own comment records the first
> (the launch called `rejoinControllerIfNeeded()` before anything had fetched the identity). Same
> function, same shape — a restore that moved state into the machinery and forgot the screen.
>
> ⚠ **The test had to dodge a trap worth remembering.** Its first version asserted the published
> address after a restore that *also* joined successfully — and a successful join publishes the
> address from the **join target**, the same string, so the defect was invisible behind it and the
> test **passed with the fix removed**. It now runs on a restore that produces **no join** (the
> identity read fails first, the realistic case on a node booting before its controller) and uses a
> **different** address from the previous block, so a merely stale value fails too. With the fix
> reverted it fails with `got: 192.168.1.21:8080` — the previous address, exactly what the owner was
> seeing. **An assertion placed after the thing that would mask it is not an assertion.**
>
> **0.6.107 — the joiner levels, and Breeze ships.**
>
> **The joiner had no loudness matching for any family.** On Breeze's own 14 sections of a
> 1,227-word script the integrated loudness spans **4.9 LU** (−30.0 to −25.0 LUFS). The obvious fix
> was measured and **rejected**: one linear gain per section narrowed the span only **4.9 → 4.3 LU**,
> because the quiet sections are exactly the ones with tall peaks (crest factor 18.4–22.7 dB), so the
> ceiling capped their gain and the step survived. FFmpeg's EBU R128 `loudnorm` per section brings the
> same material to **1.5 LU** and produces **−17.50 LUFS / −1.42 dBTP — identical to the sample the
> owner approved.** That is what ships, with the rejected measurement recorded beside the constants
> in `long_form_policy.py` so nobody re-derives it. Per family via `catalog.FAMILIES`
> (`section_loudness_target_lufs`, `_true_peak_ceiling_dbtp`, `_loudness_range_lu`; `None` opts out).
>
> ⚠ **The pass is length-preserving — measured ratio 1.000 at 50 ms, 200 ms and 1 s — and both
> guards on that are load-bearing.** `loudnorm` writes a **0-frame file** for a pathologically short
> input rather than failing, and the first version accepted it on rate and channel count alone, so a
> section's audio was silently replaced by nothing. It was caught by the *existing*
> `test_qwen_clone_join_preserves_segment_audio_and_pause` (30 samples expected, 10 returned). An
> empty-file **and** a length guard now abandon levelling for the whole join rather than mixing
> levelled and unlevelled sections. Missing FFmpeg joins unlevelled and says so.
>
> **Breeze TTS 2 4-bit (MLX) ships**: `mlx-community/Breeze-TTS-2-mlx-4bit` at revision `3a06d26b`
> (12 files / 3,042,440,328 bytes), a `design_clone_or_direct` resolver, the `MLX_AUDIO_FAMILIES`
> entry, and **the 0.60 s join pause the owner chose by ear**. 16 GB floor — RSS held flat at
> ~3.3 GB but the kernel peak-footprint reading ranged 5.41–23.93 GB and was never settled, so the
> claim is the tier that is not at risk. **It refuses a transcript-less voice rather than
> substituting a placeholder**: Breeze conditions its clone prompt on `f"{speaker}{ref_text}"`, so
> Pocket TTS's `"."` fallback would not degrade the clone, it would condition it on the wrong words.
>
> **The voice runtime moved 0.4.7 → 0.5.7**, and it was audited and then **rendered** before the pin
> moved — this is the mflux shape, where a runtime swap shipped unnoticed for five releases. Every
> call the worker makes is byte-identical, no dependency floor rises, **769 voice tests pass
> identically**, and a 16-render A/B on the one family with a real change (**Qwen3-TTS clone
> sampler**: 64-token repetition window; EOS no longer restored after top-k/top-p filtering) found
> **16/16 terminating by EOS and 16/16 accepted** by the project's own Whisper gate. The audio is
> genuinely different (same-seed cross-version waveform correlation ≈ 0); the shipped **sectioned**
> path is where the delta is smallest, 1–3 tokens.
>
> ⚠ **Two residual risks, stated rather than buried.** The **1.7B Base was not measured** — no real
> copy exists on this disk, only dangling symlinks onto `/Volumes/KH-STUDIO-ATLAS` — and it is a
> different talker with a different budget, so these results **do not transfer to it**. And the rare
> EOS-failure tail did not reproduce in **either** version, so it is bounded (≤1 in 5 single-shot,
> ≤1 in 3 sectioned per arm at 95%) without being resolved. The pin is one line either way:
> `2c9461f5…` **is** v0.4.7, `94c77162…` is v0.5.7.
>
> **The regenerated lock changed nothing but the pin** (`uv pip compile`, same constraints).
> `research/st01-runtime-contracts.json` records the lock's SHA-256 for both voice entries and was
> updated — a gate caught it, which is the gate working.
>
> Gate **1512 tests, OK (11 skipped)**, 613 s. `native/build.sh` green. **Verified in the installed
> bundle, not the checkout**: `/Applications/Studio Hub.app` is 0.6.107 / `3dc006a`, its voice
> runtime reports `mlx-audio 0.5.7`, `breeze_tts` imports from it, and the family and manifest are
> installed.

> **0.6.105 — Show in Folder explained itself in a place macOS never displays.**
>
> 0.6.104 made the button *work*. This makes it *say why* when it cannot. It was
> `.disabled(!job.canRevealArtifact)` with the reason in a `.help` tooltip — and **macOS does not
> show a tooltip for a disabled control**, so the button declined in exactly the one situation where
> it had something to say, and said it nowhere on screen.
>
> Always clickable now; a refused reveal reports through `fileActionError` (the workspace banner
> every other file action uses) with two different sentences for two different causes: **no path
> published** means the job ran on another Mac, and **a path with no file behind it** means the file
> was moved or cleaned up. The old single sentence called both "not a file on this Mac", which is
> wrong for a local file that was deleted.
>
> Gate 1478 tests / 11 skipped. `native/build.sh` green. **The two behavioural tests were proved to
> fail with the old disabled button restored** — a test that has never been red is a test nobody has
> checked.
>
> **Still open, and it is a decision rather than a bug:** the artifact lives on the Mac that made it
> and the Hub only proxies it (`standalone.open_worker_artifact` → `peers.studio_request`), so a job
> becomes unreadable when its worker is offline. The stale-address half is **already fixed** — the
> route re-resolves the worker's current origin at request time — so what remains is genuinely "that
> Mac is off". And the Hub not keeping a copy is **deliberate**: `job_storage.py` says *"Image and
> voice generation artifacts live on the worker Macs"* in its own docstring. Changing it means
> copying every output to the controller on every machine in the fleet, so it is put to the owner as
> a decision. **Do not do it quietly.**

> **0.6.104 — four things the job rows were missing, and every one of them was the same mistake.**
>
> The owner sent screenshots of his own Audio and Jobs pages: *"the audio scrubber is appearing and
> disappearing … it shouldn't move the list"*, *"i think we should have a divider or a line between
> them — they all in the same black background"*, *"even on this machine, i can't click show in finder
> too"*, and *"there's is no generation date so i don't know when each of them was generated on."*
>
> **Four reports, one cause: a thing written once per panel or once per row builder.** Five panels
> each hand-rolled their own `ScrollView { LazyVStack(spacing: 12) { ForEach } }`; six row builders
> each hand-wrote the same "status · model" `HStack`. So a fix had to be made in five places and a
> new fact had to be added in six — and the date was added in none.
>
> 1. **Show in Folder was disabled on every row of every machine, including his own.**
>    `public_item` dropped `artifact_path` from every item it published, so `canRevealArtifact` read
>    nil, the `fileExists` check was never reached, and the button was dead everywhere. The rule the
>    strip came from is real and is kept — another Mac's path names a file on that Mac — so
>    `local_artifact_path` publishes the path only when `peers.is_local_studio` says the producing
>    worker is this one. `worker_artifact_url` and `worker_accepted_params` stay withheld.
> 2. **One `RecentRows`** draws the row and then the `Divider()`, the way the Jobs page always had.
> 3. **The transport is always on screen**, takes an optional job, and is inert with a stated reason
>    until something is loaded — so pressing Play no longer pushes the list down.
> 4. **One `jobMetaLine`** renders `generatedAtLabel`, which is `batch.created_at` through the same
>    `moment(_:)` the fleet pages use. `moment` is `nonisolated` now: it is a pure function, and a
>    value type has to be able to date itself.
>
> **The tests were rewritten from membership to shape, and that is the durable part.**
> `test_generation_time.py`, `test_jobs_tabs.py` and `test_chat_token_speed.py` each asserted
> `job.generationSuffix` *once per row style* — "six rows, six occurrences". That is a test that
> passes for as long as nobody adds a row and has to be hand-edited for every new fact, which is
> exactly how the date reached no row at all. They now assert one call site and six rows drawing it.
> `test_recent_job_lists.py` went 3 → 14 tests; new `test_local_artifact_reveal.py` (14) pins both
> halves of the path contract; four checks were added to `tests/native/api_main.swift`.
>
> **Gate: 1475 tests, OK (11 skipped), 612 s, exit 0. `native/build.sh` green.**
>
> ⚠ **Run the gate with `.build/hub-gate/bin/python`, not `.build/hub-tests/bin/python`.** The
> handoff named the command and not the interpreter, and the wrong venv produced **24
> `ModuleNotFoundError` errors in `test_chat_worker` (no `mlx_lm`) and three espeak failures** —
> 32 red tests that were all one wrong `python`. `.build/hub-gate` has both pieces. This is the same
> class as a stale drive index: a full gate run, read as a product failure, that was the harness.
>
> **0.6.101, 0.6.102 and 0.6.103 shipped without this file being updated** — the Join button sending
> the typed token, the one-token screen, and the five-capped studio lists. Their record is
> `CHANGELOG.md` and `release-notes.md`; this line is the correction, not a substitute.
>

> **0.6.97 — the check that keeps each drive's front page honest was refusing to run, and saying so
> in a message that read like a path.**
>
> `packaging/kit_stamp_index.py` exists to derive the kit index's Story and Gateway rows from the
> installers actually on the drive, and to rewrite every backticked `*.dmg` in the kit's own
> instructions. It was written to close a bug recorded **three times** — a drive index naming `0.6.63`
> beside a `0.6.70` bundle, `0.6.70` beside `0.6.89`, Story `1.4.219` beside a `1.4.234` installer.
>
> **It had never once succeeded.** `prepare-kit.sh` passes the bare volume name it loops over, and the
> script did `Path(sys.argv[1])` — so `KH-STUDIO-NOVA` was a **relative** path, the index was looked
> for under the current directory, and the write refused. Every drive, every time, since the script
> was written. Found while refreshing the four drives to 0.6.96 and reading a log line that had been
> printed in full and ignored four releases running:
>
> ```
> no kit index at KH-STUDIO-NOVA/Video Generation Kit/Studio Hub KH/README.md
>    kit index NOT stamped — see above
> ```
>
> **That is a real path missing its `/Volumes`, and it reads like a path.** The drive's summary line
> three lines below says `launch-PASS`, which is about the app and not the index. So nothing was
> ever red. The rows were right for four releases **by luck**: the repository template the drives are
> copied from happened to name current versions. Only the *next* release would have shown it — as the
> same stale row this script exists to prevent, in the document an operator reads to answer "which
> build is on this drive".
>
> Fixed at both ends, deliberately: a bare name is now read as a volume (`KH-STUDIO-NOVA` →
> `/Volumes/KH-STUDIO-NOVA`), an explicit path is passed through untouched, **a refusal names an
> absolute path and asks whether that was the drive**, and `prepare-kit.sh` passes
> `/Volumes/$VOLUME`. The script is defensible alone; the caller was still corrected, because the
> caller is what was wrong.
>
> **Two stranded `if __name__ == "__main__":` guards were found and moved in the same sweep** — one
> inside `stamp()` in the stamper (unreachable after its `return`), and one **mid-file** in
> `tests/test_document_inventory.py`, which meant running that file directly silently skipped the
> entire kit-index class. Same accident, same shape; the second is how tests stop running without
> anything going red. Both files are clean; three tests were added (`resolve_drive`, an explicit path
> untouched, and a refusal printing where it looked).
>
> **Verified on the four drives, not just in tests:** ORICO and UGREEN stamped **during** this
> refresh — the fix landed between volumes — and NOVA and ATLAS were re-stamped afterwards. All four
> now carry `Story Studio 1.4.235` and `GatewayKH 0.2.67` **derived from the installers beside them**,
> in both the kit copy and the drive-root copy.
>
> **The drives carry 0.6.96 and were deliberately not re-staged for 0.6.97.** Nothing in this release
> is inside `Studio Hub.app`: the changes are this stamping tool, its tests, and the version/document
> files. Re-copying 4 × 5.4 GB would buy a version string and nothing else, and the drive's own index
> correctly describes the drive it is on — which is the point of the fix.
>
> **A gap in this file's own release loop was found and fixed below:** step 3 named `--image-runtime`
> and `--music-runtime` but not `--video-runtime`, `--lance-runtime` or `--yue2-runtime`, so the
> documented command would stage a bundle with no Video lane. The staged 0.6.96 manifest proves all
> three were passed. The loop now shows the command that was actually run.
>
> **0.6.92 — the fleet work has landed** (per-node credentials so a removal sticks, promotion with a
> handover the cluster follows on its own, Fleet token rotation, and the Connections page showing who
> has joined and when each Mac was last seen).
>
> **0.6.91 — the distribution kits are current, and their index documents can no longer drift.**
>
> **Every drive is at 0.6.91 and every verifier says PASS.** `verify-kit.sh` on each volume reports
> app 0.6.91 (92,896 files, 5.37 GB), identity `0.6.91 92d7642…`, 0 quarantined files, and the app in
> the kit starting and answering its health check:
>
> | Drive | Kit | Packages | Kit library | `Studio Hub KH.md` | Lance |
> |---|---|---|---|---|---|
> | KH-STUDIO-NOVA | 0.6.91 | **30 / 30** | 132.4 GB (123.8 GB checked) | `0.6.91 · 92d7642` | shipped |
> | KH-STUDIO-ATLAS | 0.6.91 | 28 / 30 | 118.8 GB (108.2 GB checked) | `0.6.91 · 92d7642` | not shipped |
> | KH-STUDIO-ORICO | 0.6.91 | 28 / 30 | 118.8 GB (108.2 GB checked) | `0.6.91 · 92d7642` | not shipped |
> | KH-STUDIO-UGREEN | 0.6.91 | 28 / 30 | 118.8 GB (108.2 GB checked) | `0.6.91 · 92d7642` | not shipped |
>
> **Lance: corrected 2026-09-28.** This said *"Lance is on NOVA only, deliberately … those manifests
> say 'not shipped', which is true."* **That was wrong, and the bytes say so.** Both Lance packages
> were physically on **all four** drives; on ATLAS, ORICO and UGREEN the processor's
> `model/config.json` was **1301 bytes where the manifest records 1373**, so verification refused it
> every time and the video pack could not be adopted either (it names the processor as a companion).
> NOVA carried the corrected file. So `28/30` was not a shipping decision — it was **a stale file
> failing verification, written up as a choice**, which is worse than either. All four drives now
> carry the corrected bytes, and the kit index, the drive's own index and `release_kit.py`'s manifest
> all agree on **30 of 30, `models_not_shipped: []`, no problems, 115.3 GiB**.
>
> **Three defects were found by preparing the drives, and all three are fixed at their cause:**
>
> 1. **The kit's index documents were three releases old.** `README.md` named Story Studio **1.4.219**
>    and GatewayKH **0.2.44** against kits carrying 1.4.234 and 0.2.60; `Studio Hub KH.md` — the page
>    whose job is to say which build an operator has — said **0.6.58 / `eea477e`**, and the
>    repository's own copy was worse (GatewayKH **0.2.2**). **Nothing copied them**: `prepare-kit.sh`
>    refreshed the app, the Technical folder and the kit manifest and never touched the index pages,
>    so every drive kept whatever it was born with. They are copied on every prepare now, the Studio
>    Hub row in `README.md` names no version at all (it points at the drive's generated
>    `focused-release-manifest.json`), and **the two version lines in `Studio Hub KH.md` are stamped
>    from the app being placed on the drive** — `CFBundleShortVersionString` plus
>    `implementation_commit` from that bundle's own `release-manifest.json`. The first attempt read a
>    key that does not exist (`commit`) and stamped `0.6.91 · unknown` on four drives; the key is
>    `implementation_commit`.
> 2. **The kit was a subset of the catalogue — 24 of 30.** Every kit library now carries the six the
>    catalogue adds: **YuE2 3B and its decoder** (on no drive before this), **LTX-2.3 22B**,
>    **gemma-3-12b** (LTX's text encoder) and, where the volume already had the bytes, Lance, in the
>    layout a package verifies against. Each drive's `.studiohub/installed.json` records them after a
>    **full rehash on that drive** — a copy is not an installation until the bytes are read back,
>    which is why `release_kit.py` called packages "not shipped" while they sat on the disk.
> 3. **Story Studio and GatewayKH were never refreshed either, and GatewayKH's installer was
>    missing.** The drives offered **Story Studio 1.4.219** and a `SHA256SUMS.txt` that named
>    `installers/gateway-kh-0.2.44.dmg` — **for an `installers/` folder that did not exist**. All four
>    drives now carry `Story Studio KH 1.4.234-arm64.dmg` and `GatewayKH/installers/gateway-kh-0.2.60.dmg`,
>    with every checksum file **regenerated from the file beside it** and the two READMEs stamped from
>    the installer's name and (for Gateway) the `studio_hub.SUPPORTED_STUDIO_HUB_VERSION` its own
>    source declares. Superseded installers went to each drive's `_trash`.
>
> **Four tools, all in `.build/` (untracked, like the DMG and kit scripts):**
> `kit-add-models.sh` (hard link when the volume has the bytes, copy when it does not),
> `kit-verify-models.sh` (rehash a drive's library into its index, **adopting companions before their
> dependents** — catalogue order answers `dependency_missing` for ever otherwise),
> `kit-refresh-apps.sh` (Story + Gateway installers and their checksums) and `kit-reprepare-all.sh`
> (prepare and verify all four drives). `prepare-kit.sh` now takes `STAGE_DIR` instead of a hard-coded
> `.build/StudioHub-0670` — that hard-coding is how a drive gets prepared from an old build while the
> release moves on.
>
> **Gate: `unittest discover -s tests -p 'test_*.py'` → 1407 tests, OK (11 skipped), 600 s, exit 0.**
> **Released 2026-09-28.** `dist/StudioHub-0.6.91-92d7642.dmg` — **1,939,227,793 bytes**, sha256
> `430bcd114a02b4e389cd98d78f3201af8fa07527f74639c5681f979211aa0b60`. Installed and live: 0.6.91.
> `implementation/voice-runtime` and `main` are both `92d7642`.

> **0.6.90 — the distribution kits are current, and their index documents can no longer drift.**
>
> **The drift, found while preparing the drives for distribution.** The kit's two index documents
> named versions *three releases old*: `README.md` said Story Studio **1.4.219** and GatewayKH
> **0.2.44** against kits carrying 1.4.234 and 0.2.60, and `Studio Hub KH.md` — the page whose entire
> job is to say which build an operator has — said **0.6.58 / `eea477e`**. The repository's copy was
> worse than the drives': GatewayKH **0.2.2**. **Nothing copied them**, which is why: `prepare-kit.sh`
> refreshed the app, the Technical folder and the kit manifest and never touched the index documents,
> so each drive kept whatever it was born with. Both are now copied on every prepare, and the Studio
> Hub row in `README.md` no longer states a number — it points at the drive's own
> `focused-release-manifest.json`, which is generated from the bundle **on that drive** and cannot
> disagree with it.
>
> **The kits were a subset of the catalogue.** The drives carried **24 of 30** packages. Every kit
> library now carries the six the catalogue adds — **YuE2 3B and its decoder** (on no drive before
> this), **LTX-2.3 22B**, **gemma-3-12b** (LTX's text encoder) and, where the volume already held the
> weights, **Lance 3B and its processor** — in the layout a package verifies against, recorded in each
> drive's own `.studiohub/installed.json` after a **full rehash on that drive**. A copy is not an
> installation until the bytes are read back, and that is why `release_kit.py` called packages "not
> shipped" while they sat on the disk. Where the exploration library already had the bytes they were
> adopted by **hard link**: 15.6 GB on NOVA with no new bytes written.
>
> **Two tools, in `.build/` (untracked, like the DMG and kit scripts).** `kit-add-models.sh` places a
> package in the kit layout — hard link when the volume already has it, copy when it does not — and
> `kit-verify-models.sh` rehashes a drive's library into its own index, **adopting companions before
> their dependents** (`yue2-3b-mlx` requires `yue2-vae`; verifying in catalogue order answers
> `dependency_missing` for ever). `prepare-kit.sh` now takes its stage from `STAGE_DIR` instead of a
> hard-coded `.build/StudioHub-0670`, which is how a drive gets prepared from an old build while the
> release moves on.
>
> **The four drives, as they now stand** (`verify-kit.sh` → PASS on all four):
>
> | Drive | Kit | Packages | Library | Note |
> |---|---|---|---|---|
> | KH-STUDIO-NOVA | 0.6.90 | **30 / 30** | 115.3 GiB | the only drive carrying Lance |
> | KH-STUDIO-ATLAS | 0.6.90 | 28 / 30 | 100.7 GiB | Lance not shipped |
> | KH-STUDIO-ORICO | 0.6.90 | 28 / 30 | 100.7 GiB | Lance not shipped |
> | KH-STUDIO-UGREEN | 0.6.90 | 28 / 30 | 100.7 GiB | Lance not shipped |
>
> **Lance is on NOVA only, deliberately.** The owner is not pursuing it and asked for it to be kept
> rather than removed; copying 15.6 GB to three more drives would buy nothing, and those kit manifests
> say "not shipped", which is true and is what the app offers as a download.
>
> **Gate: `unittest discover -s tests -p 'test_*.py'` → 1407 tests, OK (11 skipped), 588 s, exit 0.**
> **Released 2026-09-28.** `dist/StudioHub-0.6.90-20d1795.dmg` — **1,941,403,910 bytes**, sha256
> `PENDING`. Installed and live: 0.6.90. `implementation/voice-runtime` and `main` are `20d1795`.

> **0.6.89 — the app said "Not installed" about a package that was installed.**
>
> The owner asked twice, and the second time was the tell: the Hub answered `files_verified` /
> `eligible` for `yue2-3b-mlx` while the picker read `missing`. The picker reads the **worker's**
> catalogue, and three defects were stacked there, each hiding the next:
>
> 1. **`MusicCache` checked every manifest against one global engine constant.** `catalog.ENGINE` is
>    `music-mlx` (ACE-Step's token); YuE2's manifest correctly says `music-yue2`, so the cache raised
>    *"Music manifest engine does not match the worker"* before looking for a single file. Models now
>    carry their own `ModelEntry.manifest_engine`, read via `manifest_engine_for`. **`runtime` (the
>    executor in this worker) and the manifest's `engine` (the Hub's routing token) are different
>    facts** — for ACE-Step they are `ace-step` and `music-mlx`, for YuE2 both are `music-yue2`.
>    Conflating them is what cost this release.
> 2. **The companion check ran without the catalogue that names companions.** `preflight` takes a
>    `catalog` map to resolve `requires_packages`; the worker passed none, so `yue2-3b-mlx` — which
>    requires `yue2-vae` — answered `dependency_missing` for ever, however well it was verified.
>    `model_packages.load_catalog(directory)` is the worker-side reader (the Hub keeps its own, which
>    degrades per manifest because it feeds a route) and `MusicCache` passes it.
> 3. **The route flattened every failure to `"missing"`.** `manifest_unavailable`,
>    `runtime_unsupported` and `engine_root_missing` all reached the operator as *"download it"*. The
>    route publishes the state the worker measured, with its reason. **Do not re-flatten it:** a
>    refusal that names its cause and is renamed by its caller is the defect.
>
> `preflight` also refused the engine it ships (`runtime_unsupported` for anything not ACE-Step or
> Stable Audio), so YuE2 could never report as installed even with a readable manifest; both
> self-runtime engines now pass through to the package answer.
>
> **Live proof, not just tests:** the Music worker's own catalogue now answers
> `vanch007/mlx-Yue2-3B → cache=cached, runtime_ready=True` (ACE-Step is `missing` because it really
> is not in this Mac's library — it lives on the SSD kit), and `preflight` returns `cached` with its
> checkpoint root.
>
> **Gate: `unittest discover -s tests -p 'test_*.py'` → 1407 tests, OK (11 skipped), 590 s, exit 0.**
> **Released 2026-09-28.** `dist/StudioHub-0.6.89-86e5a45.dmg` — **1,941,667,170 bytes**, sha256
> `1edd2b926fdd79d70455b041bf3e34323262b1c4dd3593acca73647adbd59456`. Installed and live: 0.6.89.
> `implementation/voice-runtime` and `main` are both `86e5a45`.

> **0.6.88 — the video verdicts, and six things the app could not show you.**
>
> **Two owner verdicts, one cancelling a run.** **Lance is not pursued** — the owner tried its output
> and the quality does not hold up — so the 16 GB Lance run `qualification/README-video-hardware-test.md`
> had been asking him for is **cancelled**. Nothing is removed: the runner stays pinned and staged,
> both packages stay in the catalog, and its measurements stay published, because a model kept as a
> known spare is not a model being recommended. **LTX-2.3 at 1024×576 true 16:9 with audio is
> accepted** (*"close to acceptable"*), and it now carries the statistic that decides its tier,
> measured through the shipped worker's own job path (job `e9caa8c4…`, image-to-video, 49 frames at
> 24 fps, 2.04 s with an audio track, 258.0 s): **16.92 GB peak process footprint** against **10.05 GB**
> on the MLX allocator. The allocator alone reads as though a 16 GB Mac fits; the footprint says it
> does not, so the tier stays **24 GB minimum / 32 GB recommended** and 16 GB stays refused by name.
> The LTX manifest's `memory_evidence`, its capture spec and the frozen digest moved together, and
> `docs/model-benchmarks.md` was regenerated — **the generated page and its generator must be
> changed together; editing a manifest alone fails `test_model_manifest_catalog`.**
>
> **An environment lesson worth keeping.** The first 576p attempt died at 15 % after 13 s with
> `[METAL] Command buffer execution failed: Impacting Interactivity` — macOS's GPU watchdog firing on
> a command buffer that held the GPU while the display wanted it, **not** the lane — and the identical
> command completed on retry (258.0 s, `done`). If a video job fails that way, retry once before
> believing it.
>
> **Six reports from the owner's own session with the app.**
>
> 1. **Buttons stopped moving.** Each row action was sized by its own label, so the column shifted as
>    the list scrolled. `RowActions` + `rowAction()` give every action one width (104 pt) in one fixed
>    column (214 pt, `layoutPriority(1)` so a long prompt wraps beside it rather than under it) on
>    every Studio page and on the Jobs tab. `Check Original Result` is now `Check Result` — a label
>    that cannot fit its own column will drift again.
> 2. **Every result can be found.** No row could answer *where is the file?* The Hub has always
>    recorded the worker's `output_path` as `artifact_path`; the app now decodes it and every finished
>    row offers **Show in Folder**, disabled with its reason when the artifact is not on this Mac.
> 3. **Every row says what it was asked for.** `requestFacts(for:)` prints the controls per modality
>    (Image: steps/guidance/seed; Music: length/steps/guidance/shift/seed/instrumental; Video:
>    resolution/ratio/length/audio/mode/seed; Audio: character count and speed) — one function read by
>    every row, so the Studio pages and the Jobs tab cannot describe one job two ways. The clip's own
>    measured facts (`width`, `height`, `frames`, `frame_rate`, `has_audio`) were already on the Hub's
>    item and are now decoded, so a finished 1024×576 clip no longer reads as "2.0 s · 381 KB".
>    Frames print **only where a frame rate exists**: the Hub copies a raw `frames` count for Music and
>    Voice items too, where it is a sample count, and a song read "441000 frames" beside its length.
> 4. **Video rows have a preview**, and Preview plays the clip in place. `VideoThumbnail` fetches
>    through the Hub, writes to the app's own `Playback` folder (`playbackFile(named:)` — a view never
>    invents a path), asks AVFoundation for a frame **0.1 s in** — frame zero of an image-to-video clip
>    is the still that was submitted — and caches the downscaled frame.
> 5. **The Audio page has a transport**: play/pause, stop, a scrub bar, `0:07 / 0:30`, and the speed
>    the clip was generated at, plus the script's character count on every finished row. The Music
>    lyrics field gained the count its prompt box already had.
> 6. **YuE2 is installed and verified.** It read as not installed for two separate reasons: **the SSD
>    kits were built at 0.6.70 and do not carry YuE2 at all** (the open kit-refresh item), and the
>    downloaded copy was in the layout it arrived in (`model/`, `vae/` at the top) rather than
>    `<library>/<package_id>/<revision>/…`. Both packages are now in that layout, **hard-linked** so no
>    byte is duplicated, and verified by full rehash — the Hub answers `files_verified` /
>    `dispatch_state: eligible` for `yue2-3b-mlx` and its `yue2-vae` companion. The VAE's two licence
>    files were missing from the download and **byte-identical copies were already in the model
>    package**, so they were linked from there: the publisher's own bytes, checked against the
>    manifest's hashes.
>
> **One test changed with the code, not around it.** `tests/test_jobs_tabs.py` asserted the literal
> `job.durationSeconds` inside `videoJobRow`; the measured length now reaches the row through
> `assetFacts`, so the test asserts both halves (the row reads the derivation, the derivation reads
> the measured fields) and keeps `Preview` forbidden only where it meant *another Studio's* artifact.
> `tests/native/api_main.swift`'s video fixture carries the clip facts, the worker path and the video
> params, and pins the two new lines by value.
>
> **Gate: `unittest discover -s tests -p 'test_*.py'` → 1401 tests, OK (11 skipped), 558 s, exit 0;
> `native/build.sh` green (boundary, API contract and lifecycle checks).**
>
> **Released 2026-09-28.** `dist/StudioHub-0.6.88-65cb5e7.dmg` — **1,938,831,262 bytes**, sha256
> `c670514f81b593bf3b44bb7362129562844bc9fff12fc95e017c439f85e929af`. Installed and live:
> `hub-ready.json` → `0.6.88`, and the route the Music page reads answers `files_verified` /
> `eligible` for `yue2-3b-mlx` and `yue2-vae`. `implementation/voice-runtime` and `main` are both
> `65cb5e7`. The row renders used to check the layout are in
> `qualification/ui-rows-20260928/` (rendered offscreen against the live Hub, so they are the pages
> that ship).

> **0.6.84 — the model lists were empty, and the switch reached the Video page.**
>
> **A live defect, found by a test written for something else.** The Video page needed a test for the
> audio switch, so the native fixture gained a video model carrying the lane contract the running Hub
> actually publishes. The catalogue stopped decoding — and it turned out the app on this Mac had been
> in that state since the video lane began publishing canvases:
>
> - **`enum` is not always strings.** The video lane publishes `"enum": [2, 4]` for
>   `duration_seconds` and `[24]` for `frame_rate`; `NativeLaneParameter` decoded `enum` as
>   `[String]`, so the response failed with `typeMismatch … parameters[4].enum[0]`. The lane's values
>   are read as JSON values (`optionValues`) now and rendered as text for pickers (`options`), so `2`
>   is the integer the lane published and shows as `2`.
> - **A snake_case `CodingKey` can never match under this app's key strategy.** `perform` sets
>   `.convertFromSnakeCase`, which rewrites the JSON's `aspect_ratio` to `aspectRatio` before
>   matching — so `case aspectRatio = "aspect_ratio"` in `NativeLaneCanvas` was unreachable and every
>   canvas failed with `keyNotFound: aspect_ratio`.
>
> **Why one bad row emptied every picker:** `/api/hub/catalog` is decoded whole. Measured against the
> running Hub 0.6.83 with the app's own types: **33 models failed to decode before, 33 decode now**,
> with the LTX lane's five canvases, its `["2", "4"]` duration options and its `audio` switch. The
> fixture keeps a video row with both shapes, so either regression empties the list and fails the
> completeness assertion by name.
>
> **The switch is on the Video page.** Every boolean parameter the selected model publishes gets a
> toggle, with the lane's own note under it. A lane that publishes none (Lance) gets no control; an
> untouched switch is left out of the request so the lane's own default decides; a model change drops
> a choice the newly selected model does not publish; and a switch with no published default is
> disabled rather than drawn as off, which would be this app answering for the lane. The store owns
> the pruning (`snapVideoSwitchesToPublished`), so a second caller cannot forget it.
>
> **`native/render-page.sh` gained `STUDIOHUB_RENDER_VIDEO_MODEL=<repo>`** so the controls below the
> picker can be looked at without selecting a model by hand. Evidence:
> `qualification/native-video-switch-20260927/video-page-with-published-switch.png`.
>
> **Gate 1384 passed / 11 skipped / 1111 subtests; `native/build.sh` green.**

> **0.6.85 — five documents still said the Lance runner was not adopted, and it was.**
>
> Found while re-verifying the adoption for the goal's evidence: the runner is pinned
> (`f8ecc5a8…`), staged (`runtimes/video-lance`), measured twice and published eligible — and five
> places still said otherwise. All five now agree with the code:
>
> | Where | What it said |
> | --- | --- |
> | `components/video/app/backend/generation.py` — the module docstring | "``video-lance`` … is **not adopted into this build yet**" |
> | `packaging/capture_model_manifests.py` | "neither the lance-mlx runner nor the weights are adopted here yet" |
> | `packaging/video-runtime/smoke_imports.py` | the `None` mapping is right, its reason was stale |
> | `qualification/README-video-hardware-test.md` | "offered nowhere" and "not something these commands can measure" — **the one document whose job is to say which run to do** |
> | `tests/test_model_manifest_catalog.py` | a digest comment describing a row that no longer exists |
>
> The hardware README now carries the measured numbers (8.11 GB `relay`, 20.0 GB `parallel`, both by
> sampled `footprint phys_footprint_peak`) and the honest remainder: **no 16 GB machine has run it —
> that is the owner's run**, and the card's claim stays `[V]`. Also fixed there: `SDD` → SSD.
> **No behaviour changes in this release.**
>
> **Gate 1384 passed / 11 skipped / 1111 subtests; `native/build.sh` green.**

> **0.6.86 — YuE2 adopted into the Music lane, and its tier measured rather than claimed.**
>
> **A third music engine, and the first that cannot share an interpreter.** `mlx-Yue` pins
> `mlx==0.32.2` while the Voice runtime (and therefore the Music overlay) carries `mlx==0.31.2`, so
> YuE2 runs in its own pinned Python 3.12 tree — `packaging/yue2-runtime/build.sh`, the port at
> `9253ed133343406947bde7b67d43c7a63fb39d99`, MLX 0.32.2, pinned by content at
> `YUE2_RUNTIME_SHA256 = 75465dc4f1621423511f2500f6d6dec8b7cf3248ce60fc07895350aac41d4fb4` and
> staged as `runtimes/music-yue2`. The worker spawns `yue2_runner.py` with it
> (`generation.yue2_runtime_python()` resolves env → the sibling of the worker's own tree → the
> checkout → `.build/yue2-runtime`), reads one JSON object per line, and the **bridge samples its
> own footprint** because the memory is in the child.
>
> **The owner asked what the minimum machine is. The model card publishes no memory table at all**,
> so the answer is three measured runs on this 32 GB Mac, peak by sampled
> `footprint phys_footprint_peak`:
>
> | Configuration | Audio | Wall | Peak |
> | --- | --- | --- | --- |
> | 8-bit, mode off, 600 tokens | 24.0 s | 58.6 s | **10.79 GB** |
> | 8-bit, 900 tokens, **through this app's bridge** | 36.0 s | 103.2 s | **11.81 GB** |
> | 8-bit, mode off, 3000 tokens | 120.0 s | 429.8 s | **11.93 GB** |
>
> **8 GB cannot run it in any configuration this port offers** (the AR is the only quantizable
> part; 4-bit saves ~1 GB against an 11.9 GB peak), **16 GB is the minimum**, 24 GB is comfortable.
> Receipts: `qualification/yue2-music-results-20260927/`.
>
> **Two packages.** `yue2-3b-mlx` (9 files, 9.92 GB) and the decoder companion `yue2-vae` (7 files,
> 0.53 GB). The generator's capture **includes `ar-bf16.safetensors`** — the 4.33 GB file the 8-bit
> AR was quantized from — because the port's `verify_conversion` refuses a converted tree without
> it. That is why the owner had to pull it before anything could run.
>
> **The lane refuses what the engine cannot do, by name:** lyrics are required (it sings; the
> refusal points at ACE-Step and Stable Audio), steps are bounded by the port's two documented
> configurations (8/32), guidance by its protocol's 0–20, mode by its three, and seconds are
> converted to the port's token budget at the measured ~25 tokens per second.
>
> **One test stopped testing a list:** the music contract test intersected published parameters with
> a hard-coded four-name set, so it failed on the new engine's honest `mode`/`precision` while still
> passing if a declared parameter were dropped. It compares both directions now.
>
> **Two records were behind the fleet, and tests caught both.** The generated model record
> (`docs/model-benchmarks.md`) maps engines to lanes in the generator, and that table did not name
> `music-yue2` — so the package could ship and be silently absent from the page. It learned the
> engine, and the page gained the two measured rows that until now lived only in `qualification/`:
> **Lance** (8.11 GB `relay` / 20.0 GB `parallel`, 184.9 s / 329.5 s) and **YuE2** (10.79 / 11.81 /
> 11.93 GB), each naming its receipt. Every cell in the measured table now states its unit — the
> configuration-qualified keys (`peak_mlx_gb_default`, `generation_seconds_low_ram`) were dropping
> it. `research/st01-runtime-contracts.json` also refused the new engine until told what it is; that
> entry exposed its `video-lance` reason still saying the runner "is not adopted into this build
> yet", false since 0.6.82, and it now carries the tree and the two measured runs instead.
>
> **Gate: `unittest discover -s tests -p 'test_*.py'` → 1401 tests, OK (11 skipped), 573 s, exit 0;
> `native/build.sh` green.** Two notes worth keeping. First, **that is the gate** — the same command
> the project has always used (`docs/implementation-plan.md`), and running `pytest` with an explicit
> list of test files is *not* equivalent: it collects `tests/hub_regression/cases/*` in-process,
> where they have no `reset`/`fixture_monitor` fixtures, because the gate runs them in an isolated
> child interpreter through `tests/hub_regression/test_regression.py`. Doing that produced 44 bogus
> "fixture not found" errors and an hour of chasing them. Second, **the stager refuses to ship a file
> Git does not track**: `stage_voice.py` failed with *"imports components/music/app/backend/yue2,
> which is not in the bundle"* until the new files were `git add`ed, because staging ships
> `git ls-files`, not the working tree. That guard is why an untracked module cannot reach a DMG.
>
> **Released 2026-09-27.** `dist/StudioHub-0.6.86-3a08e02.dmg` — **1,938,565,858 bytes**, sha256
> `bc461a1e66c5f0effe864278961e628184c119c572dea785a3bcfa6091583ec1`. Installed and live:
> `hub-ready.json` → `{"version": "0.6.86"}`, six runtimes in the bundle (`image`, `music`,
> `music-yue2`, `video`, `video-lance`, `voice`), and the route the pickers read —
> `/api/hub/model-packages` — answers **30 rows** (28 before) with `yue2-3b-mlx` on engine
> `music-yue2` publishing `mode`, `lyrics` and `precision` in its `lane_contract` and no default for
> `lyrics`, plus its `yue2-vae` companion. `implementation/voice-runtime` and `main` are both
> `3a08e02`. Model state is `not_installed`: the weights are in the owner's `~/StudioHubModels/
> yue2-3b-mlx/`, not in the app's library, so the lane publishes the model without claiming it is
> runnable from where it sits today.

**A note on the DMGs named below.** The release loop moves each superseded DMG out of `dist/`, so
`dist/` holds exactly the build just made; **a DMG path in an older entry here is a record of what
shipped, not a file still on disk.** `_trash/dist/` holds whatever history has not been cleared, and
`_trash/` is the owner's to sort out — the 0.6.69 and 0.6.70 DMGs named further down are gone.

> **0.6.83 — sound is a choice, and 16:9 is finally 16:9.**
>
> **The audio switch.** `lane_parameters` appends `{'name': 'audio', 'type': 'boolean',
> 'default': True}` **only when `model.generates_audio`** — LTX shows it, Lance publishes nothing
> because its t2v is silent. `validate_request` resolves it (absent ⇒ `True`; accepts `1`/`0`/
> `'true'`/`'false'`) into `params['audio']`; `run_ltx` sets the pipeline **attribute**
> `generate_audio = False` (the pack's decode/mux honors it, and the pack's own CLI does exactly
> this). The artifact gate follows the request: a track is required only when `audio` was left on,
> and **a track that appears when silence was asked for is refused**.
>
> **The sentence that must not be lost:** the model denoises picture and sound **together**, so
> unticking `audio` **does not shorten the run** — it skips the audio decode block and writes an MP4
> with no audio track. The parameter's `note` publishes that, in the contract, not just in the UI.
> A checkbox that implies a saving it does not make is worse than no checkbox.
>
> **A true-16:9 canvas: `576p` `16:9` = 1024×576, duration `[2]` only.** Measured on this Mac
> `[M]`: 49 frames, 24 fps, seed 42, **351.6 s**, **9.36 GB peak MLX Metal**, 1,403,485 B, clean —
> receipt `qualification/ltx-video-results-16x9-20260927/` (`measurement.json` + `run.log` +
> `generated.mp4`). Both LTX `measured_runs` and `measured_runtime.measured` now carry it, so the
> contract answers "what does 1024×576 cost" with a measurement instead of silence. 4 s at this
> canvas is 55,872 latent tokens, past the 30,000 cliff, so the canvas publishes 2 s and the
> validator refuses 4 s by name.
>
> **Lance at 16:9 was tried and is not offered.** 512×288 is on Lance's 16-px grid and renders
> (25 frames, **11.0 GB** peak, **784 s**), but **the subject degrades badly at that width** — see
> the contact sheet in the owner's `~/Desktop/Video samples/` — and the card only validates square
> configurations. The manifest's `reason` records the attempt and the refusal, so the next person
> does not repeat it. The clip is kept at
> `qualification/lance-video-results-16x9-20260927.mp4` as the evidence for that sentence.
>
> **A request nobody made used to be refused.** Naming a canvas without a duration fell back to
> LTX's default 4 s even where that canvas has no 4-second configuration, so `576p 16:9` (the
> default the picker now shows for it) produced `1024x576 … not published at 4s` from the lane's own
> validator. The duration default is canvas-aware now: the model default if that canvas accepts it,
> else the canvas's longest published duration.
>
> **Doc drift fixed in the same change:** `docs/lane-execution-contract.md` still called
> `video.generation` "a forward declaration — no video worker ships", two releases after one did;
> it now documents the shipped lane and both of its video-specific fields (`canvases`, `audio`).
> `model-manifests/` was re-captured for both video packages (LTX `af066411…`, Lance `e8633b91…`).
>
> **What is NOT done:** Story Studio does not yet send or display the `audio` option — its video
> request and video screen are the next change, in its own cycle. The 16 GB hardware run is still
> the owner's.
>
> **0.6.82 — the second engine runs, and the lane stops being LTX-shaped.**
> `lance-mlx` declares `requires-python >= 3.12` and `ltx_pipelines_mlx` needs 3.11, so
> "model-agnostic lane" turned out to mean **two pinned runtimes and an interpreter per engine**.
> `packaging/lance-runtime/build.sh` builds the second tree (Python 3.12.14 + lance-mlx at
> `f8ecc5a86d28b94bd8dc688c200c3e22ffafff4a` + MLX 0.31.2), pinned at
> `LANCE_RUNTIME_SHA256 = 48dd2bd2d34ea9561cc66a02461505220889651983bd6b7e3430d14b5d31ff40`, and
> `stage_voice.py --lance-runtime` stages it to `runtimes/video-lance` beside the LTX tree.
> `generation.lance_runtime_python()` resolves it (env override → the sibling of the worker's own
> tree → the checkout), and `run_lance` spawns `lance_runner.py` with it — the runner's own CLI is a
> **stub** at the pinned commit, so the library API is the only surface. The bridge streams one JSON
> object per line (loading / generating / muxing / ok) and writes the MP4 with **this app's ffmpeg**
> via `IMAGEIO_FFMPEG_EXE`, so imageio never downloads its own.
>
> **Measured `[M]`, twice, in the two modes the policy chooses between:**
>
> | Mode | Config | Peak footprint | Wall |
> | --- | --- | --- | --- |
> | `relay` (what a 16 GB Mac gets) | 256², 13 frames | **8.11 GB** | 184.9 s |
> | `parallel` (this 32 GB Mac, through the worker's own routes) | 256², 25 frames | **20.0 GB** | 329.5 s |
>
> Both by sampled `footprint phys_footprint_peak` in the process doing the work. The card's 16 GB
> claim stays `[V]`: 8.11 GB with no memory pressure is a floor, and the 16 GB run is the owner's.
> Receipts: `qualification/lance-video-worker-results-20260927-first/measurement.json` (job
> `0f259095f44a4c03958e64119f435b47`, `done`, 256×256, 2.084 s, **no audio track** — Lance's t2v is
> silent and the LTX-only audio check correctly did not reject it) and
> `_orchestra/evidence/lance-first-run-20260927.json`.
>
> **The lane stopped assuming LTX's shape.** Canvases, frame rate, frame grid and the latent ceiling
> were module constants; a Lance request would have been offered 704×448 at 24 fps on an `8k+1`
> grid, outside everything its runner was validated on. They are per model now (`VideoModel.canvases`,
> `frame_rate`, `frame_grid`, `latent_grid`, `latent_temporal_divisor`, `max_latent_tokens`), with
> LTX's values as the defaults so nothing about LTX changed. Lance publishes square 256²/512² at
> **12 fps** on a **`4k+1`** grid — measured: 16 frames requested, **13** returned — checked against
> its card's own `n_lat ≤ 16,128` production bound rather than LTX's 30,000-token cliff.
>
> **Four defects the adoption found, all fixed here.** (1) The worker's footprint sampler watches the
> *worker*, and for this engine the memory is in the child: the first lane run recorded **57 MB**
> for an ~8 GB job. The bridge samples itself and its number wins. (2) `run_lance` copied five named
> keys from the bridge's final line and dropped the measurement ones — the list is explicit now.
> (3) `lane_parameters` still published LTX's 24 fps, so the Lance default request failed its own
> validator. (4) `packaging/video-runtime/normalize_records.py` hardcoded `python3.11`: it walked
> past the 3.12 tree and would have left every RECORD hashing pre-rewrite scripts whose shebangs
> carry the absolute build path — the machine-specific digest that file exists to prevent. It globs
> `lib/python3.*/site-packages` now.
>
> **A new required companion.** `lance-3b-processor-qwen2-5-vl` (six files, 4.5 MB, captured at
> `66285546d2b821cf421d4f5eb2576359d3770cd3`): the pipeline calls `AutoProcessor.from_pretrained`
> and the weights pack ships only the tokenizer. Without it the runner reaches for huggingface.co at
> load time; the measured runs used `HF_HUB_OFFLINE=1` to prove a node can work with it installed.
>
> **What is NOT done:** the 16 GB hardware run (the owner's), and the lane's own tier verdict for a
> 16 GB host is still inference — the policy resolves `relay` below 18 GiB, and relay is what was
> measured at 8.11 GB. The weights are on the NOVA shelf, sha256-verified, and a hard-linked
> installed layout exists at `/Volumes/KH-STUDIO-NOVA/model-library/_installed` for qualification
> runs (`--models-dir`).
>
> **0.6.81 — the lane's canvases are pairs now, and the pack was resizing the old ones.**
> `CANVAS_CLASSES` was `288p: {16:9: 512x288, 9:16: 288x512}` and `448p: {1:1, 11:7}`, and the LTX
> pack floors each output side to a multiple of **64** on its two-stage path
> (`snap_output_dimensions(height, width, two_stage=True)`, pinned runtime
> `ltx_core_mlx/components/patchifiers.py`). `512x288` is off that grid, so the lane's published
> 16:9 canvas came back **512x256 — 2:1 — while the job's acceptance still said 512x288**, and the
> model's own default canvas was that pair. Measured on the first run of the new SSD harness, which
> is the run that found it:
>
> ```
> two-stage half-res snaps dims to multiples of 64; output will be 512x256 (requested 512x288)
> ```
>
> The table is now `256p: {7:4: 448x256, 4:7: 256x448}` and `448p: {1:1: 448x448, 11:7: 704x448}`,
> every pair a fixed point of the snap, the default is the measured `11:7` at `448p`, and
> `_assert_canvases_are_snap_stable()` refuses to import a table off the grid by naming the entry
> and the size the pack would substitute. **There is no 16:9 entry on purpose:** with both sides
> multiples of 64 the smallest exact 16:9 the two-stage path can produce is `1024x576`, and every
> smaller "16:9" is a 2:1 or 3:2 clip wearing the wrong label.
>
> `lane_contract` gained **`canvases`** — every `(aspect_ratio, resolution)` pair with its exact
> pixels and the durations that canvas accepts, from the same `canvas_durations()` the validator
> enforces. The two parameter enums were never a set of canvases: the gateway built its variants as
> their cross product and published `aspect_ratios` from the union, so Story Studio's picker offered
> `448p + 16:9`, which the lane refuses, and its widescreen request at `288p` was answered with a
> 2:1 clip. The gateway now builds variants from `canvases` (each carrying its own ratio), Story
> sends the ratio of the row it priced, the native Video page snaps its controls onto a published
> pair, and `tests/test_video_worker.py` walks the whole cross product asserting published ==
> accepted.
>
> **Also 0.6.81: the hardware step can run from the SSD.** `qualification/RUN-LTX-VIDEO-WORKER.command`
> drives the shipped worker using the app's own `components/` and `runtimes/video` (no checkout),
> prints the machine, the published tier and the expected verdict before the run — on a 16 GB Mac a
> refusal is that test passing — and writes `measurement.json`. `ltx-video-worker-run.py` resolves
> its hub root `STUDIOHUB_HUB_ROOT` → checkout → `/Applications/Studio Hub.app/Contents/Resources`,
> and its results guard permits the wrapper's own log folder while still refusing to overwrite a
> receipt. `sixteen-gb-ltx-video-run.sh` picks the first runtime that can import `mlx.core` +
> `ltx_pipelines_mlx` and now knows the app's runtime exists — it used to tell a Mac with the app
> that the runtime was missing. `qualification/README-video-hardware-test.md` says which command
> answers which question and what to send back; both commands were copied to each SSD's
> `Technical/` folder. Wrapper run end to end on the 32 GB M2 Max with the bundle's runtime:
> 512x256, 49 frames, 2.0417 s, 1 video + 1 audio track, 103.7 s, peak MLX 9,977,828,986 B, peak
> footprint 15,386,290,304 B. **Gate 1376 passed / 11 skipped / 1081 subtests; native build green.**
>
> **0.6.80 — the app's ffmpeg had no H.264 encoder, so a finished clip could not be written.**
> 0.6.79 put the Voice runtime's `bin` on the worker's PATH; that ffmpeg is built for audio, its
> encoder list has `mpeg4` and no H.264, and it answers `-h encoder=libx264` with exit 0 and
> *"Codec 'libx264' is not recognized"* — so the real failure was
> `ffmpeg encode_single_frame failed: … Unrecognized option 'preset'`. `packaging/video-runtime/
> build.sh` now fetches the same pinned **ffmpeg 8.1** Story Studio ships (osxexperts.net arm64,
> sha256 `ebb82529562b71…`) plus its ffprobe, verifies both on every build, and refuses a runtime
> whose ffmpeg cannot answer for `libx264`. `generation.muxer_problem()` asks the same question at
> catalog time and publishes `ready: false` with the sentence, cached, instead of advertising a
> model that generates for five minutes and then fails at the last step. (This release was shipped
> without a handoff update; the paragraph is written here so the record is not two releases deep in
> one file.)
>
> **0.6.76…0.6.79 — the Video lane, and what the first real chain run found in it.**
> There was no video lane: `components/` was chat, hub, image, music, subtitle and voice, while the
> broker had mapped a `video` modality to `/api/generate/txt2video` and classified it `durable_async`
> since it was written. `components/video/` now serves both of the broker's doors — text-to-video
> (JSON) and image-to-video (multipart `file`) — accepts before the result exists, applies the
> pack's own mixed-precision loader before the pipeline is built, verifies the produced MP4's own
> boxes (ftyp, a video track, a positive duration, and the audio track this pack's runs carry), and
> releases the pipeline after every job. **The memory tier is the load-bearing part:** below a
> model's qualified tier the worker publishes `memory_eligible: false`, which is what stops the
> gateway offering 22B weights to a machine that cannot load them. LTX-2.3 22B (mixed 2–8 bit) is
> qualified at **24 GB** by two matching runs of 704×448×97 with audio (298.5 s standalone, 304.0 s
> through the worker, peak MLX 14,163,847,570 bytes, peak `ri_phys` footprint 16.38 GB); Lance 3B
> is the **16 GB** tier's T2V model, its manifest and tier ship, and its row says the `lance-mlx`
> runner is not adopted yet rather than claiming it runs.
>
> **Two defects the first through-the-chain run found, both fixed in the next release:**
> `0.6.78` — the free-memory floor was copied from the measured footprint (16.5 GB) and the Hub
> answered `waiting for memory: needs ~16.5GB, ~14.2GB free`, deadlocking the lane on a healthy
> 32 GB Mac; a footprint is not a free-memory requirement, and the floor is now 8 GB. `0.6.79` —
> the LTX pack muxes its MP4 with an `ffmpeg` subprocess and the worker's PATH could not see the
> app's bundled ffmpeg, so it generated the whole clip and failed at the last step; the Voice
> runtime's `bin` is on the worker's PATH now, and `muxer_problem()` publishes a readiness reason
> when it is absent. The same release added `reconcile_video_item`, which an uncertain video item
> had been missing (the route fell through to the Image reconciler and answered `unknown Image
> batch`, pinning the machine until discard).
>
> The Video worker runs on its own **Python 3.11** tree (`runtimes/video`), built by
> `packaging/video-runtime/build.sh` from a pinned interpreter plus `requirements.lock.txt` and the
> `ltx-2-mlx` checkout at `3b2e77fd73af5f08961f4436ae979c0579d5deed`, pinned by content in
> `stage_voice.py` (`VIDEO_RUNTIME_SHA256`) and made read-only after the build so a stray import
> cannot move the digest. Manifests: LTX (11 files, 19.12 GB), its text encoder companion (13 files,
> 8.07 GB) and Lance (10 files, 15.63 GB); the capture tool gained `&limit=100` on its tree URL,
> which refreshed every existing manifest's canonical bytes in the same pass. The native app moved
> Video from "Coming later" into Create with a real page, Jobs rows and the lane's own published
> options. **Gate: 1371 passed / 11 skipped / 1057 subtests, exit 0; `bash native/build.sh` green.**
>
> **0.6.75 — the installer can no longer leave a half-installed app, and the disk has a keeper.**
> The install that filled the disk left `/Applications/Studio Hub.app` with **no `Info.plist` and no
> `MacOS`**, and the rollback is what made it unrecoverable: `rm -rf "$TARGET"` could not clear the
> wreckage (the image runtime's `tcl9` directories are `dr-xr-xr-x`, and unlinking an entry needs write
> permission on its *parent*), so the `mv` that should have restored the previous app put it **inside**
> the wreckage instead. `install_local.sh` now (a) **refuses to start when the copy would fill the
> disk**, before quitting the app, (b) rolls back through a `chmod`-then-verify path that stops rather
> than nesting, and (c) counts entries against the stage instead of checking one file — the old guard's
> `runtimes/voice/bin/python3` survives a copy that stopped inside `runtimes/`, which is exactly how a
> bundle with no `Info.plist` passed it. Both guards are proved to fire on the defect by
> `tests/test_install_local.py`; `STUDIOHUB_INSTALL_TARGET` and `STUDIOHUB_INSTALL_DATA` exist so they
> can be tested without touching `/Applications` or the operator's readiness markers.
>
> **`.build/` was the ratchet, not `_trash/`.** The owner cleared `_trash/` by hand and recovered
> **38 GB**; the audit then measured `.build/` at **100.9 GB, 70.7 GB of it 32 superseded staged
> bundles** — every release staged ~2.9 GB and the previous DMG moved to `_trash/`, which is on the
> same volume, so nothing was ever reclaimed. Those 32 stages are now on **KH-STUDIO-NOVA**
> (`_build-archive/studio-hub-kh/`), each verified by sha256 manifest on both sides before the local
> copy was removed — and the check earned its keep twice: it caught a truncated copy from an
> interrupted run, and it caught a "newest by mtime" stage-selection pass that proposed moving the
> 0.6.74 stage this release was built from (`chmod` updates a directory's mtime). **It is automatic now.** `make-dmg.sh` calls `packaging/prune-build-scratch.sh` at the end of
> every release, so `.build/` keeps **exactly one stage** — the one whose `StudioHubBuild` stamp
> matches what is installed, decided by evidence rather than by version, because a development
> re-stage carries the same version as the release it re-stages (0.6.75 left both a `0675` and a
> `0675b`). Everything older is moved to the drive that already holds the biggest archive, each
> file verified by sha256 manifest on both sides first — never deleted. **`.build/` is not only
> stages, and that distinction is the trap:** ~7.4 GB is pinned runtimes (`voice-runtime`,
> `image-runtime-mflux020`, `st03-image-runtime-worker` — kept deliberately for the ST-03 receipt,
> and not rebuildable from this repo), and ~21 GB is model/smoke-test scratch that no release
> reads. The step that keeps
> it that way is `packaging/prune-build-scratch.sh`: it keeps the stage named in `VERSION`, keeps the
> newest of the rest, moves the remainder to a drive, and does nothing when no drive has room.
> **Disk: 462 MB free before → 101 GB after.**
>
> **`8bd8495`…`1e9e7e6`; DMG `dist/StudioHub-0.6.75-5e6ba36.dmg`; installed to `/Applications` and
> verified; the previous DMG moved to `_trash/dist/` — nothing `rm`d.**
>
> **0.6.74 — the Voice worker's background loops started on `import`, not on startup.** Both the
> idle-release ticker and the output-retention loop ran in *any* process that imported
> `backend.main`, a test run included. That made `test_memory_policy.py` fail intermittently
> (`last_release_details.released is False`, previously recorded as a 300 s timeout — it was never a
> timeout: the 5-second ticker unloaded the managers the test had just injected, through the test's
> own settings file), and it let a test session enforce the operator's retention policy against the
> **real** `OUTPUT_DIR`. Both are now on the app's `startup` handler, the pattern Music and Subtitle
> already use; production still starts them (uvicorn runs startup handlers — the installed worker
> logs `Application startup complete.`). `tests/test_background_loops_start_with_the_app.py` pins
> both halves. **`8bd8495`…`a554254` on both branches; DMG
> `dist/StudioHub-0.6.74-b2021fc.dmg` (1.3 GB, 60,436 files, `hdiutil verify` VALID); the previous DMG
> moved to `_trash/dist/` — nothing `rm`d.**
> Installed to `/Applications` as build **`b2021fc`**, `development_build: false`, and verified
> **byte-for-byte identical to the stage** (60,436 files, `shasum` diff empty). Gate: **1345 passed,
> 11 skipped, 1005 subtests**, exit 0; the Voice suite inside it **769 passed, 19 skipped**.
>
> **The internal disk is full, and it is the reason the audio lane is down.** `df` reports
> **462 MB free of 460 GB** (100% capacity). The Hub's own device policy
> (`device_settings.json`: `max_bytes` 50 GB, `retention_days` 30, **`minimum_free_bytes` 1 GB**) then
> refuses every submission whose studio has a managed output root —
> `409 OUTPUT_STORAGE_BLOCKED: managed output storage has insufficient free space`. The message names
> a quota but the failing term is the **free-space floor**, which is why `used_bytes` (60 MB) is
> nowhere near `max_bytes`. **The safeguard is correct; the disk is the problem.** Measured while
> full: image **succeeded** (85 s, 1024×1024 PNG through the whole chain), chat **succeeded** (20 s),
> TTS **refused**. The DMG step is blocked by arithmetic — a 0.6.73 DMG is 1.3 GB and there is
> 462 MB — so no DMG was attempted rather than starting a build that would consume the last free
> space. **Reclaiming it needs an external drive: moving anything to `_trash/` cannot free the
> internal volume, because `_trash/` is on it.**
>
> **RESOLVED the same day, by the owner, who found the actual ratchet.** He emptied `_trash/` by hand
> and reclaimed **38 GB** (462 MB → **39 GB free**). The audio lane came straight back — a real TTS
> artifact, 3.28 s of 24 kHz speech, 12 s wall — and the 0.6.74 DMG then built and verified.
> **The lesson is bigger than one cleanup: every release moved a DMG and a bundle into `_trash/` and
> nothing ever left it, so the disk only ratcheted one way.** `_trash/` is empty now, but **`.build/` is
> the same ratchet and was never curated: 100.9 GB, of which 32 staged app bundles
> (`StudioHub-0647` … `StudioHub-check`) hold 70.7 GB** — each rebuildable from git in about two
> minutes, while the DMG is the durable artifact. That is the next ~70 GB, and it is a decision rather
> than a cleanup: `.build/` is gitignored scratch, so the never-`rm` rule protects the wrong thing
> there. Biggest local consumers, measured: `.build/`
> **100.9 GB** (`.build/StudioHub-06xx` staged bundles, ~2.9 GB each, superseded),
> `_trash/` **26.3 GB** (`dmg-scratch` 11.6, `app` 9.8, `dist` 4.9), `~/.cache` 29.7 GB,
> `~/StudioHubModels` 52.7 GB. All four kit drives are mounted (ATLAS included).
>
> **The install step lost a bundle and it was recoverable, but the script has two real holes.**
> `packaging/install_local.sh` moved the old app to `/Applications/.studiohub-old-<pid>`, copied the
> new one, and the copy stopped part-way through `Contents/Resources` — leaving an app with **no
> `Info.plist` and no `MacOS`** while `/Applications/Studio Hub.app` still existed. Recovered by
> moving the wreckage to `_trash/app/Studio Hub.app-0.6.74-partial-install-20260926` and installing
> with `ditto` (which reports `Permission denied` on the image runtime's **symlinks** — harmless, the
> links are present and the totals match). Two holes worth fixing: the guard checks one file
> (`runtimes/voice/bin/python3`), which a partial copy can still satisfy, and `rm -rf "$OLD"` cannot
> delete the **read-only** `runtimes/image/lib/tcl9` tree (`dr-xr-xr-x`), so a 1.3 GB
> `.studiohub-old-*` is left in `/Applications` every time.
>
> **0.6.73 — the Chat page had no outer margin, and the video runner wasted two runs before it
> worked.** Chat was the only one of the five workspace pages without `.padding(28)`, so its form
> column met the window's edges while the picker below it sat correctly inset;
> `test_audio_layout.py` now pins that margin on all five **and it is the assertion that was
> missing** — every other check in that test passed while this was wrong. The runner preferred a
> 10 GB partial copy of the model over the complete 17.81 GiB one on the drive (the failure reads as
> a corrupt model, which is the wrong place to look), and passed `--low-ram-streaming`
> unconditionally to a pack that cannot take it. **The first successful LTX run is recorded in the
> changelog with its full footprint — 298.5 s, peak MLX 13.19 GB, peak process 16.38 GB on this
> 32 GB M2 Max — and the 16 GB claim is not supported by it.** Nothing is refreshed on the drives.
>
> **0.6.72 is released, installed and has its DMG. The four drives are NOT refreshed, deliberately**
> — see the 0.6.71 note below for why a kit refresh is a whole-kit action, not the tail of a code
> release. `dffee03` on both branches; DMG `dist/StudioHub-0.6.72-dffee03.dmg` (1.2 GB); installed and
> answering `{"version":"0.6.72","build":"dffee03"}`. `model_limits.*` now carries **`source_kind`**
> beside its sentence, so the gateway relays *whose* number a limit is instead of guessing `provider`
> for every stated value. GatewayKH's half is **0.2.48**, and **the chain is verified closed on the
> live wire**: `max_output_tokens: 16384` now arrives with `max_output_tokens_source: worker_default`,
> where it previously said `provider` while the sentence beside it said the opposite. Previous bundle
> preserved at `_trash/app/Studio Hub.app-0.6.71`, previous DMG at `_trash/dist/` — nothing `rm`d.
>
> **0.6.71 is released, installed and has its DMG. The four drives are NOT refreshed, deliberately.**
> `971d12c` on both `implementation/voice-runtime` and `main`; DMG `dist/StudioHub-0.6.71-971d12c.dmg`
> (1.2 GB, 60436 files); installed to `/Applications` and the Hub is answering
> `{"version":"0.6.71","build":"971d12c"}`; the gateway sees the local location reachable at 0.6.71.
> **1338 tests green** (1344 collected, 11 skipped, 1000 subtests) and `bash native/build.sh` passed
> its boundary, API contract and lifecycle checks. The guard is `tests/test_stall_visibility.py`,
> proved to fail on the defect. The previous installed bundle is kept at
> `_trash/app/Studio Hub.app-0.6.70` and the previous DMG at `_trash/dist/` — **nothing was `rm`d.**
>
> **Why the drives were left alone.** A kit refresh is a whole-kit action: each drive carries all
> three apps plus **two** README indexes that both name every app's version, and OWNER-6 in
> GatewayKH's tracker is the row where exactly that drifted — both indexes on all four drives read
> `0.6.63` while the bundle beside them was `0.6.70`. Refreshing only the Hub would move one app's
> bundle and leave both indexes, and possibly the other two apps, describing the old set. Do it as a
> deliberate full-kit refresh with `prepare-kit.sh` per drive and `verify-kit.sh` after each, not as
> the tail of a code release.
>
> **0.6.70 is released, installed, on all four drives and pushed** — `7eb09ac`, DMG
> `dist/StudioHub-0.6.70-30ecef1.dmg` (1.2 GB, 60436 files), every drive `RESULT: PASS`, 1336 tests
> green. Three things a reader of this file needs to know about it:
>
> **1. The Hub's job deadline is per generator now, and the number in a lane contract is not one
> number.** `standalone.deadline_floor_seconds(modality)` feeds **both** the published
> `deadline_seconds` and the proxy's enforced timeout, so what a scheduler reads is what the Hub
> holds the job to. Video **7200**, chat **1800**, voice **1800**, image/transcription/music **900**.
> **GatewayKH must refresh discovery**; their cached row read `4096` / `900` until this release, and
> anything of theirs that caches a single deadline is now wrong.
>
> **2. A stall is visible, and it is not a timeout.** `components/hub/backend/stall.py` marks a job
> whose worker has gone quiet as `stalled_seconds` on every job row. It fires only for active states,
> never for an unstarted or terminal one, never for a studio with no receipt, and **it never kills
> the job**. This is what makes a 30-minute ceiling safe rather than merely long.
> **App-side loose end: nothing in the native app consumes `stalled_seconds` yet**, so the operator
> sees it in the API and not on screen.
>
> **3. Z-Image-Turbo 6B MLX 4-bit is qualified on 8 GB** — the owner's M1 Mac mini run. The floor
> moved 16 -> 8 through `capture_model_manifests.py`, its producer, and the copy step moved it from
> `CARRY_BELOW_TIER` (now empty, and kept) to `PROVEN_SMALLER`. **The engine default also completed
> on that Mac and that is not licence to drop the low-RAM rule** — 8.72 GB footprint on an 8.0 GB
> machine, by paging 3443 MB. `Z_IMAGE_LOW_RAM_BELOW_BYTES` stays at 24 GB.

**Three open items closed on 2026-09-26, and each guard was proved to fail on the defect it was written for.** *SH-4*: FLUX.2 Klein 4B MLX 4-bit published `79.34 s` for a scene it described exactly, and **the number was in no file in this repository**. The stored run of that scene is **105.943 s** (`qualification/st03-image-real-inference.md`, job `af4913b2`, 2026-09-15) — and the host was wrong too: the row claimed a 32 GiB M2 Max, the record says **Apple M4, 16 GiB**, which is a measurement on the tier the fleet runs. `packaged_inference` said `pending` while that completed run sat in the tree. *SH-8*: `max_output_duration_s` was enforced by the worker and published by no family, so no contract offered a bound that was being applied; the `qwen3-tts` family declares it now, and it is the only family that honours it. *SH-18*: Voice's "lane decides" exception is the only one in the fleet (Image, Music, Subtitle and Chat all fill an omitted field unconditionally) and now has four tests on its boundary.

**The lesson each of the three taught, in one line each.** A published number needs an artefact, so the test opens the file the row names — a scene description is not a source. A shape in a table nobody references is not a contract. And **a guard the runner does not collect is not a guard**: the first version of the SH-18 test class was named `LaneDecidesBoundaryTests`, pytest collected nothing, and the suite reported the same count as before — which is how a boundary goes unguarded while looking guarded.

**SH-15(a) is closed as hardened, not as fixed, and the distinction is deliberate.** The `native/build.sh` cancellation flake was hunted deliberately: **60 runs of the native suite with the pre-fix behaviour restored, and 40 with the fix — 100 runs, zero failures.** So the original occurrence is *unattributed*. Two real timing races were found in the assertion's own test and removed: the hold's semaphore was process-wide (a `signal()` arriving after its hold ended stayed in its count and would release the *next* hold early), and the assertion read the cancellation count once, racing the `stopLoading` callback that arrives on URLSession's own delegate queue. **The code comment says the cause is unproven rather than claiming it.** A later session that sees this message again should read that comment before believing it was fixed.

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

**Next: the video phase — begun 2026-09-26 at the owner's instruction**, after the LLM phase was shelved (*"let's shelf llm for now. since qwen 3.5 works now"*) and with SH-7, the 8 GB Z-Image-Turbo **6B MLX 4-bit** run, deferred to when he is next at that Mac (*"we can circle back later for the z-image 8gb"*). **Video is not an extension of the shipped engine**: mflux 0.20.0 exposes 30 `mflux-generate-*` commands and every one is an image model, so the phase carries a new runtime dependency the Image phase did not. The whole LLM surface is **one package** — `Qwen3.5-9B 4-bit`, floor 16 GB, `qualified` on the three 16 GB Macs and excluded on 8 GB. So there is nothing to prune there either, and the open question is the one the 8 GB exclusion creates: **chat runs on 5 of his 7 Macs.**

**Video phase status — 2026-09-26. Nothing is shipped and nothing is half-built: there is no video worker, no video manifest, no video engine and no `lane_contract`, and `StudioFunction.video` still reports `hasEngine = false`.** What exists is research and two paused downloads. The engine finding above is the load-bearing one. Four candidates were assessed, every one with its quantization named:

| Candidate | Quantization | Package | Reach |
|---|---|---:|---|
| Wan2.2 **TI2V-5B** | q8 (q8 saves disk only — it dequantizes to BF16 at load; the encoder is bf16) | 16.94 GiB | 24 / 32 GB |
| LTX-2.3 22B distilled-1.1 (Mrbizarro) | int4 g64, transformer blocks only | ~27.2 GiB + 7.5 GiB Gemma | 24 / 32 GB |
| LTX-2.3 22B RAM-12GB (baa-ai) | mixed 2–8 bit, avg 4.6; **254 layers at 2–3 bit** | 17.81 GiB + 7.5 GiB Gemma | **16 GB claimed, 0 runs behind it** |
| Lance 3B Video | bf16 | ~15.6 GB | **8 GB measured** — but T2V only |

**The Hub is already video-aware in six places and worker-aware in none.** `broker.MODALITY["video"]`, `MODALITY_RETRY_CLASS`, `artifact_metadata._MEDIA_TYPES` (`video/mp4`, `video/webm`, `video/quicktime`), `capabilities.OPERATION_BY_MODALITY`, `memory_control.SUPPORTED_MODALITIES` and an **untested `img2video` → `/api/generate/video2video` path that appears in `broker.py` and nowhere else in the tree.** `WORKER_FOR_MODALITY`, `ENGINES` and `_ENGINE_ROUTE` all need a video entry, and `tests/test_hub_modality_vocabulary.py` currently **asserts video's absence**, as do two assertions in `tests/native/main.swift`. Placement needs nothing — `_placement_key` is one factory for every lane and reads each model's own published floor.

**Two things the owner must decide before building, because they change what gets built.** Whether **image-to-video is a hard requirement** (Story Studio retired text-to-video and sends I2V only), and whether a **2–3 bit mixed quant** is acceptable to reach the 16 GB machines. The good news found late: **I2V is a property of the pipeline, not the vendor's script** — `DistilledPipeline` and `TI2VidOneStagePipeline` both accept `image`, so every LTX pack can do I2V even though the baa-ai pack's own `generate.py` and `webapp.py` expose only a prompt. And **`low_ram_streaming` defaults to `False`** in both pipelines, a lever the baa-ai pack never enables, while the sibling LTX-2.3 q8 pack measured block streaming at **~2.8 GB peak with bit-identical output**. Testing that one boolean is the cheapest route to a real 16 GB lane.

**Downloads are paused at 2.3 GB by the owner's instruction** (*"wait, why are we downloading the models right now? you didn't tell me more about each models first or discuss thoroughly"* — he was right). They live on the drive, not the root volume, because the root has 13 GiB free and the packages do not fit; all four drives carry a reserved `model-library/video-models/` shelf and all four were empty. Every transfer resumes with `curl -C -` without re-fetching a byte. The LTX runtime is **installed and verified working** at `.build/ltx-2-mlx` (Python 3.11, `mlx 0.32.2`, 12 pipelines) — see the install recipe in `research/video-fleet-shortlist-2026-09-24.md`, which is not guessable and took six attempts.

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

**Shipped state:** implementation commit `49792c4`, installed to `/Applications`; DMG
`dist/StudioHub-0.6.69-49792c4.dmg`. `development_build: false`, `runtime_qualified: true`.
**Kits:** all four at `0.6.69`, `files 60435`, `"problems": []`, each `RESULT: PASS`, libraries
**24 installed / 24 advertised**, still carrying **mflux 0.20.0**.
**Gate on this commit:** `tests/` **1322 passed, 11 skipped, 998 subtests**; `bash native/build.sh`
green; `components/voice/app/tests` green (including the new `test_system_info.py`).
**Pushed:** `main` and `implementation/voice-runtime`.
**Superseded:** `4badc84` (0.6.68) and the rest in `_trash/dist/`.

**`.build` is a managed set too, and it filled the volume.** It held 99 GB — twelve superseded
staged bundles at 2.9 GB each plus audit trees — and the root volume fell to **2.1 GB free**, where
one DMG build failed. 35 GB was moved to
`/Volumes/KH-STUDIO-NOVA/_trash/build-archive` (moved, not deleted), taking the volume to 26 GB.
**Moving into this project's `_trash` would have freed nothing: it is on the same volume.** Only
emptying `_trash` returns the space, and that is the owner's. Keep one or two recent bundles locally
and archive the rest.

**Watch the root volume:** it had **6.5 GB free** during this release, and one DMG attempt failed on
it before succeeding on the retry. `.build/` holds nine staged bundles at ~3 GB each.

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

## 0.6.92 — the fleet work: removal that sticks, a cluster that follows its own promotion, and a token that can be rotated

**Read this section before touching the fleet routes.** Everything below is in this release and
measured; the receipts are in `qualification/` and the gateway's own receipts are in
`gateway-kh/repository/qualification/`.

### What changed, and why each one was load-bearing

1. **Per-node credentials (A8).** Every join mints a credential for *that* node; the controller
   stores only its SHA-256 in the roster row and returns the value once, in the join answer. The
   node stores it `0600` (`<data>/.node_credential`) and presents it on every call home. The shared
   Fleet token keeps exactly two jobs: **enrolment** and the controller↔Gateway direction. This is
   what makes an eviction real, and it is the prerequisite of removal meaning anything.
   **Backwards compatible:** a node that joins with only the Fleet token is issued its own
   credential on that first join, so the rollout needs no flag day. `POST /api/hub/fleet/adopt`
   accepts `node_credential` and writes it for the app.
2. **Removal is an eviction (A2, the owner's decision B).** `remove()` drops the row *and* the
   credential hash and records the id, name, address, date and who removed it in `fleet_nodes.json`.
   `join()` refuses a removed id **by name** with *"this controller removed this Mac on <date>;
   re-admit it from Connections to join again"* (`NodeRemovedError`, HTTP **403**). Re-admitting is
   `POST /api/hub/fleet/nodes/<id>/readmit`, Hub-token only. The old two-step 403 is gone: a node
   may remove **itself** with its own credential, and a Fleet-token caller may remove nothing
   (a shared credential identifies nobody).
3. **Promotion and handover (A3, the owner's decision A).** Promotion already minted a fresh group
   credential; it now also clears the borrowed node credential. `POST /api/hub/fleet/handover`
   (Hub token) takes `{controller_url, token}` and pushes the new head to every node over the
   channel the controller already has, using `peers.sync_fleet_token` — which is the same mechanism
   A4's rotation needs, so there is one push and two callers. The old controller records
   `handed_over_to`/`handed_over_at` and **stops accepting dispatch**: `accepts_customer_jobs()`
   returns false, every remote submission is refused with a sentence naming the new head, and
   `refusal_for_remote_work()` is one function so the two submission routes cannot drift.
   **The honest limit is in the answer, not only in a document:** a node that cannot be reached is
   named in `machines`, `verified` counts what actually moved, and there is no silent success.
4. **Rotate the Fleet token (A4).** `POST /api/hub/fleet` with this Mac's **Hub token** mints a new
   token, pushes it to every node with `sync_fleet_token`, and verifies each write with the *new*
   credential before committing locally; `manual` counts the Macs that need SSH over Tailscale.
   The same route with the **Fleet token** is how a controller tells a node its group membership —
   one route, two scopes, deliberately.
5. **Members, last seen and honest lights (B1/B4/B2).** `last_seen_at` is stamped for every node
   that answered a probe, in **one batched write** per fleet read (`note_seen` returns the stamp and
   the caller sets it on the rows it is about to publish, so the read that caused the stamp reports
   it). `inventory()` publishes `removed[]` and `summary.removed`, and every node row carries
   `has_credential` and `credential_issued_at`. The node's health light is an authenticated read
   that uses the **Fleet token** (the only credential `/api/hub/resources` accepts from a node), and
   a refused credential reads differently from an unreachable Mac.
6. **Whisper is published once per machine (M9).** The Subtitle studio and the Voice studio's STT
   surface serve the same weights; the aggregate catalogue published both, which was 96 phantom
   machine-model rows in the gateway's fleet view. De-duplicated on `(machine, model identity)`,
   with `duplicate_rows_folded` published rather than silently subtracted.
7. **Quantisation on every manifest (M14).** All 30 manifests carry `quantization` **and**
   `quantization_source` — the value read from the identifier the publisher chose, or `null` with
   the reason when no precision is stated. `capture_model_manifests.py` preserves both across a
   re-capture, and `tests/test_model_manifest_catalog.py`'s frozen digests were refreshed because
   the manifest content genuinely changed.
8. **Documentation truths (C7).** `docs/gatewaykh-handoff-0.6.63.md` documented a **two-step** join
   and omitted `POST /api/hub/fleet/adopt`, which is why a scripted enrolment produced Macs the
   controller calls `unauthorized`; it now states all three steps and what the node must keep. The
   README's "rotate independently" claim names the route that does it.
9. **In the app (A2/B2/B4/H12/A3/A4/C6).** Removal is terminal in the UI: the re-join loop stops,
   the group credential is dropped, and the page keeps the controller's own sentence. A node's own
   `controller_id` is exposed. Role changes, leave and remove are confirmed. A "Send here" pin is
   cleared when its machine leaves and hidden on a Mac that cannot dispatch to the pool. Promote,
   Hand over and Rotate each report the Hub's per-Mac answer, and a partial result is never shown
   as success.

### New and changed routes (all additive)

| Route | Credential | What it does |
|---|---|---|
| `POST /api/hub/fleet/nodes` | Hub, Fleet (enrolment) | Join. Returns `{"node", "credential"}`; **403 by name** for a removed id; refuses a silent takeover of an id that already holds a credential |
| `DELETE /api/hub/fleet/nodes/<id>` | Hub (any id), node credential (own id only) | Remove. Hub-token removal is an **eviction**; self-removal is "leave this group" |
| `POST /api/hub/fleet/nodes/<id>/readmit` | Hub only | Undo an eviction |
| `POST /api/hub/fleet` | Hub (rotate) / Fleet (set membership) | One route, two scopes |
| `POST /api/hub/fleet/handover` | Hub only | Push the new head to every node and retire this controller |
| `POST /api/hub/fleet/adopt` | Hub only | Now also stores `node_credential` when the app hands it back |

### Traps this release created or confirmed

- **A revoked credential is `401`, not `403`.** After a removal the node's credential matches no
  roster row, so it is simply not a credential any more. The app's re-join therefore presents its
  own credential first and retries **once** with the Fleet token, which is the only way to hear the
  removal sentence (403). Both statuses are deliberate.
- **`fleet_nodes.json` changed shape** to `{"version": 2, "nodes": {...}, "removed": {...}}`. The
  old flat `{node_id: row}` file is still read (and upgraded on the next write), so a controller
  keeps its fleet across the update; a row from before credentials reads as "the next join must
  prove itself", never as "trusted".
- **Two tests were updated deliberately, not deleted.** `tests/test_fleet_credentials.py` pinned a
  flat 403 on the removal route and `tests/test_standalone_connections.py` pinned the same refusal;
  both encode the old decision, both now state the new one, and the route-level tests were rewritten
  so the controller and the node have **different** ids — the first version gave them the same id,
  which is why it was green while every real leave was refused.
- **The native fixture answered a stubbed 200** for the removal call, which is why a 100%-broken
  action survived its own suite. It now answers by host and credential and serves the removal
  sentence where the real Hub serves it.


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

> **Order matters, and this loop had it wrong for one step. Corrected 2026-09-28.**
>
> **Commit before you stage, and push after you have verified.** The loop used to end *"stage,
> install, build the DMG, push"*, and a build that runs before its own commit can only stamp the
> **parent** commit — `stage_voice.py` writes `implementation_commit` from `git HEAD`, and
> `make-dmg.sh` names the file from it too. Measured twice on 2026-09-28: the 0.6.96 stage carried
> `789bd41` while the release was `5684a6a`, and GatewayKH's 0.2.68 build stamped `01c5992` against a
> pushed `01f1eac`. The drive indexes record that stamp, so **a lagging stamp travels all the way to
> the operator's front page** — the same class of defect as the drive index naming a version the
> drive does not carry.
>
> So the order is: write the version and both documents → **commit locally** → gate → stage →
> install → DMG → **then push** (both branches). Committing first costs nothing and the push still
> happens only after the artifact is verified, which is what the old order was protecting.
> **The `VERSION` value is the release; the stamp is where it was built.** If they disagree, the
> stamp is the younger truth.
>
> Applied to 0.6.98: committed as `a7021b6`, staged from it, and the bundle's own
> `release-manifest.json` reads `"version": "0.6.98"` / `"implementation_commit": "a7021b6…"`.

1. `VERSION` → the new number.
2. Write the `CHANGELOG.md` and `release-notes.md` entries (both, every time; the release notes
   are read by the owner, the changelog by a developer).
3. Stage:
   **`.build/image-runtime-mflux020` is the only Image runtime to stage.** This line used to read
   `.build/st03-image-runtime-worker`, the **superseded mflux 0.17.5** tree preserved for the ST-03
   receipt — and following it shipped 0.17.5 in **every release from 0.6.63 to 0.6.67**, after
   0.6.59–0.6.62 had shipped 0.20.0. Five releases published Image numbers (Z-Image's 166 s / 236 s,
   its peaks, its `measured_on`) for an engine the app did not contain. `stage_voice.py` now pins the
   qualified tree by content digest and refuses anything else, so a stale line cannot repeat it. A
   version string would not have caught it: both trees carry an `mflux` package and accept the same
   commands. Found by the owner's 8 GB Z-Image run, whose harness checks the engine and refused — and
   whose own advice named this path.

   ```sh
   .build/voice-runtime/bin/python3 -B packaging/stage_voice.py \
     --runtime .build/voice-runtime --native-shell .build/StudioHub \
     --image-runtime .build/image-runtime-mflux020 \
     --yue2-runtime .build/yue2-runtime \
     --output ".build/StudioHub-<n>/Studio Hub.app"
   ```

   **LTX-2.3 video is retired (owner, 2026-10-01), so there is no `--video-runtime` here any more.**
   While `ltx-2-3-22b-ram-12gb-mlx` is on `components/retired_models.py`, `stage()` ignores the flag
   (it prints a NOTE) and the release has **no `video` component** - no `runtimes/video` (517 MB), no
   `video_worker.py`, no `components/video` - which is correct: a Video worker whose one manifest is
   retired would stop the whole Hub at start-up. The YuE2 runtime used to be staged *inside* the Video
   stager and is now staged by Music's (`--yue2-runtime` alone is enough). The Hub's installer table
   (`runtimePaths`) still knows `video`, so a release with or without it installs; the BRIDGE label
   in `_stage_video` stays until no Mac runs < 0.6.121. The paragraph below describes the pre-retirement build.

   **Corrected 2026-09-28.** This line showed only `--image-runtime` and `--music-runtime`, so
   following it would have produced a bundle with **no Video lane at all** — the three flags above
   were passed by hand every time and nothing recorded them. Checked against the code rather than
   assumed: `_stage_video` opens with `if context['video_runtime'] is None: return None`, and the
   manifest is assembled by iterating the component table and keeping only the stagers that returned
   a record — so no `--video-runtime` means no `components.video`, no `components/video` source, no
   `video_worker.py` and no `runtimes/video`. The staged 0.6.96 `release-manifest.json` is what proves
   the three flags *were* passed: its `components.video` carries `lance_runtime_sha256` and
   `yue2_runtime_sha256` beside the LTX runtime, none of which exist unless those flags are given.
   **Check that manifest after staging** — a missing `components.video` is the whole defect, and it
   is one `json.load` away from being visible.
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
| Image runtime (**the only one to stage**) | `.build/image-runtime-mflux020` — mflux 0.20.0, digest pinned in `packaging/stage_voice.py` as `IMAGE_RUNTIME_SHA256` (`c3acf766…`), which now **refuses any other tree**. `.build/st03-image-runtime-worker` is the superseded 0.17.5, kept for the ST-03 receipt and never staged |
| Music overlay / ACE-Step engine source | **retired in 0.6.116** — `.build/music-runtime` and `.build/music-engine-source` are unused scratch; the stage command no longer takes them |
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

**STATUS OF THIS LIST, corrected 2026-09-26 — read this before the entries below.** The list is an
accumulation from 0.6.63 and **most of it has since closed**, so the entries are kept as the record
of what was found rather than as a statement of what is open. Closed since, each with its own row in
`docs/cross-project-open-items.md`:

- **Voice's below-tier warning** — closed (SH-3, 0.6.69). All three links built, and the reason it
  could never have fired was found underneath them.
- **FLUX.2 Klein 4B MLX 4-bit's `79.34 s`** — closed (SH-4, 0.6.69). The number was in no file in
  this repository; the stored run is **105.943 s**, and the host was wrong too.
- **The model-exposure approval surface** — closed (SH-5 / HUB-12, 0.6.69). Zero callers, nothing
  gated by it, and the gateway verified from its own side that it does not need one.
- **Chat's memory tier** — closed (SH-6, 0.6.69). Measured on three 16 GB Macs; 8 GB excluded by the
  owner's decision and the harness refuses it immediately.
- **The 8 GB Z-Image-Turbo run** — closed (SH-7, 2026-09-26). The owner ran it: **the tier PASSES**
  with low-RAM mandatory, floor moved 16 → 8, and the entry moved to `PROVEN_SMALLER`.
- **ST-13's re-stage** — closed by the 0.6.59 refresh, and the prune has nothing left to do.

### What is actually open, as of 0.6.97

> **Re-read and corrected 2026-09-28 at 0.6.97.** This list was headed *"as of 0.6.71"* while the
> release was **0.6.97** — twenty-six releases later — and item 2 had gone from stale to **false**:
> it described the video lane as entirely unbuilt while the lane was live, published, offered by the
> Gateway and already producing files. See item 2 for the measurements and for what is genuinely
> still open. **A status list nobody re-reads is a status list that lies**, and the version in its
> own heading is the cheapest way to notice.

1. ~~**The app half of stall visibility.**~~ **CLOSED 2026-09-26 (0.6.71).** The Hub published
   `stalled_seconds` on the job rows and the fleet machine rows (`03ea173`) and the app read none of
   it: `HubJobSummary` decoded two fields, `id` and `modality`. All three links are built now —
   `HubJobSummary`/`HubJobDetail` → `NativeJob.stalledSeconds` → the Jobs row draws *"No progress for
   4 min 12 s — still held, not cancelled"* — and `tests/test_stall_visibility.py` pins all three
   together, because any one alone looks like it works. **Advisory only**, per `stall.py`'s own rule
   that a stall is surfaced and never acted on destructively.
2. **The video lane — CORRECTED 2026-09-28, and the correction is the finding.** This item read
   *"Nothing about it is shipped: no worker, no manifest, no `lane_contract` … a 7,200 s deadline
   that is unbacked until the worker exists."* **Every clause of that was false when it was
   written, and had been for some time.** It was checked against the running system rather than
   against another document:

   | The claim | What the live Hub and Gateway answer (0.6.97, 2026-09-28) |
   |---|---|
   | no worker | **`video` worker is live** — `studio_type: video`, `studio_version: 0.6.97`, `online: true`, `ready: true`, serving `video.generation`, 1 slot. `COMPONENT_WORKERS['video'] = _video_spec` spawns it from the release manifest, and the 0.6.97 bundle carries `components.video`. |
   | no manifest | **Four video manifests ship** (`ltx-2-3-22b-ram-12gb-mlx`, `gemma-3-12b-it-4bit` its text encoder, `lance-3b-video-bf16`, `lance-3b-processor-qwen2-5-vl`), and `/api/hub/model-packages` publishes all four as `modality: video`. |
   | no `lane_contract` | **Both generation models publish one.** LTX: operation `video.generation`, eight parameters, `deadline_seconds: 7200.0`, and **two measured runtimes**. Lance: seven parameters, same deadline, none measured. |
   | the deadline is unbacked | **It is backed by measurement.** `runtime_seconds.measured` carries **298.5 s** (image-to-video, 97 frames, 704×448, seed 42) and **351.628 s** (1024×576, 49 frames), both with their receipt paths, `measured_on: 32 GiB M2 Max (Mac14,13), mlx 0.32.2`. |
   | it "won't appear as an offer" | **It is offered end to end.** The Gateway publishes `studio-hub::baa-ai/LTX-2.3-22B-RAM-12GB-MLX` with `modality: video`, `operation: video.generation`, `available: true`, `activation_blockers: []`, modes `image-to-video` + `text-to-video`, `generates_audio: true`. Story has the `motion` surface for `media-type="video"`. |
   | never run end to end | **It has run.** The worker's own `video/jobs.json` holds **13 jobs** — and the 11 failures are all inside one 35-minute debugging window on 09-26 22:33–23:08 (`[METAL] Command buffer execution failed`, `ffmpeg not found on PATH`, `ffmpeg encode_single_frame failed`). **The two runs after that window both succeeded**: 09-26 23:36 → 1,639,307 bytes in 378.0 s, and 09-27 01:25 → 381,087 bytes in 110.3 s. Both `.mp4` files are in the worker's `video/outputs/`. |

   **What is actually still open about video is narrower and more useful than "nothing is built":**

   - **The 16 GB tier is unmeasured, and it is the tier that matters.** Every LTX measurement is
     `measured_on: 32 GiB M2 Max`. The owner's fleet is ~53 machines and **50 of them are 16 GB or
     below**, so the one number the rollout needs is the one nobody has. This is the same open item
     as ledger VIDEO-6, and it has not moved.
   - **The harness that would settle it is not on the drives.** `qualification/RUN-16GB-LTX-VIDEO.command`
     exists in this repository and is in **no** kit's `Technical/` folder — those carry
     `RUN-8GB-Z-IMAGE.command` and `RUN-CHAT-TIER.command` and nothing for video. So the owner
     cannot run the 16 GB video test from a drive even though the LTX pack is on every drive.
   - **Lance is the genuinely unbuilt engine**, and the code says so: `_ENGINE_ROUTE`'s own comment
     reads *"`video-lance` names the lance-mlx source runner it cannot yet"*, its two packages resolve
     to no files in a library, and its `lane_contract` carries no measured runtime.
   - **Two successful runs is thin evidence**, not a reliability claim. Nothing has run since
     09-27 01:25.

   The lesson is the project's own, inverted: `AGENTS.md` warns that *"implemented"* is not *"in the
   build you are running"*. This item was the reverse — **a shipped, live, offered feature written
   down as unbuilt**, which is just as costly, because it sends the next session to build what
   already exists and tells the owner his video lane is missing when it is not.
3. **Chat's packaged page — the measurement half is now done; the acceptance half is the owner's.**
   **Streaming end to end through the Hub was unexercised and now is not.** Measured 2026-09-28
   against the live Hub at 0.6.99 / `9b65a4f`: `POST /api/hub/chat/completions` with `stream: true`
   returns real SSE chunks — incremental `delta.content` (`"the"`, `" chat"`, `" lane"`, `" works"`),
   `model: mlx-community/Qwen3.5-9B-4bit`, a terminal chunk carrying `finish_reason: "stop"` **and**
   `usage {prompt_tokens: 20, completion_tokens: 4, total_tokens: 24}`, `studio_hub.elapsed_seconds:
   0.514`, then `[DONE]` — in 9.2 s wall including the model load. One chat worker live: `chat`
   service, version 0.6.99, online and ready, one slot.
   **What is still true is the other half, and it is not a measurement:** nobody has *typed into* the
   packaged Chat page. Streaming is exercised over HTTP; the page's own render of those deltas —
   cursor, scroll behaviour, a stop button mid-stream, what it shows on a dropped connection — is an
   **acceptance question for the owner**, and no test in this tree answers it. Do not record the lane
   as accepted on the strength of the HTTP result above.
4. **Nothing in the product refuses on memory tier** — a decision, not a measurement, and it matches
   the image precedent of warning rather than refusing.

---

## Appendix — items recorded rather than built

> **Audited 2026-09-28 at 0.6.99, and three of the first four were already done.** This appendix is
> the second open-item list in this file, and it had drifted the same way the first one did: entries
> stayed here after the work landed, so a reader was told to build what existed. Each claim below is
> corrected against the code, with the check that settles it.
>
> **And the file contradicted itself.** The list above says *"Chat's memory tier — closed (SH-6,
> 0.6.69). Measured on three 16 GB Macs"* while this appendix said *"Chat's memory tier is still
> unmeasured."* Both cannot be true, and nothing compared them, because they are two lists in one
> file. **Two lists of the same thing is one list too many** — the numbered list is the one that gets
> re-read, so this appendix is now history and corrections, not a to-do.

- ~~**Voice publishes no below-tier warning, and there are two missing links, not one.**~~
  **WRONG — all three links are built.** Verified 2026-09-28:
  `components/voice/app/backend/catalog.py:1582` publishes
  `"memory_warning": fit.get("hint") if fit and fit.get("state") == "risky" else None`; the Audio
  branch of `combineModels` passes `memoryWarning: item.memoryWarning` (`native/StudioAPI.swift:7537`
  for `blockReason`, `:7576` for the model); and the Audio page renders it
  (`native/StudioViews.swift:2297`, `if let warning = selectedAudioModel?.memoryWarning`) — the same
  render site all six modalities use. **It still cannot fire on this fleet**, which is the part of
  the original note that was right and the reason it was easy to leave here: the copy step skips any
  model above the machine's tier, so no below-tier voice model is installed to be warned about.
- ~~**FLUX.2 Klein 4B MLX 4-bit's `79.34 s` lane-contract runtime traces to nothing.**~~
  **CLOSED 2026-09-26 (SH-4).** The number was never in the tree; the stored run of that exact scene
  is **105.943 s**, in `qualification/st03-image-real-inference.md` and as
  `worker_terminal.duration_seconds` in `evidence/st03-image-real-inference.jsonl` (job `af4913b2`),
  and the host was corrected to *"Apple M4, 16 GiB RAM"* — a measurement on the tier the fleet runs
  rather than on the one machine. Guarded by
  `tests/test_image_loading_mode.py::test_every_published_runtime_is_found_in_the_record_it_names`,
  which opens the file each row names. Re-verified 2026-09-28: `79.34` appears nowhere in the tree
  outside vendored libraries. See `docs/cross-project-open-items.md:191`.
- **The model-exposure approval surface cannot be used, for any modality.** ✅ **Still true
  (re-verified 2026-09-28).** No production file publishes a `genstudio_candidate` block — the only
  references are in tests — and `monitor.candidate_models()`, `model_exposure.approve()` and
  `.revoke()` have **0 callers**. So there is no route or UI by which the owner approves a model for
  the gateway. The chat model is visible in `GET /api/hub/capabilities` as a direct observable
  (`chat.completion`, audit and exposure `null`). Whether this is needed depends on which path
  GatewayKH actually uses.
- ~~**Chat's memory tier is still unmeasured.**~~ **CLOSED (SH-6, 0.6.69)** — measured on three
  16 GB Macs, 8 GB excluded by the owner's decision, and the harness refuses it immediately. The
  entry above had it right and this one had it wrong. The rest of the note still stands and is why
  the tooling is on the drives: `Technical/chat-tier-run.sh` plus the double-clickable
  `Technical/RUN-CHAT-TIER.command`, because Finder has no application bound to a `.sh`.
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
## Native fleet controls — 0.6.179 delivered and rollout completed (2026-10-15)

Status: product source `e99a1b789d3044ded6659fcb9683d2b213973bc0` is integrated on `main`, the primary DMG is verified, normal installation and the paced fleet rollout are complete. This delivery does not enable or qualify the separate private renderer.

Behavior recorded from the native feature: a healthy backend keeps fleet controls reachable when the model catalog read fails; the catalog error remains visible; an unknown roster is shown honestly until a fresh authenticated roster read succeeds; stale rows cannot be removed; and a pending removal confirmation refuses after local services stop, the connection generation changes, the roster is stale or refreshing, or the node is no longer in the current roster. Existing authentication, pause, data, model-readiness and fresh-roster requirements remain intact. The underlying remote catalog error cause is not established.

Receipts and exact source pins:

- Feature checkpoint: `/build/Developer/_orchestra/codex-takeover-20261012/native-controls-ui-qa/native-fleet-controls-feature-checkpoint.json`; feature and remote SHA `d2235dd54cd94fc55a726c28359c9988fbb417a6`; independent review `clean323d6fa2`.
- Native source gate: `/build/Developer/_orchestra/codex-takeover-20261012/native-controls-ui-qa/native-source-gates-root-complete.json`; boundary compile/run exit 0, lifecycle compile/run exit 0, optimized compile exit 0; 3 compiles and 2 runs passed, with no remaining observed owned process.
- Actual external ConnectionsView proof: `/build/Developer/_orchestra/codex-takeover-20261012/native-controls-ui-qa/actual-external-controls-ui-verified.json`; catalog 503 stayed visible, Refresh recovered the roster, stale Remove stayed disabled, stop-before-confirm refused visibly, DELETE/mutations/foreign requests/missing auth were all 0.
- Presentation checks: 24, recorded in the feature checkpoint. These are inherited QA receipts; no tests, compiler, lint, build, media or live API work was run for this metadata preparation.
- Source SHA-256 pins at the feature commit: `native/StudioAPI.swift` `c6eb6193b63e04ee8899aac632c3f9e1bde168276ead1d2c83fc8729e7898417`; `native/StudioViews.swift` `fbb4d880689bcfc3504a53ceff453dcf29edae053ed1af21599b969db15196ed`; `tests/native/api_main.swift` `c0cd9780311cdd5d0495bcf59f09134d18e163f0149a7d51485a0d3ac5df14ff`; `tests/test_native_operator_surfaces.py` `fe5622f959d06c53f9038644bd8b0816a9572b155d7d468fc9defebe346da9c9`.

Release state: `main` integration complete; primary DMG and normal install verified; rollout complete for 59 reachable healthy Macs across 8 groups, with `studio-mac-1106-m2-8-256` unreachable and deferred. The two fresh normal reads were at `2026-10-15T00:58:50.273596+00:00` and `2026-10-15T00:59:49.048952+00:00`; all 8 existing publication receipts were accepted. The baseline controller pause values remain partly unknown: 51 known reachable comparisons passed and 8 baseline controller rows were unknown. The exact completion receipt is `/build/Developer/_orchestra/codex-takeover-20261012/hub-0179-rollout-complete.json`.

Metadata preparation receipt: originals of `VERSION`, `CHANGELOG.md`, `release-notes.md`, and this handoff were preserved before editing at `_trash/source-history/20261015-0.6.179-native-fleet-controls/`. No existing `_trash/` content was changed. No product source, tests, shared render branch, model JSON, generated graph or settings were edited.
